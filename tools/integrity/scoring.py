# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Scoring + verdict (sipa-score-01).

The final deterministic stage of the assessment pipeline. Everything upstream
*produces signals* — scanners and the intent reconciler write
``integrity_findings`` rows, the capability extractor writes
``integrity_capabilities`` rows, and ``ingest.reverify`` writes a
``tamper_mismatch`` finding when a staged tree changed. ``score(assessment_id)``
*combines those signals* into a single 0–100 risk score and a verdict
(ALLOW / REVIEW / QUARANTINE), then records an append-only ``integrity_verdicts``
row (``decided_by='engine'``) whose JSON rationale enumerates the top
contributors so the disposition is auditable.

Aggregation (weights/thresholds from ``args/integrity_config.yaml``):

  * **Findings** dominate the score. Each finding contributes
    ``FINDING_BASE_POINTS * RISK_WEIGHTS_SEVERITY[severity]`` — a critical finding
    is worth far more than a low one. This single bucket already covers the
    scanner findings, the reconciliation findings
    (``undisclosed_capability`` / ``unauthorized_capability``), and the
    ``tamper_mismatch`` finding, because they all land in ``integrity_findings``.
  * **Capabilities** add a smaller, *capped* baseline of intrinsic risk:
    ``CAPABILITY_BASE_POINTS * risk_weight`` (config ``risk_weights`` first, then
    ``constants.RISK_WEIGHTS_CAPABILITY``), summed and clamped to
    :data:`CAPABILITY_CONTRIBUTION_CAP`. The cap is deliberate — a legitimate,
    fully-disclosed tool that genuinely needs many capabilities must not score
    into QUARANTINE on capability presence alone; the *undisclosed* gap (a
    finding) is what carries weight.

The summed score is clamped to ``[0, 100]`` and mapped through the configured
thresholds:

    score <  review_at                   -> ALLOW
    review_at <= score < quarantine_at   -> REVIEW
    score >= quarantine_at               -> QUARANTINE

Two **hard overrides** force QUARANTINE regardless of the numeric score (the
score can be gamed down by a single well-hidden payload, these shapes cannot):

  * any ``critical`` ``known_bad_signature`` finding (a malware-signature match), or
  * any ``undisclosed_capability`` finding whose capability is ``process_exec``
    (undisclosed arbitrary command execution).

Verdict tokens stored in the DB / returned here are the canonical lowercase
``constants.VERDICTS`` values; :data:`ALLOW` / :data:`REVIEW` / :data:`QUARANTINE`
symbols are exported so downstream code (engine, gate) compares against names
rather than string literals.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from tools.integrity.constants import (
    RISK_WEIGHTS_CAPABILITY,
    RISK_WEIGHTS_SEVERITY,
    VERDICTS,
)
from tools.integrity.db.init_db import init_db

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.scoring")

# Canonical verdict symbols (lowercase, matching constants.VERDICTS + the DB
# CHECK constraint). Downstream code should reference these, not bare literals.
ALLOW, REVIEW, QUARANTINE = "allow", "review", "quarantine"
assert {ALLOW, REVIEW, QUARANTINE} == set(VERDICTS)  # constants drift guard

# --------------------------------------------------------------------------- #
# Scoring weights — how each signal converts into 0–100 risk points
# --------------------------------------------------------------------------- #
# A finding's points = FINDING_BASE_POINTS * RISK_WEIGHTS_SEVERITY[severity].
# With the severity multipliers (critical 1.0 / high 0.7 / medium 0.4 / low 0.2 /
# info 0.05) this yields critical=50, high=35, medium=20, low=10, info≈2.5 — so a
# single undisclosed network_egress (high) lands in REVIEW and two criticals
# saturate to QUARANTINE on findings alone.
FINDING_BASE_POINTS = 50.0

