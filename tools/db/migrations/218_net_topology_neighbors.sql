-- CUI // SP-CTI
-- Migration 218: Network Migration Topology Neighbors
-- Adds mc_net_topology_neighbors table for auto-discovered/config-inferred neighbor
-- devices linked to a network migration session.

CREATE TABLE IF NOT EXISTS mc_net_topology_neighbors (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    neighbor_name   TEXT DEFAULT '',
    neighbor_ip     TEXT DEFAULT '',
    relationship    TEXT DEFAULT '',
    source_interface TEXT DEFAULT '',
    is_discovered   INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mc_net_topo_neighbor_session ON mc_net_topology_neighbors(session_id);

-- Ensure topology JSON columns exist on mc_net_sessions for deployments that
-- initialized the schema before the COA/topology feature landed.
ALTER TABLE mc_net_sessions ADD COLUMN IF NOT EXISTS topology_json TEXT DEFAULT '';
ALTER TABLE mc_net_sessions ADD COLUMN IF NOT EXISTS topology_neighbors_json TEXT DEFAULT '';
