#!/usr/bin/env python3
# CUI // SP-CTI
"""GovLift Demo Data Seeder.

Generates synthetic workloads, waves, migrations, STIG checks, audit log,
and related tables for the GovLift Cloud Migration dashboard.
Idempotent — safe to run multiple times (uses INSERT OR IGNORE).
"""
from __future__ import annotations

import random
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection
from tools.govlift.constants import (
    WORKLOAD_STATUSES,
    MIGRATION_STATUSES,
    RISK_LEVELS,
    STIG_SEVERITIES,
    STIG_CHECK_STATUSES,
    INTEGRATION_SYSTEMS,
    ROLLBACK_STATUSES,
)

_NOW = datetime.now(timezone.utc)


# ── Synthetic Data Generators ──────────────────────────────────────────────

def _ts(days_offset: int = 0) -> str:
    return (_NOW + timedelta(days=days_offset)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())[:8]


_WORKLOAD_NAMES = [
    ("auth-service", "api_service", "Ubuntu", "22.04"),
    ("payroll-db", "database", "Windows Server", "2019"),
    ("hr-web", "web_app", "Ubuntu", "20.04"),
    ("file-store", "storage", "Amazon Linux", "2023"),
    ("batch-recon", "batch_job", "RHEL", "8.7"),
    ("msg-broker", "message_queue", "Ubuntu", "22.04"),
    ("fw-core", "network_appliance", "Cisco IOS", "17.9"),
    ("legacy-fin", "legacy_app", "Windows Server", "2012 R2"),
    ("report-engine", "batch_job", "Ubuntu", "22.04"),
    ("vpn-gw-01", "network_appliance", "Palo Alto", "PAN-OS 11"),
    ("customer-api", "api_service", "Ubuntu", "22.04"),
    ("analytics-db", "database", "PostgreSQL", "15"),
]

_STIG_BENCHMARKS = [
    ("Windows Server 2019 STIG", "WN19-00-000010", "Password complexity"),
    ("Windows Server 2019 STIG", "WN19-00-000020", "Account lockout"),
    ("Ubuntu 22.04 STIG", "UBTU-22-010010", "OS vendor support"),
    ("Ubuntu 22.04 STIG", "UBTU-22-010020", "Kernel parameters"),
    ("RHEL 8 STIG", "RHEL-08-010010", "FIPS mode"),
    ("RHEL 8 STIG", "RHEL-08-010020", "SELinux policy"),
    ("PostgreSQL 15 STIG", "PGSQL-15-010010", "Authentication"),
    ("PostgreSQL 15 STIG", "PGSQL-15-010020", "Logging"),
]

# ── Seed Functions ─────────────────────────────────────────────────────────

def seed_waves(conn) -> list[str]:
    wave_ids = []
    waves = [
        ("Wave 1 — Critical", 1, "ready", _ts(-7), _ts(7)),
        ("Wave 2 — High Risk", 2, "planned", _ts(7), _ts(21)),
        ("Wave 3 — Standard", 3, "planned", _ts(21), _ts(42)),
        ("Wave 4 — Low Risk", 4, "planned", _ts(42), _ts(60)),
    ]
    for name, seq, status, ps, pe in waves:
        wid = f"wave-{_uid()}"
        wave_ids.append(wid)
        conn.execute(
            """INSERT OR IGNORE INTO govlift_waves
               (id, name, sequence_num, status, planned_start, planned_end, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (wid, name, seq, status, ps, pe, f"Synthetic wave {seq}"),
        )
    return wave_ids


def seed_workloads(conn, wave_ids: list[str]) -> list[str]:
    wl_ids = []
    for i, (name, wtype, os_name, os_ver) in enumerate(_WORKLOAD_NAMES):
        wid = f"wl-{_uid()}"
        wl_ids.append(wid)
        risk = random.choice(RISK_LEVELS)
        status = random.choice(WORKLOAD_STATUSES)
        wvid = random.choice(wave_ids + ["", ""]) if status in ("wave_assigned", "in_migration", "migrated", "failed") else ""
        conn.execute(
            """INSERT OR IGNORE INTO govlift_workloads
               (id, name, workload_type, os_name, os_version, environment,
                ip_address, cpu_cores, memory_gb, storage_tb, classification,
                risk_level, migration_status, wave_id, last_scanned, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                wid, name, wtype, os_name, os_ver, "production",
                f"10.0.{i//256}.{i % 256}",
                random.choice([2, 4, 8, 16, 32]),
                random.choice([4.0, 8.0, 16.0, 32.0, 64.0]),
                random.choice([0.5, 1.0, 2.0, 5.0, 10.0]),
                "CUI", risk, status, wvid or None, _ts(),
                f"Synthetic {wtype} workload for demo",
            ),
        )
    return wl_ids


def seed_migrations(conn, wl_ids: list[str], wave_ids: list[str]) -> list[str]:
    mig_ids = []
    for wl_id in wl_ids[:8]:
        mid = f"mig-{_uid()}"
        mig_ids.append(mid)
        status = random.choice(MIGRATION_STATUSES)
        wvid = random.choice(wave_ids)
        started = _ts(random.randint(-14, -1)) if status != "pending" else None
        completed = _ts(random.randint(-7, 0)) if status == "completed" else None
        conn.execute(
            """INSERT OR IGNORE INTO govlift_migrations
               (id, workload_id, wave_id, status, started_at, completed_at,
                executor_log, pre_check_passed, post_check_passed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid, wl_id, wvid, status, started, completed,
                f"Log for {mid}",
                random.choice([0, 1]) if status != "pending" else None,
                random.choice([0, 1]) if status in ("completed", "failed") else None,
                _ts(),
            ),
        )
    return mig_ids


def seed_stig_checks(conn, wl_ids: list[str]) -> None:
    for wl_id in wl_ids:
        for benchmark, check_id, check_name in random.sample(_STIG_BENCHMARKS, k=random.randint(2, 5)):
            severity = random.choice(STIG_SEVERITIES)
            status = random.choice(STIG_CHECK_STATUSES)
            conn.execute(
                """INSERT OR IGNORE INTO govlift_stig_checks
                   (id, workload_id, stig_benchmark, check_id, check_name,
                    severity, status, finding, remediation, checked_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"stig-{_uid()}", wl_id, benchmark, check_id, check_name,
                    severity, status,
                    "Finding detail here" if status == "open" else "",
                    "Apply patch X" if status == "open" else "N/A",
                    _ts(), _ts(),
                ),
            )


