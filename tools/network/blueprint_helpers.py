# CUI // SP-CTI
"""Network Design Canvas — Blueprint helper functions."""

import json
import uuid
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.blueprint_helpers")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row):
    if row is None:
        return {}
    return dict(row) if hasattr(row, "keys") else {}


# ── Backend-aware existence probes ─────────────────────────────────────────────
# Runtime routes probe table/column existence repeatedly per request. The old
# per-file `SELECT name FROM sqlite_master ...` was SQLite-dialect SQL that only
# worked on PostgreSQL because the storage translate layer regex-rewrites
# sqlite_master; that is fragile. These shared helpers branch explicitly on the
# backend (PG → information_schema, SQLite → sqlite_master / PRAGMA) and cache
# only POSITIVE results process-wide: once a table/column is known to exist it
# never disappears, so caching True is safe. Negatives are NEVER cached —
# migrations and ensure-paths create tables/columns while the process is up, so
# a probe that ran before creation must re-probe rather than latch "absent".
_TABLE_EXISTS_CACHE: dict = {}
_COL_EXISTS_CACHE: dict = {}


def _conn_backend(conn) -> str:
    """Return 'postgresql' or 'sqlite' for a connection, via the canonical is_pg."""
    try:
        from tools.db.storage import is_pg

        return "postgresql" if is_pg(conn) else "sqlite"
    except Exception:
        # Fall back to the connection's own marker; default sqlite.
        return "postgresql" if getattr(conn, "_backend", "sqlite") == "postgresql" else "sqlite"


def table_exists(conn, name: str) -> bool:
    """Backend-aware table existence check with a positive-only cache.

    Only True results are cached (keyed by (backend, table_name)): a table that
    exists never disappears, so the probe can be skipped thereafter. A negative
    is never cached — tables can be created later in-process (migrations,
    ensure-paths) — so absence always re-probes.
    """
    backend = _conn_backend(conn)
    key = (backend, name)
    if _TABLE_EXISTS_CACHE.get(key):
        return True
    if backend == "postgresql":
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (name,),
        ).fetchone()
    else:
        # pg-portability: sqlite-only path — explicit backend branch (PG branch
        # above uses information_schema); wraps the shared probe semantics with a
        # positive-only per-request cache.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = %s",
            (name,),
        ).fetchone()
    exists = row is not None
    if exists:
        _TABLE_EXISTS_CACHE[key] = True
    return exists


def col_exists(conn, table: str, col: str) -> bool:
    """Backend-aware column existence check with a positive-only cache.

    Only True results are cached (keyed by (backend, table, col)): a column that
    exists never disappears. A negative is never cached — columns can be added
    later in-process (migrations) — so absence always re-probes. PG uses
    information_schema.columns; SQLite uses PRAGMA table_info.
    """
    backend = _conn_backend(conn)
    key = (backend, table, col)
    if _COL_EXISTS_CACHE.get(key):
        return True
    if backend == "postgresql":
        row = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, col),
        ).fetchone()
        exists = row is not None
    else:
        # pg-portability: sqlite-only path — explicit backend branch (PG branch
        # above uses information_schema.columns).
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]  # noqa: S608
        exists = col in cols
    if exists:
        _COL_EXISTS_CACHE[key] = True
    return exists


# ── Parsed-graph LRU cache (ndc-perf-02) ───────────────────────────────────────
# `topologies.graph_json` is a large JSON blob that runtime routes json.loads
# ~126 times (a dashboard EOL loop parses up to 20 blobs per render; the IQE
# node/edge adapters parse the same blob twice per query; the enricher re-parses
# on every call). Parsing large blobs repeatedly is pure waste when the row has
# not changed. This bounded module-level LRU memoizes the parse keyed by
# (topo_id, updated_at): because `updated_at` is bumped on every save, a stale
# entry can never be served — its key simply stops matching and the entry ages
# out. `invalidate_parsed_graph()` is a belt-and-suspenders hook for the rare
# same-timestamp save.
#
# MUTATION-SAFETY CONTRACT: the cached dict is SHARED. Callers MUST treat the
# returned value as read-only. Sites that mutate the graph (e.g. the enricher
# rebuilds nodes and rewrites device config) MUST pass ``copy=True`` to receive
# a private deepcopy; mutating the shared object would corrupt every concurrent
# reader. Read-only sites (dashboard EOL loop, IQE adapters) take the shared
# object directly to avoid the copy cost.
_PARSED_GRAPH_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_PARSED_GRAPH_MAX = 64
_PARSED_GRAPH_STATS = {"hits": 0, "misses": 0, "evictions": 0}


def _scalar(row, key, idx):
    """Read a column from a StorageConnection row (dict-like or tuple)."""
    if row is None:
        return None
    if hasattr(row, "keys"):
        return row[key]
    return row[idx]


