-- Migration 125: NDC topologies table for GNS3 / network canvas lab data
-- Stores discovered or manually designed network topologies, device IP maps,
-- and ZTP state. Feeds topology_scanner.py and ztp_config_generator.py.

CREATE TABLE IF NOT EXISTS ndc_topologies (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',   -- 'gns3' | 'manual' | 'imported'
    project_id  TEXT,                              -- GNS3 project_id when source='gns3'
    design_json TEXT NOT NULL DEFAULT '{}',        -- {nodes:[...], edges:[...]}
    device_map  TEXT NOT NULL DEFAULT '{}',        -- {node_id: {ip, role, creds_ref}}
    ztp_status  TEXT NOT NULL DEFAULT 'pending',   -- pending | generated | pushed | validated
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ndc_topologies_source     ON ndc_topologies(source);
CREATE INDEX IF NOT EXISTS idx_ndc_topologies_project    ON ndc_topologies(project_id);
CREATE INDEX IF NOT EXISTS idx_ndc_topologies_ztp_status ON ndc_topologies(ztp_status);
