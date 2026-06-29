-- CUI // SP-CTI
-- Migration 222: Agent coordination key-value bus for sibling agent loops.
-- Agents in the same coordination namespace can share typed artifacts via
-- post_result / read_result tools without spawning subagents.

CREATE TABLE IF NOT EXISTS agent_coordination (
    id          TEXT PRIMARY KEY,
    namespace   TEXT NOT NULL DEFAULT '',
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL DEFAULT 'null',
    posted_by   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agcoord_ns_key
    ON agent_coordination (namespace, key);

CREATE INDEX IF NOT EXISTS idx_agcoord_ns
    ON agent_coordination (namespace);
