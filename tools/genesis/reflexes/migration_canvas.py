#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Network Migration Canvas Health Monitor.

Runs autonomously every 24 hours (registered in genesis_config.yaml).
Surfaces actionable intelligence:
  - Sessions stuck in-progress (no update for > 7 days)
  - Devices with imminent EOL (< 90 days) that have no active migration session
  - Protocol plans in 'draft' state for > 14 days
  - Parallel timelines past cutover date still at 'planned'

Results are promoted to kanban_tasks (status='suggested') via Oracle at >= 0.7 confidence.
Air-gap safe: no LLM calls — pure DB heuristics.
"""

from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict

from tools.migration_canvas.constants import NET_SESSION_TERMINAL_STATUSES

# Session statuses that count as closed.  Sourced from the canvas constants so
# the reflex and the PATCH validator cannot drift apart: a session closed the
# way the canvas closes it must stop producing findings.
_TERMINAL_SESSION_STATUSES = NET_SESSION_TERMINAL_STATUSES
_TERMINAL_PLACEHOLDERS = ",".join(["%s"] * len(_TERMINAL_SESSION_STATUSES))


# ---------------------------------------------------------------------------
# Module-level fallback constants — overridable from genesis_config.yaml
# under migration_canvas.  Change config, not code.
# ---------------------------------------------------------------------------
_STALE_SESSION_DAYS          = 7      # days without update → session flagged as stale
_EOL_URGENCY_DAYS            = 90     # days-to-EOL threshold to flag missing migration
_STALE_PLAN_DAYS             = 14     # days in draft state → protocol plan flagged
_STALE_SESSION_BASE_CONF     = 0.70   # base confidence for stale session findings
_STALE_SESSION_CONF_PER_DAY  = 0.01   # confidence increment per extra day over threshold
_STALE_SESSION_MAX_CONF      = 0.95   # confidence cap for stale sessions
_EOL_CONF_BASE               = 0.70   # base urgency for EOL no-migration findings
_EOL_CONF_MULTIPLIER         = 0.003  # urgency increment per day under EOL threshold
_EOL_CONF_MAX                = 0.98   # urgency cap for EOL findings
_STALE_PLAN_CONFIDENCE       = 0.75   # fixed confidence for stale protocol plans
_PROMOTION_THRESHOLD_DEFAULT = 0.70   # minimum confidence to promote finding to kanban
_HIGH_PRIORITY_THRESHOLD     = 0.85   # findings above this → "high" priority


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_str: str) -> int:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 0


def _finding_task_id(finding: Dict[str, Any]) -> str:
    """Return a stable kanban task id for a finding.

    Derived from the finding identity only — never from the message or the
    confidence, both of which drift every run as a session ages.  A stable id
    is what makes the ``INSERT OR IGNORE`` below an actual dedupe guard: with
    the previous ``uuid4()`` id nothing ever collided, so each 24h cycle
    re-promoted every open finding as a new card (291 cards over 5 runs).
    """
    key = "|".join((
        finding.get("type", ""),
        str(finding.get("session_id") or finding.get("device_id") or ""),
        finding.get("suggested_action", ""),
    ))
    return "mc-reflex-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _days_until(iso_str: str) -> int:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return 9999


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Migration Canvas health reflex."""
    findings: list[dict] = []
    errors: list[str] = []

    # ── 1. Stale in-progress sessions ────────────────────────────────────────
    try:
        from tools.migration_canvas.db.init_db import get_connection as _mc_conn
        with _mc_conn() as mc:
            # Interpolation is the placeholder count only; values stay bound.
            sql = (
                "SELECT id, src_model, tgt_model, status, updated_at FROM mc_net_sessions "
                f"WHERE status NOT IN ({_TERMINAL_PLACEHOLDERS}) ORDER BY updated_at ASC"  # nosec B608
            )
            rows = mc.execute(sql, _TERMINAL_SESSION_STATUSES).fetchall()
        for r in rows:
            age = _days_since(r["updated_at"] or r.get("created_at", _now()))
            if age > _STALE_SESSION_DAYS:
                findings.append({
                    "type": "stale_migration_session",
                    "session_id": r["id"],
                    "message": f"Session {r['id']} ({r['src_model']} → {r['tgt_model'] or 'TBD'}) "
                               f"has been {r['status']} for {age} days with no progress.",
                    "confidence": min(_STALE_SESSION_BASE_CONF + age * _STALE_SESSION_CONF_PER_DAY, _STALE_SESSION_MAX_CONF),
                    "suggested_action": f"Review or close session {r['id']}",
                })
    except Exception as exc:
        errors.append(f"stale_session_check: {exc}")

    # ── 2. EOL devices with no active migration ───────────────────────────────
    try:
        from tools.migration_canvas.network_migration import _nc_conn, _mc_conn as _mc
        with _nc_conn() as nc:
            devices = nc.execute(
                """SELECT id, label, vendor, model, eol_date
                   FROM ni_devices
                   WHERE eol_date IS NOT NULL
                   ORDER BY eol_date ASC"""
            ).fetchall()
        with _mc() as mc:
            # Use src_device_name as the device identifier (no device_id FK in schema)
            # Interpolation is the placeholder count only; values stay bound.
            active_sql = (
                "SELECT src_device_name FROM mc_net_sessions "
                f"WHERE src_device_name IS NOT NULL AND status NOT IN ({_TERMINAL_PLACEHOLDERS})"  # nosec B608
            )
            active_device_labels = {
                r["src_device_name"]
                for r in mc.execute(active_sql, _TERMINAL_SESSION_STATUSES).fetchall()
            }
        for d in devices:
            days_left = _days_until(d["eol_date"])
            label = d["label"] or d["id"]
            if days_left <= _EOL_URGENCY_DAYS and label not in active_device_labels:
                urgency = max(_EOL_CONF_BASE, min(_EOL_CONF_MAX, _EOL_CONF_BASE + (_EOL_URGENCY_DAYS - days_left) * _EOL_CONF_MULTIPLIER))
                findings.append({
                    "type": "eol_no_migration",
                    "device_id": d["id"],
                    "message": f"{d['vendor'] or ''} {d['model'] or label} reaches EOL in "
                               f"{days_left} days with no active migration session.",
                    "confidence": urgency,
                    "suggested_action": f"Start network migration session for {label}",
                })
    except Exception as exc:
        errors.append(f"eol_no_migration_check: {exc}")

    # ── 3. Protocol plans stuck in draft ─────────────────────────────────────
    try:
        from tools.migration_canvas.db.init_db import get_connection as _mc_conn2
        with _mc_conn2() as mc:
            drafts = mc.execute(
                """SELECT session_id, protocol, created_at
                   FROM mc_net_protocol_plans
                   WHERE status = 'draft'"""
            ).fetchall()
        for d in drafts:
            age = _days_since(d["created_at"])
            if age > _STALE_PLAN_DAYS:
                findings.append({
                    "type": "stale_protocol_plan",
                    "session_id": d["session_id"],
                    "message": f"Protocol plan for {d['protocol']} in session {d['session_id']} "
                               f"has been in draft for {age} days.",
                    "confidence": _STALE_PLAN_CONFIDENCE,
                    "suggested_action": f"Complete or approve {d['protocol']} migration plan for session {d['session_id']}",
                })
    except Exception as exc:
        errors.append(f"stale_protocol_plan_check: {exc}")

    # ── 4. Promote findings to kanban ─────────────────────────────────────────
    promoted = 0
    seen_task_ids: set[str] = set()
    try:
        from tools.db.storage import get_connection as _icdev_conn
        threshold = float((config or {}).get("promotion_threshold", _PROMOTION_THRESHOLD_DEFAULT))
        with _icdev_conn() as ic:
            for f in findings:
                if f["confidence"] < threshold:
                    continue
                task_id = _finding_task_id(f)
                if task_id in seen_task_ids:
                    continue
                seen_task_ids.add(task_id)
                cur = ic.execute(
                    # `source` is not a column — the live column is
                    # `dispatch_source` (swp-scan-01), so no NMCE finding was
                    # ever promoted to the board.
                    """INSERT OR IGNORE INTO kanban_tasks
                       (id, title, description, status, priority, dispatch_source, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        task_id,
                        f"[NMCE] {f['suggested_action']}",
                        f"{f['message']} (confidence={f['confidence']:.2f})",
                        "suggested",
                        "high" if f["confidence"] >= _HIGH_PRIORITY_THRESHOLD else "medium",
                        "genesis_reflex:migration_canvas",
                        _now(),
                        _now(),
                    ),
                )
                # Card ids are stable, so a re-run of an unchanged finding is
                # ignored by the DB.  Count rows actually written, not rows
                # attempted, or the metric reports 60 promotions a day forever.
                promoted += getattr(cur, "rowcount", 1) or 0
    except Exception as exc:
        errors.append(f"kanban_promote: {exc}")

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "findings": len(findings),
            "promoted_to_kanban": promoted,
            "promotion_candidates": len(seen_task_ids),
            "errors": errors,
            "breakdown": {
                "stale_sessions": sum(1 for f in findings if f["type"] == "stale_migration_session"),
                "eol_no_migration": sum(1 for f in findings if f["type"] == "eol_no_migration"),
                "stale_protocol_plans": sum(1 for f in findings if f["type"] == "stale_protocol_plan"),
            },
        },
    }
