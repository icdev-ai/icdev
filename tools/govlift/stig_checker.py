# CUI // SP-CTI
"""GovLift — STIG Checker.

Manages STIG compliance checks for workloads: create, update, summarize,
and run a quick simulated scan.
All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import (
    STIG_SEVERITIES,
    STIG_CHECK_STATUSES,
)

# Sample STIG checks for the quick-scan simulator (DISA STIG-inspired)
_SAMPLE_CHECKS = [
    ("RHEL-09-211010", "OS must be supported", "cat1", "OS version EOL check"),
    ("RHEL-09-211025", "Default passwords must be changed", "cat1", "Default credentials audit"),
    ("RHEL-09-255040", "SSH Protocol 2 required", "cat2", "SSH protocol version enforcement"),
    ("RHEL-09-255055", "SSH root login disabled", "cat2", "SSH root login restriction"),
    ("RHEL-09-271040", "Audit logging enabled", "cat2", "Audit daemon configuration"),
    ("RHEL-09-271055", "Audit log storage capacity", "cat2", "Audit storage overflow handling"),
    ("RHEL-09-411010", "Password complexity enforced", "cat2", "PAM password quality module"),
    ("RHEL-09-411025", "Account lockout threshold set", "cat3", "Lockout policy configuration"),
    ("RHEL-09-412010", "Inactive accounts disabled", "cat3", "Stale account management"),
    ("RHEL-09-631010", "FIPS 140-3 mode enabled", "cat1", "Federal cryptography standard"),
]

_STIG_BENCHMARK = "RHEL-09-STIG-V1R1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stig_id() -> str:
    return "stig-" + uuid4().hex[:8]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_stig_check(check_id: str) -> dict | None:
    """Return a single STIG check by its internal id, or None if not found."""
    try:
        conn = get_connection()
        try:
            sql = translate_sql("SELECT * FROM govlift_stig_checks WHERE id = ?")
            row = conn.execute(sql, (check_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
    except Exception as exc:
        print(f"stig_checker.get_stig_check error: {exc}", file=sys.stderr)
        return None


def list_stig_checks(
    workload_id: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return STIG checks filtered by optional workload_id, severity, or status."""
    try:
        conn = get_connection()
        try:
            clauses = []
            params: list = []
            if workload_id:
                clauses.append("workload_id = ?")
                params.append(workload_id)
            if severity:
                clauses.append("severity = ?")
                params.append(severity)
            if status:
                clauses.append("status = ?")
                params.append(status)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = translate_sql(
                f"SELECT * FROM govlift_stig_checks {where} "
                f"ORDER BY severity ASC, created_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        print(f"stig_checker.list_stig_checks error: {exc}", file=sys.stderr)
        return []


def create_stig_check(
    workload_id: str,
    stig_benchmark: str,
    check_id: str,
    check_name: str,
    severity: str = "cat2",
    finding: str = "",
    remediation: str = "",
) -> dict:
    """Insert a new STIG check record and return it."""
    if severity not in STIG_SEVERITIES:
        raise ValueError(f"Invalid severity '{severity}'. Valid: {STIG_SEVERITIES}")

    sc_id = _stig_id()
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "INSERT INTO govlift_stig_checks "
                "(id, workload_id, stig_benchmark, check_id, check_name, "
                " severity, status, finding, remediation, created_at) "
                "VALUES (?,?,?,?,?,'not_reviewed',?,?,?)"
                # Note: status defaults to 'not_reviewed', severity set explicitly
            )
            # Rebuild to include severity explicitly
            sql = translate_sql(
                "INSERT INTO govlift_stig_checks "
                "(id, workload_id, stig_benchmark, check_id, check_name, "
                " severity, status, finding, remediation, created_at) "
                "VALUES (?,?,?,?,?,?,'not_reviewed',?,?,?)"
            )
            conn.execute(
                sql,
                (sc_id, workload_id, stig_benchmark, check_id, check_name,
                 severity, finding, remediation, now),
            )
            conn.commit()
            # Return the newly created record
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_stig_checks WHERE id = ?"),
                (sc_id,),
            ).fetchone()
            return _row_to_dict(row) if row else {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"stig_checker.create_stig_check error: {exc}", file=sys.stderr)
        raise


