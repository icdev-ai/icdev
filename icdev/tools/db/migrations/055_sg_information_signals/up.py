# CUI // SP-CTI
"""Migration 055 — Create sg_information_signals and sg_information_scores tables.

sg_information_signals: input signal store for the information scorer.
  Stores news items, cyber-recon telemetry, and disinformation baseline
  stats keyed by scenario_id.

sg_information_scores: append-only audit trail for computed scores
  (NIST AU requirement). Never updated — only inserted.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_information_signals (
                id          SERIAL PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_ts   TEXT NOT NULL,
                payload_json TEXT,
                source      TEXT,
                created_at  TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_information_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_ts   TEXT NOT NULL,
                payload_json TEXT,
                source      TEXT,
                created_at  TEXT NOT NULL
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_info_signals_scenario "
        "ON sg_information_signals(scenario_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_info_signals_type "
        "ON sg_information_signals(signal_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_info_signals_ts "
        "ON sg_information_signals(signal_ts)"
    )

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_information_scores (
                id                   SERIAL PRIMARY KEY,
                scenario_id          TEXT NOT NULL,
                information_score    REAL NOT NULL,
                rhetoric_score       REAL NOT NULL,
                dehumanization_index REAL NOT NULL,
                cyber_recon_score    REAL NOT NULL,
                disinformation_surge REAL NOT NULL,
                detail_json          TEXT,
                created_at           TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_information_scores (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                scenario_id          TEXT NOT NULL,
                information_score    REAL NOT NULL,
                rhetoric_score       REAL NOT NULL,
                dehumanization_index REAL NOT NULL,
                cyber_recon_score    REAL NOT NULL,
                disinformation_surge REAL NOT NULL,
                detail_json          TEXT,
                created_at           TEXT NOT NULL
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_info_scores_scenario "
        "ON sg_information_scores(scenario_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_info_scores_created "
        "ON sg_information_scores(created_at DESC)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 055 applied.")
