# CUI // SP-CTI
"""Migration 043 rollback — remove content_fingerprint from memory_entries.

Drops:
  UNIQUE INDEX idx_me_fingerprint
  memory_entries.content_fingerprint column

SQLite DROP COLUMN requires SQLite ≥ 3.35.0 (2021-03-12).
"""
import os

from tools.db.storage import get_connection

_BACKEND = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()


def down():
    conn = get_connection()
    try:
        conn.execute("DROP INDEX IF EXISTS idx_me_fingerprint")

        if _BACKEND == "postgresql":
            conn.execute(
                "ALTER TABLE memory_entries DROP COLUMN IF EXISTS content_fingerprint"
            )
        else:
            # SQLite: DROP COLUMN supported since 3.35.0
            conn.execute(
                "ALTER TABLE memory_entries DROP COLUMN content_fingerprint"
            )

        conn.commit()
        print("Migration 043 down: idx_me_fingerprint dropped, content_fingerprint column removed.")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
