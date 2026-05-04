#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 058 — Create Strategos interdiction tables.

Creates:
  sg_supply_nodes       — adversary logistics nodes (depots, rail hubs, ports, etc.)
  sg_supply_edges       — directed dependency edges between supply nodes
  sg_interdiction_results — ranked interdiction analysis outputs (append-only)
  sg_kg_nodes           — Strategos knowledge-graph nodes (IF NOT EXISTS)
  sg_kg_edges           — Strategos knowledge-graph edges (IF NOT EXISTS)

Also creates base tables needed by blueprint.py (all IF NOT EXISTS):
  sg_orbat_units, sg_ghost_signals, sg_iw_effects, sg_wargames, sg_hitl_items

Idempotent: all CREATE TABLE / CREATE INDEX use IF NOT EXISTS.
"""
from tools.db.storage import get_connection

MIGRATION_ID = "058"
MIGRATION_NAME = "sg_interdiction"
DESCRIPTION = "Create sg_supply_nodes, sg_supply_edges, sg_interdiction_results + base SG tables"

_DDL = [
    # ------------------------------------------------------------------
    # Supply chain nodes — adversary logistics infrastructure
    # ------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS sg_supply_nodes (
        node_id             TEXT PRIMARY KEY,
        label               TEXT NOT NULL,
        node_type           TEXT NOT NULL DEFAULT 'depot',
        criticality         TEXT NOT NULL DEFAULT 'medium',
        substitutability    REAL NOT NULL DEFAULT 0.5,
        controlled_by       TEXT,
        lat                 REAL,
        lon                 REAL,
        metadata_json       TEXT,
        created_at          TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    )""",

    # ------------------------------------------------------------------
    # Supply chain dependency edges
    # ------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS sg_supply_edges (
        id          TEXT PRIMARY KEY,
        source_id   TEXT NOT NULL,
        target_id   TEXT NOT NULL,
        edge_type   TEXT NOT NULL DEFAULT 'SUPPLIES',
        weight      REAL NOT NULL DEFAULT 1.0,
        created_at  TEXT NOT NULL
    )""",

    # ------------------------------------------------------------------
    # Interdiction analysis results — append-only per NIST AU
    # ------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS sg_interdiction_results (
        id                      TEXT PRIMARY KEY,
        run_id                  TEXT NOT NULL,
        node_id                 TEXT NOT NULL,
        rank                    INTEGER NOT NULL,
        blast_radius            INTEGER NOT NULL DEFAULT 0,
        criticality_score       REAL NOT NULL DEFAULT 0.0,
        substitutability_inverse REAL NOT NULL DEFAULT 0.0,
        composite_score         REAL NOT NULL DEFAULT 0.0,
        affected_units_json     TEXT,
        computed_at             TEXT NOT NULL
    )""",

    # ------------------------------------------------------------------
    # Strategos KG nodes — general entity registry
    # ------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS sg_kg_nodes (
        node_id     TEXT PRIMARY KEY,
        node_type   TEXT NOT NULL DEFAULT 'event',
        label       TEXT,
        metadata_json TEXT,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    # ------------------------------------------------------------------
    # Strategos KG edges — general relationship registry
    # ------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS sg_kg_edges (
        id          TEXT PRIMARY KEY,
        source_id   TEXT NOT NULL,
        target_id   TEXT NOT NULL,
        relation    TEXT NOT NULL,
        metadata_json TEXT,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    # ------------------------------------------------------------------
    # Base Strategos tables (IF NOT EXISTS — may already exist elsewhere)
    # ------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS sg_orbat_units (
        id          TEXT PRIMARY KEY,
        unit_name   TEXT NOT NULL,
        nation      TEXT,
        unit_type   TEXT,
        strength    INTEGER,
        location    TEXT,
        status      TEXT DEFAULT 'active',
        metadata_json TEXT,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    """CREATE TABLE IF NOT EXISTS sg_ghost_signals (
        id          TEXT PRIMARY KEY,
        signal_type TEXT NOT NULL,
        source      TEXT,
        description TEXT,
        detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        metadata_json TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS sg_iw_effects (
        id          TEXT PRIMARY KEY,
        effect_type TEXT NOT NULL,
        target      TEXT,
        description TEXT,
        detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        metadata_json TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS sg_wargames (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        scenario    TEXT,
        state       TEXT NOT NULL DEFAULT 'pending',
        blue_force  TEXT,
        red_force   TEXT,
        outcome     TEXT,
        attrition_coefficients_json TEXT,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",

    """CREATE TABLE IF NOT EXISTS sg_hitl_items (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        description TEXT,
        status      TEXT NOT NULL DEFAULT 'pending',
        decision    TEXT,
        resolved_at TEXT,
        resolved_by TEXT,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
]

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_sn_type         ON sg_supply_nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sn_criticality  ON sg_supply_nodes(criticality)",
    "CREATE INDEX IF NOT EXISTS idx_sg_sn_controlled_by ON sg_supply_nodes(controlled_by)",
    "CREATE INDEX IF NOT EXISTS idx_sg_se_source        ON sg_supply_edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_se_target        ON sg_supply_edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ir_run_id        ON sg_interdiction_results(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ir_node_id       ON sg_interdiction_results(node_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ir_rank          ON sg_interdiction_results(rank)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ir_computed_at   ON sg_interdiction_results(computed_at)",
    "CREATE INDEX IF NOT EXISTS idx_sg_kg_nodes_type    ON sg_kg_nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_sg_kg_edges_src     ON sg_kg_edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_kg_edges_tgt     ON sg_kg_edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_kg_edges_rel     ON sg_kg_edges(relation)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for ddl in _DDL:
            conn.execute(ddl)
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("Migration 058 up: sg_supply_nodes, sg_supply_edges, sg_interdiction_results, "
              "sg_kg_nodes, sg_kg_edges, and base SG tables created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
