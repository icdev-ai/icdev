#!/usr/bin/env python3
# CUI // SP-CTI
"""Fitness Evaluator — wraps ICDEV™ tools into single-metric scorers (D-AR-7).

Each evaluator runs an existing ICDEV™ tool and normalizes the output
to a float in [0, 1]. Read-only, advisory-only (D110).

Usage:
    python tools/autoresearch/fitness_evaluator.py --evaluate compliance --project-id sparkpilot --json
    python tools/autoresearch/fitness_evaluator.py --evaluate code_quality --project-dir tools/ --json
    python tools/autoresearch/fitness_evaluator.py --evaluate-all --json
    python tools/autoresearch/fitness_evaluator.py --list-domains --json
    python tools/autoresearch/fitness_evaluator.py --health --json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.common.helpers import now_iso


def _run_tool(cmd: str, timeout: int = 120) -> dict:
    """Run an ICDEV™ tool via subprocess and parse JSON output."""
    try:
        result = subprocess.run(
            [sys.executable] + cmd.split(),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_ROOT),
            stdin=subprocess.DEVNULL,
            env=None,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr[:500]}
        # Parse JSON from stdout (handle markdown-wrapped JSON)
        stdout = result.stdout.strip()
        if stdout.startswith("```"):
            lines = stdout.split("\n")
            stdout = "\n".join(lines[1:-1])
        return {"success": True, "data": json.loads(stdout) if stdout else {}}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except (json.JSONDecodeError, Exception) as exc:
        return {"success": False, "error": str(exc)[:500]}


def _extract_metric(data: dict, metric_path: str) -> float:
    """Extract a metric value from nested dict using dot-path."""
    parts = metric_path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part, 0.0)
        else:
            return 0.0
    return float(current) if current is not None else 0.0


# ── Domain Evaluators ────────────────────────────────────────────────────────


def evaluate_compliance(project_id: str = "sparkpilot", **kwargs) -> dict:
    """Gate pass rate (0-1). Runs multi_regime_assessor."""
    result = _run_tool(f"tools/compliance/multi_regime_assessor.py --project-id {project_id} --json")
    if not result["success"]:
        return {
            "domain": "compliance",
            "metric_name": "gate_pass_rate",
            "metric_value": 0.0,
            "success": False,
            "error": result.get("error", "unknown"),
            "timestamp": now_iso(),
        }
    data = result["data"]
    # Extract pass rate from assessor output
    total = data.get("total_controls", 1)
    passed = data.get("satisfied", 0) + data.get("partially_satisfied", 0)
    value = min(1.0, passed / max(total, 1))
    return {
        "domain": "compliance",
        "metric_name": "gate_pass_rate",
        "metric_value": round(value, 4),
        "success": True,
        "details": {
            "total_controls": total,
            "satisfied": data.get("satisfied", 0),
            "project_id": project_id,
        },
        "timestamp": now_iso(),
    }


def evaluate_code_quality(project_dir: str = "tools/", **kwargs) -> dict:
    """Maintainability score (0-1). Runs code_analyzer."""
    result = _run_tool(f"tools/analysis/code_analyzer.py --project-dir {project_dir} --json")
    if not result["success"]:
        return {
            "domain": "code_quality",
            "metric_name": "maintainability_score",
            "metric_value": 0.0,
            "success": False,
            "error": result.get("error", "unknown"),
            "timestamp": now_iso(),
        }
    data = result["data"]
    value = data.get("avg_maintainability_score", 0.0)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            value = 0.0
    return {
        "domain": "code_quality",
        "metric_name": "maintainability_score",
        "metric_value": round(min(1.0, max(0.0, value)), 4),
        "success": True,
        "details": {
            "files_analyzed": data.get("files_analyzed", 0),
            "total_smells": data.get("total_smells", 0),
            "avg_complexity": data.get("avg_cyclomatic_complexity", 0),
        },
        "timestamp": now_iso(),
    }


def evaluate_security(project_dir: str = "tools/", **kwargs) -> dict:
    """1 - vulnerability_density (0-1). Runs sast_runner."""
    result = _run_tool(f"tools/security/sast_runner.py --project-dir {project_dir} --json")
    if not result["success"]:
        return {
            "domain": "security",
            "metric_name": "inverse_vulnerability_density",
            "metric_value": 0.0,
            "success": False,
            "error": result.get("error", "unknown"),
            "timestamp": now_iso(),
        }
    data = result["data"]
    total_findings = data.get("total_findings", 0)
    files_scanned = data.get("files_scanned", 1)
    density = total_findings / max(files_scanned, 1)
    value = max(0.0, 1.0 - min(1.0, density / 10.0))
    return {
        "domain": "security",
        "metric_name": "inverse_vulnerability_density",
        "metric_value": round(value, 4),
        "success": True,
        "details": {
            "total_findings": total_findings,
            "files_scanned": files_scanned,
            "density": round(density, 4),
        },
        "timestamp": now_iso(),
    }


def evaluate_rag(query_set: list = None, **kwargs) -> dict:
    """Avg retrieval relevance@5 (0-1). Runs RAG evaluator."""
    result = _run_tool("tools/rag/evaluator.py --project-id sparkpilot --json")
    if not result["success"]:
        return {
            "domain": "rag_quality",
            "metric_name": "retrieval_relevance_at_5",
            "metric_value": 0.0,
            "success": False,
            "error": result.get("error", "unknown"),
            "timestamp": now_iso(),
        }
    data = result["data"]
    value = _extract_metric(data, "metrics.ndcg_at_k")
    return {
        "domain": "rag_quality",
        "metric_name": "retrieval_relevance_at_5",
        "metric_value": round(min(1.0, max(0.0, value)), 4),
        "success": True,
        "details": data.get("metrics", {}),
        "timestamp": now_iso(),
    }


def evaluate_pulse(**kwargs) -> dict:
    """WriteGuard score (0-1). Runs quality_checker on latest post."""
    result = _run_tool("tools/pulse/engine/quality_checker.py --latest --json")
    if not result["success"]:
        return {
            "domain": "pulse_quality",
            "metric_name": "writeguard_score",
            "metric_value": 0.0,
            "success": False,
            "error": result.get("error", "unknown"),
            "timestamp": now_iso(),
        }
    data = result["data"]
    value = data.get("overall_score", 0.0)
    if isinstance(value, (int, float)):
        value = value / 100.0 if value > 1.0 else value
    return {
        "domain": "pulse_quality",
        "metric_name": "writeguard_score",
        "metric_value": round(min(1.0, max(0.0, float(value))), 4),
        "success": True,
        "details": {
            "grammar_score": data.get("grammar_score"),
            "readability_score": data.get("readability_score"),
            "tone_score": data.get("tone_score"),
        },
        "timestamp": now_iso(),
    }


def evaluate_skill(skill_name: str = None, assertions: list = None, **kwargs) -> dict:
    """Assertion pass rate (0-1). Stub — requires skill-specific assertions."""
    if not assertions:
        return {
            "domain": "skill_quality",
            "metric_name": "assertion_pass_rate",
            "metric_value": 0.5,
            "success": True,
            "placeholder_metrics": True,
            "heuristic": True,
            "details": {
                "note": "No assertions provided — returning constant 0.5 placeholder, not a measured pass rate.",
            },
            "timestamp": now_iso(),
        }
    passed = 0
    for assertion in assertions:
        result = _run_tool(assertion, timeout=60)
        if result["success"]:
            passed += 1
    value = passed / max(len(assertions), 1)
    return {
        "domain": "skill_quality",
        "metric_name": "assertion_pass_rate",
        "metric_value": round(value, 4),
        "success": True,
        "details": {
            "total_assertions": len(assertions),
            "passed": passed,
            "failed": len(assertions) - passed,
        },
        "timestamp": now_iso(),
    }


def evaluate_proposal_quality(project_id: str = None, **kwargs) -> dict:
    """Proposal quality score (0-1). Queries quality scores + win/loss records."""
    from tools.autoresearch.fitness_proposal_quality import evaluate as _pq_evaluate

    return _pq_evaluate(project_id=project_id, **kwargs)


def evaluate_marketplace_asset_quality(**kwargs) -> dict:
    """Marketplace asset quality composite score (0-1).

    Aggregates: scan pass rate, avg rating, installation count, freshness.
    """
    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            # Scan pass rate
            total_scans = conn.execute("SELECT COUNT(*) as cnt FROM marketplace_scan_results").fetchone()
            passed_scans = conn.execute(
                "SELECT COUNT(*) as cnt FROM marketplace_scan_results WHERE status = 'pass'"
            ).fetchone()
            total_s = total_scans["cnt"] if total_scans else 0
            passed_s = passed_scans["cnt"] if passed_scans else 0
            scan_rate = passed_s / max(total_s, 1)

            # Avg rating
            avg_rating = conn.execute("SELECT AVG(rating) as avg_r FROM marketplace_ratings").fetchone()
            rating = (avg_rating["avg_r"] or 0) / 5.0  # Normalize 0-5 to 0-1

            # Published assets count (higher = healthier ecosystem)
            published = conn.execute(
                "SELECT COUNT(*) as cnt FROM marketplace_assets WHERE status = 'published'"
            ).fetchone()
            pub_count = published["cnt"] if published else 0
            pub_score = min(1.0, pub_count / 50.0)  # Saturates at 50 assets

            # Composite: scan_rate(0.3) + rating(0.3) + pub_health(0.2) + freshness(0.2)
            value = 0.3 * scan_rate + 0.3 * rating + 0.2 * pub_score + 0.2 * 0.5

        return {
            "domain": "marketplace_asset_quality",
            "metric_name": "marketplace_quality_score",
            "metric_value": round(min(1.0, max(0.0, value)), 4),
            "success": True,
            "details": {
                "scan_pass_rate": round(scan_rate, 4),
                "avg_rating_normalized": round(rating, 4),
                "published_assets": pub_count,
                "pub_health_score": round(pub_score, 4),
            },
            "timestamp": now_iso(),
        }
    except Exception as exc:
        return {
            "domain": "marketplace_asset_quality",
            "metric_name": "marketplace_quality_score",
            "metric_value": 0.0,
            "success": False,
            "error": str(exc)[:200],
            "timestamp": now_iso(),
        }


# ── Registry ─────────────────────────────────────────────────────────────────

EVALUATORS = {
    "compliance": evaluate_compliance,
    "code_quality": evaluate_code_quality,
    "security": evaluate_security,
    "rag_quality": evaluate_rag,
    "pulse_quality": evaluate_pulse,
    "skill_quality": evaluate_skill,
    "marketplace_asset_quality": evaluate_marketplace_asset_quality,
    "proposal_quality": evaluate_proposal_quality,
}


def evaluate(domain: str, **kwargs) -> dict:
    """Dispatch to domain-specific evaluator."""
    if domain not in EVALUATORS:
        return {
            "domain": domain,
            "metric_name": "unknown",
            "metric_value": 0.0,
            "success": False,
            "error": f"Unknown domain: {domain}. Available: {list(EVALUATORS.keys())}",
            "timestamp": now_iso(),
        }
    return EVALUATORS[domain](**kwargs)


def evaluate_all(**kwargs) -> dict:
    """Evaluate all domains."""
    results = {}
    for domain in EVALUATORS:
        results[domain] = evaluate(domain, **kwargs)
    return {
        "domains_evaluated": len(results),
        "results": results,
        "timestamp": now_iso(),
    }


_DOMAIN_METRIC_NAMES: dict[str, str] = {
    "compliance": "gate_pass_rate",
    "code_quality": "maintainability_score",
    "security": "inverse_vulnerability_density",
    "rag_quality": "retrieval_relevance_at_5",
    "pulse_quality": "writeguard_score",
    "skill_quality": "assertion_pass_rate",
    "marketplace_asset_quality": "marketplace_quality_score",
    "proposal_quality": "proposal_quality_score",
}


def list_domains() -> dict:
    """List available evaluation domains without invoking heavy tool pipelines."""
    return {
        "domains": [
            {"name": name, "metric_name": _DOMAIN_METRIC_NAMES.get(name, "unknown")}
            for name in EVALUATORS
        ],
        "count": len(EVALUATORS),
    }


def health_check() -> dict:
    """Health check for fitness evaluator."""
    return {
        "status": "healthy",
        "domains_available": len(EVALUATORS),
        "domain_names": list(EVALUATORS.keys()),
        "timestamp": now_iso(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Fitness Evaluator — single-metric scorers")
    parser.add_argument("--evaluate", type=str, help="Domain to evaluate")
    parser.add_argument("--evaluate-all", action="store_true", help="Evaluate all domains")
    parser.add_argument("--list-domains", action="store_true", help="List available domains")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--project-id", type=str, default="sparkpilot")
    parser.add_argument("--project-dir", type=str, default="tools/")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
    elif args.list_domains:
        result = list_domains()
    elif args.evaluate_all:
        result = evaluate_all(project_id=args.project_id, project_dir=args.project_dir)
    elif args.evaluate:
        result = evaluate(
            args.evaluate,
            project_id=args.project_id,
            project_dir=args.project_dir,
        )
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
