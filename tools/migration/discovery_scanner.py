#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Workflow — Discovery Scanner.

Reads the migration canvas DB and produces a structured discovery report:
  - Active projects, designs, and migration types
  - Server / VM inventory summary (CPU, RAM, disk, OS)
  - Application inventory summary
  - Wave plan status
  - Readiness score (0-100)

Usage (called by workflow runner):
  python tools/migration/discovery_scanner.py --project-id default --json
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


def _safe(row, key, default=None):
    try:
        return row[key] if row[key] is not None else default
    except (KeyError, IndexError):
        return default


def run(project_id: str = "default") -> dict:
    conn = _conn()
    try:
        # ── Projects ──────────────────────────────────────────────────────────
        projects = [dict(r) for r in conn.execute(
            "SELECT id, name, customer, status, classification, impact_level FROM mc_projects"
        ).fetchall()]

        # ── Migration designs ─────────────────────────────────────────────────
        designs = [dict(r) for r in conn.execute(
            "SELECT id, name, migration_type, classification, created_at FROM migration_designs ORDER BY created_at DESC"
        ).fetchall()]

        # ── Server inventory ──────────────────────────────────────────────────
        srv_rows = conn.execute(
            "SELECT vcpus, ram_gb, total_disk_gb, disk_type FROM mc_srv_inventory"
        ).fetchall()
        srv_summary = {
            "count": len(srv_rows),
            "total_vcpus": sum(r[0] or 0 for r in srv_rows),
            "total_ram_gb": round(sum(r[1] or 0 for r in srv_rows), 1),
            "total_disk_tb": round(sum(r[2] or 0 for r in srv_rows) / 1024, 2),
            "disk_types": list({r[3] for r in srv_rows if r[3]}),
        }

        # ── Application inventory ─────────────────────────────────────────────
        app_rows = conn.execute(
            "SELECT name, environment, migration_strategy, criticality FROM mc_app_inventory"
        ).fetchall() if _table_exists(conn, "mc_app_inventory") else []
        app_summary = {
            "count": len(app_rows),
            "strategies": {},
            "criticality_breakdown": {},
        }
        for r in app_rows:
            s = r[2] or "unknown"
            app_summary["strategies"][s] = app_summary["strategies"].get(s, 0) + 1
            c = r[3] or "unknown"
            app_summary["criticality_breakdown"][c] = app_summary["criticality_breakdown"].get(c, 0) + 1

        # ── Wave plans ────────────────────────────────────────────────────────
        waves = [dict(r) for r in conn.execute(
            "SELECT wave_number, name, strategy, status FROM mc_wave_plans ORDER BY wave_number"
        ).fetchall()]
        migration_waves = [dict(r) for r in conn.execute(
            "SELECT wave_number, name, status, cutover_date FROM mc_migration_waves ORDER BY wave_number"
        ).fetchall()]

        # ── Readiness score ───────────────────────────────────────────────────
        checks = {
            "has_project": len(projects) > 0,
            "has_design": len(designs) > 0,
            "has_inventory": srv_summary["count"] > 0 or app_summary["count"] > 0,
            "has_wave_plan": len(waves) > 0 or len(migration_waves) > 0,
            "classified": any(p.get("classification") for p in projects),
        }
        readiness = round(sum(checks.values()) / len(checks) * 100)

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "readiness_score": readiness,
            "readiness_checks": checks,
            "projects": projects,
            "designs": designs,
            "server_inventory": srv_summary,
            "app_inventory": app_summary,
            "wave_plans": waves,
            "migration_waves": migration_waves,
            "summary": (
                f"Discovered {len(projects)} project(s), {len(designs)} design(s), "
                f"{srv_summary['count']} server(s), {app_summary['count']} app(s), "
                f"{len(waves) + len(migration_waves)} wave(s). "
                f"Readiness: {readiness}%."
            ),
        }
    finally:
        conn.close()


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def main():
    parser = argparse.ArgumentParser(description="Migration Discovery Scanner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(args.project_id)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
