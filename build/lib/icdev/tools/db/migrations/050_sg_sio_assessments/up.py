# CUI // SP-CTI
"""Migration 050 — Create sg_sio_assessments table.

Stores the last 24h of SIO lens assessments produced by
intelligence/oracle/sio_engine.SIOEngine.  Older rows are pruned by the
engine after each run; the migration only creates the schema.
"""

from tools.db.storage import get_connection

MIGRATION_ID = "050"
MIGRATION_NAME = "sg_sio_assessments"
DESCRIPTION = "Create sg_sio_assessments table for SIO Engine lens results"

_DDL = """
CREATE TABLE IF NOT EXISTS sg_sio_assessments (
    id                TEXT PRIMARY KEY,
    confidence        REAL    NOT NULL,
    nato_reliability  TEXT    NOT NULL,
    recommendation    TEXT,
    lens_source       TEXT    NOT NULL,
    timestamp         TEXT    NOT NULL,
    score             REAL,
    narrative         TEXT,
    evidence_json     TEXT,
    created_at        TEXT    NOT NULL
);
"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_sio_lens   ON sg_sio_assessments(lens_source)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sio_conf   ON sg_sio_assessments(confidence DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sio_ts     ON sg_sio_assessments(created_at)",
]


def up(conn=None) -> None:
    conn = get_connection()
    conn.execute(_DDL)
    for idx in _INDICES:
        try:
            conn.execute(idx)
        except Exception:
            pass
    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 050 applied.")
