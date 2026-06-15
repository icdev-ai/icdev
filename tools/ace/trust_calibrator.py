# CUI // SP-CTI
"""NOVA TRUST — Trust Calibration & Progressive Autonomy.

Every ACE coworker role carries a trust_score (0.0–1.0).  Trust climbs with
successful task completions and falls with failures, timeouts, or HITL
escalations.  As trust crosses thresholds the system auto-expands the
coworker's autonomy level — fewer HITL gates, broader tool permissions,
more parallel dispatch.

Trust bands:
  < 0.3   Probationary — all decisions require human review; dispatch paused
  0.3–0.6 Supervised   — standard HITL on risky actions; sequential dispatch
  0.6–0.8 Trusted      — HITL only on irreversible actions; parallel(2)
  ≥ 0.8   Autonomous   — routine tasks auto-approved; parallel(4)

The trust ledger is append-only (NIST AU) — every delta is recorded with
reason and source_task_id.

Inspired by SIA's measurable convergence + OpenClaw's permission-transparent
design.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Trust bands
# ────────────────────────────────────────────────────────────────────────────

TRUST_PROBATIONARY = 0.3
TRUST_SUPERVISED = 0.6
TRUST_TRUSTED = 0.8

# Bayesian-style update magnitudes
_DELTA_SUCCESS = +0.05
_DELTA_FAILURE = -0.10
_DELTA_HITL_ESCALATION = -0.03
_DELTA_TIMEOUT = -0.05
_DELTA_PHANTOM = -0.08

# Starting trust for a new role
INITIAL_TRUST = 0.5

# Trust score floor/ceiling
TRUST_MIN = 0.05
TRUST_MAX = 0.98


@dataclass
class TrustBand:
    name: str
    hitl_mode: str          # all | risky | irreversible | none
    max_parallel: int
    auto_approve_routine: bool


def get_trust_band(score: float) -> TrustBand:
    if score < TRUST_PROBATIONARY:
        return TrustBand("probationary", "all", 0, False)
    if score < TRUST_SUPERVISED:
        return TrustBand("supervised", "risky", 1, False)
    if score < TRUST_TRUSTED:
        return TrustBand("trusted", "irreversible", 2, True)
    return TrustBand("autonomous", "none", 4, True)


# ────────────────────────────────────────────────────────────────────────────
# DB helpers
# ────────────────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _ensure_tables(conn) -> None:
    try:
        from tools.nova.db.init_db import init_nova_tables
        init_nova_tables(conn)
    except Exception as exc:
        logger.debug("[trust] lazy init: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Core API
# ────────────────────────────────────────────────────────────────────────────


def get_trust_score(role_id: str) -> float:
    """Return current trust score for a role (defaults to INITIAL_TRUST)."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        # Try ace_coworkers first (may not exist in test/SQLite environments)
        try:
            row = conn.execute(
                "SELECT trust_score FROM ace_coworkers WHERE role_id = ? ORDER BY updated_at DESC LIMIT 1",
                (role_id,),
            ).fetchone()
            if row:
                val = row[0] if not isinstance(row, dict) else row["trust_score"]
                if val is not None:
                    return float(val)
        except Exception:
            pass  # ace_coworkers may not exist in all environments
        # Fall back to ledger (always available via init_nova_tables)
        row = conn.execute(
            "SELECT new_score FROM ace_trust_ledger WHERE role_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (role_id,),
        ).fetchone()
        if row:
            return float(row[0] if not isinstance(row, dict) else row["new_score"])
    except Exception as exc:
        logger.debug("[trust] get_trust_score failed: %s", exc)
    return INITIAL_TRUST


