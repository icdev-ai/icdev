#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Workflow — Wave Planner.

Reads existing wave plans from the migration canvas DB and produces a
structured migration wave schedule with dependencies, risk scoring, and
recommended cutover windows.

Usage (called by workflow runner):
  python tools/migration/wave_planner.py --project-id default --json
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

_RISK_WEIGHTS = {
    "lift_and_shift": 1,
    "rehost": 1,
    "replatform": 2,
    "refactor": 3,
    "rearchitect": 3,
    "retire": 0,
    "retain": 0,
    "unknown": 1,
}


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def run(project_id: str = "default") -> dict:
    conn = _conn()
    try:
        # ── Canvas wave plans (design-level) ──────────────────────────────────
        design_waves = [dict(r) for r in conn.execute(
            "SELECT wp.*, md.name AS design_name, md.migration_type "
            "FROM mc_wave_plans wp "
            "LEFT JOIN migration_designs md ON md.id = wp.design_id "
            "ORDER BY wp.wave_number"
        ).fetchall()] if _table_exists(conn, "mc_wave_plans") else []

        # ── Session-level migration waves ─────────────────────────────────────
        session_waves = [dict(r) for r in conn.execute(
            "SELECT wave_number, name, cutover_date, status, notes "
            "FROM mc_migration_waves ORDER BY wave_number"
        ).fetchall()] if _table_exists(conn, "mc_migration_waves") else []

        # ── Server inventory for sizing ────────────────────────────────────────
        srv_rows = conn.execute(
            "SELECT vcpus, ram_gb, total_disk_gb FROM mc_srv_inventory"
        ).fetchall() if _table_exists(conn, "mc_srv_inventory") else []

        # ── App inventory for risk ────────────────────────────────────────────
        app_rows = conn.execute(
            "SELECT name, migration_strategy, criticality, environment "
            "FROM mc_app_inventory ORDER BY criticality DESC"
        ).fetchall() if _table_exists(conn, "mc_app_inventory") else []

        # ── Build enriched wave schedule ──────────────────────────────────────
        schedule = []
        for w in design_waves:
            node_ids = json.loads(w.get("node_ids_json") or "[]")
            strategy = (w.get("strategy") or "unknown").lower()
            risk = _RISK_WEIGHTS.get(strategy, 1)
            schedule.append({
                "wave": w.get("wave_number"),
                "name": w.get("name"),
                "source": "design",
                "design": w.get("design_name"),
                "migration_type": w.get("migration_type"),
                "strategy": strategy,
                "status": w.get("status", "planned"),
                "node_count": len(node_ids),
                "risk_level": ["Low", "Medium", "High"][min(risk - 1, 2)] if risk > 0 else "None",
                "description": w.get("description"),
            })

        for w in session_waves:
            schedule.append({
                "wave": w.get("wave_number"),
                "name": w.get("name"),
                "source": "session",
                "cutover_date": w.get("cutover_date"),
                "status": w.get("status"),
                "notes": w.get("notes"),
            })

        # Sort by wave number
        schedule.sort(key=lambda x: x.get("wave") or 999)

        # ── Risk summary ──────────────────────────────────────────────────────
        # Flag high-criticality apps with complex migration strategies
        high_risk_apps = [dict(zip(["name", "strategy", "criticality", "env"], r))
                          for r in app_rows
                          if (r[1] or "") in ("refactor", "rearchitect") and (r[2] or "") in ("critical", "high")]

        total_servers = len(srv_rows)
        total_vcpus = sum(r[0] or 0 for r in srv_rows)
        total_ram = round(sum(r[1] or 0 for r in srv_rows), 1)

        recommendations = []
        if high_risk_apps:
            recommendations.append(
                f"{len(high_risk_apps)} critical/high app(s) require refactor/rearchitect — plan extended timelines."
            )
        if not schedule:
            recommendations.append("No wave plans found. Create waves in the Migration Canvas before running.")
        else:
            planned = sum(1 for w in schedule if w.get("status") == "planned")
            recommendations.append(f"{planned} of {len(schedule)} wave(s) in 'planned' state — ready to schedule.")

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "wave_count": len(schedule),
            "schedule": schedule,
            "infrastructure": {
                "servers": total_servers,
                "total_vcpus": total_vcpus,
                "total_ram_gb": total_ram,
            },
            "risk_flags": [
                {"app": r["name"], "strategy": r["strategy"], "criticality": r["criticality"], "env": r["env"]}
                for r in high_risk_apps[:10]
            ],
            "recommendations": recommendations,
            "summary": (
                f"Wave plan contains {len(schedule)} wave(s) covering "
                f"{total_servers} server(s) / {total_vcpus} vCPUs / {total_ram} GB RAM. "
                f"{len(high_risk_apps)} high-risk app(s) flagged."
            ),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migration Wave Planner")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(args.project_id)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
