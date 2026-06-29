# CUI // SP-CTI
"""Hierarchical notes — Trilium adaptation.

Adds cloneable, attribute-rich notes to the Second Brain canvas.
A note can exist in multiple parent locations (clone model); its
content is stored once and referenced by multiple sb_note_parents rows.
Typed attributes support both key-value labels and typed relations
between notes (sb_note_attributes).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    from tools.second_brain.personal_rag import _conn as _sb_conn
    return _sb_conn()


def _ensure_schema(conn: Any) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sb_notes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT 'text/plain',
            is_protected INTEGER NOT NULL DEFAULT 0,
            date_created TEXT NOT NULL,
            date_modified TEXT NOT NULL,
            classification TEXT NOT NULL DEFAULT 'CUI'
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sb_note_parents (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            parent_id TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            date_created TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sb_note_attributes (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            attr_type TEXT NOT NULL,
            name TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            target_note_id TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            date_created TEXT NOT NULL
        )"""
    )
    conn.commit()


def create_note(
    user_id: str,
    title: str,
    content: str = "",
    parent_id: str | None = None,
    mime_type: str = "text/plain",
    tenant_id: str = "default",
) -> dict:
    """Create a note and place it under parent_id (or at root if None)."""
    from tools.db.storage import sql_placeholder
    note_id = str(uuid.uuid4())
    parent_row_id = str(uuid.uuid4())
    now = _now()
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        conn.execute(
            f"INSERT INTO sb_notes (id,user_id,tenant_id,title,content,mime_type,"
            f"is_protected,date_created,date_modified,classification) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},0,{ph},{ph},'CUI')",
            (note_id, user_id, tenant_id, title, content, mime_type, now, now),
        )
        conn.execute(
            f"INSERT INTO sb_note_parents (id,note_id,parent_id,position,date_created) "
            f"VALUES ({ph},{ph},{ph},0,{ph})",
            (parent_row_id, note_id, parent_id, now),
        )
        conn.commit()
    return {"id": note_id, "title": title, "parent_id": parent_id}


def clone_note(note_id: str, new_parent_id: str | None, position: int = 0) -> dict:
    """Add a new parent link for an existing note (true clone — content shared)."""
    from tools.db.storage import sql_placeholder
    parent_row_id = str(uuid.uuid4())
    now = _now()
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        conn.execute(
            f"INSERT INTO sb_note_parents (id,note_id,parent_id,position,date_created) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (parent_row_id, note_id, new_parent_id, position, now),
        )
        conn.commit()
    return {"note_id": note_id, "new_parent_id": new_parent_id}


def get_note(note_id: str, user_id: str) -> dict | None:
    """Return note dict with attributes and parents lists."""
    from tools.db.storage import sql_placeholder
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        row = conn.execute(
            f"SELECT * FROM sb_notes WHERE id={ph} AND user_id={ph}",
            (note_id, user_id),
        ).fetchone()
        if row is None:
            return None
        note = dict(row)
        parents = conn.execute(
            f"SELECT id,parent_id,position FROM sb_note_parents WHERE note_id={ph}",
            (note_id,),
        ).fetchall()
        attrs = conn.execute(
            f"SELECT id,attr_type,name,value,target_note_id,position "
            f"FROM sb_note_attributes WHERE note_id={ph} ORDER BY position",
            (note_id,),
        ).fetchall()
        note["parents"] = [dict(p) for p in parents]
        note["attributes"] = [dict(a) for a in attrs]
    return note


def list_children(
    parent_id: str | None,
    user_id: str,
    tenant_id: str = "default",
) -> list[dict]:
    """Return direct children of parent_id ordered by position."""
    from tools.db.storage import sql_placeholder
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        if parent_id is None:
            rows = conn.execute(
                f"SELECT n.id,n.title,n.mime_type,n.date_modified,p.position "
                f"FROM sb_notes n JOIN sb_note_parents p ON n.id=p.note_id "
                f"WHERE n.user_id={ph} AND n.tenant_id={ph} AND p.parent_id IS NULL "
                f"ORDER BY p.position",
                (user_id, tenant_id),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT n.id,n.title,n.mime_type,n.date_modified,p.position "
                f"FROM sb_notes n JOIN sb_note_parents p ON n.id=p.note_id "
                f"WHERE n.user_id={ph} AND n.tenant_id={ph} AND p.parent_id={ph} "
                f"ORDER BY p.position",
                (user_id, tenant_id, parent_id),
            ).fetchall()
    return [dict(r) for r in rows]


