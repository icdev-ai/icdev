# CUI // SP-CTI
"""Dependency health checker — pip check, CVE scan (pip-audit), outdated packages.

Checks three dimensions of dependency health and files kanban bug tasks for
critical/high findings. Integrates with Genesis reflex system via run().

Usage:
    python tools/testing/dep_health.py [--json] [--dry-run] [--no-kanban]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.testing.dep_health")

DEDUP_DB = BASE_DIR / "data" / "dep_health_filed.db"
REQUIREMENTS = BASE_DIR / "requirements.txt"


# ---------------------------------------------------------------------------
# Dedup DB — local SQLite to track what's already been filed as kanban tasks
# ---------------------------------------------------------------------------

def _dedup_conn() -> sqlite3.Connection:
    DEDUP_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DEDUP_DB))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS dep_health_filed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pkg_name TEXT NOT NULL,
            finding_key TEXT NOT NULL,
            filed_at TEXT NOT NULL,
            UNIQUE(pkg_name, finding_key)
        )"""
    )
    conn.commit()
    return conn


def _already_filed(dedup: sqlite3.Connection, pkg_name: str, finding_key: str) -> bool:
    row = dedup.execute(
        "SELECT id FROM dep_health_filed WHERE pkg_name=? AND finding_key=?",
        (pkg_name, finding_key),
    ).fetchone()
    return row is not None


def _mark_filed(dedup: sqlite3.Connection, pkg_name: str, finding_key: str) -> None:
    dedup.execute(
        "INSERT OR IGNORE INTO dep_health_filed (pkg_name, finding_key, filed_at) VALUES (?,?,?)",
        (pkg_name, finding_key, datetime.now(timezone.utc).isoformat()),
    )
    dedup.commit()


# ---------------------------------------------------------------------------
# Check 1 — pip check (broken installs / dependency conflicts)
# ---------------------------------------------------------------------------

def _check_pip(timeout: int = 60) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(BASE_DIR),
        )
        output = (result.stdout or "").strip()
        if result.returncode == 0 or output.lower().startswith("no broken"):
            return {"status": "pass", "conflicts": []}
        conflicts = [
            {"text": line.strip(), "severity": "high"}
            for line in output.splitlines()
            if line.strip() and "has requirement" in line.lower() or "incompatible" in line.lower()
        ]
        if not conflicts and output:
            # Catch all non-empty lines as conflicts
            conflicts = [{"text": line.strip(), "severity": "high"} for line in output.splitlines() if line.strip()]
        return {"status": "fail" if conflicts else "pass", "conflicts": conflicts}
    except subprocess.TimeoutExpired:
        return {"status": "error", "conflicts": [], "error": "pip check timed out"}
    except Exception as exc:
        return {"status": "error", "conflicts": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Check 2 — pip-audit (CVE scan)
# ---------------------------------------------------------------------------

def _cvss_severity(score: Optional[float]) -> str:
    if score is None:
        return "medium"
    if score >= 7.0:
        return "critical"
    if score >= 4.0:
        return "high"
    return "medium"


def _check_pip_audit(timeout: int = 60) -> Dict[str, Any]:
    req_arg = ["-r", str(REQUIREMENTS)] if REQUIREMENTS.exists() else []
    cmd = [sys.executable, "-m", "pip_audit", "--format", "json"] + req_arg
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(BASE_DIR),
        )
        stderr = (result.stderr or "").lower()
        if result.returncode != 0 and (
            "no module named pip_audit" in stderr or "no module named 'pip_audit'" in stderr
        ):
            return {"status": "unavailable", "vulns": [], "critical": 0, "high": 0}

        try:
            raw = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return {"status": "error", "vulns": [], "critical": 0, "high": 0, "error": "invalid JSON from pip-audit"}

        vulns: List[Dict] = []
        # pip-audit JSON: list of {name, version, vulns: [{id, description, fix_versions, aliases}]}
        for pkg in (raw if isinstance(raw, list) else raw.get("dependencies", [])):
            for v in pkg.get("vulns", []):
                cvss = v.get("cvss", None)
                if cvss is None:
                    # Try aliases for CVSS
                    for alias in v.get("aliases", []):
                        if "CVSS" in str(alias).upper():
                            try:
                                cvss = float(str(alias).split(":")[-1])
                            except Exception:
                                pass
                sev = _cvss_severity(cvss)
                vulns.append({
                    "name": pkg.get("name", ""),
                    "version": pkg.get("version", ""),
                    "vuln_id": v.get("id", ""),
                    "description": (v.get("description", "") or "")[:200],
                    "fix_versions": v.get("fix_versions", []),
                    "severity": sev,
                    "cvss": cvss,
                })

        crit = sum(1 for v in vulns if v["severity"] == "critical")
        high = sum(1 for v in vulns if v["severity"] == "high")
        status = "fail" if (crit + high) > 0 else ("warn" if vulns else "pass")
        return {"status": status, "vulns": vulns, "critical": crit, "high": high}

    except subprocess.TimeoutExpired:
        return {"status": "error", "vulns": [], "critical": 0, "high": 0, "error": "pip-audit timed out"}
    except Exception as exc:
        return {"status": "error", "vulns": [], "critical": 0, "high": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Check 3 — outdated packages
# ---------------------------------------------------------------------------

def _check_outdated(timeout: int = 60) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(BASE_DIR),
        )
        try:
            packages = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            packages = []

        count = len(packages)
        status = "warn" if count > 10 else "pass"
        return {
            "status": status,
            "count": count,
            "packages": packages[:10],  # first 10 for the report
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "count": 0, "packages": [], "error": "pip list timed out"}
    except Exception as exc:
        return {"status": "error", "count": 0, "packages": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Kanban task filing
# ---------------------------------------------------------------------------

def _file_kanban_task(title: str, description: str, priority: str) -> Optional[str]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        task_id = f"task-dep-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, task_type, priority, status,
                 scheduled_at, created_at, updated_at, dispatch_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, title, description, "bug", priority, "backlog",
             now, now, now, "dep_health"),
        )
        conn.commit()
        conn.close()
        return task_id
    except Exception as exc:
        logger.warning("dep_health: kanban file failed: %s", exc)
        return None


