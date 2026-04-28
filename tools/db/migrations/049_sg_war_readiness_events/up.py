# CUI // SP-CTI
"""Migration 049 — Add baseline columns to sg_war_readiness_events.

Extends the existing sg_war_readiness_events table with columns needed
for labeled historical baseline cases (pre_war / exercise / coercive).
"""
from tools.db.storage import get_connection

MIGRATION_ID = "049"
MIGRATION_NAME = "sg_war_readiness_events"
DESCRIPTION = "Extend sg_war_readiness_events with baseline label/case columns"

_NEW_COLUMNS = [
    ("case_id",       "TEXT"),
    ("label",         "TEXT"),
    ("conflict_name", "TEXT"),
    ("year",          "INTEGER"),
    ("start_date",    "TEXT"),
    ("aggressor",     "TEXT"),
    ("defender",      "TEXT"),
    ("region",        "TEXT"),
    ("duration_days", "INTEGER"),
    ("metadata_json", "TEXT"),
    ("source",        "TEXT"),
]


def up(conn=None) -> None:
    conn = get_connection()
    for col, col_type in _NEW_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE sg_war_readiness_events ADD COLUMN {col} {col_type}"
            )
        except Exception:
            pass  # column already exists
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_wre_label ON sg_war_readiness_events(label)"
        )
    except Exception:
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_wre_case_id ON sg_war_readiness_events(case_id)"
        )
    except Exception:
        pass
    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 049 applied.")
