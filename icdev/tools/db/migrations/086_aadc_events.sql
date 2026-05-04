-- Migration 086: aadc_design_events table for activity feed
-- DDL only — no Python logic

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'aadc_design_events'
    ) THEN
        CREATE TABLE aadc_design_events (
            id           SERIAL PRIMARY KEY,
            design_id    TEXT NOT NULL,
            event_type   TEXT NOT NULL CHECK (event_type IN (
                             'save',
                             'export_json',
                             'export_svg',
                             'export_drawio',
                             'export_pdf',
                             'export_csv',
                             'assess',
                             'version_save',
                             'node_add',
                             'node_delete',
                             'edge_add',
                             'edge_delete',
                             'simulation_run'
                         )),
            actor        TEXT,
            metadata_json JSONB,
            created_at   TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX idx_aadc_design_events_design_id
            ON aadc_design_events (design_id);

        CREATE INDEX idx_aadc_design_events_created_at
            ON aadc_design_events (created_at);
    END IF;
END $$;
