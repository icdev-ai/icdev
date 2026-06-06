#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 120 — OHC Ops Hub Canvas tables.

Creates the following tables in ohc_canvas.db:
  ohc_experiments        — Experiment tracking metadata
  ohc_experiment_runs    — Individual run metrics and params
  ohc_model_registry     — Registered model versions and stages
  ohc_dataset_versions   — Dataset versioning with drift flags
  ohc_adapter_status     — Current health of each ops adapter
  ohc_adapter_health_log — Historical adapter health log
  ohc_data_drift_events  — Data drift detection events

Idempotent: uses CREATE TABLE IF NOT EXISTS. Delegates to
tools/ops_hub/db/init_db.py for the actual schema execution.
"""

from __future__ import annotations

MIGRATION_ID = "120"
MIGRATION_NAME = "ops_hub"
DESCRIPTION = "Create OHC (Ops Hub Canvas) tables in ohc_canvas.db"


def run(conn=None) -> None:
    """Apply migration 120 — initialise OHC database."""
    from tools.ops_hub.db.init_db import get_connection, init_db
    ohc_conn = get_connection()
    try:
        init_db(ohc_conn)
        print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")
    finally:
        ohc_conn.close()


def up(conn) -> None:
    run(conn)


if __name__ == "__main__":
    run()
