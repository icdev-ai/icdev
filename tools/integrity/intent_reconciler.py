# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Intent reconciler (Mode B).

The **primary external-code path**. When a third-party artifact arrives with no
formal RTM / PRD (the Mode A provenance-aware input), SIPA still has two signals:

  * the **exercised** capability manifest from
    :func:`tools.integrity.capability_extractor.extract` — *what the code can
    actually do*, derived from the AST (never executed); and
  * the **claimed** capability set from
    :func:`tools.integrity.claim_parser.parse_claim` — *what the author says it
    does*, derived from README/docstrings/declared-purpose prose.

``reconcile_blind(manifest, claim)`` judges the first against the second and the
*intrinsic* danger of the code itself, producing ``integrity_findings`` rows in
the exact shape the scanner adapters emit (``source_scanner='reconciliation'``):

  1. **Undisclosed-capability pass.** For every capability the manifest exercises
     that the claim never implies, emit an ``undisclosed_capability`` finding. A
     "JSON formatter" whose code opens a socket has exercised a behavior it never
     disclosed — that gap *is* the Mode B signal. Severity is derived from the
     capability's inherent risk (``constants.RISK_WEIGHTS_CAPABILITY``): an
     undisclosed ``network_egress`` is ``high``, an undisclosed ``process_exec``
     or ``dynamic_code`` is ``critical``.

  2. **Intrinsic-risk pass.** Some shapes are dangerous *regardless* of what the
     author claims. ``dynamic_code`` combined with ``obfuscation`` in the same
     file is the decode-then-exec backdoor shape (``payload = b64decode(...);
     exec(payload)``); it is flagged ``critical`` (``dangerous_api``) even when a
     README cheerfully discloses both. The extractor's per-record
     ``obfuscated_input`` taint link is the strongest form of this signal.

Pure-Python + ``constants`` + the sibling extractor/claim modules. The only
side-effecting entrypoints (``reconcile_and_persist`` / ``assess_blind`` with an
``assessment_id``) append to ``integrity_findings`` via the same RLS-aware path
(``_insert_finding`` / ``_caller_context``) the scanners and capability writer
use, so reconciliation findings can never drift from the rest.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from tools.integrity.constants import (
    RISK_WEIGHTS_CAPABILITY,
    SEVERITY,
)
from tools.integrity.db.init_db import init_db

# Reuse ingest's context/backend/insert helpers so the reconciliation INSERT and
# the tenant/classification stamping match the scanner + capability writers exactly.
from tools.integrity.ingest import _caller_context, _insert_finding

logger = logging.getLogger("icdev.integrity.intent_reconciler")

# This module is the disclosed-vs-exercised reconciler — its findings are tagged
# with this scanner so the UI / risk scorer can group them.
SOURCE_SCANNER = "reconciliation"

# Cap how many call sites a single finding's ``detail`` carries — a backdoored
# blob could exercise the same capability hundreds of times; the finding stays
# auditable without ballooning the persisted JSON.
_MAX_SITES = 25


# --------------------------------------------------------------------------- #
# Severity derivation — capability inherent risk -> integrity_findings.severity
# --------------------------------------------------------------------------- #
# Bands over constants.RISK_WEIGHTS_CAPABILITY (0.0-1.0). Ordered high->low; the
# first band whose threshold the weight meets wins. Chosen so the task's anchors
# hold: network_egress (0.90) -> high, process_exec (0.95)/dynamic_code (1.00) ->
# critical, and crypto (0.40) -> low.
_SEVERITY_BANDS: list[tuple[float, str]] = [
    (0.95, "critical"),
    (0.80, "high"),
    (0.60, "medium"),
    (0.30, "low"),
]


def _severity_for_weight(weight: float) -> str:
    """Map a capability's inherent risk weight onto ``constants.SEVERITY``."""
    try:
        w = float(weight)
    except (TypeError, ValueError):
        return "info"
    for threshold, sev in _SEVERITY_BANDS:
        if w >= threshold:
            return sev if sev in SEVERITY else "info"
    return "info"