def record_trust_event(
    role_id: str,
    event_type: str,
    source_task_id: str = "",
) -> dict[str, Any]:
    """
    Record a trust event and update the role's trust score.

    event_type: success | failure | hitl_escalation | timeout | phantom_completion
    Returns: {role_id, old_score, delta, new_score, band}
    """
    delta_map = {
        "success": _DELTA_SUCCESS,
        "failure": _DELTA_FAILURE,
        "hitl_escalation": _DELTA_HITL_ESCALATION,
        "timeout": _DELTA_TIMEOUT,
        "phantom_completion": _DELTA_PHANTOM,
    }
    delta = delta_map.get(event_type, 0.0)
    if delta == 0.0:
        return {"skipped": True, "reason": f"unknown event_type {event_type!r}"}

    old_score = get_trust_score(role_id)
    new_score = max(TRUST_MIN, min(TRUST_MAX, round(old_score + delta, 4)))
    band = get_trust_band(new_score)

    try:
        conn = _conn()
        _ensure_tables(conn)
        ledger_id = f"trust-{uuid.uuid4().hex[:10]}"
        conn.execute(
            """
            INSERT INTO ace_trust_ledger (id, role_id, delta, reason, new_score, source_task_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ledger_id, role_id, delta, event_type, new_score, source_task_id),
        )
        # Best-effort update on ace_coworkers
        try:
            conn.execute(
                "UPDATE ace_coworkers SET trust_score = ? WHERE role_id = ?",
                (new_score, role_id),
            )
        except Exception:
            pass
        conn.commit()
        logger.info(
            "[trust] %s: %s → %.3f (delta=%+.3f) band=%s",
            role_id, event_type, new_score, delta, band.name,
        )
    except Exception as exc:
        logger.warning("[trust] record_trust_event failed: %s", exc)
        return {"error": str(exc)}

    return {
        "role_id": role_id,
        "event_type": event_type,
        "old_score": old_score,
        "delta": delta,
        "new_score": new_score,
        "band": band.name,
        "hitl_mode": band.hitl_mode,
        "max_parallel": band.max_parallel,
        "auto_approve_routine": band.auto_approve_routine,
    }


def get_dispatch_config(role_id: str) -> dict[str, Any]:
    """Return dispatch configuration for a role based on its current trust band."""
    score = get_trust_score(role_id)
    band = get_trust_band(score)
    return {
        "role_id": role_id,
        "trust_score": score,
        "band": band.name,
        "hitl_mode": band.hitl_mode,
        "max_parallel": band.max_parallel,
        "auto_approve_routine": band.auto_approve_routine,
    }


def run_weekly_recalibration() -> dict[str, Any]:
    """
    Weekly batch recalibration: compute role-level outcome rates from
    lesson_learned patterns over the last 7 days and apply trust updates.

    Called by the reflexion_loop reflex or a dedicated calibration reflex.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_str = cutoff.isoformat(timespec="seconds")

    results: dict[str, list] = {}
    try:
        conn = _conn()
        _ensure_tables(conn)

        # Get recent kanban task outcomes joined to lesson_learned patterns
        # Use ace_audit_log if available; fall back to kanban_tasks outcome inference
        rows = conn.execute(
            """
            SELECT k.executor_type, k.status, k.last_failure_reason,
                   k.failure_count, k.id
              FROM kanban_tasks k
             WHERE k.updated_at >= ?
               AND k.executor_type LIKE 'ace_%'
             LIMIT 500
            """,
            (cutoff_str,),
        ).fetchall()

        for row in rows:
            if isinstance(row, dict):
                role_id = row.get("executor_type", "").replace("ace_", "")
                status = row.get("status", "")
                fail_count = row.get("failure_count", 0)
                task_id = row.get("id", "")
            else:
                role_id = (row[0] or "").replace("ace_", "")
                status = row[1] or ""
                fail_count = row[3] or 0
                task_id = row[4] or ""

            if not role_id:
                continue

            if status == "done" and fail_count == 0:
                evt = "success"
            elif status in ("failed", "dismissed") or fail_count > 2:
                evt = "failure"
            else:
                continue

            result = record_trust_event(role_id, evt, source_task_id=task_id)
            results.setdefault(role_id, []).append(result)

    except Exception as exc:
        logger.warning("[trust] weekly recalibration failed: %s", exc)
        return {"error": str(exc)}

    return {
        "roles_updated": len(results),
        "events_recorded": sum(len(v) for v in results.values()),
        "details": {r: len(v) for r, v in results.items()},
    }


def get_trust_summary() -> list[dict]:
    """Return current trust scores for all known roles."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT role_id, new_score, recorded_at
              FROM ace_trust_ledger
             WHERE (role_id, recorded_at) IN (
                 SELECT role_id, MAX(recorded_at)
                   FROM ace_trust_ledger
                  GROUP BY role_id
             )
             ORDER BY new_score DESC
            """
        ).fetchall()
        cols = ["role_id", "trust_score", "last_event_at"]
        result = []
        for row in rows:
            d = dict(zip(cols, row)) if not isinstance(row, dict) else dict(row)
            d["band"] = get_trust_band(d["trust_score"]).name
            result.append(d)
        return result
    except Exception as exc:
        logger.debug("[trust] get_trust_summary failed: %s", exc)
        return []
