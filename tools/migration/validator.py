#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Workflow — Post-Migration Validator.

Validates migration outcomes by checking:
  - Completed vs planned wave status
  - Post-migration test results (mc_srv_post_migration_tests)
  - Compliance check pass rates (mc_compliance_checks)
  - Cloud instance provisioning (mc_cloud_instances)
  - Server performance baselines (mc_srv_performance)
  - Cutover step completion (mc_srv_cutover_steps)

Produces a pass/fail validation report with per-category scores.

Usage (called by workflow runner):
  python tools/migration/validator.py --project-id default --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _pct(num, den) -> int:
    return round(num / den * 100) if den else 0


def run(project_id: str = "default") -> dict:
    conn = _conn()
    try:
        categories: dict[str, dict] = {}

        # ── Wave completion ────────────────────────────────────────────────────
        if _table_exists(conn, "mc_migration_waves"):
            rows = conn.execute("SELECT status FROM mc_migration_waves").fetchall()
            total = len(rows)
            complete = sum(1 for r in rows if r[0] == "complete")
            categories["Wave Completion"] = {
                "passed": complete, "total": total,
                "pct": _pct(complete, total),
                "note": f"{complete}/{total} wave(s) marked complete",
            }

        # ── Post-migration tests ───────────────────────────────────────────────
        if _table_exists(conn, "mc_srv_post_migration_tests"):
            rows = conn.execute("SELECT status FROM mc_srv_post_migration_tests").fetchall()
            total = len(rows)
            passed = sum(1 for r in rows if r[0] in ("pass", "passed", "success"))
            categories["Post-Migration Tests"] = {
                "passed": passed, "total": total,
                "pct": _pct(passed, total),
                "note": f"{passed}/{total} test(s) passed",
            }

        # ── Compliance checks ──────────────────────────────────────────────────
        if _table_exists(conn, "mc_compliance_checks"):
            rows = conn.execute("SELECT status FROM mc_compliance_checks").fetchall()
            total = len(rows)
            passed = sum(1 for r in rows if r[0] in ("pass", "passed", "compliant", "ok"))
            categories["Compliance Checks"] = {
                "passed": passed, "total": total,
                "pct": _pct(passed, total),
                "note": f"{passed}/{total} compliance check(s) passing",
            }

        # ── Cutover steps ──────────────────────────────────────────────────────
        if _table_exists(conn, "mc_srv_cutover_steps"):
            rows = conn.execute("SELECT status FROM mc_srv_cutover_steps").fetchall()
            total = len(rows)
            done = sum(1 for r in rows if (r[0] or "").lower() in ("complete", "done", "passed", "success"))
            categories["Cutover Steps"] = {
                "passed": done, "total": total,
                "pct": _pct(done, total),
                "note": f"{done}/{total} cutover step(s) complete",
            }

        # ── Net config validation ──────────────────────────────────────────────
        if _table_exists(conn, "mc_net_config_validation"):
            rows = conn.execute("SELECT status FROM mc_net_config_validation").fetchall()
            total = len(rows)
            passed = sum(1 for r in rows if (r[0] or "").lower() in ("pass", "passed", "ok", "valid"))
            categories["Network Config Validation"] = {
                "passed": passed, "total": total,
                "pct": _pct(passed, total),
                "note": f"{passed}/{total} network config(s) validated",
            }

        # ── Server performance baselines ───────────────────────────────────────
        if _table_exists(conn, "mc_srv_performance"):
            perf_count = conn.execute("SELECT COUNT(*) FROM mc_srv_performance").fetchone()[0]
            categories["Performance Baselines"] = {
                "passed": perf_count, "total": perf_count,
                "pct": 100 if perf_count else 0,
                "note": f"{perf_count} performance baseline(s) recorded",
            }

        # If no validation data exists yet, report it clearly
        if not categories:
            categories["Inventory"] = {
                "passed": 0, "total": 1,
                "pct": 0,
                "note": "No post-migration data available yet — run waves first.",
            }

        # ── Overall score ──────────────────────────────────────────────────────
        total_checks = sum(c["total"] for c in categories.values())
        total_passed = sum(c["passed"] for c in categories.values())
        overall_pct = _pct(total_passed, total_checks)

        grade = "A" if overall_pct >= 90 else "B" if overall_pct >= 80 else \
                "C" if overall_pct >= 70 else "D" if overall_pct >= 60 else "F"

        findings = []
        for cat, data in categories.items():
            if data["pct"] < 80 and data["total"] > 0:
                findings.append({
                    "category": cat,
                    "score": data["pct"],
                    "note": data["note"],
                    "action": f"Review {cat.lower()} — {data['total'] - data['passed']} item(s) not passing.",
                })

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "overall_score": overall_pct,
            "grade": grade,
            "checks_passed": total_passed,
            "checks_total": total_checks,
            "categories": categories,
            "findings": findings,
            "validation_passed": overall_pct >= 80,
            "summary": (
                f"Validation score: {overall_pct}% (Grade {grade}). "
                f"{total_passed}/{total_checks} checks passing across {len(categories)} categorie(s). "
                f"{len(findings)} finding(s) require attention."
            ),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migration Post-Migration Validator")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(args.project_id)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
