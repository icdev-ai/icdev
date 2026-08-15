# CUI // SP-CTI
"""Migration 153: add ontology_id column to kg_nodes and canvas_kg_nodes."""

MIGRATION_ID = "153"
MIGRATION_NAME = "ontology_id"


def _is_postgresql(conn) -> bool:
    return "psycopg2" in type(conn).__module__ or "postgresql" in str(type(conn)).lower()


def _table_exists(conn, table: str) -> bool:
    if _is_postgresql(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    if _is_postgresql(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
            (table, column),
        ).fetchone()
        return row is not None
    for info in conn.execute(f"PRAGMA table_info({table})").fetchall():  # nosec B608
        name = info[1] if isinstance(info, (list, tuple)) else info["name"]
        if name == column:
            return True
    return False


def _add_column_safe(conn, table: str, column: str, col_type: str) -> bool:
    if not _table_exists(conn, table):
        return False
    if _column_exists(conn, table, column):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")  # nosec B608
    return True


def up(conn) -> dict:
    added = 0
    skipped = 0
    errors = []

    for table in ("kg_nodes", "canvas_kg_nodes"):
        try:
            if _add_column_safe(conn, table, "ontology_id", "TEXT"):
                added += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"{table}: {exc}")

    for table, idx in (
        ("kg_nodes", "idx_kg_nodes_ontology_id"),
        ("canvas_kg_nodes", "idx_canvas_kg_nodes_ontology_id"),
    ):
        try:
            if _table_exists(conn, table) and _column_exists(conn, table, "ontology_id"):
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx} ON {table}(ontology_id)"  # nosec B608
                )
        except Exception as exc:
            errors.append(f"index {idx}: {exc}")

    conn.commit()
    return {"added": added, "skipped": skipped, "errors": errors}
