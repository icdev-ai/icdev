-- Rollback: 20260902205902_asset_identity
-- CUI // SP-CTI
--
-- Safe to drop: this table is a PROJECTION. Every fact in it is re-derivable
-- from the three stacks it resolves onto (ni_devices, zig_device_registry,
-- nc_vuln_hosts) with `python -m tools.assets.identity --ingest`, and no
-- stack reads it as its own system of record -- the ZIG orchestrator falls
-- back to its fixture when it is absent.

DROP TABLE IF EXISTS asset_identity;
