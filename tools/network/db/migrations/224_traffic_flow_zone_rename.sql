-- Migration 223: Reconcile nc_traffic_flows column drift (ndc-fix-04)
-- Depends on: nc_traffic_flows (init_db.py)
--
-- TrafficFlowEngine (tools/network/traffic_flow.py) reads/writes
-- src_zone / dst_zone / app_type, but the init_db.py DDL historically created the
-- table with source_zone / destination_zone / application_type. On any PostgreSQL
-- database where init_db created the table first, POST /network/<topo>/traffic-flows
-- raised UndefinedColumn (HTTP 500). Confirmed live by ndc-e2e-01 (PR #378).
--
-- Canonical column set is the engine's names (src_zone/dst_zone/app_type): it is the
-- load-bearing runtime write/read path and every route, template and test already
-- uses it. This migration renames the drifted columns on pre-existing PG tables.
-- Idempotent: each rename is guarded so a second run (or a fresh table already using
-- the new names) is a no-op. PostgreSQL only; the SQLite path recreates the table
-- from the corrected SCHEMA on every init.

DO $$
BEGIN
    -- source_zone -> src_zone
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'nc_traffic_flows' AND column_name = 'source_zone')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'nc_traffic_flows' AND column_name = 'src_zone') THEN
        ALTER TABLE nc_traffic_flows RENAME COLUMN source_zone TO src_zone;
    END IF;

    -- destination_zone -> dst_zone
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'nc_traffic_flows' AND column_name = 'destination_zone')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'nc_traffic_flows' AND column_name = 'dst_zone') THEN
        ALTER TABLE nc_traffic_flows RENAME COLUMN destination_zone TO dst_zone;
    END IF;

    -- application_type -> app_type
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'nc_traffic_flows' AND column_name = 'application_type')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'nc_traffic_flows' AND column_name = 'app_type') THEN
        ALTER TABLE nc_traffic_flows RENAME COLUMN application_type TO app_type;
    END IF;

    -- Drop the old-named CHECK constraint (it now references app_type but keeps its
    -- original name); repair_check_constraints() re-adds a correctly named
    -- nc_traffic_flows_app_type_check derived from APPLICATION_TYPES.
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'nc_traffic_flows_application_type_check') THEN
        ALTER TABLE nc_traffic_flows DROP CONSTRAINT nc_traffic_flows_application_type_check;
    END IF;
END $$;