def seed_audit_log(conn) -> None:
    actions = [
        ("CREATE", "workload", "Created workload auth-service"),
        ("UPDATE", "workload", "Assigned wave to payroll-db"),
        ("DELETE", "migration", "Cancelled stale migration"),
        ("SCAN", "stig", "Ran STIG scan on Wave 1"),
        ("APPROVE", "wave", "Approved Wave 2 readiness gate"),
        ("ROLLBACK", "migration", "Initiated rollback for legacy-fin"),
    ]
    for action, res_type, detail in actions:
        conn.execute(
            """INSERT OR IGNORE INTO govlift_audit_log
               (id, user_id, action, resource_type, resource_id, details,
                classification, ip_address, session_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"audit-{_uid()}", "admin@icdev.local", action, res_type,
                f"res-{_uid()}", json.dumps({"detail": detail}),
                "CUI", "10.0.0.1", f"sess-{_uid()}", _ts(random.randint(-30, 0)),
            ),
        )


def seed_integrations(conn) -> None:
    for sys_name in INTEGRATION_SYSTEMS:
        conn.execute(
            """INSERT OR IGNORE INTO govlift_integrations
               (id, system_name, status, endpoint, last_sync, sync_count, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"int-{_uid()}", sys_name,
                random.choice(["connected", "disconnected", "error"]),
                f"https://{sys_name}.internal/api/v1", _ts(random.randint(-7, 0)),
                random.randint(0, 500), "",
            ),
        )


def seed_rollback_events(conn, mig_ids: list[str], wl_ids: list[str]) -> None:
    for _ in range(min(3, len(mig_ids))):
        conn.execute(
            """INSERT OR IGNORE INTO govlift_rollback_events
               (id, migration_id, workload_id, status, initiated_at,
                deadline_at, completed_at, reason, steps_log, sla_met, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"rb-{_uid()}", random.choice(mig_ids), random.choice(wl_ids),
                random.choice(ROLLBACK_STATUSES),
                _ts(-2), _ts(2), None,
                "Pre-check failed during migration",
                json.dumps(["step1: stop traffic", "step2: restore snapshot"]),
                random.choice([0, 1]), _ts(),
            ),
        )


def seed_compliance_artifacts(conn) -> None:
    artifact_types = ["ssp_delta", "poam_entry", "stig_finding", "evidence", "sbom_diff"]
    for _ in range(10):
        conn.execute(
            """INSERT OR IGNORE INTO govlift_compliance_artifacts
               (id, execution_id, step_id, control_id, control_family,
                artifact_type, content_json, generated_at, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"ca-{_uid()}", f"exec-{_uid()}", f"step-{_uid()}",
                f"AC-{random.randint(1, 6)}", f"NIST-800-53",
                random.choice(artifact_types),
                json.dumps({"finding": "synthetic"}),
                _ts(), "CUI",
            ),
        )


def seed_ato_boundaries(conn) -> None:
    boundaries = [
        ("Production Boundary", "[\"wl-prod-01\", \"wl-prod-02\"]", "system", "IL4", "CUI"),
        ("Staging Boundary", "[\"wl-stage-01\"]", "subsystem", "IL2", "CUI"),
    ]
    for name, wl_json, btype, level, cls in boundaries:
        conn.execute(
            """INSERT OR IGNORE INTO govlift_ato_boundaries
               (id, name, workload_ids_json, boundary_type, fedramp_level,
                classification_level, vendor_list_json, last_assessed, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"ato-{_uid()}", name, wl_json, btype, level, cls,
                json.dumps(["AWS", "Azure"]), _ts(), "CUI",
            ),
        )


def seed_supply_chain_risks(conn) -> None:
    vendors = ["AWS", "Azure", "Splunk", "ServiceNow", "CrowdStrike"]
    for vendor in vendors:
        conn.execute(
            """INSERT OR IGNORE INTO govlift_supply_chain_risks
               (id, runbook_id, vendor_name, risk_level, cve_count,
                findings_json, scanned_at, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"scr-{_uid()}", f"rb-{_uid()}", vendor,
                random.choice(RISK_LEVELS), random.randint(0, 15),
                json.dumps([{"cve": "CVE-2024-XXXX"}]), _ts(), "CUI",
            ),
        )


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    conn = get_connection()
    try:
        wave_ids = seed_waves(conn)
        wl_ids = seed_workloads(conn, wave_ids)
        mig_ids = seed_migrations(conn, wl_ids, wave_ids)
        seed_stig_checks(conn, wl_ids)
        seed_audit_log(conn)
        seed_integrations(conn)
        seed_rollback_events(conn, mig_ids, wl_ids)
        seed_compliance_artifacts(conn)
        seed_ato_boundaries(conn)
        seed_supply_chain_risks(conn)
        conn.commit()
        print(f"GovLift seeded: {len(wl_ids)} workloads, {len(wave_ids)} waves, {len(mig_ids)} migrations")
    except Exception as exc:
        print(f"GovLift seed error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