# A capability's points = CAPABILITY_BASE_POINTS * risk_weight. Kept small so raw
# capability *presence* nudges rather than dominates the score.
CAPABILITY_BASE_POINTS = 8.0

# Hard ceiling on the total capability contribution so a legitimate,
# fully-disclosed tool with many capabilities cannot score into QUARANTINE on
# capability presence alone (the undisclosed *gap* — a finding — must carry that).
CAPABILITY_CONTRIBUTION_CAP = 25.0

MAX_SCORE = 100.0

# How many contributors to enumerate in the rationale's ``top_contributors``.
_TOP_N = 8

# Default thresholds if args/integrity_config.yaml is missing/partial (mirrors
# the db-04 skeleton: review_at 40, quarantine_at 70).
_DEFAULT_THRESHOLDS = {"review_at": 40.0, "quarantine_at": 70.0}

BASE_DIR = Path(__file__).resolve().parents[2]  # tools/integrity/scoring.py -> repo root
_CONFIG_PATH = BASE_DIR / "args" / "integrity_config.yaml"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def _load_config() -> dict:
    """Load integrity_config.yaml; tolerate a missing/empty file with defaults."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _thresholds(cfg: dict) -> tuple[float, float]:
    """``(review_at, quarantine_at)`` from config, falling back to defaults.

    Guarantees ``review_at <= quarantine_at`` (a misconfigured pair where review
    exceeds quarantine would make REVIEW unreachable); on inversion the two are
    swapped so the score→verdict mapping stays monotonic.
    """
    th = cfg.get("thresholds") or {}
    try:
        review_at = float(th.get("review_at", _DEFAULT_THRESHOLDS["review_at"]))
    except (TypeError, ValueError):
        review_at = _DEFAULT_THRESHOLDS["review_at"]
    try:
        quarantine_at = float(th.get("quarantine_at", _DEFAULT_THRESHOLDS["quarantine_at"]))
    except (TypeError, ValueError):
        quarantine_at = _DEFAULT_THRESHOLDS["quarantine_at"]
    if review_at > quarantine_at:
        review_at, quarantine_at = quarantine_at, review_at
    return review_at, quarantine_at


def _capability_weight(cfg: dict, cap_type: str) -> float:
    """Inherent risk weight for a capability — config ``risk_weights`` first."""
    weights = cfg.get("risk_weights") or {}
    if cap_type in weights:
        try:
            return float(weights[cap_type])
        except (TypeError, ValueError):
            pass
    return float(RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0))


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


def _row_to(row: Any, cols: list[str]) -> dict:
    """Coerce a DB row (dict-like or tuple) into a ``{col: value}`` dict.

    ``get_connection()`` rows may be key-accessible (PG dict cursor) or positional
    (SQLite default); the ``hasattr(row, "keys")`` probe matches the pattern used
    in ``ingest.reverify``.
    """
    if hasattr(row, "keys"):
        return {c: row[c] for c in cols}
    return {c: row[i] for i, c in enumerate(cols)}


def _detail_dict(raw: Any) -> dict:
    """Parse an ``integrity_findings.detail`` value (JSON text or JSONB dict)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _fetch_findings(conn: Any, assessment_id: int) -> list[dict]:
    """All ``integrity_findings`` for an assessment, ``detail`` parsed to a dict."""
    cols = ["id", "source_scanner", "finding_type", "severity", "file_path", "line", "detail"]
    sql = (
        "SELECT id, source_scanner, finding_type, severity, file_path, line, detail "
        "FROM integrity_findings WHERE assessment_id = ? ORDER BY id"
    )
    rows = conn.execute(_q(conn, sql), (assessment_id,)).fetchall()
    findings = []
    for row in rows:
        rec = _row_to(row, cols)
        rec["detail"] = _detail_dict(rec.get("detail"))
        findings.append(rec)
    return findings


