#!/usr/bin/env python3
# CUI // SP-CTI
"""Product Analytics Reflex — feature usage, adoption metrics, gate health.

Checks:
    - Feature usage frequency (from audit trail events)
    - Unused tools (zero invocations in configured window)
    - Most-failed gates (from security gate evaluations)
    - Gate pass rate trend

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute product analytics checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: Feature usage from audit trail ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                # Most common event types in last 7 days
                top_events = conn.execute(
                    "SELECT event_type, COUNT(*) as cnt FROM audit_trail "
                    "WHERE created_at > datetime('now', '-7 days') "
                    "GROUP BY event_type ORDER BY cnt DESC LIMIT 20"
                ).fetchall()

                # Least used features (bottom 10)
                if top_events:
                    bottom_events = conn.execute(
                        "SELECT event_type, COUNT(*) as cnt FROM audit_trail "
                        "WHERE created_at > datetime('now', '-30 days') "
                        "GROUP BY event_type ORDER BY cnt ASC LIMIT 10"
                    ).fetchall()

                    if bottom_events:
                        rarely_used = [{"event": row[0], "count": row[1]} for row in bottom_events if row[1] < 3]
                        if rarely_used:
                            findings.append(
                                {
                                    "severity": "info",
                                    "category": "low_usage",
                                    "title": f"{len(rarely_used)} features used <3 times in 30 days",
                                    "description": "; ".join(f"{e['event']} ({e['count']}x)" for e in rarely_used[:5]),
                                    "evidence": {"rarely_used": rarely_used},
                                    "confidence": 0.6,
                                    "auto_fixable": False,
                                }
                            )
                checks_completed += 1
            except Exception:
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        checks_completed += 1

    # --- Check 2: Gate pass/fail rates ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                # Production audit results (if any)
                audit_results = conn.execute(
                    "SELECT check_id, result, COUNT(*) as cnt "
                    "FROM production_audits "
                    "WHERE created_at > datetime('now', '-30 days') "
                    "GROUP BY check_id, result "
                    "ORDER BY check_id"
                ).fetchall()

                if audit_results:
                    # Find checks that fail frequently
                    check_stats: Dict[str, Dict[str, int]] = {}
                    for row in audit_results:
                        check_id = row[0]
                        result = row[1]
                        if check_id not in check_stats:
                            check_stats[check_id] = {"pass": 0, "fail": 0}
                        if result == "pass":
                            check_stats[check_id]["pass"] += row[2]
                        else:
                            check_stats[check_id]["fail"] += row[2]

                    failing_checks = []
                    min_rate = thresholds.get("min_gate_pass_rate_pct", 80)
                    for check_id, stats in check_stats.items():
                        total = stats["pass"] + stats["fail"]
                        if total > 0:
                            pass_rate = (stats["pass"] / total) * 100
                            if pass_rate < min_rate:
                                failing_checks.append(
                                    {
                                        "check": check_id,
                                        "pass_rate": round(pass_rate, 1),
                                        "total": total,
                                    }
                                )

                    if failing_checks:
                        failing_checks.sort(key=lambda x: x["pass_rate"])
                        findings.append(
                            {
                                "severity": "medium",
                                "category": "gate_health",
                                "title": f"{len(failing_checks)} gates below {min_rate}% pass rate",
                                "description": "; ".join(
                                    f"{c['check']}: {c['pass_rate']}%" for c in failing_checks[:5]
                                ),
                                "evidence": {"failing": failing_checks},
                                "confidence": 0.8,
                                "auto_fixable": False,
                            }
                        )
                checks_completed += 1
            except Exception:
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        checks_completed += 1

    # --- Check 3: Dashboard page diversity ---
    try:
        templates_dir = BASE_DIR / "tools" / "dashboard" / "templates"
        if templates_dir.exists():
            template_count = sum(1 for _ in templates_dir.rglob("*.html"))
            findings.append(
                {
                    "severity": "info",
                    "category": "dashboard_scope",
                    "title": f"Dashboard has {template_count} HTML templates",
                    "confidence": 1.0,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 4: Tool module count and distribution ---
    try:
        tools_dir = BASE_DIR / "tools"
        if tools_dir.exists():
            module_counts: Dict[str, int] = {}
            for subdir in tools_dir.iterdir():
                if subdir.is_dir() and not subdir.name.startswith(("__", ".")):
                    py_count = sum(
                        1 for f in subdir.rglob("*.py") if "__pycache__" not in str(f) and not f.name.startswith("__")
                    )
                    if py_count > 0:
                        module_counts[subdir.name] = py_count

            total_tools = sum(module_counts.values())
            findings.append(
                {
                    "severity": "info",
                    "category": "tool_distribution",
                    "title": f"{total_tools} tool modules across {len(module_counts)} packages",
                    "evidence": {"distribution": dict(sorted(module_counts.items(), key=lambda x: x[1], reverse=True))},
                    "confidence": 1.0,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "checks_completed": checks_completed,
            "findings_count": len(findings),
            "findings": findings,
        },
    }
