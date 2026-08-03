# CUI // SP-CTI
"""Schema dependencies of the Internal Developer Portal — point 6 of the gate.

CLAUDE.md's 8-point gate asks for "DB migration — created if new tables needed;
table existence handled gracefully if migration hasn't run yet". The portal
needs **no new table**: everything it renders is derived at request time from
``args/component_registry.yaml``, the repo tree, and ``args/scorecards/*.yaml``
via the ``idp.components`` IQE collection. Inventing a table so a checklist
item could be ticked would have been the wrong trade — a second DDL source for
data nobody writes.

What the portal *does* depend on is three tables it does not own, each of which
sharpens the picture when present and none of which it requires:

  ``developer_scorecards``       persisted per-component scores (idp-score-01/03)
  ``awareness_component_health`` route probe rows behind ``failing_probes``
  ``kg_edges``                   component graph behind blast radius (idp-cat-02)

So this module is the graceful-degradation half of point 6: one catalog query
that reports which of those exist, so the page can say "not measured yet"
instead of rendering a zero that reads as "healthy". A component that was never
probed must never look the same as one that passed.

RLS: uses ``get_canvas_connection()`` per the CLAUDE.md canvas rule. The global
RLS predicate references ``classification``/``tenant_id``; the system catalogs
queried here carry neither, and injecting the predicate into a catalog read
raises ``UndefinedColumn`` (see coherence check ``canvas_rls_bypass``).
"""
from __future__ import annotations

from typing import Any

#: Tables the portal reads when they exist. Order is presentation order.
OPTIONAL_TABLES: tuple[tuple[str, str], ...] = (
    (
        "developer_scorecards",
        "Persisted per-component scores. Until idp-score-01/03 land, the portal "
        "evaluates live on every request and shows no trend.",
    ),
    (
        "awareness_component_health",
        "Route probe snapshots. Without them the probes-healthy rule reports "
        "'never measured' rather than a pass.",
    ),
    (
        "kg_edges",
        "Component dependency edges. Without them blast radius is unavailable "
        "(idp-cat-02).",
    ),
)


def get_connection():
    """Canvas-scoped connection (no RLS predicate). See module docstring."""
    from tools.db.storage import get_canvas_connection  # noqa: PLC0415

    return get_canvas_connection()


def _existing_table_names(conn: Any) -> set[str]:
    """Return every user table name visible on *conn*.

    One catalog query, not one probe per table. Probing with
    ``SELECT 1 FROM <t>`` would abort the surrounding PostgreSQL transaction on
    the first miss and make every *subsequent* table look missing too — the
    failure mode that makes a present table read as absent.
    """
    from tools.db.storage import is_pg  # noqa: PLC0415

    if is_pg(conn):
        sql = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        )
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'table'"
    cur = conn.execute(sql)
    return {str(row[0]) for row in (cur.fetchall() or [])}


def schema_status(conn: Any = None) -> list[dict[str, Any]]:
    """Report presence of each optional backing table.

    Never raises: an unreachable database yields ``present=False`` with the
    reason attached, because a portal that 500s when the DB is down is worse
    than one that says which signals are dark.
    """
    error = ""
    names: set[str] = set()
    owns_conn = conn is None
    try:
        if owns_conn:
            conn = get_connection()
        names = _existing_table_names(conn)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

    return [
        {
            "table": table,
            "present": table in names,
            "purpose": purpose,
            "error": error,
        }
        for table, purpose in OPTIONAL_TABLES
    ]


def init_db() -> dict[str, Any]:
    """No tables to create. Returns the dependency report for callers/CLI."""
    status = schema_status()
    return {
        "created": [],
        "optional_tables": status,
        "missing": [row["table"] for row in status if not row["present"]],
    }
