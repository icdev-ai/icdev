"""Compliance Checker — AADC Workflow Step 2.

Validates AI system against NIST AI RMF and DoD AI Ethics principles.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "aadc"

_DEFAULT_CHECKS = {
    "nist_rmf_govern": False,
    "nist_rmf_map": True,
    "nist_rmf_measure": False,
    "nist_rmf_manage": False,
    "dod_responsible_ai": True,
    "dod_equitable": False,
    "dod_traceable": False,
    "dod_reliable": True,
    "dod_governable": False,
    "human_in_loop_high_risk": False,
    "model_versioning": True,
    "ai_decision_logging": False,
    "input_sanitization": True,
    "output_filtering": False,
}

_NIST_CHECKS = [
    ("nist_rmf_govern", "NIST AI RMF — GOVERN", "AI governance structures and policies in place"),
    ("nist_rmf_map", "NIST AI RMF — MAP", "AI risk context established and mapped"),
    ("nist_rmf_measure", "NIST AI RMF — MEASURE", "AI risk metrics defined and tracked"),
    ("nist_rmf_manage", "NIST AI RMF — MANAGE", "AI risk response strategies implemented"),
]

_DOD_CHECKS = [
    ("dod_responsible_ai", "DoD AI Ethics — Responsible", "Human-centric responsible AI design"),
    ("dod_equitable", "DoD AI Ethics — Equitable", "Bias testing and fairness measures"),
    ("dod_traceable", "DoD AI Ethics — Traceable", "Explainable and auditable AI decisions"),
    ("dod_reliable", "DoD AI Ethics — Reliable", "Robust and reliable AI performance"),
    ("dod_governable", "DoD AI Ethics — Governable", "Human control and intervention capability"),
]

_OPERATIONAL_CHECKS = [
    ("human_in_loop_high_risk", "Human-in-the-Loop (high-risk)", "Human oversight for high-risk decisions"),
    ("model_versioning", "Model Versioning", "Tracked model versions with rollback"),
    ("ai_decision_logging", "AI Decision Logging", "Audit trail for all AI decisions"),
    ("input_sanitization", "Input Sanitization", "All agent inputs sanitized"),
    ("output_filtering", "Output Filtering", "PII/harmful content filtered from outputs"),
]


def run_compliance_checks(project_id: str) -> dict:
    """Load compliance check state from DB; fall back to defaults if absent."""
    checks = dict(_DEFAULT_CHECKS)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT check_key, check_value FROM aadc_compliance WHERE project_id = ? ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    checks[key] = str(val).lower() in ("1", "true", "yes")
    except Exception:
        pass  # table may not exist — use defaults

    findings = []
    all_check_groups = [
        ("nist_ai_rmf", _NIST_CHECKS),
        ("dod_ai_ethics", _DOD_CHECKS),
        ("operational", _OPERATIONAL_CHECKS),
    ]

    for group_name, group_checks in all_check_groups:
        for key, label, desc in group_checks:
            if not checks.get(key, False):
                severity = "fail" if key in (
                    "nist_rmf_govern", "human_in_loop_high_risk",
                    "ai_decision_logging", "input_sanitization",
                ) else "warn"
                findings.append({
                    "severity": severity,
                    "group": group_name,
                    "check": key,
                    "label": label,
                    "message": f"{label}: {desc} — NOT IMPLEMENTED",
                })

    gate = "PASS" if not any(f["severity"] == "fail" for f in findings) else "FAIL"
    return {"findings": findings, "checks": checks, "gate": gate, "project_id": project_id}


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    checks = result["checks"]
    gate = result["gate"]
    project_id = result["project_id"]

    fails = [f for f in findings if f["severity"] == "fail"]
    warns = [f for f in findings if f["severity"] == "warn"]

    def yn(key):
        return "PASS" if checks.get(key) else "FAIL"

    lines = [
        "# AI Compliance Report — AADC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Compliance Gate:** {'PASS' if gate == 'PASS' else 'FAIL'}",
        "",
        "## NIST AI RMF (4 Functions)",
        "| Function | Status |",
        "|----------|--------|",
    ]
    for key, label, _ in _NIST_CHECKS:
        lines.append(f"| {label} | {yn(key)} |")

    lines += ["", "## DoD AI Ethics Principles (5)", "| Principle | Status |", "|-----------|--------|"]
    for key, label, _ in _DOD_CHECKS:
        lines.append(f"| {label} | {yn(key)} |")

    lines += ["", "## Operational AI Controls", "| Control | Status |", "|---------|--------|"]
    for key, label, _ in _OPERATIONAL_CHECKS:
        lines.append(f"| {label} | {yn(key)} |")

    lines.append("")
    if fails:
        lines += ["## Failures (Blocking)"]
        for f in fails:
            lines.append(f"- FAIL [{f['check']}]: {f['message']}")
        lines.append("")
    if warns:
        lines += ["## Warnings"]
        for f in warns:
            lines.append(f"- WARN [{f['check']}]: {f['message']}")
        lines.append("")
    if not findings:
        lines += ["## Full Compliance Achieved", "", "All NIST AI RMF and DoD AI Ethics controls are implemented."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Compliance Checker — AADC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="aadc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_compliance_checks(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"compliance_report_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8")

        fails = [f for f in result["findings"] if f["severity"] == "fail"]
        output = {
            "status": "success" if not fails else "failed",
            "gate": result["gate"],
            "findings": len(result["findings"]),
            "failures": len(fails),
            "artifacts": [
                {"name": "Compliance Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0 if not fails else 1)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
