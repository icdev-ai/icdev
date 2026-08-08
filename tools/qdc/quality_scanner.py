"""Quality Scanner — QDC Workflow Step 1.

Scans code quality design metrics and produces a UQS (Unified Quality Score) report.
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

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "qdc"

_DEFAULT_METRICS = {
    "code_coverage_pct": 72.0,
    "complexity_score": 8.5,
    "tech_debt_hours": 42.0,
    "dependency_vulnerabilities": 3,
    "lines_of_code": 15000,
    "duplication_pct": 4.2,
}

_DIMENSION_WEIGHTS = {
    "code_quality": 0.25,
    "security_posture": 0.25,
    "test_coverage": 0.20,
    "runtime_health": 0.15,
    "compliance_readiness": 0.15,
}


def _score_dimension(metrics: dict) -> dict:
    """Compute 0–100 score for each UQS dimension."""
    cov = metrics.get("code_coverage_pct", 72.0)
    complexity = metrics.get("complexity_score", 8.5)
    debt = metrics.get("tech_debt_hours", 42.0)
    vulns = metrics.get("dependency_vulnerabilities", 3)
    duplication = metrics.get("duplication_pct", 4.2)

    # code_quality: penalise complexity and duplication
    cq = max(0.0, 100 - (complexity - 5) * 5 - duplication * 3)

    # security_posture: penalise vulnerabilities
    sp = max(0.0, 100 - vulns * 15)

    # test_coverage: directly maps
    tc = min(100.0, cov)

    # runtime_health: proxy from tech debt
    rh = max(0.0, 100 - debt * 0.5)

    # compliance_readiness: coverage + low vulns
    cr = (tc * 0.5 + sp * 0.5)

    return {
        "code_quality": round(cq, 1),
        "security_posture": round(sp, 1),
        "test_coverage": round(tc, 1),
        "runtime_health": round(rh, 1),
        "compliance_readiness": round(cr, 1),
    }


def _overall_uqs(dimensions: dict) -> float:
    total = sum(dimensions[d] * _DIMENSION_WEIGHTS[d] for d in _DIMENSION_WEIGHTS)
    return round(total, 1)


def scan_quality(project_id: str) -> dict:
    """Load quality metrics from DB; fall back to defaults if table/data absent."""
    metrics = dict(_DEFAULT_METRICS)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT metric_key, metric_value FROM qdc_metrics WHERE project_id = %s ORDER BY created_at DESC LIMIT 20",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    try:
                        metrics[key] = float(val)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass  # table may not exist — use defaults

    dimensions = _score_dimension(metrics)
    uqs = _overall_uqs(dimensions)
    return {"metrics": metrics, "dimensions": dimensions, "uqs": uqs, "project_id": project_id}


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    m = result["metrics"]
    dim = result["dimensions"]
    uqs = result["uqs"]
    project_id = result["project_id"]

    gate_label = "RELEASE" if uqs >= 85 else ("DEPLOY" if uqs >= 80 else ("MERGE" if uqs >= 70 else "BLOCKED"))

    lines = [
        "# Quality Scan Report — QDC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Overall UQS:** {uqs}/100  ",
        f"**Gate Status:** {gate_label}",
        "",
        "## Raw Metrics",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Code Coverage | {m.get('code_coverage_pct', 'N/A')}% |",
        f"| Cyclomatic Complexity (avg) | {m.get('complexity_score', 'N/A')} |",
        f"| Tech Debt | {m.get('tech_debt_hours', 'N/A')} hours |",
        f"| Dependency Vulnerabilities | {m.get('dependency_vulnerabilities', 'N/A')} |",
        f"| Lines of Code | {m.get('lines_of_code', 'N/A')} |",
        f"| Code Duplication | {m.get('duplication_pct', 'N/A')}% |",
        "",
        "## 5-Dimension UQS Scores",
        "| Dimension | Score | Weight | Weighted |",
        "|-----------|-------|--------|---------|",
    ]
    for d, w in _DIMENSION_WEIGHTS.items():
        score = dim[d]
        lines.append(f"| {d.replace('_', ' ').title()} | {score} | {int(w*100)}% | {round(score*w, 1)} |")
    lines += [
        "",
        f"**Total UQS: {uqs}/100**",
        "",
        "## Gate Thresholds",
        "| Gate | Minimum UQS | Status |",
        "|------|-------------|--------|",
        f"| Merge  | 70 | {'PASS' if uqs >= 70 else 'FAIL'} |",
        f"| Deploy | 80 | {'PASS' if uqs >= 80 else 'FAIL'} |",
        f"| Release| 85 | {'PASS' if uqs >= 85 else 'FAIL'} |",
        "",
    ]

    issues = []
    if m.get("code_coverage_pct", 100) < 80:
        issues.append(f"Coverage {m.get('code_coverage_pct')}% is below the 80% minimum threshold")
    if m.get("complexity_score", 0) > 10:
        issues.append(f"Cyclomatic complexity {m.get('complexity_score')} exceeds threshold of 10")
    if m.get("dependency_vulnerabilities", 0) > 0:
        issues.append(f"{m.get('dependency_vulnerabilities')} dependency vulnerability/vulnerabilities detected — run dependency audit")
    if not issues:
        issues.append("No critical quality issues detected")

    lines.append("## Issues")
    for issue in issues:
        prefix = "- WARNING:" if "below" in issue or "exceeds" in issue or "vulnerability" in issue else "- OK:"
        lines.append(f"{prefix} {issue}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Quality Scanner — QDC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="qdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_quality(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"quality_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        output = {
            "status": "success",
            "uqs": result["uqs"],
            "dimensions": result["dimensions"],
            "artifacts": [
                {"name": "Quality Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
