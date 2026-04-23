#!/usr/bin/env python3
# CUI // SP-CTI
"""Revert migration 027: drop ad_coach_alerts table."""

MIGRATION_ID = "027"


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS ad_coach_alerts")
    conn.execute("DROP INDEX IF EXISTS idx_ad_coach_alerts_user_type_fired")
    conn.commit()
    return {"status": "reverted", "actions": ["dropped_ad_coach_alerts"]}