def _fetch_capabilities(conn: Any, assessment_id: int) -> list[dict]:
    """All ``integrity_capabilities`` for an assessment."""
    cols = ["id", "file_path", "function_name", "capability_type", "line_start", "risk_weight"]
    sql = (
        "SELECT id, file_path, function_name, capability_type, line_start, risk_weight "
        "FROM integrity_capabilities WHERE assessment_id = ? ORDER BY id"
    )
    rows = conn.execute(_q(conn, sql), (assessment_id,)).fetchall()
    return [_row_to(row, cols) for row in rows]


# --------------------------------------------------------------------------- #
# Scoring core (pure — no DB)
# --------------------------------------------------------------------------- #
def _finding_points(severity: str) -> float:
    """Points a single finding contributes, from its severity multiplier."""
    return FINDING_BASE_POINTS * float(RISK_WEIGHTS_SEVERITY.get(severity, 0.0))


def _finding_capability(finding: dict) -> Optional[str]:
    """The capability a finding refers to (reconciliation findings carry it)."""
    cap = finding.get("detail", {}).get("capability_type")
    return cap if isinstance(cap, str) else None


def _forced_quarantine_reasons(findings: list[dict]) -> list[str]:
    """Hard-override reasons that force QUARANTINE regardless of the numeric score.

    * a ``critical`` ``known_bad_signature`` (malware-signature match), and
    * an ``undisclosed_capability`` whose capability is ``process_exec``
      (undisclosed arbitrary command execution).
    """
    reasons: list[str] = []
    for f in findings:
        ftype = f.get("finding_type")
        if ftype == "known_bad_signature" and f.get("severity") == "critical":
            loc = f"{f.get('file_path')}:{f.get('line')}" if f.get("file_path") else "(no file)"
            reasons.append(f"critical known_bad_signature at {loc}")
        elif ftype == "undisclosed_capability" and _finding_capability(f) == "process_exec":
            loc = f"{f.get('file_path')}:{f.get('line')}" if f.get("file_path") else "(no file)"
            reasons.append(f"undisclosed process_exec at {loc}")
    return reasons


def _score_signals(findings: list[dict], capabilities: list[dict], cfg: dict) -> dict:
    """Combine findings + capabilities into a clamped 0–100 score + breakdown.

    Returns the raw component sums, the clamped ``risk_score``, and the ranked
    ``top_contributors`` used to build the verdict rationale. Pure function: takes
    already-fetched rows, touches no DB, so it is unit-testable in isolation.
    """
    contributors: list[dict] = []

    findings_total = 0.0
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for f in findings:
        sev = f.get("severity") or "info"
        pts = _finding_points(sev)
        findings_total += pts
        by_severity[sev] = by_severity.get(sev, 0) + 1
        ftype = f.get("finding_type") or "unknown"
        by_type[ftype] = by_type.get(ftype, 0) + 1
        cap = _finding_capability(f)
        label = f"{ftype}({cap})" if cap else ftype
        loc = f"{f.get('file_path')}:{f.get('line')}" if f.get("file_path") else None
        contributors.append(
            {
                "kind": "finding",
                "label": label,
                "scanner": f.get("source_scanner"),
                "severity": sev,
                "capability_type": cap,
                "location": loc,
                "points": round(pts, 2),
            }
        )

    # Capabilities: summed intrinsic risk, capped. Group by type so the rationale
    # cites a capability once with its occurrence count, not one row per call site.
    cap_by_type: dict[str, dict] = {}
    cap_order: list[str] = []
    for c in capabilities:
        cap_type = c.get("capability_type")
        if not cap_type:
            continue
        weight = _capability_weight(cfg, cap_type)
        if cap_type not in cap_by_type:
            cap_by_type[cap_type] = {"weight": weight, "count": 0}
            cap_order.append(cap_type)
        cap_by_type[cap_type]["count"] += 1

    capability_raw = 0.0
    for cap_type in cap_order:
        info = cap_by_type[cap_type]
        pts = CAPABILITY_BASE_POINTS * info["weight"]
        capability_raw += pts
        contributors.append(
            {
                "kind": "capability",
                "label": cap_type,
                "capability_type": cap_type,
                "risk_weight": round(info["weight"], 3),
                "occurrences": info["count"],
                "points": round(pts, 2),
            }
        )
    capability_total = min(capability_raw, CAPABILITY_CONTRIBUTION_CAP)

    raw_score = findings_total + capability_total
    risk_score = max(0.0, min(MAX_SCORE, raw_score))

    contributors.sort(key=lambda x: x["points"], reverse=True)
    return {
        "risk_score": round(risk_score, 2),
        "findings_total": round(findings_total, 2),
        "capability_raw": round(capability_raw, 2),
        "capability_total": round(capability_total, 2),
        "capability_capped": capability_raw > CAPABILITY_CONTRIBUTION_CAP,
        "by_severity": by_severity,
        "by_type": by_type,
        "top_contributors": contributors[:_TOP_N],
    }


