# CUI // SP-CTI
"""Customer interaction history — log, retrieve, and last-contact update."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_TABLE = "user_relationship_interactions"


def _conn():
    from tools.second_brain.constants import BRIEFING_ENV_FLAG
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection(BRIEFING_ENV_FLAG)


def log_interaction(
    relationship_id: str,
    user_id: str,
    title: str,
    notes: str = "",
    action_items: list[str] | None = None,
    follow_up_date: str | None = None,
    interaction_type: str = "meeting",
    interaction_date: str | None = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    from tools.db.storage import sql_placeholder
    iid = str(uuid.uuid4())
    idate = interaction_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"INSERT INTO {_TABLE} "
            f"(id,relationship_id,user_id,tenant_id,interaction_date,title,notes,action_items,follow_up_date,interaction_type) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
            (iid, relationship_id, user_id, tenant_id, idate, title, notes,
             json.dumps(action_items or []), follow_up_date, interaction_type),
        )
        # Update last_contact_at on the relationship row
        conn.execute(
            f"UPDATE user_relationships SET last_contact_at={ph} WHERE id={ph}",
            (idate, relationship_id),
        )
        conn.commit()
    return {"id": iid, "relationship_id": relationship_id, "date": idate, "title": title}


def get_interactions(
    relationship_id: str,
    user_id: str,
    tenant_id: str = "default",
    limit: int = 20,
) -> list[dict]:
    from tools.db.storage import sql_placeholder
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                f"SELECT id,interaction_date,title,notes,action_items,follow_up_date,interaction_type,created_at "
                f"FROM {_TABLE} WHERE relationship_id={ph} AND user_id={ph} AND tenant_id={ph} "
                f"ORDER BY interaction_date DESC LIMIT {int(limit)}",
                (relationship_id, user_id, tenant_id),
            ).fetchall()
        cols = ["id", "date", "title", "notes", "action_items", "follow_up_date", "type", "created_at"]
        result = []
        for r in rows:
            row = dict(zip(cols, r))
            try:
                row["action_items"] = json.loads(row["action_items"] or "[]")
            except Exception:
                row["action_items"] = []
            result.append(row)
        return result
    except Exception as exc:
        logger.debug("[interactions] get failed: %s", exc)
        return []


def delete_interaction(
    interaction_id: str,
    user_id: str,
    tenant_id: str = "default",
) -> bool:
    from tools.db.storage import sql_placeholder
    try:
        with _conn() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"DELETE FROM {_TABLE} WHERE id={ph} AND user_id={ph} AND tenant_id={ph}",
                (interaction_id, user_id, tenant_id),
            )
            conn.commit()
        return True
    except Exception:
        return False
