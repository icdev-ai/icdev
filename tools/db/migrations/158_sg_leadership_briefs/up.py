# CUI // SP-CTI
"""Migration 158 — Create sg_leadership_briefs table.

Stores AI-generated predictive intelligence leadership briefings produced by
the Predictive Intelligence Engine from the OSINT data mesh.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_leadership_briefs (
                id                   TEXT PRIMARY KEY,
                theater              TEXT NOT NULL DEFAULT 'global',
                sio_composite_score  REAL NOT NULL DEFAULT 0.0,
                iw_triggered         INTEGER NOT NULL DEFAULT 0,
                threat_tier          TEXT NOT NULL DEFAULT 'LOW',
                signal_count_24h     INTEGER NOT NULL DEFAULT 0,
                conflict_event_count INTEGER NOT NULL DEFAULT 0,
                signal_velocity      REAL NOT NULL DEFAULT 0.0,
                p_war_posterior      REAL NOT NULL DEFAULT 0.05,
                goldstein_avg        REAL NOT NULL DEFAULT 0.0,
                dti_score            REAL NOT NULL DEFAULT 0.0,
                forecast_24h_json    TEXT,
                forecast_72h_json    TEXT,
                forecast_7d_json     TEXT,
                narrative_md         TEXT,
                generated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                classification       TEXT NOT NULL DEFAULT 'CUI'
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sg_leadership_briefs (
                id                   TEXT PRIMARY KEY,
                theater              TEXT NOT NULL DEFAULT 'global',
                sio_composite_score  REAL NOT NULL DEFAULT 0.0,
                iw_triggered         INTEGER NOT NULL DEFAULT 0,
                threat_tier          TEXT NOT NULL DEFAULT 'LOW',
                signal_count_24h     INTEGER NOT NULL DEFAULT 0,
                conflict_event_count INTEGER NOT NULL DEFAULT 0,
                signal_velocity      REAL NOT NULL DEFAULT 0.0,
                p_war_posterior      REAL NOT NULL DEFAULT 0.05,
                goldstein_avg        REAL NOT NULL DEFAULT 0.0,
                dti_score            REAL NOT NULL DEFAULT 0.0,
                forecast_24h_json    TEXT,
                forecast_72h_json    TEXT,
                forecast_7d_json     TEXT,
                narrative_md         TEXT,
                generated_at         TEXT NOT NULL DEFAULT (datetime('now')),
                classification       TEXT NOT NULL DEFAULT 'CUI'
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_lb_theater ON sg_leadership_briefs(theater)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_lb_generated ON sg_leadership_briefs(generated_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_lb_tier ON sg_leadership_briefs(threat_tier)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 158 applied.")
