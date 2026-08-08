"""Ansible Executor — Shared Workflow Step.

Runs ansible-playbook --syntax-check inside Docker (cytopia/ansible:latest).
Falls back to YAML syntax check if Docker unavailable.
Canvas-agnostic: reads the Ansible Playbook artifact from 'Generate IaC'.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

import yaml as _yaml  # noqa: E402

from tools.studio.executors._base import (  # noqa: E402
    artifacts_dir, resolve_canvas, get_iac_artifacts, find_artifact,
    docker_available, pull_image, docker_run,
)

_ANSIBLE_IMAGE = "cytopia/ansible:latest"


def run(run_id: str, project_id: str, canvas: str = "") -> dict:
    canvas = resolve_canvas(run_id, canvas)
    out_dir = artifacts_dir(canvas)
    artifacts = get_iac_artifacts(run_id)
    playbook_path = find_artifact(artifacts, "yml", "playbook")
    inventory_path = find_artifact(artifacts, "ini", "inventory")
    uid = uuid.uuid4().hex[:8]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings: list[dict] = []
    gate = "PASS"
    docker_used = False

    # Fallback: most recent playbook for this canvas
    if not playbook_path:
        pbs = sorted(out_dir.glob("ansible_playbook_*.yml"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        playbook_path = pbs[0] if pbs else None

    if not playbook_path:
        return {"gate": "WARN", "canvas": canvas, "docker_used": False,
                "findings": [{"severity": "warn", "check": "no_playbook",
                               "message": f"No Ansible playbook found for canvas '{canvas}'"}]}

    use_docker = docker_available()
    if use_docker and pull_image(_ANSIBLE_IMAGE):
        docker_used = True
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            shutil.copy2(playbook_path, tmp_path / "playbook.yml")
            if inventory_path and inventory_path.exists():
                shutil.copy2(inventory_path, tmp_path / "inventory.ini")
            else:
                (tmp_path / "inventory.ini").write_text(
                    "[all]\nlocalhost ansible_connection=local\n", encoding="utf-8", newline=""
                )
            rc, out, err = docker_run(
                _ANSIBLE_IMAGE, tmp, [], "sh", "-c",
                "ansible-galaxy collection install community.postgresql community.general "
                "--ignore-errors -q 2>/dev/null; "
                "ansible-playbook --syntax-check -i inventory.ini playbook.yml",
                timeout=180,
            )
            combined = (out + err).strip()
            if rc == 0:
                findings.append({"severity": "pass", "check": "ansible_syntax",
                                  "message": "Playbook syntax check passed"})
            elif "ERROR!" in combined or "error" in combined.lower():
                findings.append({"severity": "fail", "check": "ansible_syntax",
                                  "message": combined[:500]})
                gate = "FAIL"
            else:
                findings.append({"severity": "warn", "check": "ansible_syntax",
                                  "message": combined[:300] or "Syntax check returned non-zero"})
    else:
        # Fallback: YAML parse
        try:
            docs = list(_yaml.safe_load_all(playbook_path.read_text(encoding="utf-8")))
            findings.append({"severity": "pass", "check": "yaml_syntax",
                              "message": f"{playbook_path.name}: valid YAML ({len(docs)} doc(s))"})
        except _yaml.YAMLError as e:
            findings.append({"severity": "fail", "check": "yaml_syntax",
                              "message": f"YAML error: {e}"})
            gate = "FAIL"
        if not use_docker:
            findings.append({"severity": "info", "check": "docker_unavailable",
                              "message": "Docker not available — full ansible syntax-check skipped"})

    if any(f["severity"] == "fail" for f in findings):
        gate = "FAIL"

    lines = [
        f"# Ansible Execution Report — {canvas.upper()}",
        f"**Generated:** {ts}  ",
        f"**Canvas:** {canvas}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Docker:** {'Yes — ' + _ANSIBLE_IMAGE if docker_used else 'No (fallback)'}  ",
        f"**Playbook:** {playbook_path.name}",
        "",
    ]
    for grp, label in [
        ([f for f in findings if f["severity"] == "fail"], "✗ Failures"),
        ([f for f in findings if f["severity"] == "warn"], "⚠ Warnings"),
        ([f for f in findings if f["severity"] == "pass"], "✓ Passed"),
    ]:
        if grp:
            lines.append(f"## {label}")
            for f in grp:
                lines.append(f"- **[{f['check']}]** {f['message'][:300]}")
            lines.append("")

    report_path = out_dir / f"ansible_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")
    root = Path(__file__).resolve().parents[3]
    return {
        "gate": gate, "canvas": canvas, "docker_used": docker_used,
        "findings": findings,
        "report_path": report_path.relative_to(root).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Ansible Executor (shared)")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.run_id, args.project_id, args.canvas)
        gate = result["gate"]
        output = {
            "status": "success" if gate == "PASS" else "failed",
            "gate": gate, "canvas": result["canvas"],
            "docker_used": result["docker_used"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [{"name": "Ansible Execution Report",
                           "path": result["report_path"], "type": "md"}],
        }
        print(json.dumps(output))
        sys.exit(0 if gate == "PASS" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