def update_check_status(
    check_id: str,
    status: str,
    finding: str | None = None,
) -> dict:
    """Update the disposition status of a STIG check."""
    if status not in STIG_CHECK_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Valid: {STIG_CHECK_STATUSES}")

    now = _now()
    try:
        conn = get_connection()
        try:
            if finding is not None:
                sql = translate_sql(
                    "UPDATE govlift_stig_checks "
                    "SET status=?, finding=?, checked_at=? WHERE id=?"
                )
                conn.execute(sql, (status, finding, now, check_id))
            else:
                sql = translate_sql(
                    "UPDATE govlift_stig_checks SET status=?, checked_at=? WHERE id=?"
                )
                conn.execute(sql, (status, now, check_id))
            conn.commit()
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_stig_checks WHERE id = ?"),
                (check_id,),
            ).fetchone()
            return _row_to_dict(row) if row else {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"stig_checker.update_check_status error: {exc}", file=sys.stderr)
        raise


def get_stig_summary(workload_id: str | None = None) -> dict:
    """Return open/not_a_finding/not_applicable counts broken down by severity."""
    try:
        conn = get_connection()
        try:
            params: list = []
            where = ""
            if workload_id:
                where = "WHERE workload_id = ?"
                params.append(workload_id)

            rows = conn.execute(
                translate_sql(
                    f"SELECT severity, status, COUNT(*) AS cnt "
                    f"FROM govlift_stig_checks {where} "
                    f"GROUP BY severity, status"
                ),
                params,
            ).fetchall()

            summary: dict = {
                sev: {st: 0 for st in STIG_CHECK_STATUSES}
                for sev in STIG_SEVERITIES
            }
            total_open = 0
            for r in rows:
                d = _row_to_dict(r)
                sev = d.get("severity", "")
                st = d.get("status", "")
                cnt = d.get("cnt", 0)
                if sev in summary and st in summary[sev]:
                    summary[sev][st] = cnt
                if st == "open":
                    total_open += cnt

            return {
                "by_severity": summary,
                "total_open": total_open,
                "workload_id": workload_id,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"stig_checker.get_stig_summary error: {exc}", file=sys.stderr)
        return {"by_severity": {}, "total_open": 0, "workload_id": workload_id}


def run_quick_scan(workload_id: str) -> list[dict]:
    """Simulate a STIG scan: insert 10 sample checks with randomised statuses.

    In production this would invoke an actual STIG scanner (e.g. OpenSCAP).
    Here we create representative records so the UI has data to render.
    """
    statuses_pool = ["open", "not_a_finding", "not_reviewed", "not_applicable"]
    weights = [0.3, 0.4, 0.2, 0.1]  # realistic distribution

    created: list[dict] = []
    for check_id, check_name, severity, description in _SAMPLE_CHECKS:
        chosen_status = random.choices(statuses_pool, weights=weights, k=1)[0]
        finding = description if chosen_status == "open" else ""
        remediation = (
            f"Review {check_id} in {_STIG_BENCHMARK} and apply required configuration."
            if chosen_status == "open"
            else ""
        )
        try:
            rec = create_stig_check(
                workload_id=workload_id,
                stig_benchmark=_STIG_BENCHMARK,
                check_id=check_id,
                check_name=check_name,
                severity=severity,
                finding=finding,
                remediation=remediation,
            )
            if rec and chosen_status != "not_reviewed":
                rec = update_check_status(rec["id"], chosen_status, finding=finding)
            if rec:
                created.append(rec)
        except Exception as exc:
            print(f"stig_checker.run_quick_scan check {check_id} error: {exc}", file=sys.stderr)

    return created


def validate_workload_checklist(
    workload_id: str,
    timeout_seconds: float = 300.0,
    benchmark: str | None = None,
) -> dict:
    """Run a timed STIG checklist validation for a workload.

    Returns a dict with workload_id, benchmark, check_count, elapsed_seconds,
    within_budget, summary, cat1_open, gate_passed, and checks.
    Gate passes when elapsed < timeout AND zero open CAT1 findings.
    """
    import time as _time
    _start = _time.monotonic()

    checks = run_quick_scan(workload_id)
    summary = get_stig_summary(workload_id)

    elapsed = _time.monotonic() - _start
    within_budget = elapsed < timeout_seconds
    by_severity = summary.get("by_severity", {})
    cat1_open = by_severity.get("cat1", {}).get("open", 0)
    gate_passed = within_budget and cat1_open == 0

    return {
        "workload_id": workload_id,
        "benchmark": benchmark if benchmark is not None else _STIG_BENCHMARK,
        "check_count": len(checks),
        "elapsed_seconds": elapsed,
        "within_budget": within_budget,
        "summary": by_severity,
        "cat1_open": cat1_open,
        "gate_passed": gate_passed,
        "checks": checks,
    }