def set_label(note_id: str, name: str, value: str) -> dict:
    """Upsert a label attribute (replace existing by name)."""
    from tools.db.storage import sql_placeholder
    now = _now()
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        existing = conn.execute(
            f"SELECT id FROM sb_note_attributes "
            f"WHERE note_id={ph} AND attr_type='label' AND name={ph}",
            (note_id, name),
        ).fetchone()
        if existing:
            attr_id = dict(existing)["id"]
            conn.execute(
                f"UPDATE sb_note_attributes SET value={ph} WHERE id={ph}",
                (value, attr_id),
            )
        else:
            attr_id = str(uuid.uuid4())
            conn.execute(
                f"INSERT INTO sb_note_attributes "
                f"(id,note_id,attr_type,name,value,target_note_id,position,date_created) "
                f"VALUES ({ph},{ph},'label',{ph},{ph},NULL,0,{ph})",
                (attr_id, note_id, name, value, now),
            )
        conn.commit()
    return {"id": attr_id, "name": name, "value": value}


def set_relation(note_id: str, name: str, target_note_id: str) -> dict:
    """Upsert a typed relation (replace existing by name)."""
    from tools.db.storage import sql_placeholder
    now = _now()
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        existing = conn.execute(
            f"SELECT id FROM sb_note_attributes "
            f"WHERE note_id={ph} AND attr_type='relation' AND name={ph}",
            (note_id, name),
        ).fetchone()
        if existing:
            attr_id = dict(existing)["id"]
            conn.execute(
                f"UPDATE sb_note_attributes SET target_note_id={ph} WHERE id={ph}",
                (target_note_id, attr_id),
            )
        else:
            attr_id = str(uuid.uuid4())
            conn.execute(
                f"INSERT INTO sb_note_attributes "
                f"(id,note_id,attr_type,name,value,target_note_id,position,date_created) "
                f"VALUES ({ph},{ph},'relation',{ph},'',{ph},0,{ph})",
                (attr_id, note_id, name, target_note_id, now),
            )
        conn.commit()
    return {"id": attr_id, "name": name, "target_note_id": target_note_id}


def get_attributes(note_id: str) -> list[dict]:
    """Return all attributes for note_id."""
    from tools.db.storage import sql_placeholder
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        rows = conn.execute(
            f"SELECT id,attr_type,name,value,target_note_id,position "
            f"FROM sb_note_attributes WHERE note_id={ph} ORDER BY position",
            (note_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_note(note_id: str, user_id: str) -> dict:
    """Delete note and all parent/attribute rows."""
    from tools.db.storage import sql_placeholder
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        conn.execute(
            f"DELETE FROM sb_note_attributes WHERE note_id={ph}", (note_id,)
        )
        conn.execute(
            f"DELETE FROM sb_note_parents WHERE note_id={ph}", (note_id,)
        )
        conn.execute(
            f"DELETE FROM sb_notes WHERE id={ph} AND user_id={ph}",
            (note_id, user_id),
        )
        conn.commit()
    return {"deleted": note_id}


def move_note(
    note_id: str,
    old_parent_id: str | None,
    new_parent_id: str | None,
    position: int = 0,
) -> dict:
    """Move one occurrence of note_id from old_parent_id to new_parent_id."""
    from tools.db.storage import sql_placeholder
    with _conn() as conn:
        ph = sql_placeholder(conn)
        _ensure_schema(conn)
        if old_parent_id is None:
            conn.execute(
                f"UPDATE sb_note_parents SET parent_id={ph}, position={ph} "
                f"WHERE note_id={ph} AND parent_id IS NULL",
                (new_parent_id, position, note_id),
            )
        else:
            conn.execute(
                f"UPDATE sb_note_parents SET parent_id={ph}, position={ph} "
                f"WHERE note_id={ph} AND parent_id={ph}",
                (new_parent_id, position, note_id, old_parent_id),
            )
        conn.commit()
    return {"note_id": note_id, "old_parent_id": old_parent_id, "new_parent_id": new_parent_id}
