#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex: PMO Weekly Portfolio Report.

Runs every Monday at 07:00. Aggregates contract health, EVM performance,
overdue deliverables, and option period countdowns into an AI-narrated
executive summary pushed to kanban + memory.
"""
IMPLEMENTATION_STATUS = "full"

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

REPORTS_DIR = BASE_DIR / "data" / "reports"


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute weekly PMO report reflex."""
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    snapshot = _gather_portfolio_snapshot()
    narrative = _generate_narrative(snapshot, config)
    html = _render_html_report(snapshot, narrative, report_date)

    report_path = REPORTS_DIR / f"pmo_weekly_{report_date}.html"
    try:
        report_path.write_text(html, encoding="utf-8", newline="")
    except Exception:
        pass

    # Push to kanban
    task_id = _push_kanban_task(snapshot, narrative, report_date)

    # Write to memory
    try:
        from tools.memory.memory_write import write_memory
        write_memory(
            content=(
                f"PMO Weekly Report [{report_date}]: "
                f"{snapshot.get('total_contracts', 0)} contracts, "
                f"{snapshot.get('health', {}).get('red', 0)} red / "
                f"{snapshot.get('health', {}).get('yellow', 0)} yellow / "
                f"{snapshot.get('health', {}).get('green', 0)} green. "
                f"Overdue deliverables: {snapshot.get('overdue_deliverables', 0)}. "
                f"Critical options: {snapshot.get('critical_options', 0)}."
            ),
            memory_type="event",
        )
    except Exception:
        pass

    return {
        "success": True,
        "metric_value": snapshot.get("total_contracts", 0),
        "details": {
            "report_date": report_date,
            "report_path": str(report_path),
            "kanban_task_id": task_id,
            "contracts": snapshot.get("total_contracts", 0),
            "red_contracts": snapshot.get("health", {}).get("red", 0),
            "overdue_deliverables": snapshot.get("overdue_deliverables", 0),
            "critical_options": snapshot.get("critical_options", 0),
        },
    }


def _gather_portfolio_snapshot() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    try:
        from tools.govcon.portfolio_manager import get_portfolio_summary
        result = get_portfolio_summary()
        # get_portfolio_summary returns {"status": ..., "portfolio": {...}} — every
        # aggregate lives one level down. Reading the top level returned the default
        # for each key, so the brief reported 0 contracts and "no significant issues"
        # no matter how red the portfolio actually was.
        summary = result.get("portfolio") or {} if isinstance(result, dict) else {}
        snapshot.update({
            "total_contracts": summary.get("total_contracts", 0),
            "active_contracts": summary.get("active_contracts", 0),
            "total_value": summary.get("total_value", 0),
            "burn_rate": summary.get("burn_rate_pct", 0),
            "overdue_deliverables": summary.get("overdue_deliverables", 0),
            "health": summary.get("health_distribution", {"green": 0, "yellow": 0, "red": 0}),
            "upcoming_deliverables": (summary.get("upcoming_deliverables") or [])[:5],
            "contracts": summary.get("contracts") or [],
        })
    except Exception as e:
        snapshot["portfolio_error"] = str(e)

    # EVM aggregates: top 3 worst CPI
    try:
        # One row per contract. The portfolio query LEFT JOINs cpmp_evm_periods on
        # the latest period_date, which fans out when a contract has more than one
        # row for that date — otherwise a single contract can occupy all three
        # "worst CPI" slots and its CPI/SPI is counted repeatedly in the averages.
        contracts_raw = []
        seen_ids = set()
        for c in snapshot.get("contracts", []):
            key = c.get("id") or c.get("contract_number")
            if key is not None and key in seen_ids:
                continue
            if key is not None:
                seen_ids.add(key)
            contracts_raw.append(c)
        snapshot["contracts"] = contracts_raw

        contracts_with_cpi = [
            c for c in contracts_raw
            if c.get("cpi") is not None and isinstance(c["cpi"], (int, float))
        ]
        contracts_with_cpi.sort(key=lambda c: float(c.get("cpi", 1.0)))
        snapshot["worst_cpi_contracts"] = contracts_with_cpi[:3]
        cpi_vals = [float(c["cpi"]) for c in contracts_with_cpi if c.get("cpi")]
        snapshot["avg_portfolio_cpi"] = round(sum(cpi_vals) / len(cpi_vals), 3) if cpi_vals else None
        spi_vals = [float(c["spi"]) for c in contracts_raw if c.get("spi") and isinstance(c.get("spi"), (int, float))]
        snapshot["avg_portfolio_spi"] = round(sum(spi_vals) / len(spi_vals), 3) if spi_vals else None
    except Exception:
        pass

    # Option period countdown
    try:
        from tools.govcon.option_period_tracker import get_portfolio_countdown
        countdown = get_portfolio_countdown()
        snapshot["critical_options"] = countdown.get("critical", 0)
        snapshot["warning_options"] = countdown.get("warning", 0)
        snapshot["option_countdown"] = countdown.get("options", [])[:5]
    except Exception:
        snapshot["critical_options"] = 0
        snapshot["warning_options"] = 0
        snapshot["option_countdown"] = []

    # Top issues per contract
    try:
        from tools.govcon.pmo_ai_advisor import auto_detect_issues
        top_issues = []
        for c in snapshot.get("contracts", [])[:10]:
            cid = c.get("id") or c.get("contract_id")
            if not cid:
                continue
            try:
                issues_result = auto_detect_issues(cid)
                critical = [i for i in issues_result.get("issues", []) if i.get("severity") in ("critical", "high")]
                for issue in critical[:2]:
                    top_issues.append({
                        "contract": c.get("contract_number", "N/A"),
                        "issue": issue.get("description", ""),
                        "severity": issue.get("severity"),
                    })
            except Exception:
                pass
        snapshot["top_issues"] = top_issues[:8]
    except Exception:
        snapshot["top_issues"] = []

    return snapshot


