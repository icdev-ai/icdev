# CUI // SP-CTI
"""Make a SAG run joinable, and make replay an explicit opt-in (hgx-obs-01).

Migration 341 gave the runtime one telemetry table. It has two gaps that stop a
SAG (self-hosted agent) run from being observable end to end:

  1. **No join key to the traces.** ``runtime_invocations`` correlates by
     ``session_id``, which comes from an ambient environment variable. The agent
     loop already mints a per-run correlation id (``AgentLoopResult.trace_id``)
     and the router already emits ``gen_ai.invoke`` spans beneath it, but the
     tool calls in between had nowhere to write that id. ``correlation_id`` is
     that column: one run's tool invocations and its spans now share one key.

  2. **Nothing replayable, with no way to opt in.** ``arg_keys`` stores KEY NAMES
     only and that is deliberate (see the module docstring of
     ``tools/observability/invocation_recorder.py``) — 512 tools have 512
     argument shapes and trusting a redactor across all of them is a fail-open
     bet. So the default stays exactly as it was. These two columns exist for the
     operator who explicitly turns replay on with ``ICDEV_OBS_REPLAY=1``, and are
     written ONLY on that path, after ``tools/llm/output_redactor.redact``.
     With the flag off they are never written and stay NULL.

Adding columns rather than a second table: every reader of this table
(``invocation_recorder.summary``, ``icdev runtime top``, the monitoring panel)
already selects by surface and name, and a side table would need a join on every
one of them to answer "which run was this".

ALTER TABLE ADD COLUMN is expressed in Python rather than SQL because
``IF NOT EXISTS`` on ADD COLUMN is PostgreSQL-only; SQLite raises a syntax error
on it. ``column_exists`` gives the same idempotence on both backends.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, table_exists

_TABLE = "runtime_invocations"
_TAG = "[20260808161052_sag_runtime_observability_columns]"

#: (column, DDL type). All nullable — every existing row predates them, and the
#: two replay columns stay NULL forever unless the operator opts in.
_COLUMNS = (
    ("correlation_id", "TEXT"),
    ("arg_values", "TEXT"),
    ("result_preview", "TEXT"),
)

_INDEX = (
    "idx_runtime_inv_correlation",
    "CREATE INDEX IF NOT EXISTS idx_runtime_inv_correlation "
    "ON runtime_invocations (correlation_id)",
)


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        if not table_exists(conn, _TABLE):
            # 341 has not run yet. It creates the table without these columns,
            # and the runner will apply this migration after it in id order, so
            # there is nothing to do here rather than something to fail on.
            print(f"{_TAG} {_TABLE} absent — nothing to alter")
            return

        for column, ddl_type in _COLUMNS:
            if column_exists(conn, _TABLE, column):
                print(f"{_TAG} {column} already present")
                continue
            conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {column} {ddl_type}")  # nosec B608 — identifiers are module constants
            print(f"{_TAG} added {column}")

        name, sql = _INDEX
        try:
            conn.execute(sql)
        except Exception as exc:  # noqa: BLE001 — an index is not worth failing on
            print(f"{_TAG} index {name} skipped: {exc}")
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    up()
