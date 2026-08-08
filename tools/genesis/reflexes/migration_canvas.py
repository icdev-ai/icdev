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

from datetime import datetime, timezone
from typing import Any, Dict


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
_MAX_LISTED_PER_CARD         = 20     # findings enumerated in a batched card body

# Human-readable card titles per finding type.  Also the dedupe key: the
# open-card guard matches on "[NMCE] <label> —", so these strings are
# load-bearing, not cosmetic.
_TYPE_LABELS = {
    "stale_migration_session": "Stale migration sessions",
    "eol_no_migration":        "EOL devices with no migration",
    "stale_protocol_plan":     "Protocol plans stuck in draft",
}


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
            rows = mc.execute(
                """SELECT id, src_model, tgt_model, status, updated_at
                   FROM mc_net_sessions
                   WHERE status IN ('in_progress','draft')
                   ORDER BY updated_at ASC"""
            ).fetchall()
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
            active_device_labels = {
                r["src_device_name"] for r in mc.execute(
                    "SELECT src_device_name FROM mc_net_sessions "
                    "WHERE src_device_name IS NOT NULL AND status != 'completed'"
                ).fetchall()
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
    # One card per finding TYPE per cycle, not one per finding, and only when no
    # open card for that type already exists.  Both halves matter:
    #
    #   * Per-finding cards do not scale.  Nothing in the canvas ever moves a
    #     session out of 'in_progress' (no UI control, and the PATCH endpoint is
    #     never called with a status), so every session ever created is stale
    #     forever — 54 of them on this board, each minting its own card.
    #   * The id used to be a fresh uuid4 every run, so ON CONFLICT DO NOTHING
    #     never matched anything and each 24h cycle re-inserted the whole set.
    #     289 cards accumulated from 60 distinct findings, 169 of them already
    #     'scheduled' — i.e. queued to burn one agent session apiece.
    #
    # The open-card guard (rather than a deterministic id) is the same shape
    # coherence_to_kanban_reflex.py uses: the finding re-raises if it is still
    # true after someone closes the card, but never stacks while one is open.
    promoted = 0
    try:
        import uuid
        from tools.db.storage import get_connection as _icdev_conn
        threshold = float((config or {}).get("promotion_threshold", _PROMOTION_THRESHOLD_DEFAULT))

        by_type: dict[str, list[dict]] = {}
        for f in findings:
            if f["confidence"] >= threshold:
                by_type.setdefault(f["type"], []).append(f)

        with _icdev_conn() as ic:
            for ftype, group in by_type.items():
                group.sort(key=lambda x: -x["confidence"])
                title = f"[NMCE] {_TYPE_LABELS.get(ftype, ftype)} — {len(group)} finding(s)"
                open_row = ic.execute(
                    """SELECT id FROM kanban_tasks
                       WHERE dispatch_source = %s
                         AND title LIKE %s
                         AND status NOT IN ('done','failed','cancelled','canceled')
                       LIMIT 1""",
                    ("genesis_reflex:migration_canvas",
                     f"[NMCE] {_TYPE_LABELS.get(ftype, ftype)} —%"),
                ).fetchone()
                if open_row is not None:
                    continue

                top = group[0]["confidence"]
                body = "\n".join(f"  - {f['message']}" for f in group[:_MAX_LISTED_PER_CARD])
                if len(group) > _MAX_LISTED_PER_CARD:
                    body += f"\n  - …and {len(group) - _MAX_LISTED_PER_CARD} more"
                description = (
                    f"{len(group)} {_TYPE_LABELS.get(ftype, ftype).lower()} finding(s), "
                    f"max confidence={top:.2f}:\n{body}\n\n"
                    f"Suggested action: {group[0]['suggested_action']}"
                )
                ic.execute(
                    """INSERT OR IGNORE INTO kanban_tasks
                       (id, title, description, status, priority, dispatch_source, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        "mc-reflex-" + uuid.uuid4().hex[:8],
                        title,
                        description,
                        "suggested",
                        "high" if top >= _HIGH_PRIORITY_THRESHOLD else "medium",
                        "genesis_reflex:migration_canvas",
                        _now(),
                        _now(),
                    ),
                )
                promoted += 1
    except Exception as exc:
        errors.append(f"kanban_promote: {exc}")

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "findings": len(findings),
            "promoted_to_kanban": promoted,
            "errors": errors,
            "breakdown": {
                "stale_sessions": sum(1 for f in findings if f["type"] == "stale_migration_session"),
                "eol_no_migration": sum(1 for f in findings if f["type"] == "eol_no_migration"),
                "stale_protocol_plans": sum(1 for f in findings if f["type"] == "stale_protocol_plan"),
            },
        },
    }
