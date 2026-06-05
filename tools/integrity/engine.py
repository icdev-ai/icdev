# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Assessment engine (orchestration).

``assess(source, ...)`` is the single entrypoint that wires the SIPA stages into
one end-to-end assessment of an untrusted artifact, persisting at every step and
returning a compact disposition the route / reflex / CLI can act on:

    ingest.stage            quarantine-first staging + assessment row (status='quarantine')
      -> ingest.reverify    SHA-256 tamper re-baseline (writes tamper_mismatch if changed)
      -> scanners.scan_all  SAST / secrets / deps / malicious-signature -> integrity_findings
      -> capability_extractor.extract  AST behavioral manifest -> integrity_capabilities
      -> (Mode B) claim_parser.parse_claim + intent_reconciler.reconcile_and_persist
                            disclosed-vs-exercised gap -> integrity_findings (reconciliation)
      -> scoring.score      combine all signals -> risk_score + verdict (status='assessed')

Each stage owns its own append-only persistence; the engine simply threads a
single RLS-aware connection through all of them (so the whole assessment commits
against one ``tenant_id`` / ``classification`` context) and reads back the final
counts + disposition.

MODE SELECTION (``mode`` argument):
  * ``'auto'`` (default) resolves to ``provenance_aware`` when a ``project_id`` or
    ``session_id`` is supplied (a formal provenance handle is available) and to
    ``provenance_blind`` otherwise — the primary external-code path.
  * An explicit ``'provenance_aware'`` / ``'provenance_blind'`` overrides the
    auto-selection.
  * The **Mode B** reconciliation stage (claim_parser + reconcile) runs only in
    ``provenance_blind`` mode: it judges the exercised capability manifest against
    the author's *claimed* set (README / docstrings / declared purpose). In
    ``provenance_aware`` mode an RTM/PRD reconciler (a separate stage) owns that
    comparison, so the engine leaves capability-vs-claim reconciliation to it.

SECURITY INVARIANTS (inherited from every stage — the SIPA static-only contract):
  * **Never executes the target.** Staging only copies/clones bytes; scanners run
    in isolated subprocesses against the staged tree; the capability extractor is
    AST-only. Nothing here imports, ``exec``-es, or runs the quarantined artifact.
  * **Quarantine-first.** The artifact lands in ``status='quarantine'`` and stays
    there until scoring transitions it to ``'assessed'``; HITL promote/reject
    (a later stage) owns ``approved`` / ``rejected``.
  * **All DB access via ``get_connection()``** (RLS-aware) with ``tenant_id`` /
    ``classification`` stamped from the active security context.

The feature flag ``constants.FEATURE_FLAG`` (``ICDEV_INTEGRITY_ENABLED``) gates the
*automatic* invokers (route / reflex) via :func:`is_enabled`; ``assess`` itself is
the explicit engine primitive and runs whenever it is called.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from tools.integrity import (
    capability_extractor,
    claim_parser,
    ingest,
    intent_reconciler,
    scanners,
    scoring,
)
from tools.integrity.constants import FEATURE_FLAG
from tools.integrity.db.init_db import init_db

logger = logging.getLogger("icdev.integrity.engine")

# Canonical mode tokens (subset of constants.ASSESS_MODES that name a concrete
# strategy; 'auto' is resolved away before any row is written).
PROVENANCE_AWARE = "provenance_aware"
PROVENANCE_BLIND = "provenance_blind"

# --------------------------------------------------------------------------- #
# Gate — CI / pre-merge exit-code policy
# --------------------------------------------------------------------------- #
# Exit codes for ``main`` when invoked with ``--gate`` (so the CLI can wire into
# CI / pre-merge hooks). A blocking verdict fails the build; a warn verdict is
# surfaced on stderr but does not fail.
GATE_OK = 0     # verdict cleared the gate (ALLOW, or a warn-only verdict)
GATE_BLOCK = 1  # verdict is in gate.block_on — fail the build

BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/engine.py -> repo root
_CONFIG_PATH = BASE_DIR / "args" / "integrity_config.yaml"

# Fallbacks if integrity_config.yaml is missing/partial: block on QUARANTINE,
# warn (non-blocking) on REVIEW.
_DEFAULT_GATE_BLOCK_ON = ("quarantine",)
_DEFAULT_GATE_WARN_ON = ("review",)


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    """True when SIPA assessment is enabled via ``ICDEV_INTEGRITY_ENABLED``.

    Consulted by the *automatic* invokers (route / reflex) so they no-op when the
    canvas is toggled off. ``assess`` does not self-gate — it is the explicit
    engine primitive and runs whenever a caller invokes it directly.
    """
    return str(os.environ.get(FEATURE_FLAG, "")).strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #
def resolve_mode(mode: str, project_id: Optional[str], session_id: Optional[str]) -> str:
    """Resolve the assessment ``mode`` to a concrete strategy.

    ``'auto'`` -> ``provenance_aware`` when a provenance handle (``project_id`` or
    ``session_id``) is available, else ``provenance_blind``. An explicit
    ``provenance_aware`` / ``provenance_blind`` is honored verbatim.

    Raises:
        ValueError: ``mode`` is not ``auto`` / ``provenance_aware`` /
            ``provenance_blind``.
    """
    m = (mode or "auto").strip().lower()
    if m == "auto":
        return PROVENANCE_AWARE if (project_id or session_id) else PROVENANCE_BLIND
    if m in (PROVENANCE_AWARE, PROVENANCE_BLIND):
        return m
    raise ValueError(
        f"invalid mode {mode!r}; expected 'auto', "
        f"{PROVENANCE_AWARE!r}, or {PROVENANCE_BLIND!r}"
    )


# --------------------------------------------------------------------------- #
# DB helpers (mirror the sibling modules' backend handling)
# --------------------------------------------------------------------------- #
def _backend_of(conn: Any) -> str:
    """Resolve the backend ('postgresql' | 'sqlite') for a live connection."""
    declared = getattr(conn, "_backend", None)
    if declared:
        return "postgresql" if str(declared).lower().startswith(("postgre", "pg")) else "sqlite"
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    return "postgresql" if backend in ("postgresql", "postgres", "pg") else "sqlite"


