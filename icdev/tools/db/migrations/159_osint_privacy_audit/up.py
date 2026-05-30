# CUI // SP-CTI
"""Migration 159 — Create osint_privacy_audit table.

Audit trail for PII detections and redactions performed by the OSINT Privacy
Sanitizer (tools/strategos/osint_privacy_sanitizer.py). Append-only — rows
are never updated or deleted.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS osint_privacy_audit (
                id              TEXT PRIMARY KEY,
                timestamp       TIMESTAMPTZ NOT NULL,
                source          TEXT NOT NULL DEFAULT '',
                url_hash        TEXT NOT NULL DEFAULT '',
                pii_found       BOOLEAN NOT NULL DEFAULT FALSE,
                entity_types    TEXT NOT NULL DEFAULT '[]',
                detection_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS osint_privacy_audit (
                id              TEXT PRIMARY KEY,
                timestamp       TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT '',
                url_hash        TEXT NOT NULL DEFAULT '',
                pii_found       INTEGER NOT NULL DEFAULT 0,
                entity_types    TEXT NOT NULL DEFAULT '[]',
                detection_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_osint_pa_ts ON osint_privacy_audit(timestamp)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_osint_pa_url ON osint_privacy_audit(url_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_osint_pa_pii ON osint_privacy_audit(pii_found)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 159 applied.")