def _file_tasks(
    pip_result: Dict,
    audit_result: Dict,
    dry_run: bool = False,
    no_kanban: bool = False,
) -> List[str]:
    filed: List[str] = []
    try:
        dedup = _dedup_conn()
    except Exception as exc:
        logger.warning("dep_health: dedup DB unavailable: %s", exc)
        dedup = None

    # Conflicts from pip check
    for conflict in pip_result.get("conflicts", []):
        text = conflict.get("text", "")
        key = f"conflict:{text[:80]}"
        pkg_name = text.split()[0] if text else "unknown"
        if dedup and _already_filed(dedup, pkg_name, key):
            continue
        title = f"[DEP-HEALTH] {pkg_name}: broken install"
        desc = (
            f"Dependency conflict detected by `pip check`:\n\n"
            f"```\n{text}\n```\n\n"
            "Steps:\n"
            "1. Run `pip check` locally to confirm.\n"
            "2. Check `requirements.txt` for conflicting version pins.\n"
            "3. Resolve by upgrading/downgrading the conflicting package.\n"
            "4. Re-run `python tools/testing/dep_health.py` to confirm clean."
        )
        if not dry_run and not no_kanban:
            tid = _file_kanban_task(title, desc, priority="high")
            if tid:
                filed.append(tid)
                if dedup:
                    _mark_filed(dedup, pkg_name, key)
        else:
            filed.append(f"[dry-run] {title}")

    # CVEs from pip-audit
    for vuln in audit_result.get("vulns", []):
        sev = vuln.get("severity", "medium")
        if sev not in ("critical", "high"):
            continue
        pkg_name = vuln.get("name", "unknown")
        vuln_id = vuln.get("vuln_id", "")
        key = f"vuln:{vuln_id}"
        if dedup and _already_filed(dedup, pkg_name, key):
            continue
        priority = "critical" if sev == "critical" else "high"
        title = f"[DEP-HEALTH] {pkg_name} {vuln.get('version', '')}: {vuln_id}"
        fixes = vuln.get("fix_versions", [])
        fix_str = f"Fix available in: {', '.join(fixes)}" if fixes else "No fix version listed."
        desc = (
            f"**CVE/Vulnerability:** {vuln_id}  \n"
            f"**Package:** {pkg_name} {vuln.get('version', '')}  \n"
            f"**Severity:** {sev.upper()} (CVSS: {vuln.get('cvss', 'N/A')})  \n"
            f"**Description:** {vuln.get('description', '')}  \n\n"
            f"{fix_str}\n\n"
            "Steps:\n"
            f"1. `pip install --upgrade {pkg_name}` (if fix version available).\n"
            "2. Update `requirements.txt` pin.\n"
            "3. Run tests: `pytest tests/ -v --tb=short`.\n"
            "4. Re-run `python tools/testing/dep_health.py --no-kanban` to confirm clean."
        )
        if not dry_run and not no_kanban:
            tid = _file_kanban_task(title, desc, priority=priority)
            if tid:
                filed.append(tid)
                if dedup:
                    _mark_filed(dedup, pkg_name, key)
        else:
            filed.append(f"[dry-run] {title}")

    if dedup:
        try:
            dedup.close()
        except Exception:
            pass

    return filed


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any] = None, state: Any = None) -> Dict[str, Any]:
    """Genesis reflex entry point. Returns structured health report."""
    cfg = config or {}
    dry_run = bool(cfg.get("dry_run", False))
    no_kanban = bool(cfg.get("no_kanban", False))

    logger.info("dep_health: running pip check...")
    pip_result = _check_pip()

    logger.info("dep_health: running pip-audit...")
    audit_result = _check_pip_audit()

    logger.info("dep_health: checking outdated packages...")
    outdated_result = _check_outdated()

    filed = _file_tasks(pip_result, audit_result, dry_run=dry_run, no_kanban=no_kanban)

    has_critical = audit_result.get("critical", 0) > 0
    has_high = audit_result.get("high", 0) > 0 or pip_result.get("status") == "fail"
    outdated_warn = outdated_result.get("count", 0) > 10

    if has_critical or has_high:
        overall = "fail"
    elif outdated_warn:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "pip_check": pip_result,
        "pip_audit": audit_result,
        "outdated": outdated_result,
        "filed_tasks": filed,
        "overall": overall,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV Dependency Health Checker")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, do not file kanban tasks")
    parser.add_argument("--no-kanban", action="store_true", help="Skip kanban task filing")
    args = parser.parse_args()

    result = run(config={"dry_run": args.dry_run, "no_kanban": args.no_kanban})

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["overall"] == "pass" else 1

    # Human-readable output
    pip = result["pip_check"]
    audit = result["pip_audit"]
    outdated = result["outdated"]

    print("\n=== Dependency Health Check ===")
    print(f"pip check:    {pip['status'].upper()}"
          + (f" ({len(pip['conflicts'])} conflict(s))" if pip.get("conflicts") else ""))
    print(f"pip-audit:    {audit['status'].upper()}"
          + (f" ({audit.get('critical', 0)} critical, {audit.get('high', 0)} high)" if audit["status"] != "unavailable" else " (pip-audit not installed)"))
    print(f"outdated:     {outdated['status'].upper()} ({outdated.get('count', 0)} packages)")
    print(f"overall:      {result['overall'].upper()}")

    if pip.get("conflicts"):
        print("\nConflicts:")
        for c in pip["conflicts"]:
            print(f"  - {c['text']}")

    if audit.get("vulns"):
        high_crit = [v for v in audit["vulns"] if v["severity"] in ("critical", "high")]
        if high_crit:
            print("\nCritical/High CVEs:")
            for v in high_crit[:10]:
                print(f"  [{v['severity'].upper()}] {v['name']} {v['version']}: {v['vuln_id']}")

    if outdated.get("packages"):
        print(f"\nOutdated (first {len(outdated['packages'])} of {outdated['count']}):")
        for p in outdated["packages"]:
            print(f"  {p.get('name')}: {p.get('version')} → {p.get('latest_version')}")

    if result["filed_tasks"]:
        print(f"\nKanban tasks filed: {len(result['filed_tasks'])}")
        for tid in result["filed_tasks"]:
            print(f"  {tid}")

    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