def _severity_for_capability(cap_type: str) -> str:
    """Severity an *undisclosed* capability of this type warrants."""
    return _severity_for_weight(RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0))


# --------------------------------------------------------------------------- #
# Input normalization — tolerate the rich manifest or a bare capability set
# --------------------------------------------------------------------------- #
def _normalize_manifest(manifest: Any) -> list[dict]:
    """Coerce ``manifest`` into a list of capability records.

    Accepts the canonical :func:`capability_extractor.extract` output (a list of
    ``{file_path, function_name, capability_type, evidence, line_start, ...}``
    dicts) verbatim, and degrades gracefully for callers that only have a set /
    list of capability-type *strings* (each becomes a minimal record so the
    undisclosed pass still fires).
    """
    if manifest is None:
        return []
    records: list[dict] = []
    for item in manifest:
        if isinstance(item, dict) and item.get("capability_type"):
            records.append(item)
        elif isinstance(item, str):
            records.append({"capability_type": item, "evidence": {}})
    return records


def _normalize_claim(claim: Any) -> set[str]:
    """Coerce ``claim`` into the set of *claimed* capability-type strings.

    Accepts a :func:`claim_parser.parse_claim` result (a dict with
    ``claimed_capabilities``), a bare set / list of capability strings, or
    ``None`` (the pure-blind case where nothing is disclosed).
    """
    if claim is None:
        return set()
    if isinstance(claim, dict):
        return set(claim.get("claimed_capabilities", ()) or ())
    if isinstance(claim, (set, frozenset, list, tuple)):
        return {c for c in claim if isinstance(c, str)}
    return set()


def _site(rec: dict) -> dict:
    """Compact one capability record into a finding-``detail`` call site."""
    ev = rec.get("evidence") or {}
    return {
        "function": rec.get("function_name"),
        "line_start": rec.get("line_start"),
        "line_end": rec.get("line_end"),
        "api": ev.get("api"),
        "evidence": ev,
    }


def _earliest_line(records: list[dict]) -> Optional[int]:
    """Lowest ``line_start`` across records (the anchor line for a finding)."""
    lines = [r.get("line_start") for r in records if isinstance(r.get("line_start"), int)]
    return min(lines) if lines else None


