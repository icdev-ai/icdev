# CUI // SP-CTI
"""Migration — give GEPA a place to record what it DECIDED (rem-cap-01).

`agent_improvement_artifacts` had exactly one terminal state GEPA could write:
``status='applied'`` plus ``applied_at``. An artifact GEPA evaluated and
declined stayed ``'pending'`` forever, so the queue only ever grew and "GEPA ran
and correctly declined everything" was indistinguishable from "GEPA never ran" —
which is precisely why ``capability_consumption`` reported ``skill_optimizer``
at literal zero for its entire lifetime while the reflex behind it had 7
successful runs on the board.

MEASURED on the live board 2026-08-16 (162 rows):

    status                 n    composite  baseline  skill_used
    pending              132        1.0       1.0    ''
    rejected_no_evidence  30       0.75       1.0    populated

Nothing rescores an artifact after insert — the only two UPDATEs against this
table touch ``status``/``applied_at``/``applied_count`` — so the 132 pending
rows' delta of 0.0 is IMMUTABLE and can never satisfy GEPA's ``>= 0.05`` filter.
They were a permanently unselectable, permanently queued backlog written before
the exa-refine writer fixes landed, and they held the probe's own
"queue full, zero selectable = structurally cannot ever act" alarm on forever.

These two columns are GEPA's alone. ``status`` is deliberately NOT repurposed:
``tools/agent_runtime/skills_lifecycle.py`` and ``tools/ace/blueprint.py`` both
read ``status='pending'`` as NOVA's proposal queue, and widening that vocabulary
would change what those two surfaces show for reasons that have nothing to do
with GEPA.

Idempotent: the columns are also declared in
``tools/nova/db/init_db.py::_AGENT_IMPROVEMENT_ARTIFACTS_DDL`` so a fresh
install has them before this migration runs.
"""
from tools.db.storage import column_exists, get_connection

_TABLE = "agent_improvement_artifacts"
_COLUMNS = (
    ("gepa_decision", "TEXT"),
    ("gepa_decided_at", "TEXT"),
)


def up(conn=None) -> None:
    owned = conn is None
    conn = conn or get_connection()
    try:
        added = []
        for column, col_type in _COLUMNS:
            if not column_exists(conn, _TABLE, column):
                conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {column} {col_type}")
                added.append(column)
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aia_gepa_decided_at "
                f"ON {_TABLE}(gepa_decided_at)"
            )
        except Exception:  # noqa: BLE001 — index is an optimisation, not the contract
            pass
        conn.commit()
        print(
            f"Migration gepa_decision_columns up: added {added or 'nothing (already present)'} "
            f"to {_TABLE}"
        )
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":
    up()
