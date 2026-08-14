# CUI // SP-CTI
"""IQE Cortex Canvas collection adapters.

Registers three collections on the module-level Executor:
  cortex.chat_sessions  — canvas chat/agent sessions (cortex_chat_sessions)
  cortex.audit          — governance/facade audit trail (cortex_audit)
  cortex.search_history — unified-search / ask invocations (cortex_search_history)

Two properties these adapters must hold, both of which they did not (ctx-trust-03):

**They do not own the caller's connection.** ``Executor._fetch_union`` /
``_fetch_join`` fetch several collections IN PARALLEL over ONE connection
supplied by the caller (``analyst._ask_iqe`` opens it). Closing it in ``finally``
therefore closed it out from under a sibling fetch still running on another
thread. That sibling raised, the bare ``except Exception: return []`` swallowed
it without a log, and a query like
``foreach s in union("cortex.audit","cortex.search_history")`` returned HALF ITS
ROWS while looking like a complete answer.

**A failure is not an empty result.** ``return []`` on any exception made a dead
table, a permissions error and a genuinely empty collection indistinguishable —
on the very collections an operator uses to audit Cortex itself. Errors now log
and propagate; the executor's union path already downgrades a failed sub-fetch
to a warning plus an empty slice, so a truly optional collection still degrades,
but it does so visibly and at one level up where the decision belongs.

KNOWN LIMITATION (ctx-trust-04, still open): the row cap below is applied by the
SQL, but ``Executor.run`` applies the query's ``where`` clauses in PYTHON
afterwards. A question whose window matches more rows than the cap is therefore
answered from the newest ``_ROW_CAP`` rows only, and ``analyst._label_rows_result``
still stamps it ``confidence_score: 1.0``. The real fix is predicate pushdown,
which needs an ``Executor`` API change affecting every canvas adapter; it is not
attempted here. What IS done: the cap is a named, raised, per-call-overridable
constant, and hitting it logs a WARNING naming the collection — so a truncated
scan is at least observable instead of silent.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Rows fetched per collection before the executor filters in Python.
#: Raised from 500 (ctx-trust-04) — see the KNOWN LIMITATION above. Overridable
#: per query via the IQE call form, e.g. ``cortex.audit(20000)``.
_ROW_CAP = 10000


def _conn(conn: Any) -> tuple[Any, bool]:
    """Return ``(connection, owned)``.

    ``owned`` is True only when this adapter opened the connection itself, and
    it is the sole thing that may close it. A caller-supplied connection is
    shared with sibling fetches running concurrently on other threads.
    """
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415

        return get_connection(), True
    return conn, False


def _fetch(collection: str, sql: str, conn: Any, limit: int) -> list[dict]:
    """Run *sql* for *collection*, honouring connection ownership."""
    c, owned = _conn(conn)
    try:
        cur = c.execute(sql, (int(limit),))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        # Logged and re-raised, never swallowed into []: an empty list here is
        # indistinguishable from "no rows", and these are the collections used
        # to audit Cortex itself.
        logger.warning("iqe adapter %s failed", collection, exc_info=True)
        raise
    finally:
        if owned:
            c.close()

    if len(rows) >= int(limit):
        logger.warning(
            "iqe adapter %s hit the %d-row cap — the executor filters in Python "
            "AFTER this cap, so any aggregate over a larger window is computed "
            "from the newest %d rows only (ctx-trust-04)",
            collection, int(limit), int(limit),
        )
    return rows


def sessions_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    return _fetch(
        "cortex.chat_sessions",
        "SELECT session_id, user_id, mode, domain, title, status, "
        "classification, tenant_id, created_at, updated_at "
        "FROM cortex_chat_sessions ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )


def audit_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    return _fetch(
        "cortex.audit",
        "SELECT id, session_id, function, outcome, blocked, provenance_id, "
        "classification, tenant_id, created_at "
        "FROM cortex_audit ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )


def search_history_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    return _fetch(
        "cortex.search_history",
        "SELECT query_id, session_id, user_id, mode, domain, query_text, "
        "strategy, result_count, grounded, classification, tenant_id, created_at "
        "FROM cortex_search_history ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )


register_collection("cortex.chat_sessions", sessions_adapter)
register_collection("cortex.audit", audit_adapter)
register_collection("cortex.search_history", search_history_adapter)
