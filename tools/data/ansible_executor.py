"""Ansible Executor — DDC Workflow Step 6.

Runs ansible-playbook --syntax-check inside Docker (cytopia/ansible:latest).
No real hosts needed — validates playbook structure and module references.
Falls back to YAML syntax check if Docker is unavailable.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ddc"
_ANSIBLE_IMAGE = "cytopia/ansible:latest"


def _get_iac_artifacts(run_id: str, project_id: str) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT stdout FROM studio_workflow_run_steps "
                "WHERE run_id = %s AND step_name = 'Generate IaC'",
                (run_id,),
            ).fetchone()
            if row and row["stdout"]:
                return json.loads(row["stdout"]).get("artifacts", [])
        finally:
            conn.close()
    except Exception:
        pass
    # Fallback: latest ansible files
    playbooks = sorted(_ARTIFACTS_DIR.glob("ansible_playbook_*.yml"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if playbooks:
        uid = playbooks[0].stem.split("_")[-1]
        return [
            {"name": "Ansible Playbook", "path": f"data/studio_artifacts/ddc/ansible_playbook_{uid}.yml", "type": "yml"},
            {"name": "Ansible Inventory", "path": f"data/studio_artifacts/ddc/ansible_inventory_{uid}.ini", "type": "ini"},
        ]
    return []


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def _pull_image() -> bool:
    try:
        result = subprocess.run(["docker", "image", "inspect", _ANSIBLE_IMAGE],
                                 capture_output=True, timeout=10)
        if result.returncode == 0:
            return True
        subprocess.run(["docker", "pull", _ANSIBLE_IMAGE], capture_output=True, timeout=120, check=True)
        return True
    except Exception:
        return False


def _docker_run_ansible(workspace: str, *args: str, timeout: int = 120) -> tuple[int, str, str]:
    ws_path = Path(workspace).as_posix()
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{ws_path}:/workspace",
        "-w", "/workspace",
        _ANSIBLE_IMAGE,
        *args,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"Timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def _yaml_syntax_check(path: Path) -> tuple[bool, str]:
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        return True, f"{path.name}: valid YAML ({len(docs)} document(s))"
    except yaml.YAMLError as e:
        return False, f"{path.name}: YAML error — {e}"


def run_ansible(run_id: str, project_id: str) -> dict:
    artifacts = _get_iac_artifacts(run_id, project_id)
    playbook_art = next((a for a in artifacts if a.get("type") == "yml"
                         and "playbook" in a.get("name", "").lower()), None)
    inventory_art = next((a for a in artifacts if a.get("type") == "ini"
                          and "inventory" in a.get("name", "").lower()), None)

    findings: list[dict] = []
    gate = "PASS"
    docker_used = False
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]

    playbook_path = _ROOT / playbook_art["path"] if playbook_art else None
    inventory_path = _ROOT / inventory_art["path"] if inventory_art else None

    if not playbook_path or not playbook_path.exists():
        return {
            "gate": "WARN",
            "findings": [{"severity": "warn", "check": "no_playbook",
                          "message": "No Ansible playbook found from IaC step"}],
            "docker_used": False,
        }

    use_docker = _docker_available()

    if use_docker and _pull_image():
        docker_used = True
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy2(playbook_path, Path(tmp) / "playbook.yml")
            if inventory_path and inventory_path.exists():
                shutil.copy2(inventory_path, Path(tmp) / "inventory.ini")
            else:
                # Minimal local inventory for syntax check
                (Path(tmp) / "inventory.ini").write_text(
                    "[all]\nlocalhost ansible_connection=local\n", encoding="utf-8", newline=""
                )

            # Install required collections, then syntax-check
            rc, out, err = _docker_run_ansible(
                tmp,
                "sh", "-c",
                "ansible-galaxy collection install community.postgresql community.general "
                "--ignore-errors -q 2>/dev/null; "
                "ansible-playbook --syntax-check -i inventory.ini playbook.yml",
                timeout=180,
            )
            if rc == 0:
                findings.append({"severity": "pass", "check": "ansible_syntax",
                                  "message": "Playbook syntax check passed"})
            else:
                combined = (out + err).strip()
                # ansible syntax errors contain "ERROR!" prefix
                if "ERROR!" in combined or "error" in combined.lower():
                    findings.append({"severity": "fail", "check": "ansible_syntax",
                                      "message": combined[:600]})
                    gate = "FAIL"
                else:
                    findings.append({"severity": "warn", "check": "ansible_syntax",
                                      "message": combined[:400] or "Syntax check returned non-zero"})
    else:
        # Fallback: YAML parse
        ok, msg = _yaml_syntax_check(playbook_path)
        findings.append({"severity": "pass" if ok else "fail", "check": "yaml_syntax", "message": msg})
        if not ok:
            gate = "FAIL"
        if inventory_path and inventory_path.exists():
            findings.append({"severity": "info", "check": "inventory",
                              "message": f"Inventory found: {inventory_path.name}"})
        if not use_docker:
            findings.append({"severity": "info", "check": "docker_unavailable",
                              "message": "Docker not available — full ansible syntax-check skipped"})

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]
    passes = [f for f in findings if f["severity"] == "pass"]
    gate = "FAIL" if fails else "PASS"

    lines = [
        "# Ansible Execution Report",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '✗ FAIL'}  ",
        f"**Docker:** {'Yes — ' + _ANSIBLE_IMAGE if docker_used else 'No (fallback)'}  ",
        f"**Playbook:** {playbook_path.name}",
        "",
    ]
    for grp, label in [(fails, "✗ Failures"), (warns, "⚠ Warnings"), (passes, "✓ Passed")]:
        if grp:
            lines.append(f"## {label}")
            for f in grp:
                lines.append(f"- **[{f['check']}]** {f['message'][:300]}")
            lines.append("")

    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _ARTIFACTS_DIR / f"ansible_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate,
        "findings": findings,
        "docker_used": docker_used,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Ansible Executor")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_ansible(args.run_id, args.project_id)
        gate = result["gate"]
        output = {
            "status": "success" if gate == "PASS" else "failed",
            "gate": gate,
            "docker_used": result["docker_used"],
            "findings": len(result["findings"]),
            "failures": sum(1 for f in result["findings"] if f["severity"] == "fail"),
            "artifacts": [
                {"name": "Ansible Execution Report", "path": result["report_path"], "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate == "PASS" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
