#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Workflow — Migration Executor.

Generates a deployable Ansible playbook and human-readable runbook from the
migration canvas wave plan and app inventory.

Artifacts:
  data/studio_artifacts/migration/ansible/playbook.yml    — Ansible playbook
  data/studio_artifacts/migration/ansible/inventory.ini   — Ansible inventory
  data/studio_artifacts/migration/03_runbook.md           — Migration runbook

Also persists the runbook to mc_runbooks for display in the Migration Canvas.

Usage (workflow runner):
  python tools/migration/migration_executor.py --project-id default --json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.migration.migration_executor")

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "migration"

_AWS_TARGET = {
    ("Oracle", "rearchitect"):       "Amazon RDS for Oracle",
    ("Redis", "replatform"):         "Amazon ElastiCache (Redis OSS)",
    ("Elasticsearch", "replatform"): "Amazon OpenSearch Service",
    ("Apache NiFi", "rearchitect"):  "Amazon EKS (containerised NiFi)",
    ("React", "replatform"):         "Amazon S3 + CloudFront",
    ("Kubernetes", "rehost"):        "Amazon EKS",
}

_ANSIBLE_TASKS = {
    "rehost": [
        {"name": "Stop application services", "shell": "systemctl stop {{ app_service }}"},
        {"name": "Create pre-migration snapshot", "shell": "aws ec2 create-snapshot --volume-id {{ volume_id }}"},
        {"name": "Sync data to target", "shell": "aws s3 sync {{ src_path }} s3://{{ target_bucket }}/"},
        {"name": "Start application on target", "shell": "systemctl start {{ app_service }}"},
        {"name": "Validate application health", "uri": {"url": "http://{{ target_host }}/health", "status_code": 200}},
    ],
    "replatform": [
        {"name": "Export application configuration", "shell": "{{ app_export_cmd }}"},
        {"name": "Provision AWS managed service", "shell": "terraform apply -target={{ tf_resource }} -auto-approve"},
        {"name": "Import data to managed service", "shell": "{{ app_import_cmd }}"},
        {"name": "Update application config to point to AWS service", "template": {"src": "{{ config_template }}", "dest": "{{ config_dest }}"}},
        {"name": "Run smoke tests", "shell": "{{ smoke_test_cmd }}"},
        {"name": "Cut DNS over to new endpoint", "shell": "aws route53 change-resource-record-sets --hosted-zone-id {{ zone_id }} --change-batch file://dns_change.json"},
    ],
    "rearchitect": [
        {"name": "Build container image", "shell": "docker build -t {{ image_name }}:{{ version }} ."},
        {"name": "Push to ECR", "shell": "docker push {{ ecr_registry }}/{{ image_name }}:{{ version }}"},
        {"name": "Apply Kubernetes manifests", "shell": "kubectl apply -f k8s/ --namespace={{ namespace }}"},
        {"name": "Wait for deployment rollout", "shell": "kubectl rollout status deployment/{{ deployment_name }} -n {{ namespace }}"},
        {"name": "Run integration tests", "shell": "pytest tests/integration/ --tb=short -q"},
        {"name": "Update DNS / ingress", "shell": "kubectl apply -f k8s/ingress.yaml"},
        {"name": "Decommission legacy resources", "shell": "terraform destroy -target={{ legacy_resource }} -auto-approve"},
    ],
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


def _ansible_playbook(apps: list, waves: list, project: dict) -> str:
    proj_name = (project.get("name") or "migration").lower().replace(" ", "_")
    blocks = [
        "---",
        "# CUI // SP-CTI — Migration Ansible Playbook",
        f"# Project: {project.get('name', 'Unknown')}",
        f"# Generated: {_now()}",
        f"# Classification: {project.get('classification', 'CUI')}",
        "",
    ]

    seen_waves: set = set()
    for w in waves:
        wn = w.get("wave")
        if wn in seen_waves:
            continue
        seen_waves.add(wn)
        strat = w.get("strategy", "replatform")
        wave_apps = [a for a in apps if a.get("migration_strategy") == strat]

        blocks += [
            f"- name: \"Wave {wn} — {w.get('name', '')}\"",
            f"  hosts: wave{wn}_hosts",
            "  become: true",
            "  vars:",
            f"    wave_number: {wn}",
            f"    migration_strategy: {strat}",
            "    aws_region: us-gov-west-1",
            f"    project: {proj_name}",
            "  tasks:",
        ]

        tasks = _ANSIBLE_TASKS.get(strat, _ANSIBLE_TASKS["replatform"])
        for app in (wave_apps or apps)[:3]:
            fw = app.get("framework") or ""
            target = _AWS_TARGET.get((fw, strat), "EC2")
            safe = app["name"].lower().replace(" ", "_").replace(".", "_")
            blocks.append(f"    # --- {app['name']} → {target} ---")
            blocks.append(f"    - name: \"[{app['name']}] Pre-migration health check\"")
            blocks.append("      shell: echo 'Checking { inventory_hostname }...'")
            blocks.append(f"      register: health_{safe}")
            for task in tasks:
                task_name = list(task.keys())[0]
                if task_name == "name":
                    task_label = task["name"].replace("{{ app_service }}", safe)
                    blocks.append(f"    - name: \"[{app['name']}] {task_label}\"")
                    remaining = {k: v for k, v in task.items() if k != "name"}
                    for k, v in remaining.items():
                        if isinstance(v, dict):
                            blocks.append(f"      {k}:")
                            for kk, vv in v.items():
                                blocks.append(f"        {kk}: \"{vv}\"")
                        else:
                            blocks.append(f"      {k}: \"{str(v).replace('{{ app_service }}', safe)}\"")
                else:
                    # dict task format
                    t_name = task.get("name", task_name).replace("{{ app_service }}", safe)
                    blocks.append(f"    - name: \"[{app['name']}] {t_name}\"")
                    for k, v in task.items():
                        if k == "name":
                            continue
                        if isinstance(v, dict):
                            blocks.append(f"      {k}:")
                            for kk, vv in v.items():
                                blocks.append(f"        {kk}: \"{vv}\"")
                        else:
                            blocks.append(f"      {k}: \"{str(v).replace('{{ app_service }}', safe)}\"")
            blocks.append("")
        blocks.append("")

    return "\n".join(blocks)


def _inventory(apps: list, project: dict) -> str:
    lines = [
        f"# Ansible Inventory — {project.get('name', 'Migration')}",
        f"# Generated: {_now()}",
        "",
        "[all:vars]",
        "ansible_user=ec2-user",
        "ansible_ssh_private_key_file=~/.ssh/migration_key.pem",
        "aws_region=us-gov-west-1",
        "",
    ]
    # Group by wave/strategy
    by_strat: dict = {}
    for app in apps:
        s = app.get("migration_strategy", "replatform")
        by_strat.setdefault(s, []).append(app)

    for wave_num, (strat, wave_apps) in enumerate(by_strat.items(), 1):
        lines.append(f"[wave{wave_num}_hosts]")
        for app in wave_apps:
            safe = app["name"].lower().replace(" ", "_").replace(".", "_")
            lines.append(f"{safe}_source ansible_host=10.0.{wave_num}.{{{{ octet }}}}  # TODO: set real IP")
        lines.append("")

    return "\n".join(lines)


def _runbook(apps: list, waves: list, project: dict) -> str:
    lines = [
        "# Migration Runbook",
        f"> Project: **{project.get('name', 'Unknown')}** | Classification: **{project.get('classification', 'CUI')}** | Generated: {_now()}",
        "",
        "---",
        "",
        "## Pre-Migration Checklist",
        "",
        "- [ ] Change freeze approved and documented",
        "- [ ] Rollback plan reviewed by lead engineer",
        "- [ ] All source systems backed up (snapshot ID recorded)",
        "- [ ] AWS target environment validated via Terraform plan",
        "- [ ] DNS TTL reduced to 60s (at T-24h)",
        "- [ ] NOC notified of maintenance window",
        "- [ ] On-call engineers confirmed for cutover",
        "- [ ] Communication sent to stakeholders",
        "",
    ]

    seen_waves: set = set()
    for w in waves:
        wn = w.get("wave")
        if wn in seen_waves:
            continue
        seen_waves.add(wn)
        strat = w.get("strategy", "replatform")
        est_hrs = {"rehost": 2, "replatform": 4, "rearchitect": 8}.get(strat, 4)
        wave_apps = [a for a in apps if a.get("migration_strategy") == strat]

        lines += [
            f"## Wave {wn}: {w.get('name', f'Wave {wn}')}",
            "",
            f"**Strategy:** {strat.title()} | **Est. Duration:** {est_hrs}h | **Status:** {w.get('status', 'planned')}",
            "",
            "### Applications in this Wave",
            "",
        ]
        for app in (wave_apps or apps)[:3]:
            fw = app.get("framework") or ""
            target = _AWS_TARGET.get((fw, strat), "EC2")
            lines += [
                f"#### {app['name']} → {target}",
                "",
                f"- **Criticality:** {app.get('criticality', '—')}",
                f"- **Environment:** {app.get('environment', '—')}",
                f"- **AWS Target:** {target}",
                "",
                "**Steps:**",
                "",
            ]
            step_names = [t.get("name", "") for t in _ANSIBLE_TASKS.get(strat, [])]
            for i, step in enumerate(step_names, 1):
                lines.append(f"{i}. {step.replace('{{ app_service }}', app['name'])}")
            lines += [
                "",
                "**Rollback:**",
                "- [ ] Restore from pre-migration snapshot",
                "- [ ] Revert DNS to source endpoint",
                "- [ ] Notify NOC and stakeholders",
                "",
            ]

    lines += [
        "## Post-Migration Validation",
        "",
        "- [ ] All application health endpoints return HTTP 200",
        "- [ ] Database connectivity verified from all app tiers",
        "- [ ] Performance metrics within 10% of baseline",
        "- [ ] Security group rules verified",
        "- [ ] CloudWatch alarms active",
        "- [ ] Run `python tools/migration/validator.py --json` — score must be ≥ 80%",
        "",
        "---",
        "*Generated by ICDEV™ Migration Workflow — Migration Executor*",
        "*CUI // SP-CTI*",
    ]
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
            "SELECT wave_number AS wave, name, status FROM mc_migration_waves ORDER BY wave_number"
        ).fetchall()] if _table_exists(conn, "mc_migration_waves") else []

        project = dict(conn.execute(
            "SELECT id, name, customer, classification, impact_level FROM mc_projects LIMIT 1"
        ).fetchone() or {})

        all_waves_raw = waves + mig_waves
        seen: set = set()
        all_waves = []
        for w in sorted(all_waves_raw, key=lambda x: x.get("wave") or 999):
            if w.get("wave") not in seen:
                seen.add(w.get("wave"))
                all_waves.append(w)

        # ── Generate artifacts ────────────────────────────────────────────────
        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        ans_dir = _ARTIFACTS_DIR / "ansible"
        ans_dir.mkdir(exist_ok=True)

        playbook_path = ans_dir / "playbook.yml"
        inventory_path = ans_dir / "inventory.ini"
        runbook_path = _ARTIFACTS_DIR / "03_runbook.md"

        playbook_path.write_text(_ansible_playbook(apps, all_waves, project), encoding="utf-8")
        inventory_path.write_text(_inventory(apps, project), encoding="utf-8")
        runbook_path.write_text(_runbook(apps, all_waves, project), encoding="utf-8")

        # ── Persist runbook to mc_runbooks ────────────────────────────────────
        design_id = conn.execute("SELECT id FROM migration_designs LIMIT 1").fetchone()
        design_id = design_id[0] if design_id else "default"
        try:
            conn.execute(
                """INSERT INTO mc_runbooks
                   (id, design_id, title, trigger_event, severity, description,
                    steps_json, owner, classification, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    f"rb-{uuid.uuid4().hex[:10]}",
                    design_id,
                    f"Migration Runbook — {project.get('name', 'Project')}",
                    "migration_workflow",
                    "high",
                    f"Auto-generated migration runbook for {len(all_waves)} wave(s), {len(apps)} app(s).",
                    json.dumps([{"wave": w.get("wave"), "name": w.get("name"), "strategy": w.get("strategy")} for w in all_waves]),
                    project.get("customer", "Operations"),
                    project.get("classification", "CUI"),
                    _now(), _now(),
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("run: best-effort INSERT into mc_runbooks failed (non-blocking): %s", exc)

        # ── Pre-flight gates ──────────────────────────────────────────────────
        gates = [
            {"gate": "Project Defined", "passed": bool(project), "severity": "CAT1"},
            {"gate": "App Inventory", "passed": len(apps) > 0, "severity": "CAT1"},
            {"gate": "Wave Plan", "passed": len(all_waves) > 0, "severity": "CAT1"},
            {"gate": "Compliance Checks", "passed": conn.execute("SELECT COUNT(*) FROM mc_compliance_checks").fetchone()[0] > 0 if _table_exists(conn, "mc_compliance_checks") else False, "severity": "CAT2"},
            {"gate": "No Blocked Waves", "passed": not any(w.get("status") == "blocked" for w in all_waves), "severity": "CAT2"},
            {"gate": "Runbook Generated", "passed": True, "severity": "CAT2"},
        ]
        passed = sum(1 for g in gates if g["passed"])
        cat1_fail = sum(1 for g in gates if not g["passed"] and g["severity"] == "CAT1")
        go_nogo = "GO" if cat1_fail == 0 else "NO-GO"

        return {
            "status": "success",
            "timestamp": _now(),
            "project_id": project_id,
            "go_nogo": go_nogo,
            "gate_score": round(passed / len(gates) * 100),
            "gates_passed": passed,
            "gates_total": len(gates),
            "cat1_failures": cat1_fail,
            "wave_count": len(all_waves),
            "app_count": len(apps),
            "playbook_tasks": sum(len(_ANSIBLE_TASKS.get(w.get("strategy", "replatform"), [])) * min(3, len(apps)) for w in all_waves),
            "summary": (
                f"GO/NO-GO: {go_nogo} ({passed}/{len(gates)} gates). "
                f"Ansible playbook ({len(all_waves)} plays), inventory, and runbook generated."
            ),
            "artifacts": [
                {"name": "Ansible Playbook", "path": playbook_path.relative_to(_ROOT).as_posix(), "type": "yaml"},
                {"name": "Ansible Inventory", "path": inventory_path.relative_to(_ROOT).as_posix(), "type": "ini"},
                {"name": "Migration Runbook", "path": runbook_path.relative_to(_ROOT).as_posix(), "type": "markdown"},
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
