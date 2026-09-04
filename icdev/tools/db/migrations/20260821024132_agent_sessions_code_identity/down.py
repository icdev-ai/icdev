"""Roll back the agent_sessions code-identity columns.

The column list and the catalogue helper are DUPLICATED from up.py rather than
imported: these migration directories are loaded by path, not as a package, so
`from .up import COLUMNS` raises on the real runner.

Dropping is best-effort ON PURPOSE. PostgreSQL and SQLite 3.35+ support
`ALTER TABLE ... DROP COLUMN`; older SQLite does not, and a rollback that
raises there would leave the migration half-reverted with no way forward. These
columns are nullable and nothing reads them without a presence check, so
leaving them in place on an old SQLite is inert rather than harmful — which is
the outcome to prefer over an exception.
"""
# CUI // SP-CTI

COLUMNS = ("module", "code_version", "code_version_source", "code_dirty")


def _columns(conn, table: str) -> set:
    backend = getattr(conn, "_backend", None) or ""
    if "postgres" in str(backend).lower():
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    out = set()
    for r in rows:
        d = dict(r)
        out.add(d.get("name") or list(d.values())[1])
    return out


def down(conn):
    existing = _columns(conn, "agent_sessions")
    if not existing:
        return
    for name in COLUMNS:
        if name not in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE agent_sessions DROP COLUMN {name}")
        except Exception:  # noqa: BLE001 — see the module docstring
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    conn.commit()
