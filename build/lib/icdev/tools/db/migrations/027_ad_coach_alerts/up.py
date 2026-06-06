#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 027: ad_coach_alerts (append-only NIST AU).

Portfolio-level Greek exposure alert log written by
tools/trading/options/coach.py:check_greek_exposure().
Cooldown is enforced in-app; rows are never deleted.
"""

MIGRATION_ID = "027"
MIGRATION_NAME = "ad_coach_alerts"
DESCRIPTION = "Add ad_coach_alerts append-only table (Phase 7.9 Greek exposure alerts)"

# Keep in sync with ALERT_TYPES in tools/trading/options/coach.py.
ALERT_TYPES = (
    "delta_high",
    "theta_burn",
    "vega_exposure",
    "gamma_elevated",
)

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ad_coach_alerts_user_type_fired "
    "ON ad_coach_alerts(user_id, alert_type, fired_at DESC)",
]


def _ddl_sqlite() -> str:
    at = ",".join(f"'{t}'" for t in ALERT_TYPES)
    return f"""
    CREATE TABLE IF NOT EXISTS ad_coach_alerts (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL,
        alert_type   TEXT NOT NULL CHECK(alert_type IN ({at})),
        message      TEXT NOT NULL,
        fired_at     TEXT NOT NULL,
        metrics_json TEXT
    )
    """


def _ddl_pg() -> str:
    at = ",".join(f"'{t}'" for t in ALERT_TYPES)
    return f"""
    CREATE TABLE IF NOT EXISTS ad_coach_alerts (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL,
        alert_type   TEXT NOT NULL,
        message      TEXT NOT NULL,
        fired_at     TEXT NOT NULL,
        metrics_json TEXT,
        CONSTRAINT ad_coach_alerts_alert_type_check
            CHECK (alert_type IN ({at}))
    )
    """


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def up(conn) -> dict:
    conn.execute(_ddl_pg() if _is_pg(conn) else _ddl_sqlite())
    for idx in INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["ad_coach_alerts_table", "indexes_created"]}
