#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 038: Expand simulation_results.dimension CHECK to include resource_allocation, quality.

ALL_DIMENSIONS grew from 6 to 8 values; the CHECK constraint must match.
SQLite does not support ALTER TABLE to change CHECK constraints, so the table
is recreated via rename → create → copy → drop.  PostgreSQL supports
DROP/ADD CONSTRAINT directly.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_NEW_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS simulation_results_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(id),
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk', 'resource_allocation', 'quality')),
    metric_name TEXT NOT NULL,
    baseline_value REAL,
    simulated_value REAL,
    delta REAL,
    delta_pct REAL,
    confidence REAL DEFAULT 0.0,
    impact_tier TEXT CHECK(impact_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    details TEXT,
    visualizations TEXT,
    calculated_at TEXT DEFAULT (datetime('now'))
)
"""

_COPY_DATA = """
INSERT INTO simulation_results_new
    (id, scenario_id, dimension, metric_name, baseline_value, simulated_value,
     delta, delta_pct, confidence, impact_tier, details, visualizations, calculated_at)
SELECT id, scenario_id, dimension, metric_name, baseline_value, simulated_value,
       delta, delta_pct, confidence, impact_tier, details, visualizations, calculated_at
FROM simulation_results
"""


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        if backend == "postgresql":
            conn.execute(
                "ALTER TABLE simulation_results DROP CONSTRAINT IF EXISTS simulation_results_dimension_check"
            )
            conn.execute(
                "ALTER TABLE simulation_results ADD CONSTRAINT simulation_results_dimension_check "
                "CHECK(dimension IN ('architecture','compliance','supply_chain',"
                "'schedule','cost','risk','resource_allocation','quality'))"
            )
        else:
            conn.execute(_NEW_TABLE_SQLITE)
            conn.execute(_COPY_DATA)
            conn.execute("DROP TABLE simulation_results")
            conn.execute("ALTER TABLE simulation_results_new RENAME TO simulation_results")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sim_result_scenario "
                "ON simulation_results(scenario_id)"
            )
        conn.commit()
        print("[038_simulation_dimensions] up: simulation_results.dimension expanded to 8 values")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
