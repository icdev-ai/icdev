# CUI // SP-CTI
"""GovLift DoD IL4 Cloud Migration Tool — Database Initializer.

Creates all GovLift tables in the main icdev.db (or PostgreSQL backend).
Uses get_connection() from tools.db.storage — never sqlite3.connect() directly.
Uses translate_sql() for cross-backend SQL compatibility.

Usage:
    python -c "from tools.govlift.db.init_db import init_govlift_db; init_govlift_db(); print('DB OK')"
"""

from __future__ import annotations

import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_canvas_connection, translate_sql
from tools.govlift.constants import (
    CHECK_WORKLOAD_STATUS,
    CHECK_WORKLOAD_TYPE,
    CHECK_MIGRATION_STATUS,
    CHECK_STIG_SEVERITY,
    CHECK_STIG_STATUS,
    CHECK_WAVE_STATUS,
    CHECK_RISK_LEVEL,
    CHECK_INTEGRATION_SYS,
    CHECK_ROLLBACK_STATUS,
    CHECK_MARKETPLACE_STATUS,
    CHECK_RUNBOOK_CATEGORY,
)

# ---------------------------------------------------------------------------
# Schema — all CHECK constraints derived from constants.py
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = [
    # ── Workloads ────────────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_workloads (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        workload_type   TEXT NOT NULL
                            CHECK ({CHECK_WORKLOAD_TYPE}),
        os_name         TEXT DEFAULT '',
        os_version      TEXT DEFAULT '',
        environment     TEXT DEFAULT 'production',
        ip_address      TEXT DEFAULT '',
        cpu_cores       INTEGER DEFAULT 4,
        memory_gb       REAL DEFAULT 8.0,
        storage_tb      REAL DEFAULT 1.0,
        classification  TEXT DEFAULT 'CUI',
        risk_level      TEXT DEFAULT 'medium'
                            CHECK ({CHECK_RISK_LEVEL}),
        migration_status TEXT DEFAULT 'discovered'
                            CHECK ({CHECK_WORKLOAD_STATUS}),
        wave_id         TEXT,
        last_scanned    TEXT,
        notes           TEXT DEFAULT '',
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_wl_status ON govlift_workloads(migration_status)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_wl_risk ON govlift_workloads(risk_level)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_wl_wave ON govlift_workloads(wave_id)",

    # ── Waves ────────────────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_waves (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        sequence_num    INTEGER NOT NULL DEFAULT 1,
        status          TEXT DEFAULT 'planned'
                            CHECK ({CHECK_WAVE_STATUS}),
        planned_start   TEXT,
        planned_end     TEXT,
        workload_count  INTEGER DEFAULT 0,
        notes           TEXT DEFAULT '',
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_waves_status ON govlift_waves(status)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_waves_seq ON govlift_waves(sequence_num)",

    # ── Migrations ───────────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_migrations (
        id                  TEXT PRIMARY KEY,
        workload_id         TEXT NOT NULL
                                REFERENCES govlift_workloads(id),
        wave_id             TEXT
                                REFERENCES govlift_waves(id),
        status              TEXT DEFAULT 'pending'
                                CHECK ({CHECK_MIGRATION_STATUS}),
        started_at          TEXT,
        completed_at        TEXT,
        executor_log        TEXT DEFAULT '',
        pre_check_passed    INTEGER,
        post_check_passed   INTEGER,
        rollback_deadline   TEXT,
        created_at          TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_mig_workload ON govlift_migrations(workload_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_mig_wave ON govlift_migrations(wave_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_mig_status ON govlift_migrations(status)",

    # ── STIG Checks ──────────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_stig_checks (
        id              TEXT PRIMARY KEY,
        workload_id     TEXT NOT NULL
                            REFERENCES govlift_workloads(id),
        stig_benchmark  TEXT DEFAULT '',
        check_id        TEXT DEFAULT '',
        check_name      TEXT DEFAULT '',
        severity        TEXT DEFAULT 'cat2'
                            CHECK ({CHECK_STIG_SEVERITY}),
        status          TEXT DEFAULT 'not_reviewed'
                            CHECK ({CHECK_STIG_STATUS}),
        finding         TEXT DEFAULT '',
        remediation     TEXT DEFAULT '',
        checked_at      TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_stig_workload ON govlift_stig_checks(workload_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_stig_severity ON govlift_stig_checks(severity)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_stig_status ON govlift_stig_checks(status)",

    # ── Audit Log (append-only) ──────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS govlift_audit_log (
        id              TEXT PRIMARY KEY,
        user_id         TEXT DEFAULT '',
        action          TEXT NOT NULL,
        resource_type   TEXT DEFAULT '',
        resource_id     TEXT DEFAULT '',
        details         TEXT DEFAULT '{}',
        classification  TEXT DEFAULT 'CUI',
        ip_address      TEXT DEFAULT '',
        session_id      TEXT DEFAULT '',
        timestamp       TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_audit_user ON govlift_audit_log(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_audit_action ON govlift_audit_log(action)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_audit_ts ON govlift_audit_log(timestamp)",

    # ── Integrations ─────────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_integrations (
        id              TEXT PRIMARY KEY,
        system_name     TEXT UNIQUE NOT NULL
                            CHECK ({CHECK_INTEGRATION_SYS}),
        status          TEXT DEFAULT 'disconnected',
        endpoint        TEXT DEFAULT '',
        last_sync       TEXT,
        sync_count      INTEGER DEFAULT 0,
        error_message   TEXT DEFAULT '',
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_integrations_sys ON govlift_integrations(system_name)",

    # ── Rollback Events ──────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_rollback_events (
        id              TEXT PRIMARY KEY,
        migration_id    TEXT NOT NULL
                            REFERENCES govlift_migrations(id),
        workload_id     TEXT NOT NULL
                            REFERENCES govlift_workloads(id),
        status          TEXT DEFAULT 'initiated'
                            CHECK ({CHECK_ROLLBACK_STATUS}),
        initiated_at    TEXT NOT NULL,
        deadline_at     TEXT NOT NULL,
        completed_at    TEXT,
        reason          TEXT DEFAULT '',
        steps_log       TEXT DEFAULT '[]',
        sla_met         INTEGER,
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_rollback_mig ON govlift_rollback_events(migration_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_rollback_status ON govlift_rollback_events(status)",

    # ── Compliance Artifacts ─────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS govlift_compliance_artifacts (
        id              TEXT PRIMARY KEY,
        execution_id    TEXT NOT NULL,
        step_id         TEXT,
        control_id      TEXT NOT NULL,
        control_family  TEXT,
        artifact_type   TEXT CHECK(artifact_type IN ('ssp_delta','poam_entry','stig_finding','evidence','sbom_diff')),
        content_json    TEXT DEFAULT '{}',
        generated_at    TEXT,
        classification  TEXT DEFAULT 'CUI'
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_ca_execution ON govlift_compliance_artifacts(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_ca_control ON govlift_compliance_artifacts(control_id)",

    # ── ATO Boundaries ───────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS govlift_ato_boundaries (
        id                    TEXT PRIMARY KEY,
        name                  TEXT NOT NULL,
        workload_ids_json     TEXT DEFAULT '[]',
        boundary_type         TEXT,
        fedramp_level         TEXT,
        classification_level  TEXT,
        vendor_list_json      TEXT DEFAULT '[]',
        last_assessed         TEXT,
        classification        TEXT DEFAULT 'CUI'
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_ato_name ON govlift_ato_boundaries(name)",

    # ── Supply Chain Risks ───────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_supply_chain_risks (
        id              TEXT PRIMARY KEY,
        runbook_id      TEXT,
        vendor_name     TEXT NOT NULL,
        risk_level      TEXT CHECK ({CHECK_RISK_LEVEL}),
        cve_count       INT DEFAULT 0,
        findings_json   TEXT DEFAULT '[]',
        scanned_at      TEXT,
        classification  TEXT DEFAULT 'CUI'
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_scr_vendor ON govlift_supply_chain_risks(vendor_name)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_scr_risk ON govlift_supply_chain_risks(risk_level)",

    # ── Runbook Templates ────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_runbook_templates (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        category        TEXT DEFAULT ''
                            CHECK ({CHECK_RUNBOOK_CATEGORY}),
        description     TEXT DEFAULT '',
        workload_type   TEXT DEFAULT 'web_app',
        author          TEXT DEFAULT '',
        steps_json      TEXT DEFAULT '[]',
        estimated_min   INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_rbt_category ON govlift_runbook_templates(category)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_rbt_author ON govlift_runbook_templates(author)",

    # ── Runbook Instances ────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS govlift_runbooks (
        id              TEXT PRIMARY KEY,
        template_id     TEXT NOT NULL
                            REFERENCES govlift_runbook_templates(id),
        name            TEXT NOT NULL,
        status          TEXT DEFAULT 'draft'
                            CHECK (status IN ('draft','running','completed','failed','rolled_back')),
        step_count      INTEGER DEFAULT 0,
        failed_step     INTEGER,
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_rb_status ON govlift_runbooks(status)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_rb_template ON govlift_runbooks(template_id)",

    # ── Runbook Executions ───────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS govlift_runbook_executions (
        id              TEXT PRIMARY KEY,
        runbook_id      TEXT NOT NULL
                            REFERENCES govlift_runbooks(id),
        status          TEXT DEFAULT 'running',
        started_at      TEXT,
        completed_at    TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_rbe_runbook ON govlift_runbook_executions(runbook_id)",

    # ── Runbook Step Results ─────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS govlift_runbook_step_results (
        id              TEXT PRIMARY KEY,
        execution_id    TEXT NOT NULL
                            REFERENCES govlift_runbook_executions(id),
        runbook_id      TEXT NOT NULL,
        step_num        INTEGER NOT NULL,
        step_name       TEXT DEFAULT '',
        status          TEXT DEFAULT 'pending',
        output          TEXT DEFAULT '',
        started_at      TEXT,
        completed_at    TEXT
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_rbs_exec ON govlift_runbook_step_results(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_rbs_runbook ON govlift_runbook_step_results(runbook_id)",

    # ── Marketplace Items ────────────────────────────────────────────────────
    f"""CREATE TABLE IF NOT EXISTS govlift_marketplace_items (
        id              TEXT PRIMARY KEY,
        template_id     TEXT NOT NULL
                            REFERENCES govlift_runbook_templates(id),
        title           TEXT NOT NULL,
        author          TEXT NOT NULL,
        description     TEXT DEFAULT '',
        status          TEXT DEFAULT 'submitted'
                            CHECK ({CHECK_MARKETPLACE_STATUS}),
        reviewed_by     TEXT DEFAULT '',
        review_notes    TEXT DEFAULT '',
        published_at    TEXT,
        downloads       INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now'))
    )""",

    "CREATE INDEX IF NOT EXISTS idx_govlift_mp_status ON govlift_marketplace_items(status)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_mp_author ON govlift_marketplace_items(author)",
    "CREATE INDEX IF NOT EXISTS idx_govlift_mp_template ON govlift_marketplace_items(template_id)",
]


def init_govlift_db() -> None:
    """Create all GovLift tables in the main icdev.db.

    Idempotent — safe to call on every startup.
    Uses IF NOT EXISTS throughout so repeated calls are no-ops.
    """
    # cvx-sql-03: govlift_* tables have no classification/tenant_id columns, so the
    # storage-global get_connection() would attach the RLS predicate and raise
    # UndefinedColumn. Use get_canvas_connection() (RLS disabled) instead.
    conn = get_canvas_connection("GOVLIFT_DB_URL")
    try:
        for stmt in _SCHEMA_STATEMENTS:
            stmt = stmt.strip()
            if not stmt:
                continue
            translated = translate_sql(stmt)
            try:
                conn.execute(translated)
            except Exception as exc:
                # Tolerate "already exists" style errors from PostgreSQL
                msg = str(exc).lower()
                if "already exists" in msg or "duplicate" in msg:
                    pass
                else:
                    raise
        conn.commit()
        print("GovLift DB: schema initialized OK", file=sys.stderr)
    except Exception as exc:
        print(f"GovLift DB init error: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_govlift_db()
    print("DB OK")
