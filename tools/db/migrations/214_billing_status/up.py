# CUI // SP-CTI
"""Migration 214 — add billing_status column to tenants table.

billing_status tracks the Stripe subscription state:
  active, past_due, cancelled, unknown (default)

This column is updated by the Stripe webhook handler (ecr-bill-03) and
is intentionally mutable (unlike append-only usage_events).
"""
from __future__ import annotations

from tools.db.storage import get_connection

_VALID = ("active", "past_due", "cancelled", "unknown")
_CHECK = ", ".join(f"'{s}'" for s in _VALID)

DDL_SQLITE = f"""
ALTER TABLE tenants ADD COLUMN billing_status TEXT
    NOT NULL DEFAULT 'unknown'
    CHECK (billing_status IN ({_CHECK}));
"""

DDL_PG = f"""
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS billing_status TEXT
        NOT NULL DEFAULT 'unknown'
        CHECK (billing_status IN ({_CHECK}));
"""


def up() -> None:
    import os

    with get_connection() as conn:
        backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
        ddl = DDL_PG if backend == "postgresql" else DDL_SQLITE
        try:
            conn.execute(ddl.strip())
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                pass  # idempotent
            else:
                raise
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 214 (billing_status column) applied.")