def get_parsed_graph(conn, topo_id, copy: bool = False):
    """Return the parsed ``graph_json`` dict for a topology, memoized.

    Fetches ``updated_at`` (cheap PK lookup) on every call to key the cache;
    ``graph_json`` is fetched and parsed ONLY on a miss. The cache is keyed by
    (topo_id, updated_at) so a saved topology (which bumps ``updated_at``)
    naturally misses and re-parses — stale data can never be served.

    Args:
        conn: A canvas StorageConnection (``get_connection()`` / canvas conn).
        topo_id: Topology primary key.
        copy: When True return a private ``deepcopy`` safe to mutate. Default
            False returns the SHARED cached object — the caller MUST NOT mutate
            it (see the module contract above).

    Returns:
        The parsed graph dict, or ``None`` if the topology row does not exist.
    """
    stamp_row = conn.execute(
        "SELECT updated_at FROM topologies WHERE id = %s", (topo_id,)
    ).fetchone()
    if stamp_row is None:
        return None
    updated_at = _scalar(stamp_row, "updated_at", 0)
    key = (topo_id, updated_at)

    cached = _PARSED_GRAPH_CACHE.get(key)
    if cached is not None:
        _PARSED_GRAPH_CACHE.move_to_end(key)
        _PARSED_GRAPH_STATS["hits"] += 1
        return deepcopy(cached) if copy else cached

    # Miss — fetch the blob and parse it exactly once.
    row = conn.execute(
        "SELECT graph_json FROM topologies WHERE id = %s", (topo_id,)
    ).fetchone()
    if row is None:
        return None
    raw = _scalar(row, "graph_json", 0)
    parsed = json.loads(raw or '{"nodes":[],"edges":[]}')
    _PARSED_GRAPH_STATS["misses"] += 1
    _PARSED_GRAPH_CACHE[key] = parsed
    _PARSED_GRAPH_CACHE.move_to_end(key)
    while len(_PARSED_GRAPH_CACHE) > _PARSED_GRAPH_MAX:
        _PARSED_GRAPH_CACHE.popitem(last=False)
        _PARSED_GRAPH_STATS["evictions"] += 1
    return deepcopy(parsed) if copy else parsed


def invalidate_parsed_graph(topo_id) -> int:
    """Drop every cached parse for a topology (all ``updated_at`` variants).

    Called from graph-save routes for same-timestamp-save safety. Returns the
    number of entries removed.
    """
    stale = [k for k in _PARSED_GRAPH_CACHE if k[0] == topo_id]
    for k in stale:
        _PARSED_GRAPH_CACHE.pop(k, None)
    return len(stale)


def parsed_graph_cache_stats() -> dict:
    """Lightweight debug hook — current cache effectiveness counters."""
    return {
        **_PARSED_GRAPH_STATS,
        "size": len(_PARSED_GRAPH_CACHE),
        "max": _PARSED_GRAPH_MAX,
    }


def parsed_graph_cache_clear() -> None:
    """Reset cache + counters (test isolation; not used in runtime paths)."""
    _PARSED_GRAPH_CACHE.clear()
    _PARSED_GRAPH_STATS.update(hits=0, misses=0, evictions=0)


def _normalize_sop_step(step: dict) -> dict:
    """Normalize any SOP step schema variant to a canonical dict.

    Three schemas exist in ndc_sops.steps:
      A) {number, action, verify, rollback, time_est}           — most seed_sops SOPs
      B) {order, action, verify, rollback, estimated_time_min}  — E2E test SOPs
      C) {number, title, actions:[{label,command}],             — DoD/cloud SOPs
          verification:[{command,expected_output}], time_est}

    Returns: {number, text, verify, time_est, rollback}
    """
    # Step number
    number = step.get("number") or step.get("order") or ""

    # Action text: Schema A/B → 'action' string; Schema C → 'title' string or first action label
    text = step.get("action") or step.get("title") or ""
    if not text:
        actions = step.get("actions") or []
        parts = []
        for a in actions:
            if isinstance(a, dict):
                parts.append(a.get("label") or a.get("command") or "")
            elif isinstance(a, str):
                parts.append(a)
        text = "; ".join(p for p in parts if p)

    # Verification text: Schema A/B → 'verify' string; Schema C → verification[0].expected_output
    verify = step.get("verify") or ""
    if not verify:
        verif = step.get("verification") or []
        if isinstance(verif, list) and verif:
            first = verif[0]
            verify = (first.get("expected_output") or first.get("expected_result") or "") \
                if isinstance(first, dict) else str(first)
        elif isinstance(verif, str):
            verify = verif

    # Time estimate
    time_est = step.get("time_est") or step.get("estimated_time_min") or ""
    if isinstance(time_est, (int, float)):
        time_est = f"{int(time_est)}m"

    return {
        "number": number,
        "text": str(text),
        "verify": str(verify),
        "time_est": str(time_est),
        "rollback": str(step.get("rollback") or ""),
    }


def _audit(action, entity_type="", entity_id="", detail="", classification="CUI"):
    """Append-only audit log entry for NDC operations.

    Called as: _audit("CREATE", "topology", topo_id, "optional detail")
    Opens its own connection so callers don't need to pass one.
    """
    from tools.network.db.init_db import get_connection as _get_conn

    try:
        conn = _get_conn()
        conn.execute(
            'INSERT INTO ndc_audit (id, design_id, "user", action, detail, classification, created_at) '
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (f"na-{uuid.uuid4().hex[:10]}", entity_id, entity_type, action, detail, classification, _now()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Audit is best-effort — never block the operation
        logger.warning("_audit: best-effort INSERT into ndc_audit failed (non-blocking): %s", exc)


def _notify(event_type, data=None):
    """Placeholder for SSE notification."""
    pass


def nc_login_required(f):
    """No-op decorator for login (dashboard auth handles this)."""

    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)

    return decorated


def _crud_list(conn, table, order_by="created_at DESC", limit=100):
    rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY {order_by} LIMIT %s", (limit,)).fetchall()  # noqa: S608, E501
    return [_row_to_dict(r) for r in rows]


def _crud_create(conn, table, data):
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    conn.execute(f"INSERT INTO [{table}] ({cols}) VALUES ({placeholders})", list(data.values()))  # noqa: S608
    conn.commit()
    return data


def _crud_delete(conn, table, row_id):
    conn.execute(f"DELETE FROM [{table}] WHERE id = %s", (row_id,))  # noqa: S608
    conn.commit()
    return {"deleted": row_id}


_NDC_LIFECYCLE = ["draft", "review", "approved", "deployed", "retired"]
