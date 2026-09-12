# [TEMPLATE: CUI // SP-CTI]
"""The ONE merged activity-feed query (audit_trail UNION ALL hook_events).

xrv-mem-03. ``tools/dashboard/api/activity.py`` (the Flask blueprint) and
``tools/memory/hybrid_search.py`` (the ``timeline`` layer) both read the
session chronology, and the card's rule is that neither carries its own copy
of the UNION: a second spelling is how two surfaces come to disagree about
what an "event" is. So the UNION lives here, in a plain module with NO Flask
and NO dashboard config import -- a memory CLI must be able to ask for the
timeline without loading a web framework or reading ``.env``.

``build_feed_query`` returns ``(sql, params)`` for the caller's own
connection. Filters are optional and additive; ``since``/``until`` are
inclusive bounds and ``after`` is the STRICT cursor the poll route uses.
``order`` is ``DESC`` (newest first, the feed's shape) or ``ASC``.

Column contract of a returned row, in both tables' terms:
    source | id | event_type | actor_or_agent | summary | project_id |
    classification | created_at
"""
from __future__ import annotations

MERGED_QUERY = """
SELECT * FROM (
    SELECT
        'audit' AS source,
        id,
        event_type,
        actor AS actor_or_agent,
        action AS summary,
        project_id,
        classification,
        created_at
    FROM audit_trail

    UNION ALL

    SELECT
        'hook' AS source,
        id,
        hook_type AS event_type,
        session_id AS actor_or_agent,
        tool_name AS summary,
        project_id,
        classification,
        created_at
    FROM hook_events
) merged
WHERE 1=1
"""

#: The column names of one merged row, in SELECT order.
FEED_COLUMNS = (
    "source", "id", "event_type", "actor_or_agent", "summary",
    "project_id", "classification", "created_at",
)

_ORDERS = ("DESC", "ASC")


def build_feed_query(
    ph: str,
    *,
    source: str | None = None,
    event_type: str | None = None,
    actor: str | None = None,
    project_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    after: str | None = None,
    order: str = "DESC",
    limit: int | None = None,
    offset: int | None = None,
    normalise_ts: bool = False,
) -> tuple[str, list]:
    """Compose the merged feed SELECT with the given filters.

    ``ph`` is the backend's placeholder (``sql_placeholder(conn)``). ``actor``
    is a substring match on ``actor_or_agent`` (an audit actor or a hook
    session id); every other filter is equality or a bound on ``created_at``.

    ``normalise_ts`` is for SQLite ONLY, where ``created_at`` is TEXT and the
    two tables hold BOTH spellings -- ``CURRENT_TIMESTAMP`` writes
    ``YYYY-MM-DD HH:MM:SS`` while the ingest route writes ``isoformat()``'s
    ``T`` form -- and a space sorts before a ``T``, so a ``T``-form bound
    silently excludes every space-form row of the same day. With it on, the
    bounds and the ORDER BY compare ``replace(created_at, ' ', 'T')`` and the
    caller passes ``T``-form bounds. On PostgreSQL ``created_at`` is a real
    timestamp, the bound is cast, and the flag must stay off (``replace`` on
    a timestamp does not type-check). The feed routes leave it off: their
    behaviour is unchanged by this card.
    """
    order = (order or "DESC").upper()
    if order not in _ORDERS:
        raise ValueError(f"order must be one of {_ORDERS}, got {order!r}")

    ts = "replace(created_at, ' ', 'T')" if normalise_ts else "created_at"
    query = MERGED_QUERY
    params: list = []

    if source:
        query += f" AND source = {ph}"
        params.append(source)
    if event_type:
        query += f" AND event_type = {ph}"
        params.append(event_type)
    if actor:
        query += f" AND actor_or_agent LIKE {ph}"
        params.append(f"%{actor}%")
    if project_id:
        query += f" AND project_id = {ph}"
        params.append(project_id)
    if since:
        query += f" AND {ts} >= {ph}"
        params.append(since)
    if until:
        query += f" AND {ts} <= {ph}"
        params.append(until)
    if after:
        query += f" AND {ts} > {ph}"
        params.append(after)

    query += f" ORDER BY {ts} {order}"
    if limit is not None:
        query += f" LIMIT {ph}"
        params.append(int(limit))
    if offset is not None:
        query += f" OFFSET {ph}"
        params.append(int(offset))
    return query, params
