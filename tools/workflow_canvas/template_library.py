"""Process Template Library — save and retrieve reusable workflow templates."""
from __future__ import annotations
from datetime import datetime, timezone


_DDL_PG = """
CREATE TABLE IF NOT EXISTS wfc_template_library (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    industry    TEXT,
    doc_type    TEXT,
    template_yaml TEXT,
    step_count  INTEGER DEFAULT 0,
    usage_count INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS wfc_tlib_industry ON wfc_template_library (industry);
CREATE INDEX IF NOT EXISTS wfc_tlib_doc_type ON wfc_template_library (doc_type);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS wfc_template_library (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    industry    TEXT,
    doc_type    TEXT,
    template_yaml TEXT,
    step_count  INTEGER DEFAULT 0,
    usage_count INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS wfc_tlib_industry ON wfc_template_library (industry);
CREATE INDEX IF NOT EXISTS wfc_tlib_doc_type ON wfc_template_library (doc_type);
"""


def ensure_table(conn) -> None:
    from tools.db.storage import sql_placeholder
    try:
        conn.execute("SELECT 1 FROM wfc_template_library LIMIT 1")
    except Exception:
        is_pg = hasattr(conn, "autocommit")
        ddl = _DDL_PG if is_pg else _DDL_SQLITE
        for stmt in ddl.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()


def save_template(conn, name: str, workflow: dict, industry: str = "", doc_type: str = "") -> str:
    import uuid, yaml
    from tools.db.storage import sql_placeholder
    ensure_table(conn)
    ph = sql_placeholder(conn)
    tid = str(uuid.uuid4())
    step_count = len(workflow.get("steps") or [])
    now = datetime.now(timezone.utc).isoformat()
    template_yaml = yaml.dump(workflow, allow_unicode=True, sort_keys=False)
    conn.execute(
        f"""INSERT INTO wfc_template_library
            (id, name, description, industry, doc_type, template_yaml, step_count, created_at, updated_at)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
        (tid, name, workflow.get("description") or "", industry, doc_type,
         template_yaml, step_count, now, now),
    )
    conn.commit()
    return tid


def search_templates(conn, query: str = "", industry: str = "", doc_type: str = "") -> list[dict]:
    from tools.db.storage import sql_placeholder
    ensure_table(conn)
    ph = sql_placeholder(conn)
    clauses: list[str] = []
    params: list = []
    if industry:
        clauses.append(f"industry = {ph}")
        params.append(industry)
    if doc_type:
        clauses.append(f"doc_type = {ph}")
        params.append(doc_type)
    if query:
        clauses.append(f"(name ILIKE {ph} OR description ILIKE {ph})" if hasattr(conn, "autocommit")
                       else f"(lower(name) LIKE {ph} OR lower(description) LIKE {ph})")
        q = f"%{query.lower()}%"
        params += [q, q]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT id, name, description, industry, doc_type, step_count, usage_count, created_at FROM wfc_template_library {where} ORDER BY usage_count DESC LIMIT 50",
        params,
    ).fetchall()
    return [dict(r) if hasattr(r, "keys") else {} for r in rows]


def increment_usage(conn, template_id: str) -> None:
    from tools.db.storage import sql_placeholder
    ph = sql_placeholder(conn)
    conn.execute(
        f"UPDATE wfc_template_library SET usage_count = usage_count + 1 WHERE id = {ph}",
        (template_id,),
    )
    conn.commit()
