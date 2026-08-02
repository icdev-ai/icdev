#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 329: close the INSERT/schema gaps found by swp-scan-01.

``tools/lint/insert_column_linter.py`` parsed every static ``INSERT INTO`` in
``tools/`` and checked the named columns against ``information_schema`` on the
live PostgreSQL instance.  Every statement it flagged was then fed to PostgreSQL
``PREPARE`` — which parses and plans but never executes — and every one raised
``UndefinedColumn``.  Almost all of them sit inside a bare
``except Exception: pass``, so the writes had been failing silently for as long
as the columns had been missing.  The per-finding counts and classification live
in ``docs/database/insert-column-schema-parity.md``, which is the authoritative
record; this docstring deliberately does not restate a total that would rot the
moment either side changes.

This migration handles the subset where **the code was right and the database
was behind**: the column is declared in the owning ``init_db.py`` / earlier
migration, carries data with no live equivalent under another name, and simply
never landed on this database because ``CREATE TABLE IF NOT EXISTS`` does not
alter a table that already exists.

Sites where the *code* was wrong — a column that already exists under a
different name (``rack_location`` vs ``rack``), or a statement aimed at the
wrong table entirely (``tasks`` vs ``kanban_tasks``) — are fixed in the calling
modules instead, not here.  Renaming a live column to match a caller would
break every reader of that column; renaming the caller costs nothing.  The full
per-finding classification is in
``docs/database/insert-column-schema-parity.md``.