def _verdict_for_score(score: float, review_at: float, quarantine_at: float) -> str:
    """Map a 0–100 score to a verdict via the configured thresholds."""
    if score >= quarantine_at:
        return QUARANTINE
    if score >= review_at:
        return REVIEW
    return ALLOW


def assess_score(findings: list[dict], capabilities: list[dict], cfg: Optional[dict] = None) -> dict:
    """Compute ``{risk_score, verdict, rationale}`` from already-fetched signals.

    The DB-free heart of :func:`score`. Applies the threshold mapping, then the
    hard QUARANTINE overrides, and assembles the JSON-serializable rationale.
    """
    cfg = cfg if cfg is not None else _load_config()
    review_at, quarantine_at = _thresholds(cfg)

    signals = _score_signals(findings, capabilities, cfg)
    risk_score = signals["risk_score"]

    score_verdict = _verdict_for_score(risk_score, review_at, quarantine_at)
    forced_reasons = _forced_quarantine_reasons(findings)
    verdict = QUARANTINE if forced_reasons else score_verdict

    rationale = {
        "risk_score": risk_score,
        "verdict": verdict,
        "score_verdict": score_verdict,
        "forced_quarantine": bool(forced_reasons),
        "forced_reasons": forced_reasons,
        "thresholds": {"review_at": review_at, "quarantine_at": quarantine_at},
        "components": {
            "findings_total": signals["findings_total"],
            "capability_total": signals["capability_total"],
            "capability_raw": signals["capability_raw"],
            "capability_capped": signals["capability_capped"],
            "capability_cap": CAPABILITY_CONTRIBUTION_CAP,
        },
        "counts": {
            "findings": len(findings),
            "capabilities": len(capabilities),
            "by_severity": signals["by_severity"],
            "by_type": signals["by_type"],
        },
        "top_contributors": signals["top_contributors"],
    }
    return {"risk_score": risk_score, "verdict": verdict, "rationale": rationale}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _caller_context() -> tuple[str, str, str]:
    """(tenant_id, classification, created_by) from the active security context."""
    try:
        from tools.security.security_context import get_security_context

        ctx = get_security_context()
    except Exception:
        ctx = None
    tenant_id = (getattr(ctx, "tenant_id", None) or "default") if ctx else "default"
    classification = (getattr(ctx, "classification", None) or "CUI") if ctx else "CUI"
    created_by = (getattr(ctx, "user_id", None) or "system") if ctx else "system"
    return tenant_id, classification, created_by


