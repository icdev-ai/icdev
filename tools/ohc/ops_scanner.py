"""Ops Scanner — OHC Workflow Step 1.

Scans operational health metrics and reports AIOps maturity gaps.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "ohc"

_DEFAULT_OPS_METRICS = {
    "runbook_count": 8,
    "incident_mttr_minutes": 47.0,
    "alert_fatigue_score": 6.2,
    "automation_coverage_pct": 34.0,
    "total_alerts_per_day": 120,
    "noise_alerts_pct": 45.0,
    "on_call_rotation_configured": True,
    "slo_tracking_enabled": False,
    "chaos_engineering_enabled": False,
    "auto_remediation_enabled": False,
    "aiops_platform_enabled": False,
}

_AIOPS_CONTROLS = [
    ("auto_remediation_enabled", "Auto-Remediation", "Automated incident response and self-healing"),
    ("chaos_engineering_enabled", "Chaos Engineering", "Proactive resilience testing via fault injection"),
    ("slo_tracking_enabled", "SLO Tracking", "Service Level Objective monitoring and alerting"),
    ("on_call_rotation_configured", "On-Call Rotation", "Defined and configured on-call rotation"),
    ("aiops_platform_enabled", "AIOps Platform", "AI-driven anomaly detection and root cause analysis"),
]


def scan_ops(project_id: str) -> dict:
    """Load operational health metrics from DB; fall back to defaults if absent."""
    metrics = dict(_DEFAULT_OPS_METRICS)
    try:
        from tools.db.storage import get_canvas_connection
        with get_canvas_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT metric_key, metric_value FROM ohc_ops_metrics WHERE project_id = %s ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                bool_keys = {c[0] for c in _AIOPS_CONTROLS}
                for row in rows:
                    key, val = row[0], row[1]
                    if key in bool_keys:
                        metrics[key] = str(val).lower() in ("1", "true", "yes")
                    else:
                        try:
                            metrics[key] = float(val)
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass  # table may not exist — use defaults

    missing_controls = []
    for key, label, desc in _AIOPS_CONTROLS:
        if not metrics.get(key, False):
            missing_controls.append({"key": key, "label": label, "description": desc})

    # AIOps maturity score: combination of metrics + controls
    control_score = (1 - len(missing_controls) / len(_AIOPS_CONTROLS)) * 50

    # Automation component
    automation_pct = metrics.get("automation_coverage_pct", 0)
    automation_score = min(50.0, automation_pct * 0.5)

    aiops_maturity_score = round(control_score + automation_score, 1)

    maturity_level = "OPTIMIZING" if aiops_maturity_score >= 80 else (
        "MANAGING" if aiops_maturity_score >= 60 else (
            "DEFINING" if aiops_maturity_score >= 40 else "INITIAL"
        )
    )

    return {
        "metrics": metrics,
        "missing_controls": missing_controls,
        "aiops_maturity_score": aiops_maturity_score,
        "maturity_level": maturity_level,
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    m = result["metrics"]
    missing = result["missing_controls"]
    score = result["aiops_maturity_score"]
    level = result["maturity_level"]
    project_id = result["project_id"]

    lines = [
        "# Ops Health Scan Report — OHC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**AIOps Maturity Score:** {score}/100 ({level})",
        "",
        "## Operational Metrics",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Runbook Count | {m.get('runbook_count', 0)} |",
        f"| Incident MTTR | {m.get('incident_mttr_minutes', 0)} min |",
        f"| Alert Fatigue Score | {m.get('alert_fatigue_score', 0)}/10 |",
        f"| Automation Coverage | {m.get('automation_coverage_pct', 0)}% |",
        f"| Alerts per Day | {m.get('total_alerts_per_day', 0)} |",
        f"| Noise Alerts | {m.get('noise_alerts_pct', 0)}% |",
        "",
        "## AIOps Controls",
        "| Control | Status |",
        "|---------|--------|",
    ]
    for key, label, _ in _AIOPS_CONTROLS:
        status = "PRESENT" if m.get(key) else "MISSING"
        lines.append(f"| {label} | {status} |")

    lines += [""]
    if missing:
        lines += [
            "## Missing Ops Controls",
            f"**{len(missing)} of {len(_AIOPS_CONTROLS)} controls not implemented:**",
            "",
        ]
        for ctrl in missing:
            lines.append(f"- **{ctrl['label']}** — {ctrl['description']}")
    else:
        lines += ["## All AIOps Controls Present", "", "Full AIOps maturity controls achieved."]

    # Flag high alert fatigue
    if m.get("alert_fatigue_score", 0) > 7:
        lines += ["", f"WARNING: Alert fatigue score {m.get('alert_fatigue_score')}/10 is critically high — reduce noise alerts"]
    if m.get("incident_mttr_minutes", 0) > 60:
        lines += [f"WARNING: MTTR {m.get('incident_mttr_minutes')} min exceeds 60-min target — improve runbook automation"]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Ops Scanner — OHC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="ohc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_ops(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"ops_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        output = {
            "status": "success",
            "aiops_maturity_score": result["aiops_maturity_score"],
            "maturity_level": result["maturity_level"],
            "missing_controls": len(result["missing_controls"]),
            "artifacts": [
                {"name": "Ops Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
