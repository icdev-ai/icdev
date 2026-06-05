#!/usr/bin/env python3
# CUI // SP-CTI
"""ACE Team Monitor — SUPPORT tier genesis reflex.

Scans ace_instances for stale entries older than STALE_INSTANCE_HOURS and
remediates them:

  * state='running'      → UPDATE state='failed', emit ace_audit_log event
  * state='hitl_pending' → escalate via WorkflowEngine.escalate()

Appends a summary row to genesis_audit on each cycle (NIST AU — append-only).

Return value: {'stale_running': int, 'stale_hitl': int}
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stale threshold — read from ACE constants, fallback to task-spec default
# ---------------------------------------------------------------------------
try:
    from icdev.tools.ace.constants import STALE_INSTANCE_HOURS as _HOURS
except Exception:
    _HOURS = 4


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_threshold_expr(pg_mode: bool) -> str:
    """Return a SQL expression for the staleness boundary."""
    if pg_mode:
        return f"NOW() - INTERVAL '{_HOURS} hours'"
    return f"datetime('now', '-{_HOURS} hours')"


def _ph(pg_mode: bool) -> str:
    """Return the correct SQL placeholder for the backend."""
    return "%s" if pg_mode else "?"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(config: dict, db_conn: Any) -> Dict[str, Any]:
    """ACE team monitor — SUPPORT tier reflex.

    Queries ace_instances WHERE state IN ('running', 'hitl_pending') AND
    started_at < NOW() - INTERVAL '4 hours'. For each stale instance:
      - state=running:      set state=failed, emit ace_audit_log event.
      - state=hitl_pending: escalate via WorkflowEngine.escalate().
    Logs total stale count to genesis_audit.

    Returns {'stale_running': int, 'stale_hitl': int}.
    """
    stale_running = 0
    stale_hitl = 0

    try:
        from icdev.tools.db.storage import get_canvas_connection, is_pg
        from icdev.tools.workflow_hitl.engine import WorkflowEngine

        ace_conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            pg_mode = is_pg(ace_conn)
            ph = _ph(pg_mode)
            threshold = _stale_threshold_expr(pg_mode)

            rows = ace_conn.execute(
                f"SELECT id, state FROM ace_instances "
                f"WHERE state IN ('running', 'hitl_pending') "
                f"AND created_at < {threshold}"
            ).fetchall()

            engine = WorkflowEngine()

            for row in rows:
                instance_id, state = row[0], row[1]

                if state == "running":
                    now = _utcnow()
                    ace_conn.execute(
                        f"UPDATE ace_instances SET state='failed', updated_at={ph} WHERE id={ph}",
                        (now, instance_id),
                    )
                    ace_conn.execute(
                        f"INSERT INTO ace_audit_log "
                        f"(instance_id, action, detail, actor, created_at) "
                        f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
                        (
                            instance_id,
                            "state_transition",
                            f"stale_running → failed by ace_team_monitor reflex (>{_HOURS}h)",
                            "genesis",
                            now,
                        ),
                    )
                    stale_running += 1

                elif state == "hitl_pending":
                    try:
                        engine.escalate(
                            instance_id,
                            reason=f"stale hitl_pending >{_HOURS}h — escalated by ace_team_monitor",
                        )
                    except Exception as exc:
                        logger.warning("WorkflowEngine.escalate(%s) failed: %s", instance_id, exc)
                    stale_hitl += 1

            try:
                ace_conn.commit()
            except Exception:
                pass

        finally:
            ace_conn.close()

        _log_genesis_audit(stale_running, stale_hitl)

    except Exception as exc:
        logger.exception("ace_team_monitor reflex error: %s", exc)

    return {"stale_running": stale_running, "stale_hitl": stale_hitl}


# ---------------------------------------------------------------------------
# Internal: append-only genesis_audit write
# ---------------------------------------------------------------------------

def _log_genesis_audit(stale_running: int, stale_hitl: int) -> None:
    """Write a summary event to genesis_audit (NIST AU — append-only)."""
    try:
        from icdev.tools.db.storage import get_connection, is_pg
    except ImportError:
        try:
            from tools.db.storage import get_connection, is_pg
        except ImportError:
            return

    try:
        from tools.daemon.base import generate_id
    except ImportError:
        import uuid
        def generate_id(prefix: str = "aud") -> str:  # noqa: E306
            return f"{prefix}-{uuid.uuid4().hex[:10]}"

    audit_id = generate_id("aud")
    conn = get_connection()
    try:
        pg_mode = is_pg(conn)
        ph = _ph(pg_mode)
        details = json.dumps({"stale_running": stale_running, "stale_hitl": stale_hitl})
        conn.execute(
            f"""
            INSERT INTO genesis_audit
                (id, event_type, reflex_name, risk_tier, details, success,
                 duration_ms, metric_name, metric_value, gkp_id, created_at)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            """,
            (
                audit_id,
                "ace.team_monitor",
                "ace_team_monitor",
                "SUPPORT",
                details,
                1,
                0,
                "stale_total",
                float(stale_running + stale_hitl),
                None,
                _utcnow(),
            ),
        )
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
