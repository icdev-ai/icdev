# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Foundry DB schema — foundry_signals (append-only).

The first foundry table the pipeline writes to: normalized signals harvested
from the innovation/creative/research stores, Genesis predictions, and internal
telemetry. RLS-aware (tenant_id + classification columns) so it is safe under
the global row predicate when run against the primary icdev.db.

Append-only per the ACF design (docs/features/phase-acf-autonomous-capability-foundry.md):
rows are INSERTed and never UPDATEd/DELETEd. Cross-source dedup is enforced by a
UNIQUE constraint on ``dedup_hash`` — the same signal from two engines collapses
to one row via INSERT-OR-IGNORE rather than an update.

Dual schema: PostgreSQL-first with a SQLite fallback (per the new-canvas guardrail).
CHECK constraints are derived from constants, never hardcoded.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.foundry.constants import SOURCE_ENGINES, SIGNAL_STATUSES  # noqa: E402


def _check_list(values):
    """Render a SQL CHECK IN(...) clause from a Python tuple of constants."""
    return ", ".join(f"'{v}'" for v in values)


_SRC = _check_list(SOURCE_ENGINES)
_STATUS = _check_list(SIGNAL_STATUSES)

SCHEMA_SQLITE = f"""
CREATE TABLE IF NOT EXISTS foundry_signals (
    id              TEXT PRIMARY KEY,
    source_engine   TEXT NOT NULL CHECK(source_engine IN ({_SRC})),
    source_type     TEXT,
    source_ref      TEXT,
    theme           TEXT NOT NULL,
    keywords        TEXT NOT NULL DEFAULT '[]',
    summary         TEXT,
    severity        TEXT,
    weight          REAL NOT NULL DEFAULT 0.5,
    dedup_hash      TEXT NOT NULL,
    contributing_engines TEXT NOT NULL DEFAULT '[]',
    metadata        TEXT DEFAULT '{{}}',
    status          TEXT NOT NULL DEFAULT 'new' CHECK(status IN ({_STATUS})),
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    discovered_at   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dedup_hash)
);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_engine ON foundry_signals(source_engine);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_status ON foundry_signals(status);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_hash ON foundry_signals(dedup_hash);
"""

SCHEMA_PG = f"""
CREATE TABLE IF NOT EXISTS foundry_signals (
    id              TEXT PRIMARY KEY,
    source_engine   TEXT NOT NULL CHECK(source_engine IN ({_SRC})),
    source_type     TEXT,
    source_ref      TEXT,
    theme           TEXT NOT NULL,
    keywords        TEXT NOT NULL DEFAULT '[]',
    summary         TEXT,
    severity        TEXT,
    weight          DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    dedup_hash      TEXT NOT NULL,
    contributing_engines TEXT NOT NULL DEFAULT '[]',
    metadata        TEXT DEFAULT '{{}}',
    status          TEXT NOT NULL DEFAULT 'new' CHECK(status IN ({_STATUS})),
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    discovered_at   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(dedup_hash)
);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_engine ON foundry_signals(source_engine);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_status ON foundry_signals(status);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_hash ON foundry_signals(dedup_hash);
"""


def init_foundry_db(db_path=None):
    """Create the foundry_signals table. Idempotent.

    db_path: optional path to a SQLite file (used by tests). When omitted the
    configured backend (PostgreSQL or SQLite) is used via get_connection().
    """
    conn = get_connection(db_path=db_path) if db_path else get_connection()
    backend = getattr(conn, "_backend", "sqlite")
    schema = SCHEMA_PG if backend == "postgresql" else SCHEMA_SQLITE
    try:
        for stmt in (s.strip() for s in schema.split(";")):
            if stmt:
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return True


if __name__ == "__main__":
    init_foundry_db()
    print("foundry_signals schema initialized")
