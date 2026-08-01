# CUI // SP-CTI
"""Model Scanner — AIMC Workflow Step 1.

Scans ML model inventory and reports governance/MLOps gaps.
Outputs JSON with artifact paths to stdout.

Reads the tenant-less ``aimc_models`` table via ``get_canvas_connection`` (RLS
disabled) — matching the connection policy documented in ``db/init_db.py``.  On a
read failure or an empty inventory the scanner returns an explicit ``no-data``
result instead of fabricating demo inventory values.
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

_MLOPS_CONTROLS = [
    ("model_registry_present", "Model Registry", "Central registry tracking all model versions and metadata"),
    ("ab_testing_enabled", "A/B Testing", "Controlled traffic splitting for model comparison"),
    ("monitoring_enabled", "Model Monitoring", "Real-time latency, accuracy, and drift monitoring"),
    ("rollback_capability", "Model Rollback", "Ability to revert to previous model version"),
    ("drift_detection_enabled", "Drift Detection", "Automated data/concept drift detection"),
    ("model_card_present", "Model Card", "Documented model capabilities, limitations, and bias metrics"),
    ("bias_testing_done", "Bias Testing", "Automated fairness and bias evaluation"),
]

# Honest baseline: a control is "not present" until the loaded inventory proves
# otherwise.  This is NOT fabricated data — it is the absence-implies-gap default
# applied only after real rows have been read from the DB.
_CONTROL_KEYS = [c[0] for c in _MLOPS_CONTROLS]


def _load_inventory_rows(project_id: str):
    """Fetch raw inventory rows via the canvas (RLS-disabled) connection."""
    from tools.db.storage import get_canvas_connection
    with get_canvas_connection("AIMC_DB_URL") as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT metric_key, metric_value FROM aimc_models "
            "WHERE project_id = %s ORDER BY created_at DESC LIMIT 30",
            (project_id,),
        )
        return cur.fetchall()


def scan_models(project_id: str) -> dict:
    """Load ML model inventory from DB via the canvas (RLS-disabled) connection.

    Returns a ``status: success`` result when real inventory rows exist, or a
    ``status: no-data`` result on read failure / empty inventory.  Never returns
    fabricated demo values.
    """
    try:
        from tools.aimc.db.init_db import init_db as _init_aimc_db
        _init_aimc_db()
    except Exception:
        pass

    try:
        rows = _load_inventory_rows(project_id)
    except Exception as exc:
        return {
            "status": "no-data",
            "reason": "read-error",
            "detail": str(exc),
            "project_id": project_id,
        }

    if not rows:
        return {
            "status": "no-data",
            "reason": "empty-inventory",
            "detail": "No aimc_models rows for this project — inventory not recorded.",
            "project_id": project_id,
        }

    inv = {key: False for key in _CONTROL_KEYS}
    inv["model_count"] = 0
    inv["frameworks"] = []
    inv["deployment_targets"] = []

    bool_keys = set(_CONTROL_KEYS)
    for row in rows:
        key, val = row[0], row[1]
        if key in bool_keys:
            inv[key] = str(val).lower() in ("1", "true", "yes")
        elif key == "model_count":
            try:
                inv[key] = int(float(val))
            except (TypeError, ValueError):
                pass
        else:
            inv[key] = val

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
        "status": "success",
        "inventory": inv,
        "frameworks": frameworks,
        "deployment_targets": deployment_targets,
        "missing_controls": missing_controls,
        "governance_score": governance_score,
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    project_id = result.get("project_id", "unknown")

    if result.get("status") == "no-data":
        return "\n".join([
            "# ML Model Scan Report — AIMC",
            f"**Generated:** {ts}  ",
            f"**Project:** {project_id}  ",
            "**Model Governance Score:** N/A (no inventory recorded)",
            "",
            "## No Data",
            "",
            f"No ML model inventory is recorded for project `{project_id}` "
            f"(reason: {result.get('reason', 'unknown')}).",
            "",
            "No governance score or MLOps control assessment can be produced until "
            "model inventory metrics are written to `aimc_models`. This report "
            "intentionally reports no data rather than placeholder values.",
        ])

    inv = result["inventory"]
    missing = result["missing_controls"]
    score = result["governance_score"]

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

        artifacts = [
            {"name": "Model Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
        ]

        if result.get("status") == "no-data":
            output = {
                "status": "no-data",
                "reason": result.get("reason"),
                "project_id": result.get("project_id"),
                "artifacts": artifacts,
            }
        else:
            output = {
                "status": "success",
                "governance_score": result["governance_score"],
                "model_count": result["inventory"].get("model_count", 0),
                "missing_controls": len(result["missing_controls"]),
                "artifacts": artifacts,
            }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
