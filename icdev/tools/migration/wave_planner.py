#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Workflow — Wave Planner.

Reads migration canvas wave plans and app inventory, generates:
  - WAVE_PLAN.yaml   — Ansible-compatible wave execution plan
  - terraform/terraform.tfvars — Terraform variable file for target AWS infra

Artifacts:
  data/studio_artifacts/migration/02_wave_plan.yaml
  data/studio_artifacts/migration/terraform/terraform.tfvars

Usage (workflow runner):
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "migration"

# Maps (framework, strategy) → AWS target service
_AWS_TARGET = {
    ("Oracle", "rearchitect"):       ("Amazon RDS for Oracle", "db.r6i.xlarge"),
    ("Redis", "replatform"):         ("Amazon ElastiCache (Redis OSS)", "cache.r7g.large"),
    ("Elasticsearch", "replatform"): ("Amazon OpenSearch Service", "r6g.large.search"),
    ("Apache NiFi", "rearchitect"):  ("Amazon EKS (containerised NiFi)", "m5.xlarge"),
    ("React", "replatform"):         ("Amazon S3 + CloudFront", "N/A"),
    ("Kubernetes", "rehost"):        ("Amazon EKS", "m5.xlarge"),
}


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _table_exists(conn, name):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s", (name,)
    ).fetchone())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _tfvars(apps: list, project: dict) -> str:
    proj_name = (project.get("name") or "migration").lower().replace(" ", "_")
    lines = [
        "# CUI // SP-CTI — Terraform Variables",
        f"# Project: {project.get('name', 'Unknown')}",
        f"# Generated: {_now()}",
        "",
        f'project_name    = "{proj_name}"',
        'environment     = "production"',
        f'classification  = "{project.get("classification", "CUI")}"',
        f'impact_level    = "{project.get("impact_level", "IL4")}"',
        'aws_region      = "us-gov-west-1"',
        "",
        "# Application targets",
    ]
    for app in apps:
        fw = app.get("framework") or ""
        strat = app.get("migration_strategy") or ""
        target, instance = _AWS_TARGET.get((fw, strat), ("EC2 (manual)", "m5.large"))
        safe = app["name"].lower().replace(" ", "_").replace(".", "_")
        lines.append(f'# {app["name"]} ({strat}) → {target}')
        lines.append(f'{safe}_instance_type = "{instance}"')
        lines.append(f'{safe}_target        = "{target}"')
        lines.append("")
    return "\n".join(lines)


def _wave_yaml(waves: list, apps: list) -> str:
    lines = [
        "# CUI // SP-CTI — Migration Wave Execution Plan",
        f"# Generated: {_now()}",
        "",
        "waves:",
    ]
    seen_waves: set = set()
    for w in waves:
        wn = w.get("wave")
        if wn in seen_waves:
            continue
        seen_waves.add(wn)
        strat = w.get("strategy", "replatform")
        est_hrs = {"rehost": 2, "replatform": 4, "rearchitect": 8, "refactor": 12}.get(strat, 4)
        lines += [
            f"  - wave: {wn}",
            f"    name: \"{w.get('name', f'Wave {wn}')}\"",
            f"    strategy: {strat}",
            f"    status: {w.get('status', 'planned')}",
            f"    estimated_hours: {est_hrs}",
            "    applications:",
        ]
        wave_apps = [a for a in apps if a.get("migration_strategy") == strat] or apps[:2]
        for app in wave_apps[:3]:
            fw = app.get("framework") or ""
            target, _ = _AWS_TARGET.get((fw, app.get("migration_strategy", "")), ("EC2", "m5.large"))
            lines += [
                f"      - name: \"{app['name']}\"",
                f"        strategy: {app.get('migration_strategy', 'replatform')}",
                f"        criticality: {app.get('criticality', 'medium')}",
                f"        aws_target: \"{target}\"",
                f"        cutover_requires_downtime: {'true' if app.get('criticality') in ('mission_critical','critical') else 'false'}",
            ]
        lines.append("")
    return "\n".join(lines)


def run(project_id: str = "default") -> dict:
    conn = _conn()
    try:
        apps = [dict(r) for r in conn.execute(
            "SELECT id, name, migration_strategy, environment, criticality, framework, app_type "
            "FROM mc_app_inventory"
        ).fetchall()] if _table_exists(conn, "mc_app_inventory") else []

        waves = [dict(r) for r in conn.execute(
            "SELECT wave_number AS wave, name, strategy, status FROM mc_wave_plans ORDER BY wave_number"
        ).fetchall()] if _table_exists(conn, "mc_wave_plans") else []

        mig_waves = [dict(r) for r in conn.execute(
            "SELECT wave_number AS wave, name, cutover_date, status FROM mc_migration_waves ORDER BY wave_number"
        ).fetchall()] if _table_exists(conn, "mc_migration_waves") else []

        project = dict(conn.execute(
            "SELECT id, name, customer, classification, impact_level FROM mc_projects LIMIT 1"
        ).fetchone() or {})

        srv_rows = conn.execute("SELECT vcpus, ram_gb, total_disk_gb FROM mc_srv_inventory").fetchall() \
            if _table_exists(conn, "mc_srv_inventory") else []

        all_waves = waves + mig_waves
        all_waves.sort(key=lambda w: w.get("wave") or 999)

        # ── Generate artifacts ────────────────────────────────────────────────
        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        tf_dir = _ARTIFACTS_DIR / "terraform"
        tf_dir.mkdir(exist_ok=True)

        wave_yaml_path = _ARTIFACTS_DIR / "02_wave_plan.yaml"
        tfvars_path = tf_dir / "terraform.tfvars"

        wave_yaml_path.write_text(_wave_yaml(all_waves, apps), encoding="utf-8", newline="")
        tfvars_path.write_text(_tfvars(apps, project), encoding="utf-8", newline="")

        high_risk = [a for a in apps if a.get("migration_strategy") in ("rearchitect", "refactor")
                     and a.get("criticality") in ("mission_critical", "critical", "high")]

        return {
            "status": "success",
            "timestamp": _now(),
            "project_id": project_id,
            "wave_count": len({w.get("wave") for w in all_waves}),
            "app_count": len(apps),
            "high_risk_apps": len(high_risk),
            "infrastructure": {
                "servers": len(srv_rows),
                "total_vcpus": sum(r[0] or 0 for r in srv_rows),
                "total_ram_gb": round(sum(r[1] or 0 for r in srv_rows), 1),
            },
            "summary": (
                f"Wave plan generated: {len({w.get('wave') for w in all_waves})} wave(s), "
                f"{len(apps)} application(s), {len(high_risk)} high-risk. "
                f"Terraform vars and YAML execution plan written."
            ),
            "artifacts": [
                {"name": "Wave Plan (YAML)", "path": wave_yaml_path.relative_to(_ROOT).as_posix(), "type": "yaml"},
                {"name": "Terraform Variables", "path": tfvars_path.relative_to(_ROOT).as_posix(), "type": "terraform"},
            ],
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(args.project_id)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