Idempotent on both backends: every add is guarded by a live column read rather
than ``ADD COLUMN IF NOT EXISTS``, which SQLite does not support.  Re-running is
a no-op.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# {table: [(column, type), ...]} — types stay inside the subset SQLite tolerates
# verbatim (TEXT / INTEGER / REAL / BOOLEAN), so the init-fallback applies
# without leaning on translate_sql, which is never load-bearing.
COLUMNS: dict[str, list[tuple[str, str]]] = {
    # --- FathomDesk / AlphaDesk -------------------------------------------
    # Both are written by an upsert that already names them in its DO UPDATE
    # SET list, so the statement could never have run at all.
    "ad_quality_scores": [("model_version", "TEXT"), ("updated_at", "TEXT")],
    "ad_ticker_performance": [("is_anomaly", "INTEGER DEFAULT 0")],
    # --- Registry / evolution ---------------------------------------------
    "child_capabilities": [("metadata", "TEXT"), ("updated_at", "TEXT")],
    "icdev_capability_map": [("grade", "TEXT"), ("metadata", "TEXT")],
    # --- Compliance --------------------------------------------------------
    "control_crosswalk": [("created_at", "TEXT")],
    "data_classifications": [("subcategory", "TEXT"), ("confidence", "REAL")],
    # fairness_assessments held the one-row-per-project shape while the
    # assessor writes one row per dimension. The per-dimension columns are
    # added rather than the assessor rewritten because assessment_data/
    # overall_score have no per-dimension meaning and the dashboard reads both.
    "fairness_assessments": [
        ("dimension", "TEXT"),
        ("status", "TEXT"),
        ("evidence", "TEXT"),
        ("score", "REAL"),
        ("assessed_at", "TEXT"),
    ],
    # --- Document Intelligence --------------------------------------------
    # origin/status are the AI-provenance and review-gate fields: without them
    # no DIC document could record whether a human or a model wrote it.
    "dic_documents": [("origin", "TEXT"), ("status", "TEXT")],
    "dic_doc_freshness": [("score", "REAL")],
    # --- Foundry / GameDay -------------------------------------------------
    "foundry_signals": [("content_hash", "TEXT")],
    "gd_ai_training_pairs": [("tournament_id", "TEXT"), ("artifact_type", "TEXT")],
    # --- GovLift / GovCon --------------------------------------------------
    "govlift_runbook_templates": [("estimated_min", "INTEGER")],
    "pg_ai_clause_compliance": [
        ("artifact_type", "TEXT"),
        ("artifact_status", "TEXT"),
        ("artifact_content", "TEXT"),
        ("source_tool", "TEXT"),
    ],
    "pg_cmmc_supply_chain": [("created_at", "TEXT"), ("updated_at", "TEXT")],
    "pg_win_loss_records": [("bid_decision_id", "TEXT"), ("debrief_notes", "TEXT")],
    "pg_win_themes": [("keywords", "TEXT")],
    "pma_credential_alerts": [("severity", "TEXT")],
    "pma_personnel": [("role", "TEXT"), ("secret_expiry", "TEXT"), ("ts_expiry", "TEXT")],
    # --- Dashboard / platform ---------------------------------------------
    "hook_events": [("severity", "TEXT"), ("message", "TEXT")],
    "innovation_signals": [("raw_score", "REAL"), ("keywords", "TEXT")],
    "memory_entries": [("metadata", "TEXT")],
    "notification_log": [("body", "TEXT"), ("tenant_id", "TEXT")],
    "oracle_predictions": [
        ("status", "TEXT"),
        ("suggested_action", "TEXT"),
        ("expires_at", "TEXT"),
    ],
    "pulse_demand_signals": [("sam_opportunity_ids", "TEXT"), ("status", "TEXT")],
    "rag_ingestion_log": [("status", "TEXT"), ("started_at", "TEXT"), ("completed_at", "TEXT")],
    "redaction_audit": [("action", "TEXT")],
    "workflow_acceptance_criteria": [("cot_config", "TEXT")],
    # --- Network / Migration canvases -------------------------------------
    "nc_intent_policies": [("rule_json", "TEXT"), ("severity", "TEXT")],
    "nc_nqe_audit_log": [("user_confirmed", "BOOLEAN"), ("row_count", "INTEGER")],
    "nc_patch_plans": [("action", "TEXT"), ("blast_radius_json", "TEXT")],
    "ni_devices": [
        ("downstream_count", "INTEGER"),
        ("properties_json", "TEXT"),
        ("annual_maintenance_cost", "REAL"),
    ],
    # The hardware-profile shape and the asset-inventory shape are both real
    # and both write through mc_srv_inventory; the union is additive.
    "mc_srv_inventory": [
        ("hostname", "TEXT"),
        ("ip_address", "TEXT"),
        ("os", "TEXT"),
        ("cpu_cores", "INTEGER"),
        ("disk_gb", "REAL"),
        ("environment", "TEXT"),
        ("status", "TEXT"),
        ("tags", "TEXT"),
        ("source_format", "TEXT"),
        ("imported_at", "TEXT"),
    ],
    # --- Observability / Security canvases --------------------------------
    "odc_twin_snapshots": [("coverage_basis", "TEXT"), ("payload_json", "TEXT")],
    "sdc_attack_snapshots": [
        ("design_id", "TEXT"),
        ("label", "TEXT"),
        ("node_count", "INTEGER"),
        ("path_count", "INTEGER"),
    ],
    "zig_maturity_scores": [("target_id", "TEXT")],
    # --- Strategos ---------------------------------------------------------
    "sg_coa_options": [("resource_allocation", "TEXT")],
    "sg_kg_edges": [("created_at", "TEXT")],
    "sg_kg_nodes": [("created_at", "TEXT")],
    # A TRUST invariant: without these an INTSUM cannot record whether its
    # paragraphs were grounded or which sources they cite, so the citation
    # gate had nothing to read back at rest.
    "sg_intsums": [("grounding_status", "TEXT"), ("grounding_json", "TEXT")],
    "sg_intsum_paragraphs": [
        ("grounded", "INTEGER"),
        ("require_citations", "INTEGER"),
        ("citations", "TEXT"),
    ],
    # --- Digital Program Twin ---------------------------------------------
    # simulation_results is one name owned by two subsystems in one database:
    # the Network Canvas won the CREATE race with its topology shape, so the
    # twin's per-dimension columns never landed. Adding them is additive and
    # leaves every Network Canvas reader untouched.
    "simulation_results": [
        ("scenario_id", "TEXT"),
        ("dimension", "TEXT"),
        ("metric_name", "TEXT"),
        ("baseline_value", "TEXT"),
        ("simulated_value", "TEXT"),
        ("delta", "TEXT"),
        ("delta_pct", "REAL"),
        ("confidence", "REAL"),
        ("impact_tier", "TEXT"),
        ("details", "TEXT"),
        ("visualizations", "TEXT"),
        ("calculated_at", "TEXT"),
    ],
    # --- TTX / billing -----------------------------------------------------
    "ttx_api_log": [("token_count", "INTEGER"), ("cost_usd", "REAL")],
    # Same collision shape: migration 067 created the web-request log and
    # migration 213's billing CREATE TABLE IF NOT EXISTS silently no-opped.
    "usage_events": [
        ("tenant_id", "TEXT"),
        ("event_type", "TEXT"),
        ("quantity", "REAL"),
        ("model", "TEXT"),
        ("canvas_key", "TEXT"),
        ("metadata", "TEXT"),
        ("recorded_at", "TEXT"),
    ],
    # --- Workflow canvas ---------------------------------------------------
    "wfc_chain_phases": [("workflow_snapshot_yaml", "TEXT")],
}

# Adding a column is not enough when the shape that won the CREATE race left a
# NOT NULL the other subsystem never writes: the INSERT still fails, just with
# NotNullViolation instead of UndefinedColumn. PostgreSQL only — SQLite cannot
# ALTER COLUMN, and relaxing it there would mean rebuilding the table, which is
# not worth the risk on a backend that is only ever the init fallback.
DROP_NOT_NULL: list[tuple[str, str]] = [
    # assessment_data was NOT NULL because every row used to be a whole-project
    # assessment. Per-dimension rows have no value for it, so the constraint has
    # to relax for the assessor's INSERT to land at all.
    ("fairness_assessments", "assessment_data"),
    # sim_type is the Network Canvas's own NOT NULL. Once _split_simulation_results
    # has moved the NC rows to nc_simulation_results the column is vestigial here,
    # but while it stays NOT NULL every Digital Program Twin INSERT — which names
    # scenario_id/dimension/metric_name and never sim_type — is rejected. Adding
    # the twin's columns without this leaves the site exactly as broken.
    ("simulation_results", "sim_type"),
]