def _generate_narrative(snapshot: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Try LLM narrative; fall back to deterministic template."""
    narrative = _try_llm_narrative(snapshot)
    if narrative:
        return narrative
    return _deterministic_narrative(snapshot)


def _try_llm_narrative(snapshot: Dict[str, Any]) -> Optional[str]:
    try:
        from tools.llm.router import LLMRouter, LLMRequest

        router = LLMRouter()
        health = snapshot.get("health", {})
        prompt = (
            "You are a Federal PMO Director writing a Monday morning portfolio brief. "
            "Be direct, concise, and actionable. In 5-6 sentences, summarize the portfolio status and "
            "recommend top 3 actions.\n\n"
            f"Portfolio: {snapshot.get('total_contracts', 0)} total contracts "
            f"({snapshot.get('active_contracts', 0)} active)\n"
            f"Health: {health.get('green', 0)} green / {health.get('yellow', 0)} yellow / "
            f"{health.get('red', 0)} red\n"
            f"Avg CPI: {snapshot.get('avg_portfolio_cpi', 'N/A')} | "
            f"Avg SPI: {snapshot.get('avg_portfolio_spi', 'N/A')}\n"
            f"Overdue Deliverables: {snapshot.get('overdue_deliverables', 0)}\n"
            f"Critical Option Windows (≤30d): {snapshot.get('critical_options', 0)}\n"
            f"Top Issues: {json.dumps(snapshot.get('top_issues', [])[:4])}\n"
        )
        resp = router.route(LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            function="pmo_corrective_action",
        ))
        return resp.content if resp else None
    except Exception:
        return None


def _deterministic_narrative(snapshot: Dict[str, Any]) -> str:
    health = snapshot.get("health", {})
    red = health.get("red", 0)
    yellow = health.get("yellow", 0)
    green = health.get("green", 0)
    total = snapshot.get("total_contracts", 0)
    overdue = snapshot.get("overdue_deliverables", 0)
    crit_opts = snapshot.get("critical_options", 0)
    avg_cpi = snapshot.get("avg_portfolio_cpi")
    avg_spi = snapshot.get("avg_portfolio_spi")

    lines = []
    if red > 0:
        lines.append(f"ATTENTION: {red} contract(s) are RED — immediate intervention required.")
    if yellow > 0:
        lines.append(f"{yellow} contract(s) are at YELLOW risk — monitor closely this week.")
    if green == total and total > 0:
        lines.append(f"All {total} contracts are GREEN — portfolio is healthy.")

    if avg_cpi is not None:
        if avg_cpi < 0.90:
            lines.append(f"Portfolio average CPI is {avg_cpi:.2f} — cost performance is concerning.")
        else:
            lines.append(f"Portfolio average CPI is {avg_cpi:.2f}.")

    if avg_spi is not None and avg_spi < 0.90:
        lines.append(f"Portfolio average SPI is {avg_spi:.2f} — schedule recovery actions needed.")

    if overdue > 0:
        lines.append(f"{overdue} deliverable(s) are overdue — review Deliverable Command Center.")

    if crit_opts > 0:
        lines.append(
            f"URGENT: {crit_opts} option period(s) have ≤30 days to exercise deadline — "
            "review option countdown and initiate go/no-go decisions immediately."
        )

    issues = snapshot.get("top_issues", [])
    if issues:
        lines.append(f"Top open issue: {issues[0].get('issue', '')} ({issues[0].get('contract', '')})")

    return " ".join(lines) if lines else "No significant portfolio issues detected this week."


def _render_html_report(snapshot: Dict[str, Any], narrative: str, report_date: str) -> str:
    health = snapshot.get("health", {})
    cpi = snapshot.get("avg_portfolio_cpi", "N/A")
    spi = snapshot.get("avg_portfolio_spi", "N/A")
    worst = snapshot.get("worst_cpi_contracts", [])
    upcoming = snapshot.get("upcoming_deliverables", [])
    options = snapshot.get("option_countdown", [])
    top_issues = snapshot.get("top_issues", [])

    def badge(color):
        colors = {"green": "#28a745", "yellow": "#ffc107", "red": "#dc3545"}
        return colors.get(color, "#888")

    worst_rows = "".join(
        f"<tr><td>{c.get('contract_number','—')}</td><td>{c.get('title','—')[:40]}</td>"
        f"<td style='color:#dc3545;font-weight:700;'>{c.get('cpi','—')}</td>"
        f"<td>{c.get('spi','—')}</td></tr>"
        for c in worst
    )
    upcoming_rows = "".join(
        f"<tr><td>{d.get('cdrl_number','—')}</td><td>{d.get('title','—')[:40]}</td>"
        f"<td>{d.get('due_date','—')}</td><td>{d.get('status','—')}</td></tr>"
        for d in upcoming
    )
    def _option_row(o):
        tier_color = "#dc3545" if o.get("risk_tier") == "critical" else "#ffc107"
        days_str = str(o.get("days_to_deadline", "—"))
        tier_str = (o.get("risk_tier") or "").upper()
        return (
            "<tr>"
            "<td>" + (o.get("contract_number") or "—") + "</td>"
            "<td>Option " + str(o.get("option_number", "?")) + "</td>"
            "<td>" + (o.get("exercise_deadline") or "—") + "</td>"
            "<td style='color:" + tier_color + ";font-weight:700;'>" + days_str + "d (" + tier_str + ")</td>"
            "</tr>"
        )
    option_rows = "".join(_option_row(o) for o in options)
    def _issue_row(i):
        sev_color = "#dc3545" if i.get("severity") == "critical" else "#fd7e14"
        return (
            "<tr>"
            "<td>" + (i.get("contract") or "—") + "</td>"
            "<td style='color:" + sev_color + ";'>" + (i.get("severity") or "—").upper() + "</td>"
            "<td>" + (i.get("issue") or "—")[:80] + "</td>"
            "</tr>"
        )
    issue_rows = "".join(_issue_row(i) for i in top_issues)

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>PMO Weekly Report — {report_date}</title>
<style>
body{{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;color:#1a1a2e;background:#f8f9fa;}}
h1{{color:#1a1a2e;border-bottom:3px solid #4a90d9;padding-bottom:8px;}}
h2{{color:#2c3e6b;margin-top:28px;}}
.banner{{background:#1a1a2e;color:#fff;padding:12px 20px;border-radius:6px;margin-bottom:20px;font-size:13px;}}
.stat-grid{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0;}}
.stat{{background:#fff;border:1px solid #dde;border-radius:8px;padding:14px 20px;min-width:120px;text-align:center;}}
.stat-val{{font-size:28px;font-weight:700;}}
.stat-lbl{{font-size:12px;color:#666;margin-top:4px;}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:6px;overflow:hidden;margin-top:8px;}}
th{{background:#2c3e6b;color:#fff;padding:8px 12px;text-align:left;font-size:12px;}}
td{{padding:7px 12px;border-bottom:1px solid #eee;font-size:12px;}}
.narrative{{background:#fff;border-left:4px solid #4a90d9;padding:16px 20px;border-radius:4px;
           margin:16px 0;font-size:14px;line-height:1.6;}}
.footer{{font-size:11px;color:#888;margin-top:32px;text-align:center;}}
</style></head>
<body>
<div class="banner">CUI // SP-CTI &nbsp;&nbsp; ICDEV™ PMO Weekly Report &nbsp;&nbsp; {report_date}</div>
<h1>Portfolio Executive Brief</h1>
<div class="narrative">{narrative}</div>

<h2>Portfolio Health</h2>
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{snapshot.get("total_contracts",0)}</div><div class="stat-lbl">Total Contracts</div></div>
  <div class="stat"><div class="stat-val" style="color:#28a745;">{health.get("green",0)}</div><div class="stat-lbl">Green</div></div>
  <div class="stat"><div class="stat-val" style="color:#ffc107;">{health.get("yellow",0)}</div><div class="stat-lbl">Yellow</div></div>
  <div class="stat"><div class="stat-val" style="color:#dc3545;">{health.get("red",0)}</div><div class="stat-lbl">Red</div></div>
  <div class="stat"><div class="stat-val">{cpi}</div><div class="stat-lbl">Avg Portfolio CPI</div></div>
  <div class="stat"><div class="stat-val">{spi}</div><div class="stat-lbl">Avg Portfolio SPI</div></div>
  <div class="stat"><div class="stat-val" style="color:#dc3545;">{snapshot.get("overdue_deliverables",0)}</div><div class="stat-lbl">Overdue Deliverables</div></div>
  <div class="stat"><div class="stat-val" style="color:#dc3545;">{snapshot.get("critical_options",0)}</div><div class="stat-lbl">Critical Option Windows</div></div>
</div>

{f'<h2>Worst CPI Contracts</h2><table><thead><tr><th>Contract #</th><th>Title</th><th>CPI</th><th>SPI</th></tr></thead><tbody>{worst_rows}</tbody></table>' if worst_rows else ''}
{f'<h2>Upcoming Deliverables</h2><table><thead><tr><th>CDRL #</th><th>Title</th><th>Due Date</th><th>Status</th></tr></thead><tbody>{upcoming_rows}</tbody></table>' if upcoming_rows else ''}
{f'<h2>Option Period Countdown</h2><table><thead><tr><th>Contract</th><th>Option</th><th>Exercise Deadline</th><th>Time Remaining</th></tr></thead><tbody>{option_rows}</tbody></table>' if option_rows else ''}
{f'<h2>Critical Issues</h2><table><thead><tr><th>Contract</th><th>Severity</th><th>Issue</th></tr></thead><tbody>{issue_rows}</tbody></table>' if issue_rows else ''}

<div class="footer">Generated by ICDEV™ Genesis PMO Weekly Report Reflex &bull; {report_date} &bull; CUI</div>
</body></html>"""


def _push_kanban_task(snapshot: Dict[str, Any], narrative: str, report_date: str) -> Optional[int]:
    try:
        from tools.db.storage import get_connection
        conn = get_connection(db_path=str(BASE_DIR / "data" / "icdev.db"))
        conn.set_security_context(None)  # rls-bypass: background reflex; kanban_tasks has no classification/tenant_id columns
        health = snapshot.get("health", {})
        import uuid as _uuid3
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        task_id_str = f"pmo-rpt-{_uuid3.uuid4().hex[:10]}"
        tags = json.dumps({
            "report_date": report_date,
            "total_contracts": snapshot.get("total_contracts", 0),
            "red": health.get("red", 0),
            "yellow": health.get("yellow", 0),
            "overdue_deliverables": snapshot.get("overdue_deliverables", 0),
            "critical_options": snapshot.get("critical_options", 0),
            "report_path": str(REPORTS_DIR / f"pmo_weekly_{report_date}.html"),
        })
        conn.execute(
            """INSERT INTO kanban_tasks
               (id, task_type, title, description, status, priority,
                tags, dispatch_source, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'suggested', %s, %s, 'pmo_weekly_report', %s, %s)""",
            (
                task_id_str,
                "chore",
                f"Weekly PMO Report — {report_date}",
                narrative[:500],
                "high" if health.get("red", 0) > 0 else "medium",
                tags,
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        try:
            from tools.notification_service.event_service import notify_kanban_task_created
            notify_kanban_task_created(task_id=task_id_str, title=f"Weekly PMO Report — {report_date}")
        except Exception:
            pass

        return task_id_str
    except Exception:
        return None
