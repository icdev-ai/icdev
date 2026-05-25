# CUI // SP-CTI
"""HITLGate — hooks into the Kanban state machine to block in_progress→done."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging

from tools.db.storage import get_connection

logger = get_logger(__name__)


class HITLGate:

    def should_gate(self, task_id: str) -> bool:
        """Return True if there is a pending HITL approval gate for this task."""
        return self.get_pending(task_id) is not None

    def get_pending(self, task_id: str) -> dict | None:
        """Return the pending approval dict for this task, or None if no gate active."""
        conn = get_connection()
        try:
            row = conn.execute(
                """SELECT wa.* FROM wf_approvals wa
                   JOIN wf_instances wi ON wi.id = wa.instance_id
                   WHERE wi.task_id=%s
                     AND wa.status='pending'
                     AND wi.status IN ('active','waiting_external')
                   ORDER BY wa.created_at DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
        finally:
            conn.close()

    def get_instance_for_task(self, task_id: str) -> dict | None:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM wf_instances WHERE task_id=%s AND status IN ('active','waiting_external') LIMIT 1",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None
        finally:
            conn.close()