# ON CONFLICT needs a matching unique index; the assessor upserts one row per
# (project_id, dimension) and no such index existed.
UNIQUE_INDEXES: list[tuple[str, str, str]] = [
    (
        "idx_fairness_project_dimension",
        "fairness_assessments",
        "project_id, dimension",
    ),
]


def _split_simulation_results(conn, is_pg: bool) -> None:
    """Give the Network Canvas its own ``nc_simulation_results``.

    ``simulation_results`` was created by whichever subsystem initialised the
    database first: the Network Canvas (topology_id / sim_type / input_json /
    result_json / ran_at) or the Digital Program Twin (scenario_id / dimension
    / metric_name / …).  Both CREATE statements are ``IF NOT EXISTS``, so the
    loser silently got the winner's table and every one of its INSERTs raised
    ``UndefinedColumn``.  Observed live during swp-scan-01: the shape flipped
    between two scans minutes apart, so which subsystem is broken was not even
    stable.

    The Network Canvas prefixes its other 134 tables ``nc_``; this one was the
    anomaly, so it moves and the twin keeps the unprefixed name that
    ``init_icdev_db.py`` declares.  Any existing NC rows are carried across.
    """
    live = _live_columns(conn, "simulation_results", is_pg)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nc_simulation_results ("
        "id TEXT PRIMARY KEY, "
        "topology_id TEXT, "
        "sim_type TEXT NOT NULL, "
        "input_json TEXT DEFAULT '{}', "
        "result_json TEXT DEFAULT '{}', "
        "ran_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    # Only when the live table is still the Network Canvas shape is there
    # anything to carry across.
    if {"topology_id", "sim_type", "input_json", "result_json", "ran_at"} <= live:
        conn.execute(
            "INSERT INTO nc_simulation_results "
            "(id, topology_id, sim_type, input_json, result_json, ran_at) "
            "SELECT id, topology_id, sim_type, input_json, result_json, ran_at "
            "FROM simulation_results WHERE sim_type IS NOT NULL"
            + (" ON CONFLICT (id) DO NOTHING" if is_pg else "")
        )
        logger.info("329: Network Canvas simulation rows copied to nc_simulation_results")


def _is_pg(conn) -> bool:
    try:
        from tools.db.storage import is_pg

        return bool(is_pg(conn))
    except Exception:
        return conn.__class__.__module__.startswith("psycopg2") or "postgres" in str(
            getattr(conn, "backend", "")
        ).lower()


def _live_columns(conn, table: str, is_pg: bool) -> set[str]:
    """Return the table's live column names, or an empty set when absent."""
    try:
        if is_pg:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            ).fetchall()
            return {(dict(r) if not isinstance(r, tuple) else {"column_name": r[0]})["column_name"].lower() for r in rows}
        rows = conn.execute('PRAGMA table_info("%s")' % table).fetchall()
        out = set()
        for r in rows:
            rec = r if isinstance(r, tuple) else dict(r)
            out.add(str(rec[1] if isinstance(rec, tuple) else rec.get("name", "")).lower())
        return {c for c in out if c}
    except Exception:
        return set()


def up(conn=None) -> None:
    """Add every column the live schema is missing. Safe to re-run."""
    own = conn is None
    if own:
        from tools.db.storage import get_connection

        conn = get_connection()
    is_pg = _is_pg(conn)
    added = skipped = absent = 0

    try:
        _split_simulation_results(conn, is_pg)

        for table, cols in COLUMNS.items():
            live = _live_columns(conn, table, is_pg)
            if not live:
                # The table does not exist on this database. Creating it here
                # would fork a second definition away from its owning
                # init_db.py, so leave it alone and say so.
                absent += 1
                logger.info("329: table %s absent — skipped", table)
                continue
            for column, coltype in cols:
                if column.lower() in live:
                    skipped += 1
                    continue
                conn.execute(
                    'ALTER TABLE %s ADD COLUMN "%s" %s' % (table, column, coltype)
                )
                added += 1
                logger.info("329: %s.%s added", table, column)

        if is_pg:
            for table, column in DROP_NOT_NULL:
                if column.lower() in _live_columns(conn, table, is_pg):
                    conn.execute(
                        "ALTER TABLE %s ALTER COLUMN %s DROP NOT NULL" % (table, column)
                    )

        for index, table, columns in UNIQUE_INDEXES:
            live = _live_columns(conn, table, is_pg)
            if not live or not all(c.strip() in live for c in columns.split(",")):
                continue
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s (%s)"
                    % (index, table, columns)
                )
            except Exception as exc:  # noqa: BLE001
                # Pre-existing duplicates. The upsert stays correct without the
                # index on every row written from here on; failing the whole
                # migration over historic data would be worse.
                logger.warning("329: unique index %s not created: %s", index, exc)

        conn.commit()
        logger.info(
            "329 complete: %d column(s) added, %d already present, %d table(s) absent",
            added,
            skipped,
            absent,
        )
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    # get_logger() already attaches handlers, so there is no basicConfig call
    # here — and there must not be: `logging` is deliberately not imported.
    up()
