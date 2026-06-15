"""Model Scanner — AIMC Workflow Step 1.

Scans ML model inventory and reports governance/MLOps gaps.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "aimc"

_DEFAULT_INVENTORY = {
    "model_count": 4,
    "frameworks": ["PyTorch", "sklearn"],
    "deployment_targets": ["SageMaker", "Lambda"],
    "drift_detection_enabled": False,
    "model_registry_present": False,
    "ab_testing_enabled": False,
    "monitoring_enabled": False,
    "rollback_capability": False,
    "model_card_present": False,
    "bias_testing_done": False,
}

_MLOPS_CONTROLS = [
    ("model_registry_present", "Model Registry", "Central registry tracking all model versions and metadata"),
    ("ab_testing_enabled", "A/B Testing", "Controlled traffic splitting for model comparison"),
    ("monitoring_enabled", "Model Monitoring", "Real-time latency, accuracy, and drift monitoring"),
    ("rollback_capability", "Model Rollback", "Ability to revert to previous model version"),
    ("drift_detection_enabled", "Drift Detection", "Automated data/concept drift detection"),
    ("model_card_present", "Model Card", "Documented model capabilities, limitations, and bias metrics"),
    ("bias_testing_done", "Bias Testing", "Automated fairness and bias evaluation"),
]


def scan_models(project_id: str) -> dict:
    """Load ML model inventory from DB; fall back to defaults if absent."""
    inv = dict(_DEFAULT_INVENTORY)
    try:
        from tools.aimc.db.init_db import init_db as _init_aimc_db
        _init_aimc_db()
    except Exception:
        pass
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT metric_key, metric_value FROM aimc_models WHERE project_id = ? ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    bool_keys = {c[0] for c in _MLOPS_CONTROLS}
                    if key in bool_keys:
                        inv[key] = str(val).lower() in ("1", "true", "yes")
                    elif key == "model_count":
                        try:
                            inv[key] = int(float(val))
                        except (TypeError, ValueError):
                            pass
                    else:
                        inv[key] = val
    except Exception:
        pass  # table may not exist — use defaults

    missing_controls = []
    for key, label, desc in _MLOPS_CONTROLS:
        if not inv.get(key, False):
            missing_controls.append({"key": key, "label": label, "description": desc})

    # Governance score: 0–100
    total = len(_MLOPS_CONTROLS)
    missing_count = len(missing_controls)
    governance_score = round((1 - missing_count / total) * 100, 1)

    frameworks = inv.get("frameworks", [])
    if isinstance(frameworks, str):
        frameworks = [frameworks]
    deployment_targets = inv.get("deployment_targets", [])
    if isinstance(deployment_targets, str):
        deployment_targets = [deployment_targets]

    return {
        "inventory": inv,
        "frameworks": frameworks,
        "deployment_targets": deployment_targets,
        "missing_controls": missing_controls,
        "governance_score": governance_score,
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    inv = result["inventory"]
    missing = result["missing_controls"]
    score = result["governance_score"]
    project_id = result["project_id"]

    score_label = "MATURE" if score >= 80 else ("DEVELOPING" if score >= 50 else "INITIAL")

    lines = [
        "# ML Model Scan Report — AIMC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Model Governance Score:** {score}/100 ({score_label})",
        "",
        "## Model Inventory",
        "| Attribute | Value |",
        "|-----------|-------|",
        f"| Model Count | {inv.get('model_count', 0)} |",
        f"| Frameworks | {', '.join(result['frameworks'])} |",
        f"| Deployment Targets | {', '.join(result['deployment_targets'])} |",
        "",
        "## MLOps Controls",
        "| Control | Status |",
        "|---------|--------|",
    ]
    for key, label, _ in _MLOPS_CONTROLS:
        status = "PRESENT" if inv.get(key) else "MISSING"
        lines.append(f"| {label} | {status} |")

    lines += [""]
    if missing:
        lines += [
            "## Missing MLOps Controls",
            f"**{len(missing)} of {len(_MLOPS_CONTROLS)} controls not implemented:**",
            "",
        ]
        for ctrl in missing:
            lines.append(f"- **{ctrl['label']}** — {ctrl['description']}")
    else:
        lines += ["## All MLOps Controls Present", "", "Full MLOps maturity achieved."]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Model Scanner — AIMC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="aimc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_models(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"model_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8")

        output = {
            "status": "success",
            "governance_score": result["governance_score"],
            "model_count": result["inventory"].get("model_count", 0),
            "missing_controls": len(result["missing_controls"]),
            "artifacts": [
                {"name": "Model Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
