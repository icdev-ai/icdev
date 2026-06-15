-- CUI // SP-CTI
-- Migration 190: Create dm_ports table.
--
-- dm_ports is queried by tools/data_canvas/twin.py (simulate_delta) via
-- tools.db.storage.get_connection() (main icdev.db).  The table schema was
-- previously only defined in the canvas-local tools/data_canvas/db/init_db.py
-- which writes to a separate data_canvas.db, making dm_ports an orphan in
-- the main database from the perspective of the awareness engine.
--
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Carries tenant_id + classification so all queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS dm_ports (
    id              TEXT    PRIMARY KEY,
    product_id      TEXT,
    name            TEXT    NOT NULL,
    port_type       TEXT    NOT NULL DEFAULT 'input'
                    CHECK (port_type IN ('input', 'output')),
    transport_type  TEXT    DEFAULT 'api',
    schema_json     TEXT    DEFAULT '{}',
    endpoint        TEXT    DEFAULT '',
    source_system   TEXT    DEFAULT '',
    sla_json        TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_ports_product   ON dm_ports (product_id);
CREATE INDEX IF NOT EXISTS idx_dm_ports_port_type ON dm_ports (port_type);
CREATE INDEX IF NOT EXISTS idx_dm_ports_tenant    ON dm_ports (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS dm_ports (
    id              TEXT    PRIMARY KEY,
    product_id      TEXT,
    name            TEXT    NOT NULL,
    port_type       TEXT    NOT NULL DEFAULT 'input'
                    CHECK (port_type IN ('input', 'output')),
    transport_type  TEXT    DEFAULT 'api',
    schema_json     TEXT    DEFAULT '{}',
    endpoint        TEXT    DEFAULT '',
    source_system   TEXT    DEFAULT '',
    sla_json        TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_ports_product   ON dm_ports (product_id);
CREATE INDEX IF NOT EXISTS idx_dm_ports_port_type ON dm_ports (port_type);
CREATE INDEX IF NOT EXISTS idx_dm_ports_tenant    ON dm_ports (tenant_id);

-- @all
