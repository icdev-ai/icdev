"""Governance Scanner — AADC Workflow Step 1.

Scans AI governance posture and reports missing controls.
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

_DEFAULT_POSTURE = {
    "agent_count": 3,
    "model_types": ["gpt-4", "llama3", "mistral"],
    "data_classification": "CUI",
    "compliance_frameworks": ["NIST-AI-RMF", "DoD-AI-Ethics"],
    "model_cards_present": False,
    "bias_testing_done": False,
    "explainability_enabled": False,
    "rate_limiting_enabled": True,
    "input_validation_enabled": True,
    "human_in_loop_enabled": False,
    "output_filtering_enabled": False,
    "model_versioning_enabled": True,
    "ai_decision_logging": False,
}

_REQUIRED_CONTROLS = [
    ("model_cards_present", "Model Cards", "Documentation of model capabilities, limitations, and bias metrics"),
    ("bias_testing_done", "Bias Testing", "Automated bias detection and fairness testing"),
    ("explainability_enabled", "Explainability", "Model decision explanation capability (XAI)"),
    ("rate_limiting_enabled", "Rate Limiting", "API rate limiting to prevent abuse"),
    ("input_validation_enabled", "Input Validation", "Sanitization of all agent inputs"),
    ("human_in_loop_enabled", "Human-in-the-Loop", "Human oversight for high-risk AI decisions"),
    ("output_filtering_enabled", "Output Filtering", "PII/harmful content filtering on agent outputs"),
    ("model_versioning_enabled", "Model Versioning", "Tracked model versions with rollback capability"),
    ("ai_decision_logging", "AI Decision Logging", "Audit log of all AI decisions"),
]


def scan_governance(project_id: str) -> dict:
    """Load AI governance posture from DB; fall back to defaults if absent."""
    posture = dict(_DEFAULT_POSTURE)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT check_key, check_value FROM aadc_governance WHERE project_id = ? ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    if key in {c[0] for c in _REQUIRED_CONTROLS}:
                        posture[key] = str(val).lower() in ("1", "true", "yes")
                    elif key == "agent_count":
                        try:
                            posture[key] = int(float(val))
                        except (TypeError, ValueError):
                            pass
                    else:
                        posture[key] = val
    except Exception:
        pass  # table may not exist — use defaults

    # Risk scoring: each missing required control adds risk points
    missing_controls = []
    for key, label, desc in _REQUIRED_CONTROLS:
        if not posture.get(key, False):
            missing_controls.append({"key": key, "label": label, "description": desc})

    # AI risk score: 0 (max risk) to 100 (no risk)
    total_controls = len(_REQUIRED_CONTROLS)
    missing_count = len(missing_controls)
    ai_risk_score = round((1 - missing_count / total_controls) * 100, 1)

    return {
        "posture": posture,
        "missing_controls": missing_controls,
        "ai_risk_score": ai_risk_score,
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    posture = result["posture"]
    missing = result["missing_controls"]
    risk_score = result["ai_risk_score"]
    project_id = result["project_id"]

    risk_label = "LOW" if risk_score >= 80 else ("MEDIUM" if risk_score >= 50 else "HIGH")
    model_types = posture.get("model_types", [])
    if isinstance(model_types, str):
        model_types = [model_types]

    lines = [
        "# AI Governance Scan Report — AADC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**AI Risk Score:** {risk_score}/100 ({risk_label} RISK)",
        "",
        "## AI System Inventory",
        "| Attribute | Value |",
        "|-----------|-------|",
        f"| Agent Count | {posture.get('agent_count', 0)} |",
        f"| Model Types | {', '.join(model_types)} |",
        f"| Data Classification | {posture.get('data_classification', 'Unknown')} |",
        f"| Compliance Frameworks | {', '.join(posture.get('compliance_frameworks', [])) if isinstance(posture.get('compliance_frameworks'), list) else posture.get('compliance_frameworks', '')} |",
        "",
        "## Governance Control Status",
        "| Control | Status |",
        "|---------|--------|",
    ]
    for key, label, _ in _REQUIRED_CONTROLS:
        status = "PRESENT" if posture.get(key) else "MISSING"
        lines.append(f"| {label} | {status} |")

    lines += [""]
    if missing:
        lines += [
            "## Missing Governance Controls",
            f"**{len(missing)} of {len(_REQUIRED_CONTROLS)} controls not implemented:**",
            "",
        ]
        for ctrl in missing:
            lines.append(f"- **{ctrl['label']}** — {ctrl['description']}")
        lines.append("")
    else:
        lines += ["## All Governance Controls Present", "", "Full AI governance posture achieved.", ""]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Governance Scanner — AADC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="aadc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_governance(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"governance_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8")

        output = {
            "status": "success",
            "ai_risk_score": result["ai_risk_score"],
            "missing_controls": len(result["missing_controls"]),
            "artifacts": [
                {"name": "Governance Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
