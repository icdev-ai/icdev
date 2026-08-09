# CUI // SP-CTI
"""Migration 20260809041642 — give harness_eval a graph-node grain (hgx-eval-01).

``harness_eval`` has correlated on ``task_id`` alone since it shipped, and there
has never been an ``ALTER TABLE harness_eval`` anywhere in the tree. That grain
is fine for a reflex, which makes one decision per task, and wrong for a graph
run: Studio's ``workflow_runner`` executes many nodes under a single run, each
node making its own decision with its own confidence. Rolled up by ``task_id``
those decisions are indistinguishable, so a node that is consistently right and
a node that is consistently wrong average into one number and the meta-harness
cannot tell them apart — which is exactly the discrimination it exists to make.

Four NULLABLE columns, deliberately:

  run_id          the Studio ``studio_workflow_runs.run_id`` (or any run
                  correlator) the decision was made under.
  node_id         the step id within that run.
  node_type       the node's kind — ``tool`` | ``mcp`` | ``agent`` |
                  ``human`` | ``approval`` in workflow_runner's vocabulary.
                  This is the key ``_AnomalyDetector`` partitions on, so a
                  high-performing node type is not penalised by another's drift.
  edge_condition  the branch predicate that selected this node, when the run
                  reached it conditionally. Without it a node's precision is
                  averaged across branches that were never comparable.

Nullable is the whole compatibility story. Every existing row keeps a NULL in
all four, every existing query (``WHERE reflex = ... AND created_at >= ...``)
keeps matching exactly the rows it matched before, and ``record_decision``'s
INSERT — which names its columns explicitly — is untouched.

Python rather than a flat .sql file because idempotency differs by engine:
PostgreSQL has ``ADD COLUMN IF NOT EXISTS``, SQLite does not, and
``migration_runner`` only tolerates a failing script whose error says "already
exists" — SQLite says "duplicate column name". A flat .sql would abort on
re-run under SQLite and skip the index creation below with it.

Companion edits that a migration alone cannot make, and without which this is
half a change:
  * tests/conftest.py::MINIMAL_ICDEV_SCHEMA
  * tools/db/schema/pg_consolidated.sql (+ the icdev/ mirror of both)
A fresh PostgreSQL bootstrap is built from the consolidated schema, not by
replaying migrations, so omitting it breaks only fresh installs.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, table_exists

_TABLE = "harness_eval"
_TAG = "[20260809041642_harness_eval_graph_node_columns]"

_NEW_COLUMNS = (
    ("run_id", "TEXT"),
    ("node_id", "TEXT"),
    ("node_type", "TEXT"),
    ("edge_condition", "TEXT"),
)

_INDEXES = (
    # "all decisions for this node of this run" — the per-node metric read.
    ("idx_harness_eval_run_node",
     "CREATE INDEX IF NOT EXISTS idx_harness_eval_run_node "
     "ON harness_eval (run_id, node_id)"),
    # _AnomalyDetector partitions by node_type over a created_at window; the
    # composite ordering matches idx_harness_eval_reflex's shape.
    ("idx_harness_eval_node_type",
     "CREATE INDEX IF NOT EXISTS idx_harness_eval_node_type "
     "ON harness_eval (node_type, created_at)"),
)


def up(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    added: list[str] = []
    try:
        if not table_exists(conn, _TABLE):
            # A database that predates migration 302 and was not built from the
            # consolidated bootstrap. Creating the table here would fork a
            # second definition away from the migration that owns it.
            print(f"{_TAG} {_TABLE} absent; nothing to widen")
            return {"status": "skipped", "added": [], "note": f"{_TABLE} absent"}

        for column, coltype in _NEW_COLUMNS:
            if column_exists(conn, _TABLE, column):
                print(f"{_TAG} {_TABLE}.{column} already present")
                continue
            conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {column} {coltype}")
            added.append(column)
            print(f"{_TAG} added {_TABLE}.{column}")

        for name, sql in _INDEXES:
            try:
                conn.execute(sql)
            except Exception as exc:  # noqa: BLE001 — an index is not worth failing on
                print(f"{_TAG} index {name} skipped: {exc}")

        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "applied", "added": added}


if __name__ == "__main__":
    print(up())