# --------------------------------------------------------------------------- #
# Pass 1 — undisclosed capabilities (exercised but never claimed)
# --------------------------------------------------------------------------- #
def _undisclosed_findings(records: list[dict], claimed: set[str]) -> list[dict]:
    """One ``undisclosed_capability`` finding per (file, capability) gap.

    Records are grouped by ``(file_path, capability_type)`` so multiple call
    sites of the same undisclosed capability in one file collapse into a single
    actionable finding (anchored at the earliest line, every site retained in
    ``detail.sites`` up to :data:`_MAX_SITES`).
    """
    # Preserve first-seen order for stable output across runs.
    groups: dict[tuple[Optional[str], str], list[dict]] = {}
    order: list[tuple[Optional[str], str]] = []
    for rec in records:
        cap = rec.get("capability_type")
        if not cap or cap in claimed:
            continue
        key = (rec.get("file_path"), cap)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    findings: list[dict] = []
    claimed_sorted = sorted(claimed)
    for key in order:
        file_path, cap = key
        group = groups[key]
        weight = RISK_WEIGHTS_CAPABILITY.get(cap, 0.0)
        findings.append(
            {
                "source_scanner": SOURCE_SCANNER,
                "finding_type": "undisclosed_capability",
                "severity": _severity_for_weight(weight),
                "file_path": file_path,
                "line": _earliest_line(group),
                "detail": {
                    "capability_type": cap,
                    "reason": (
                        f"code exercises '{cap}' but no claim source "
                        f"(README / docstring / declared purpose) discloses it"
                    ),
                    "risk_weight": weight,
                    "occurrences": len(group),
                    "claimed_capabilities": claimed_sorted,
                    "sites": [_site(r) for r in group[:_MAX_SITES]],
                },
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# Pass 2 — intrinsic risk (dangerous regardless of the claim)
# --------------------------------------------------------------------------- #
def _intrinsic_findings(records: list[dict]) -> list[dict]:
    """Flag intrinsically dangerous shapes, ignoring what the author claims.

    The headline rule: ``dynamic_code`` co-located with ``obfuscation`` in one
    file is the decode-then-exec backdoor shape and is ``critical``. The
    extractor's ``obfuscated_input`` taint flag (``exec(payload)`` where
    ``payload = b64decode(...)``) is the strongest form of the same signal and is
    sufficient on its own. At most one such finding is emitted per file.
    """
    by_file: dict[Optional[str], dict[str, list[dict]]] = {}
    order: list[Optional[str]] = []
    for rec in records:
        cap = rec.get("capability_type")
        if cap not in ("dynamic_code", "obfuscation"):
            continue
        fp = rec.get("file_path")
        if fp not in by_file:
            by_file[fp] = {"dynamic_code": [], "obfuscation": []}
            order.append(fp)
        by_file[fp][cap].append(rec)

    findings: list[dict] = []
    for fp in order:
        dyn = by_file[fp]["dynamic_code"]
        obf = by_file[fp]["obfuscation"]
        tainted = [r for r in dyn if (r.get("evidence") or {}).get("obfuscated_input")]
        # Fire on a direct decode->exec taint link, or on plain co-presence of
        # dynamic code and obfuscation in the same file.
        if not (tainted or (dyn and obf)):
            continue
        signals = []
        if tainted:
            signals.append("dynamic_code(obfuscated_input)")
        if dyn and obf:
            signals.append("dynamic_code+obfuscation co-located")
        anchor = _earliest_line(tainted or dyn or obf)
        findings.append(
            {
                "source_scanner": SOURCE_SCANNER,
                "finding_type": "dangerous_api",
                "severity": "critical",
                "file_path": fp,
                "line": anchor,
                "detail": {
                    "rule": "dynamic_code+obfuscation",
                    "reason": (
                        "runtime code execution combined with obfuscation/decoding "
                        "— the decode-then-exec backdoor shape; dangerous regardless "
                        "of any disclosure"
                    ),
                    "signals": signals,
                    "capabilities": ["dynamic_code", "obfuscation"],
                    "dynamic_sites": [_site(r) for r in dyn[:_MAX_SITES]],
                    "obfuscation_sites": [_site(r) for r in obf[:_MAX_SITES]],
                },
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# Public reconciliation API
# --------------------------------------------------------------------------- #
def reconcile_blind(manifest: Any, claim: Any) -> list[dict]:
    """Reconcile an exercised capability manifest against a claimed set (Mode B).

    Args:
        manifest: the :func:`capability_extractor.extract` output (list of
            capability records), or a bare iterable of capability-type strings.
        claim: a :func:`claim_parser.parse_claim` result, a bare set/list of
            claimed capability-type strings, or ``None`` (nothing disclosed).

    Returns:
        A list of ``integrity_findings``-shaped dicts
        (``{source_scanner, finding_type, severity, file_path, line, detail}``):
        the undisclosed-capability gaps followed by the intrinsic-risk flags.
        Identical in shape to the scanner adapters' output, so the same
        ``_persist`` path writes them.
    """
    records = _normalize_manifest(manifest)
    claimed = _normalize_claim(claim)
    findings = _undisclosed_findings(records, claimed)
    findings.extend(_intrinsic_findings(records))
    return findings


# --------------------------------------------------------------------------- #
# Persistence — append-only to integrity_findings
# --------------------------------------------------------------------------- #
def _persist(conn: Any, assessment_id: int, findings: list[dict]) -> list[int]:
    """Append every reconciliation finding to ``integrity_findings``; return ids."""
    tenant_id, classification, _ = _caller_context()
    ids: list[int] = []
    for f in findings:
        fid = _insert_finding(
            conn,
            (
                assessment_id,
                f["source_scanner"],
                f["finding_type"],
                f["severity"],
                f["file_path"],
                f["line"],
                json.dumps(f["detail"]),
                tenant_id,
                classification,
            ),
        )
        ids.append(fid)
    return ids


def _summarize(findings: list[dict], assessment_id: Optional[int], finding_ids: list[int]) -> dict:
    """Build the ``{by_severity, by_type, ...}`` rollup returned by the writers."""
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        by_type[f["finding_type"]] = by_type.get(f["finding_type"], 0) + 1
    return {
        "assessment_id": assessment_id,
        "findings": findings,
        "findings_persisted": len(finding_ids),
        "finding_ids": finding_ids,
        "by_severity": by_severity,
        "by_type": by_type,
    }


def reconcile_and_persist(
    assessment_id: int,
    manifest: Any,
    claim: Any,
    conn: Any = None,
) -> dict:
    """Reconcile (Mode B) and persist the findings append-only.

    Opens an RLS-aware connection when ``conn`` is ``None`` (closing it on exit);
    ``init_db`` runs idempotently so this works standalone. Returns the
    :func:`_summarize` rollup.
    """
    findings = reconcile_blind(manifest, claim)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        finding_ids = _persist(conn, assessment_id, findings)
    finally:
        if own_conn:
            conn.close()

    return _summarize(findings, assessment_id, finding_ids)


def assess_blind(
    path: str | os.PathLike,
    declared_purpose: Optional[str] = None,
    assessment_id: Optional[int] = None,
    conn: Any = None,
) -> dict:
    """End-to-end Mode B assessment of an external artifact at ``path``.

    Extracts the exercised capability manifest, parses the claimed set from the
    artifact's own prose (+ an optional ``declared_purpose``), reconciles them,
    and — when ``assessment_id`` is given — persists the findings append-only.

    This is the primary provenance-blind entrypoint: no RTM / PRD required.
    """
    # Imported lazily so the pure reconcile path carries no extra import cost.
    from tools.integrity import capability_extractor, claim_parser

    manifest = capability_extractor.extract(path)
    claim = claim_parser.parse_claim(path, declared_purpose=declared_purpose)

    if assessment_id is None:
        findings = reconcile_blind(manifest, claim)
        result = _summarize(findings, None, [])
    else:
        result = reconcile_and_persist(assessment_id, manifest, claim, conn=conn)

    result["claimed_capabilities"] = sorted(_normalize_claim(claim))
    result["exercised_capabilities"] = sorted(
        {r["capability_type"] for r in _normalize_manifest(manifest)}
    )
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SIPA intent reconciler (Mode B) — reconcile the exercised "
        "capability manifest against the author's claim; emit undisclosed_capability "
        "+ intrinsic-risk integrity_findings. The primary external-code path (no PRD).",
    )
    parser.add_argument("--path", required=True, help="file or directory to assess")
    parser.add_argument(
        "--declared-purpose",
        default=None,
        help="free-text purpose claim to fold into the claimed set",
    )
    parser.add_argument(
        "--assessment-id",
        type=int,
        default=None,
        help="integrity_assessments.id to attach + persist findings to "
        "(omit to print the reconciliation without persisting)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    result = assess_blind(
        args.path,
        declared_purpose=args.declared_purpose,
        assessment_id=args.assessment_id,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"SIPA Mode B reconciliation — {args.path}")
    print(f"  claimed:   {', '.join(result['claimed_capabilities']) or '(none)'}")
    print(f"  exercised: {', '.join(result['exercised_capabilities']) or '(none)'}")
    for f in result["findings"]:
        loc = f"{f['file_path']}:{f['line']}" if f.get("file_path") else "(no file)"
        cap = f["detail"].get("capability_type") or f["detail"].get("rule", "")
        print(f"  [{f['severity']:>8}] {f['finding_type']}({cap}) — {loc}")
    if args.assessment_id is not None:
        print(f"  persisted: {result['findings_persisted']} finding(s)")
    else:
        print(f"  total: {len(result['findings'])} finding(s) (not persisted)")


if __name__ == "__main__":
    main()
