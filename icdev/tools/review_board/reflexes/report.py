#!/usr/bin/env python3
# CUI // SP-CTI
"""Weekly Digest Report Reflex — generates markdown summary of Review Board activity.

Produces a report covering:
    - Health score and trend
    - Findings by persona and severity
    - Auto-remediation stats
    - Top unresolved issues
    - Recommendations

Architecture: D-RB-1 (scanner-tier), D-RB-13 (weekly digest)
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

try:
    from tools.db.storage import get_connection as _raw_get_connection
except ImportError:
    _raw_get_connection = None


def _get_connection():
    if not _raw_get_connection:
        return None
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Generate weekly digest report."""
    report_lines: List[str] = []
    now = datetime.now(timezone.utc)

    report_lines.append("# Engineering Review Board — Weekly Digest")
    report_lines.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    report_lines.append("")

    # --- Section 1: Health Score ---
    try:
        from tools.review_board.health_scorer import compute_health_score

        health = compute_health_score()
        score = health.get("score", 0)
        grade = health.get("grade", "N/A")
        trend = health.get("trend", "stable")
        trend_icon = {"improving": "+", "degrading": "-", "stable": "="}

        report_lines.append(f"## Codebase Health: {score:.0f}/100 ({grade}) {trend_icon.get(trend, '=')}")
        report_lines.append("")

        breakdown = health.get("breakdown", {})
        sev = breakdown.get("severity_counts", {})
        report_lines.append("| Metric | Value |")
        report_lines.append("|--------|-------|")
        report_lines.append(f"| Unfixed findings | {breakdown.get('finding_count_unfixed', 0)} |")
        report_lines.append(f"| Critical | {sev.get('critical', 0)} |")
        report_lines.append(f"| High | {sev.get('high', 0)} |")
        report_lines.append(f"| Medium | {sev.get('medium', 0)} |")
        report_lines.append(f"| Fix bonus | +{breakdown.get('fix_bonus', 0):.1f} |")
        report_lines.append(f"| Trend | {trend} |")
        report_lines.append("")
    except Exception as e:
        report_lines.append(f"## Health Score: unavailable ({e})")
        report_lines.append("")
        health = {}

    # --- Section 2: Findings by Persona (last 7 days) ---
    conn = _get_connection()
    if conn:
        try:
            report_lines.append("## Findings by Persona (Last 7 Days)")
            report_lines.append("")
            report_lines.append("| Persona | Critical | High | Medium | Low | Total |")
            report_lines.append("|---------|----------|------|--------|-----|-------|")

            rows = conn.execute(
                "SELECT reflex_name, severity, COUNT(*) as cnt "
                "FROM review_board_findings "
                "WHERE created_at > datetime('now', '-7 days') "
                "GROUP BY reflex_name, severity "
                "ORDER BY reflex_name"
            ).fetchall()

            persona_data: Dict[str, Dict[str, int]] = {}
            for r in rows:
                name = r[0]
                if name not in persona_data:
                    persona_data[name] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
                persona_data[name][r[1]] = r[2]

            for persona in ["sre", "qa", "security", "perf", "ux", "docs", "product"]:
                d = persona_data.get(persona, {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0})
                total = sum(d.values())
                report_lines.append(
                    f"| {persona.upper()} | {d['critical']} | {d['high']} | {d['medium']} | {d['low']} | {total} |"
                )
            report_lines.append("")

            # --- Section 3: Auto-Remediation Stats ---
            report_lines.append("## Auto-Remediation (Last 7 Days)")
            report_lines.append("")
            try:
                rem_rows = conn.execute(
                    "SELECT tier, status, COUNT(*) FROM review_board_remediation_log "
                    "WHERE created_at > datetime('now', '-7 days') "
                    "GROUP BY tier, status"
                ).fetchall()
                if rem_rows:
                    report_lines.append("| Tier | Status | Count |")
                    report_lines.append("|------|--------|-------|")
                    for r in rem_rows:
                        report_lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
                else:
                    report_lines.append("No remediation activity this week.")
            except Exception:
                report_lines.append("Remediation data unavailable.")
            report_lines.append("")

            # --- Section 4: Top Unresolved Issues ---
            report_lines.append("## Top Unresolved Issues")
            report_lines.append("")
            try:
                unresolved = conn.execute(
                    "SELECT severity, reflex_name, category, title "
                    "FROM review_board_findings "
                    "WHERE fix_applied = 0 "
                    "ORDER BY "
                    "  CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 "
                    "  WHEN 'medium' THEN 3 ELSE 4 END "
                    "LIMIT 10"
                ).fetchall()
                if unresolved:
                    for r in unresolved:
                        report_lines.append(f"- **[{r[0].upper()}]** ({r[1]}/{r[2]}): {r[3]}")
                else:
                    report_lines.append("No unresolved issues.")
            except Exception:
                report_lines.append("Issue data unavailable.")
            report_lines.append("")

            # --- Section 5: Reflex Health ---
            report_lines.append("## Reflex Health")
            report_lines.append("")
            try:
                reflex_rows = conn.execute(
                    "SELECT reflex_name, total_runs, total_successes, "
                    "circuit_breaker_open, last_run_at "
                    "FROM review_board_reflex_state ORDER BY reflex_name"
                ).fetchall()
                if reflex_rows:
                    report_lines.append("| Persona | Runs | Pass Rate | CB | Last Run |")
                    report_lines.append("|---------|------|-----------|----|---------| ")
                    for r in reflex_rows:
                        total = r[1] or 0
                        succ = r[2] or 0
                        rate = f"{(succ / total * 100):.0f}%" if total > 0 else "N/A"
                        cb = "OPEN" if r[3] else "OK"
                        last = r[4] or "never"
                        report_lines.append(f"| {r[0].upper()} | {total} | {rate} | {cb} | {last} |")
                else:
                    report_lines.append("No reflex state data.")
            except Exception:
                report_lines.append("Reflex data unavailable.")

        except Exception as e:
            report_lines.append(f"Error generating report: {e}")
        finally:
            conn.close()

    report_text = "\n".join(report_lines)

    # Save report to file
    report_dir = BASE_DIR / "data" / "review_board" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"digest-{now.strftime('%Y-%m-%d')}.md"
    report_file.write_text(report_text, encoding="utf-8", newline="")

    return {
        "success": True,
        "metric_value": float(health.get("score", 0)),
        "details": {
            "report_file": str(report_file),
            "health_score": health.get("score", 0),
            "health_grade": health.get("grade", "N/A"),
            "health_trend": health.get("trend", "stable"),
            "report_length": len(report_text),
        },
    }