_VERDICT_SQL = (
    "INSERT INTO integrity_verdicts "
    "(assessment_id, verdict, risk_score, rationale, decided_by, tenant_id, classification) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def _insert_verdict(conn: Any, params: tuple) -> int:
    """Insert the append-only ``integrity_verdicts`` row; return its PK."""
    if _backend_of(conn) == "postgresql":
        cur = conn.execute(_VERDICT_SQL.replace("?", "%s") + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return int((row[0] if not hasattr(row, "keys") else row["id"])) if row else 0
    cur = conn.execute(_VERDICT_SQL, params)
    conn.commit()
    return int(cur.lastrowid or 0)


def _update_assessment(conn: Any, assessment_id: int, verdict: str, risk_score: float) -> None:
    """Stamp the computed verdict/score onto the assessment and mark it assessed.

    Only ``integrity_assessments`` carries a mutable lifecycle; ``status`` moves
    ``quarantine`` -> ``assessed`` (scanners run, findings recorded, verdict
    decided). HITL promote/reject (sipa-engine-03) owns ``approved``/``rejected``.
    """
    sql = (
        "UPDATE integrity_assessments "
        "SET verdict = ?, risk_score = ?, status = 'assessed', "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    )
    conn.execute(_q(conn, sql), (verdict, risk_score, assessment_id))
    conn.commit()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def score(assessment_id: int, conn: Any = None) -> dict:
    """Combine an assessment's signals into a risk score + verdict and persist it.

    Reads every ``integrity_findings`` (scanner + reconciliation + tamper) and
    ``integrity_capabilities`` row for ``assessment_id``, computes a 0–100
    ``risk_score`` and a verdict (ALLOW / REVIEW / QUARANTINE), writes an
    append-only ``integrity_verdicts`` row (``decided_by='engine'``) with a JSON
    rationale enumerating the top contributors, and stamps the verdict/score back
    onto the assessment (status -> ``assessed``).

    Args:
        assessment_id: the assessment to score.
        conn: optional existing DB connection to reuse (engine / tests); when
            ``None`` an RLS-aware connection is opened and closed internally.

    Returns:
        ``{"assessment_id", "risk_score", "verdict", "rationale", "verdict_id"}``.
        ``verdict`` is the canonical lowercase token (``constants.VERDICTS``).
    """
    cfg = _load_config()

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        findings = _fetch_findings(conn, assessment_id)
        capabilities = _fetch_capabilities(conn, assessment_id)

        result = assess_score(findings, capabilities, cfg)
        verdict = result["verdict"]
        risk_score = result["risk_score"]

        tenant_id, classification, _ = _caller_context()
        verdict_id = _insert_verdict(
            conn,
            (
                assessment_id,
                verdict,
                risk_score,
                json.dumps(result["rationale"]),
                "engine",
                tenant_id,
                classification,
            ),
        )
        _update_assessment(conn, assessment_id, verdict, risk_score)
    finally:
        if own_conn:
            conn.close()

    logger.info(
        "scored assessment %s -> %s (%.1f) via %d finding(s), %d capability(ies)",
        assessment_id, verdict, risk_score, len(findings), len(capabilities),
    )
    return {
        "assessment_id": assessment_id,
        "risk_score": risk_score,
        "verdict": verdict,
        "rationale": result["rationale"],
        "verdict_id": verdict_id,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SIPA scoring — combine an assessment's findings + "
        "capabilities + reconciliation + tamper signals into a 0–100 risk score "
        "and an ALLOW / REVIEW / QUARANTINE verdict; record an append-only "
        "integrity_verdicts row (decided_by='engine').",
    )
    parser.add_argument(
        "--assessment-id", type=int, required=True, help="integrity_assessments.id to score"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    result = score(args.assessment_id)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"SIPA scoring — assessment {result['assessment_id']}")
    print(f"  risk_score: {result['risk_score']}")
    print(f"  verdict:    {result['verdict'].upper()}")
    rationale = result["rationale"]
    if rationale.get("forced_quarantine"):
        for reason in rationale["forced_reasons"]:
            print(f"  forced QUARANTINE: {reason}")
    for c in rationale.get("top_contributors", []):
        print(f"  [{c['points']:>6}] {c['kind']}: {c['label']}")


if __name__ == "__main__":
    main()
