# CUI // SP-CTI
"""Migration 158 — Create conflict_predictions table.

Stores ML-derived conflict escalation risk scores produced by
tools/conflict_mesh/escalation_predictor.py.

This table is APPEND-ONLY (NIST AU-2, AU-12 audit trail compliance).
Never UPDATE or DELETE rows — create a new row with updated values instead.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()
    try:
        if is_pg():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conflict_predictions (
                    id               TEXT    PRIMARY KEY,
                    event_id         TEXT    NOT NULL,
                    source           TEXT,
                    prediction_date  TEXT,
                    escalation_risk  REAL    NOT NULL CHECK(escalation_risk >= 0.0 AND escalation_risk <= 1.0),
                    signals          JSONB,
                    model_version    TEXT    NOT NULL DEFAULT 'v1.0',
                    confidence       REAL,
                    created_at       TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_event_id "
                "ON conflict_predictions(event_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_escalation_risk "
                "ON conflict_predictions(escalation_risk DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_prediction_date "
                "ON conflict_predictions(prediction_date)"
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conflict_predictions (
                    id               TEXT NOT NULL PRIMARY KEY,
                    event_id         TEXT NOT NULL,
                    source           TEXT,
                    prediction_date  TEXT,
                    escalation_risk  REAL NOT NULL
                                          CHECK(escalation_risk >= 0.0 AND escalation_risk <= 1.0),
                    signals          TEXT,
                    model_version    TEXT NOT NULL DEFAULT 'v1.0',
                    confidence       REAL,
                    created_at       TEXT NOT NULL
                )
                """
            )
            for stmt in [
                "CREATE INDEX IF NOT EXISTS idx_cp_event_id ON conflict_predictions(event_id)",
                "CREATE INDEX IF NOT EXISTS idx_cp_escalation_risk ON conflict_predictions(escalation_risk)",
                "CREATE INDEX IF NOT EXISTS idx_cp_prediction_date ON conflict_predictions(prediction_date)",
            ]:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
        conn.commit()
        print("Migration 158 (conflict_predictions) applied.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