def _q(conn: Any, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    return sql.replace("?", "%s") if _backend_of(conn) == "postgresql" else sql


def _scalar(row: Any, key: str) -> Any:
    """Single aliased column of a DB row, dict-cursor or positional.

    Mirrors the ``hasattr(row, "keys")`` probe used across the sibling modules:
    PG dict cursors expose the aliased ``key``; SQLite ``Row`` / positional tuples
    fall back to index 0 (each caller selects exactly one aliased column).
    """
    if row is None:
        return None
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return row[0]


def _set_mode(conn: Any, assessment_id: int, mode: str) -> None:
    """Stamp the engine-resolved mode onto the assessment row.

    ``ingest.stage`` derives ``mode`` from provenance presence; when the caller
    forces an explicit ``mode`` the two can diverge, so the engine writes the
    authoritative resolved value back. ``integrity_assessments`` is the single
    mutable-lifecycle table (mode/status/verdict/risk_score), so this UPDATE is
    permitted (it is not one of the append-only findings tables).
    """
    sql = "UPDATE integrity_assessments SET mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    conn.execute(_q(conn, sql), (mode, assessment_id))
    conn.commit()


def _count_findings(conn: Any, assessment_id: int) -> int:
    """Total ``integrity_findings`` rows for an assessment (all scanners)."""
    sql = "SELECT COUNT(*) AS n FROM integrity_findings WHERE assessment_id = ?"
    row = conn.execute(_q(conn, sql), (assessment_id,)).fetchone()
    return int(_scalar(row, "n") or 0)


def _count_capabilities(conn: Any, assessment_id: int) -> int:
    """Total ``integrity_capabilities`` rows for an assessment."""
    sql = "SELECT COUNT(*) AS n FROM integrity_capabilities WHERE assessment_id = ?"
    row = conn.execute(_q(conn, sql), (assessment_id,)).fetchone()
    return int(_scalar(row, "n") or 0)


def _get_status(conn: Any, assessment_id: int) -> Optional[str]:
    """Current lifecycle ``status`` of an assessment row."""
    sql = "SELECT status FROM integrity_assessments WHERE id = ?"
    row = conn.execute(_q(conn, sql), (assessment_id,)).fetchone()
    return _scalar(row, "status")


# --------------------------------------------------------------------------- #
# Gate config
# --------------------------------------------------------------------------- #
def _load_config() -> dict:
    """Load integrity_config.yaml; tolerate a missing/empty file with defaults."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _gate_verdicts(cfg: Optional[dict] = None) -> tuple[set[str], set[str]]:
    """``(block_on, warn_on)`` verdict sets from config (lowercased).

    Reads ``gate.block_on`` / ``gate.warn_on`` from ``integrity_config.yaml``,
    falling back to ``block on QUARANTINE`` / ``warn on REVIEW`` when absent.
    """
    cfg = cfg if cfg is not None else _load_config()
    gate = (cfg or {}).get("gate") or {}
    block = gate.get("block_on")
    warn = gate.get("warn_on")
    block_set = {str(v).strip().lower() for v in (block if block is not None else _DEFAULT_GATE_BLOCK_ON)}
    warn_set = {str(v).strip().lower() for v in (warn if warn is not None else _DEFAULT_GATE_WARN_ON)}
    return block_set, warn_set


def gate_exit_code(verdict: str, cfg: Optional[dict] = None) -> int:
    """Map a verdict to a ``--gate`` exit code.

    ``GATE_BLOCK`` (1) when ``verdict`` is in ``gate.block_on`` (QUARANTINE by
    default, and REVIEW too when the operator adds it to ``block_on``);
    ``GATE_OK`` (0) otherwise. Verdict matching is case-insensitive.
    """
    block_set, _warn = _gate_verdicts(cfg)
    return GATE_BLOCK if str(verdict).strip().lower() in block_set else GATE_OK


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def assess(
    source: str,
    mode: str = "auto",
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    declared_purpose: Optional[str] = None,
    conn: Any = None,
) -> dict:
    """Run a full SIPA assessment of ``source`` and return its disposition.

    Orchestrates the SIPA pipeline end-to-end against a single RLS-aware
    connection, persisting at each step:

      1. **Ingest** — ``ingest.stage`` quarantines the artifact and writes the
         ``integrity_assessments`` row (``status='quarantine'``).
      2. **Tamper re-baseline** — ``ingest.reverify`` records a ``tamper_mismatch``
         finding if the staged tree changed between staging and assessment
         (best-effort; a reverify hiccup never aborts the assessment).
      3. **Scanners** — ``scanners.scan_all`` fans out the enabled static scanners
         (SAST / secrets / deps / malicious-signature) -> ``integrity_findings``.
      4. **Capabilities** — ``capability_extractor.extract`` builds the AST
         behavioral manifest and persists it -> ``integrity_capabilities``.
      5. **Mode B reconciliation** (``provenance_blind`` only) — ``claim_parser``
         + ``intent_reconciler.reconcile_and_persist`` flag undisclosed /
         intrinsically-dangerous capabilities -> ``integrity_findings``.
      6. **Scoring** — ``scoring.score`` combines every signal into a 0–100
         ``risk_score`` + verdict and transitions the assessment to ``'assessed'``.

    Args:
        source: local path, UNC share, ``file://`` URI, or an allowlisted git
            clone URL (see ``ingest.stage``).
        mode: ``'auto'`` (default), ``'provenance_aware'``, or
            ``'provenance_blind'`` (see :func:`resolve_mode`).
        project_id / session_id: provenance handles; their presence selects
            ``provenance_aware`` under ``mode='auto'``.
        declared_purpose: optional free-text purpose claim folded into the Mode B
            claimed-capability set (e.g. a marketplace blurb / ticket summary).
        conn: optional existing DB connection to reuse (engine / tests); when
            ``None`` an RLS-aware connection is opened and closed internally.

    Returns:
        ``{"assessment_id", "verdict", "risk_score", "findings_count",
        "capabilities_count", "status"}``. ``verdict`` is the canonical lowercase
        token (``constants.VERDICTS``); ``status`` is ``'assessed'`` on success.

    Raises:
        ingest.IngestRejected: the source was refused at ingest (disallowed
            scheme/host, missing path, failed clone). Propagated so the caller
            learns the artifact never entered assessment.
        ValueError: an invalid ``mode``.

    Never executes the target — every stage is static-only (copy/clone, isolated
    scanner subprocesses, AST parsing).
    """
    resolved_mode = resolve_mode(mode, project_id, session_id)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS

        # 1. Ingest — quarantine-first staging + assessment row.
        staged = ingest.stage(
            source,
            project_id=project_id,
            session_id=session_id,
            conn=conn,
        )
        assessment_id = staged["assessment_id"]
        staged_path = staged["staged_path"]
        logger.info(
            "assess: staged %s -> assessment %s (%s) at %s",
            source, assessment_id, resolved_mode, staged_path,
        )

        # Stamp the engine-resolved mode (ingest derived it from provenance; an
        # explicit mode override must win).
        _set_mode(conn, assessment_id, resolved_mode)

        # 2. Tamper re-baseline — writes a tamper_mismatch finding iff changed.
        #    Best-effort: the assessment proceeds even if reverify cannot run.
        try:
            ingest.reverify(assessment_id, conn=conn)
        except Exception as exc:  # noqa: BLE001 — never let tamper-check abort assessment
            logger.warning("assess: reverify failed for assessment %s: %s", assessment_id, exc)

        # 3. Scanners — SAST / secrets / deps / malicious-signature fan-out.
        scan_summary = scanners.scan_all(assessment_id, staged_path=staged_path, conn=conn)

        # 4. Capabilities — AST behavioral manifest (extract once, persist it).
        #    The in-memory manifest also feeds Mode B reconciliation below, so we
        #    extract a single time rather than via extract_and_persist (which would
        #    re-parse the whole tree).
        manifest = capability_extractor.extract(staged_path)
        capability_extractor._persist(conn, assessment_id, manifest)

        # 5. Mode B reconciliation — disclosed (claim) vs exercised (manifest).
        #    Only in provenance_blind mode; the provenance_aware RTM/PRD reconciler
        #    is a separate stage that owns capability-vs-requirement comparison.
        reconciled = False
        if resolved_mode == PROVENANCE_BLIND:
            claim = claim_parser.parse_claim(staged_path, declared_purpose=declared_purpose)
            intent_reconciler.reconcile_and_persist(assessment_id, manifest, claim, conn=conn)
            reconciled = True

        # 6. Scoring — combine every signal into a risk score + verdict.
        score_result = scoring.score(assessment_id, conn=conn)

        findings_count = _count_findings(conn, assessment_id)
        capabilities_count = _count_capabilities(conn, assessment_id)
        status = _get_status(conn, assessment_id)
    finally:
        if own_conn:
            conn.close()

    logger.info(
        "assess: assessment %s -> %s (%.1f) | %d finding(s), %d capability(ies), mode=%s%s",
        assessment_id, score_result["verdict"], score_result["risk_score"],
        findings_count, capabilities_count, resolved_mode,
        " (reconciled)" if reconciled else "",
    )
    return {
        "assessment_id": assessment_id,
        "verdict": score_result["verdict"],
        "risk_score": score_result["risk_score"],
        "findings_count": findings_count,
        "capabilities_count": capabilities_count,
        "status": status,
        "mode": resolved_mode,
        "scanners": scan_summary.get("scanners", {}),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list] = None, conn: Any = None) -> int:
    """SIPA engine CLI entrypoint.

    ``python tools/integrity/engine.py --source <path|git-url|unc|uri>
    [--mode auto|provenance_aware|provenance_blind] [--project-id ...]
    [--session-id ...] [--declared-purpose ...] [--gate] [--json]``

    ``--json`` emits the assessment summary as JSON; ``--gate`` turns the verdict
    into a CI / pre-merge exit code (non-zero on ``gate.block_on`` — QUARANTINE by
    default — zero otherwise) so it can fail a build.

    Args:
        argv: optional argument vector (defaults to ``sys.argv[1:]``); passed by
            tests so they can drive the CLI without touching the process args.
        conn: optional DB connection threaded into :func:`assess` (tests inject an
            in-memory SQLite connection); ``None`` lets ``assess`` open its own.

    Returns:
        The process exit code (also raised via ``SystemExit`` so the CLI / tests
        can assert on it): ``GATE_BLOCK`` (1) when ``--gate`` and the verdict is
        blocking, else ``GATE_OK`` (0). The positional ``source`` form is still
        accepted for backward compatibility with the sipa-engine-01 CLI.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="engine.py",
        description="SIPA assessment engine — orchestrate ingest -> scan -> "
        "capability -> (Mode B) reconcile -> score over an untrusted artifact and "
        "emit a risk score + ALLOW / REVIEW / QUARANTINE verdict. Static-only: the "
        "target is never executed.",
    )
    # --source is the canonical flag; a bare positional is accepted too (back-compat).
    parser.add_argument(
        "--source",
        dest="source_opt",
        default=None,
        help="local path / UNC share / file:// URI / git clone URL",
    )
    parser.add_argument(
        "source_pos",
        nargs="?",
        default=None,
        metavar="source",
        help="positional alias for --source (back-compat)",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", PROVENANCE_AWARE, PROVENANCE_BLIND],
        help="provenance strategy (default: auto)",
    )
    parser.add_argument("--project-id", default=None, help="provenance handle (selects aware under auto)")
    parser.add_argument("--session-id", default=None, help="provenance handle (selects aware under auto)")
    parser.add_argument(
        "--declared-purpose",
        default=None,
        help="free-text purpose claim folded into the Mode B claimed-capability set",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="exit non-zero on a blocking verdict (gate.block_on; QUARANTINE by default) for CI / pre-merge",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    source = args.source_opt or args.source_pos
    if not source:
        parser.error("a source is required (--source <path|git-url|unc|uri>)")

    result = assess(
        source,
        mode=args.mode,
        project_id=args.project_id,
        session_id=args.session_id,
        declared_purpose=args.declared_purpose,
        conn=conn,
    )

    verdict = str(result["verdict"]).lower()
    block_set, warn_set = _gate_verdicts()

    if args.json:
        payload = dict(result)
        if args.gate:
            payload["gate"] = {
                "blocked": verdict in block_set,
                "warned": verdict in warn_set,
                "exit_code": GATE_BLOCK if verdict in block_set else GATE_OK,
            }
        print(json.dumps(payload, indent=2))
    else:
        print(f"SIPA assessment {result['assessment_id']} — {result['mode']}")
        print(f"  verdict:      {result['verdict'].upper()}")
        print(f"  risk_score:   {result['risk_score']}")
        print(f"  findings:     {result['findings_count']}")
        print(f"  capabilities: {result['capabilities_count']}")
        print(f"  status:       {result['status']}")

    code = GATE_OK
    if args.gate:
        if verdict in block_set:
            code = GATE_BLOCK
            print(f"GATE: BLOCKED - verdict {verdict.upper()} is blocking", file=sys.stderr)
        elif verdict in warn_set:
            print(f"GATE: WARN - verdict {verdict.upper()} (non-blocking)", file=sys.stderr)

    sys.exit(code)


if __name__ == "__main__":
    main()
