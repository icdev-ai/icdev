#!/usr/bin/env python3
# CUI // SP-CTI
"""Down migration for 020 — drops ad_options_coach_events.

Audit table drop; use only for dev. Production ops should archive rows
before running this.
"""

MIGRATION_ID = "020"
MIGRATION_NAME = "ad_options_coach_events"


def down(conn) -> dict:
    conn.execute("DROP TABLE IF EXISTS ad_options_coach_events")
    conn.commit()
    return {"status": "reverted", "actions": ["drop_ad_options_coach_events"]}
