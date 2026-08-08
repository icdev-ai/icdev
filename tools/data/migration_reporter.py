"""Migration Reporter — DDC Workflow Step 8.

Aggregates all step results and artifacts from the run into a final
post-migration report. Gate: PASS if all prior required steps passed.
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ddc"

_STEP_ICONS = {
    "success": "✓",
    "approved": "✓",
    "failed": "✗",
    "rejected": "✗",
    "timeout": "⏱",
    "skipped": "—",
    "awaiting_approval": "⏳",
}


def _get_run_steps(run_id: str) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT step_name, status, duration_ms, stdout, stderr "
                "FROM studio_workflow_run_steps WHERE run_id = %s ORDER BY started_at",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _get_all_artifacts(steps: list[dict]) -> list[dict]:
    all_arts: list[dict] = []
    seen = set()
    for s in steps:
        try:
            stdout = s.get("stdout") or ""
            if not stdout:
                continue
            data = json.loads(stdout)
            for a in data.get("artifacts", []):
                key = a.get("path", a.get("name", ""))
                if key and key not in seen:
                    seen.add(key)
                    a["from_step"] = s["step_name"]
                    all_arts.append(a)
        except (json.JSONDecodeError, AttributeError):
            pass
    return all_arts


def build_report(run_id: str, project_id: str) -> dict:
    steps = _get_run_steps(run_id)
    artifacts = _get_all_artifacts(steps)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    uid = uuid.uuid4().hex[:8]

    # Gate: FAIL if any required step failed/rejected/timeout
    failed_steps = [s for s in steps if s["status"] in ("failed", "rejected", "timeout")]
    gate = "FAIL" if failed_steps else "PASS"

    total_ms = sum(s.get("duration_ms") or 0 for s in steps)
    duration_s = f"{total_ms / 1000:.1f}s"

    # Count step outcomes
    counts = {}
    for s in steps:
        counts[s["status"]] = counts.get(s["status"], 0) + 1

    lines = [
        "# Migration Completion Report",
        f"**Generated:** {ts}  ",
        f"**Run ID:** `{run_id}`  ",
        f"**Project:** {project_id}  ",
        f"**Gate:** {'✓ PASS — Migration validated successfully' if gate == 'PASS' else '✗ FAIL — Review failures below'}  ",
        f"**Total duration:** {duration_s}",
        "",
        "## Pipeline Summary",
        "",
        "| Step | Status | Duration |",
        "|------|--------|----------|",
    ]
    for s in steps:
        icon = _STEP_ICONS.get(s["status"], "?")
        dur = f"{(s.get('duration_ms') or 0) / 1000:.1f}s"
        lines.append(f"| {s['step_name']} | {icon} {s['status']} | {dur} |")

    lines += [
        "",
        f"**Steps:** {len(steps)} total — "
        f"{counts.get('success', 0) + counts.get('approved', 0)} passed, "
        f"{counts.get('failed', 0) + counts.get('rejected', 0)} failed, "
        f"{counts.get('skipped', 0)} skipped",
        "",
    ]

    # Artifacts section
    if artifacts:
        lines += [
            "## Generated Artifacts",
            "",
            f"**Total:** {len(artifacts)} artifacts across {len(steps)} steps",
            "",
        ]
        by_step: dict[str, list] = {}
        for a in artifacts:
            step = a.get("from_step", "Unknown")
            by_step.setdefault(step, []).append(a)
        for step_name, arts in by_step.items():
            lines.append(f"### {step_name}")
            for a in arts:
                lines.append(f"- `{a['name']}` ([{a['type']}]({a['path']}))")
            lines.append("")

    # Failures detail
    if failed_steps:
        lines += ["## ✗ Failed Steps", ""]
        for s in failed_steps:
            lines.append(f"### {s['step_name']} ({s['status']})")
            if s.get("stderr"):
                lines.append(f"```\n{s['stderr'][:500]}\n```")
            lines.append("")

    # Next steps
    if gate == "PASS":
        lines += [
            "## Next Steps",
            "",
            "All automated checks passed. To apply to production:",
            "",
            "1. Review the Terraform Plan Report and confirm resource changes",
            "2. Set AWS credentials and run `terraform apply` from the generated workspace",
            "3. Run the Ansible playbook: `ansible-playbook -i inventory.ini playbook.yml`",
            "4. Execute the Data Validation Script with live AWS credentials",
            "5. Archive this report as ATO evidence",
            "",
        ]
    else:
        lines += [
            "## ⚠ Remediation Required",
            "",
            "Resolve the failed steps above before proceeding to production.",
            "",
        ]

    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _ARTIFACTS_DIR / f"migration_report_{uid}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8", newline="")

    return {
        "gate": gate,
        "steps": len(steps),
        "artifacts_collected": len(artifacts),
        "duration_s": duration_s,
        "report_path": report_path.relative_to(_ROOT).as_posix(),
    }


def main():
    parser = argparse.ArgumentParser(description="Migration Reporter")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = build_report(args.run_id, args.project_id)
        gate = result["gate"]
        output = {
            "status": "success" if gate == "PASS" else "failed",
            "gate": gate,
            "steps_summarized": result["steps"],
            "artifacts_collected": result["artifacts_collected"],
            "duration": result["duration_s"],
            "artifacts": [
                {"name": "Migration Completion Report",
                 "path": result["report_path"], "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if gate == "PASS" else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
