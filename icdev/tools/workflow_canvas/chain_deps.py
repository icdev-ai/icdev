"""Cross-chain dependency graph for Process-Ify."""
from __future__ import annotations
from datetime import datetime, timezone


_DDL_PG = """
CREATE TABLE IF NOT EXISTS wfc_chain_deps (
    id           TEXT PRIMARY KEY,
    chain_id     TEXT NOT NULL,
    phase_number INTEGER,
    depends_on_chain_id TEXT NOT NULL,
    depends_on_phase    INTEGER,
    note         TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS wfc_chain_deps_chain ON wfc_chain_deps (chain_id);
CREATE INDEX IF NOT EXISTS wfc_chain_deps_on ON wfc_chain_deps (depends_on_chain_id);
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS wfc_chain_deps (
    id           TEXT PRIMARY KEY,
    chain_id     TEXT NOT NULL,
    phase_number INTEGER,
    depends_on_chain_id TEXT NOT NULL,
    depends_on_phase    INTEGER,
    note         TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS wfc_chain_deps_chain ON wfc_chain_deps (chain_id);
CREATE INDEX IF NOT EXISTS wfc_chain_deps_on ON wfc_chain_deps (depends_on_chain_id);
"""


def ensure_table(conn) -> None:
    try:
        conn.execute("SELECT 1 FROM wfc_chain_deps LIMIT 1")
    except Exception:
        is_pg = hasattr(conn, "autocommit")
        ddl = _DDL_PG if is_pg else _DDL_SQLITE
        for stmt in ddl.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()


def add_dependency(conn, chain_id: str, phase_number: int | None,
                   depends_on_chain_id: str, depends_on_phase: int | None,
                   note: str = "") -> str:
    import uuid
    from tools.db.storage import sql_placeholder
    ensure_table(conn)
    ph = sql_placeholder(conn)
    dep_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"""INSERT INTO wfc_chain_deps
            (id, chain_id, phase_number, depends_on_chain_id, depends_on_phase, note, created_at)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
        (dep_id, chain_id, phase_number, depends_on_chain_id, depends_on_phase, note, now),
    )
    conn.commit()
    return dep_id


def get_dependencies(conn, chain_id: str) -> list[dict]:
    from tools.db.storage import sql_placeholder
    ensure_table(conn)
    ph = sql_placeholder(conn)
    rows = conn.execute(
        f"SELECT * FROM wfc_chain_deps WHERE chain_id = {ph}", (chain_id,)
    ).fetchall()
    return [dict(r) if hasattr(r, "keys") else {} for r in rows]


def check_blockers(conn, chain_id: str) -> list[dict]:
    """Return list of unsatisfied upstream dependencies (blocking phases not yet done)."""
    from tools.db.storage import sql_placeholder, get_canvas_connection
    ensure_table(conn)
    deps = get_dependencies(conn, chain_id)
    blockers: list[dict] = []
    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    cc_ph = sql_placeholder(cc)
    for dep in deps:
        upstream_id = dep.get("depends_on_chain_id")
        upstream_phase = dep.get("depends_on_phase")
        row = cc.execute(
            f"SELECT * FROM wfc_process_chains WHERE id = {cc_ph}", (upstream_id,)
        ).fetchone()
        chain_status = (dict(row).get("status") if row and hasattr(row, "keys") else "") or "unknown"
        if chain_status not in ("complete", "done"):
            blockers.append({
                "dep": dep,
                "upstream_chain_status": chain_status,
                "message": f"Chain {upstream_id} (phase {upstream_phase}) not yet complete",
            })
    cc.close()
    return blockers


def build_dependency_graph(conn) -> dict:
    """Return adjacency list of all chains and their upstream deps."""
    ensure_table(conn)
    rows = conn.execute("SELECT * FROM wfc_chain_deps").fetchall()
    graph: dict[str, list[str]] = {}
    for r in rows:
        row = dict(r) if hasattr(r, "keys") else {}
        chain = row.get("chain_id", "")
        dep = row.get("depends_on_chain_id", "")
        graph.setdefault(chain, [])
        if dep not in graph[chain]:
            graph[chain].append(dep)
    return graph
