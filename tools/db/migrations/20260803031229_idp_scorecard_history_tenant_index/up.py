# CUI // SP-CTI
"""Make ``idp_scorecard_history.tenant_id`` present and cheap to filter on.

Two jobs, and the second is the one with teeth.

## 1. Guarantee the column exists

``20260802222900_idp_score_history`` created the table with ``tenant_id``
already in its ``CREATE TABLE``. That covers every database built from that
migration forward — but ``CREATE TABLE IF NOT EXISTS`` never alters a table
that is already there, so a database that acquired ``idp_scorecard_history``
by any other route keeps whatever columns it started with. The recorder's
INSERT names ``tenant_id``, and an INSERT naming a column the live schema
lacks raises at runtime, gets swallowed by the recorder's surrounding
``except``, and reports success while persisting nothing (CLAUDE.md: "every
column in an INSERT must exist in the LIVE schema, not just in the source
DDL"). So the column is added here explicitly rather than assumed, and adding
it is a migration rather than an edit to the older ``CREATE TABLE``.

The add is conditional in Python because the two backends disagree:
PostgreSQL has ``ADD COLUMN IF NOT EXISTS`` and SQLite does not. Checking
first works on both and keeps the statement out of a failure path — which
matters on PostgreSQL, where a failed statement aborts the transaction and
makes every later query on the same connection report "relation does not
exist" whether or not it does.

## 2. Index the predicate every read now carries

Before idp-mt-01, a trend read was ``WHERE scorecard_key = ? AND
component_key = ?``. Now every read also carries a tenant predicate — either
``tenant_id = ?`` for a tenant or ``tenant_id IS NULL`` for the platform's
own series — because scoping a column nobody filters on would be decoration.
That predicate is the most selective one in the query on a multi-tenant
instance, and it was leading no index.

Both new indexes lead with ``tenant_id`` so the tenant is resolved first and
the remaining columns keep the ordering the existing indexes provide within
that tenant. The pre-existing indexes are deliberately left in place: they
still serve ``--all-tenants`` reads, which carry no tenant predicate at all.

``tenant_id IS NULL`` — the platform's own reading, and by far the most
common one — is a normal indexable condition on a PostgreSQL btree, so the
platform series benefits from the same index rather than needing a partial
one.
"""
from tools.db.storage import get_connection, table_exists

_TABLE = "idp_scorecard_history"
_TAG = "[20260803031229_idp_scorecard_history_tenant_index]"

_INDEXES = (
    # The trend read: one tenant's series for one component, oldest to newest.
    ("idx_isch_tenant_component",
     "CREATE INDEX IF NOT EXISTS idx_isch_tenant_component "
     "ON idp_scorecard_history (tenant_id, scorecard_key, component_key, evaluated_at)"),
    # The due check: has THIS tenant already recorded this window? Unscoped,
    # the first tenant to record a window suppresses every other tenant's
    # write for it, so this predicate runs on every recording pass.
    ("idx_isch_tenant_window",
     "CREATE INDEX IF NOT EXISTS idx_isch_tenant_window "
     "ON idp_scorecard_history (tenant_id, scorecard_key, window_start)"),
)


def _has_column(conn, table: str, column: str) -> bool:
    """Is *column* present on *table* in the LIVE schema, on either backend?"""
    try:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s",
            (table,),
        ).fetchall()
        if rows:
            return any(str(_first(r)).lower() == column for r in rows)
    except Exception:  # noqa: BLE001 — no information_schema means SQLite
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001, S110
            pass
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(str(_nth(r, 1)).lower() == column for r in rows)
    except Exception:  # noqa: BLE001
        # Neither introspection path worked. Report the column as present so
        # the ALTER is skipped rather than attempted blind: a failed ALTER on
        # PostgreSQL would abort the transaction and take the indexes with it.
        return True


def _first(row):
    return _nth(row, 0)


def _nth(row, index: int):
    if isinstance(row, (tuple, list)):
        return row[index]
    try:
        return list(dict(row).values())[index]
    except Exception:  # noqa: BLE001
        return row


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if not table_exists(conn, _TABLE):
            # The creating migration has not run yet. It ships tenant_id in its
            # own CREATE TABLE and this migration sorts after it, so there is
            # nothing to back-fill and nothing to index.
            print(f"{_TAG} {_TABLE} absent — nothing to do")
            return

        if _has_column(conn, _TABLE, "tenant_id"):
            print(f"{_TAG} {_TABLE}.tenant_id already present")
        else:
            conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN tenant_id TEXT")
            print(f"{_TAG} added {_TABLE}.tenant_id")

        for name, sql in _INDEXES:
            try:
                conn.execute(sql)
                print(f"{_TAG} index {name} ready")
            except Exception as exc:  # noqa: BLE001 — an index is not worth failing on
                print(f"{_TAG} index {name} skipped: {exc}")
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    up()
