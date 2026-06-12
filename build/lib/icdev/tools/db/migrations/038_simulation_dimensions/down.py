#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 038 rollback: restore simulation_results.dimension CHECK to original 6 values.

Rows with dimension in ('resource_allocation', 'quality') are deleted first.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_NEW_TABLE_SQLITE = """
CREATE TABLE IF NOT EXISTS simulation_results_old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL REFERENCES simulation_scenarios(id),
    dimension TEXT NOT NULL
        CHECK(dimension IN ('architecture', 'compliance', 'supply_chain',
            'schedule', 'cost', 'risk')),
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
INSERT INTO simulation_results_old
    (id, scenario_id, dimension, metric_name, baseline_value, simulated_value,
     delta, delta_pct, confidence, impact_tier, details, visualizations, calculated_at)
SELECT id, scenario_id, dimension, metric_name, baseline_value, simulated_value,
       delta, delta_pct, confidence, impact_tier, details, visualizations, calculated_at
FROM simulation_results
WHERE dimension IN ('architecture','compliance','supply_chain','schedule','cost','risk')
"""


def down() -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        if backend == "postgresql":
            conn.execute(
                "DELETE FROM simulation_results WHERE dimension IN ('resource_allocation','quality')"
            )
            conn.execute(
                "ALTER TABLE simulation_results DROP CONSTRAINT IF EXISTS simulation_results_dimension_check"
            )
            conn.execute(
                "ALTER TABLE simulation_results ADD CONSTRAINT simulation_results_dimension_check "
                "CHECK(dimension IN ('architecture','compliance','supply_chain','schedule','cost','risk'))"
            )
        else:
            conn.execute(_NEW_TABLE_SQLITE)
            conn.execute(_COPY_DATA)
            conn.execute("DROP TABLE simulation_results")
            conn.execute("ALTER TABLE simulation_results_old RENAME TO simulation_results")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sim_result_scenario "
                "ON simulation_results(scenario_id)"
            )
        conn.commit()
        print("[038_simulation_dimensions] down: simulation_results.dimension restored to 6 values")
    finally:
        conn.close()


if __name__ == "__main__":
    down()
