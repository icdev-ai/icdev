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

# Portfolio data-quality states. NEVER merged into one "no data" bucket — each
# one sends the reader somewhere different, and only `measured` licenses the
# brief's own conclusions:
#   unmeasurable  no contracts at all — load the portfolio
#   synthetic     rows exist but are probe/seed placeholders — the figures
#                 describe test residue, not a programme
#   degraded      real contracts, but an EVM feed with no variance — fix the feed
#   measured      act on the brief
DQ_UNMEASURABLE = "unmeasurable"
DQ_SYNTHETIC = "synthetic"
DQ_DEGRADED = "degraded"
DQ_MEASURED = "measured"

# Declared placeholder markers, not scattered literals — a title matching one of
# these is a record something created to exercise a code path. Matched on the
# whole normalised title or as a standalone token, so a real "Untitled Contract
# Modification" is not swept up by a substring hit.
PLACEHOLDER_TITLE_TOKENS = (
    "probe",
    "untitled contract",
    "seed contract",
    "test contract",
    "demo contract",
    "sample contract",
    "example contract",
    "placeholder",
)

# Share of placeholder-titled contracts at or above which the portfolio as a
# whole is called synthetic rather than merely containing a stray fixture.
PLACEHOLDER_SHARE_THRESHOLD = 0.5

# The kanban card body IS the brief for whoever opens the card, and it was
# written as a blind `narrative[:500]`. On 2026-08-24 that cut the brief at
# "4 contract(s) are at YELLOW risk — " and dropped everything after it: the 26
# overdue deliverables, the portfolio average CPI and the top open issue — every
# actionable figure the week had. `kanban_tasks.description` is an unbounded TEXT
# column (the widest row on the live board is 8,000 chars), so 500 was never a
# storage constraint, and the card named no artifact, so a reader of the cut
# brief had no route to the whole one.
CARD_DESCRIPTION_BUDGET = 2000

# Appended when, and only when, a brief really was cut. A truncation a reader
# cannot see is indistinguishable from a brief that simply ended there.
TRUNCATION_MARKER = " […brief truncated — see the full report]"


def _truncate_at_sentence(text: str, budget: int) -> str:
    """Cut at the last COMPLETED sentence within budget, never mid-sentence.

    A brief that stops mid-clause reads as a finished statement of something it
    never said. Falls back to a word boundary when the budget does not reach the
    end of even the first sentence, and marks either cut.
    """
    text = (text or "").strip()
    if len(text) <= budget:
        return text

    window = text[:budget]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut != -1:
        return window[: cut + 1] + TRUNCATION_MARKER

    word = window.rfind(" ")
    return (window[:word] if word > 0 else window).rstrip() + TRUNCATION_MARKER


def _card_description(narrative: str, report_path: Any) -> str:
    """The card body: the brief, cut safely if at all, plus the way to the rest.

    The report pointer is appended unconditionally — that is what makes a
    truncation lossless rather than merely visible.
    """
    body = _truncate_at_sentence(narrative, CARD_DESCRIPTION_BUDGET)
    return f"{body}\n\nFull brief: {report_path}"


def _contract_label(contract: Dict[str, Any]) -> str:
    """A stable, distinguishing label for a contract row.

    `contract_number` is the label whenever it is populated, verbatim. All nine
    contracts on the live board carry an EMPTY one, which the report rendered as
    "—" — so every "Worst CPI" and "Critical Issues" row named the same nothing
    and no reader could tell which contract to act on. Falling back to the title
    alone does not fix it either (five rows share "Untitled Contract"), so the
    id's short prefix is appended to keep two placeholders apart.
    """
    number = (contract.get("contract_number") or "").strip()
    if number:
        return number
    title = (contract.get("title") or "").strip() or "Untitled"
    cid = str(contract.get("id") or contract.get("contract_id") or "").strip()
    return f"{title} [{cid[:8]}]" if cid else title


def _is_placeholder_title(title: Any) -> bool:
    """True when a title is a marker something left behind, not a programme name.

    Matched on whole words ("bypass probe" hits `probe`, "GCPL Seed Contract"
    hits `seed contract`) so a real title is not caught by a bare substring.
    """
    normalised = " ".join(str(title or "").strip().lower().split())
    if not normalised:
        return True
    padded = f" {normalised} "
    return any(f" {token} " in padded for token in PLACEHOLDER_TITLE_TOKENS)


def _assess_data_quality(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Can this brief's figures be read as a measurement of a live portfolio?

    Every reason found is reported — the first one is not the only one, and a
    reader repairing the feed needs the whole list.
    """
    contracts = snapshot.get("contracts") or []
    total = snapshot.get("total_contracts", 0) or 0
    reasons = []
    details = []

    if not contracts and not total:
        return {
            "state": DQ_UNMEASURABLE,
            "reasons": ["no_contracts"],
            "detail": (
                "No contract records were returned, so this brief measures nothing. "
                "This is not the same as a portfolio with no problems."
            ),
        }

    if not contracts:
        # A count without the rows behind it — the aggregates cannot be checked.
        return {
            "state": DQ_UNMEASURABLE,
            "reasons": ["no_contract_rows"],
            "detail": (
                f"{total} contract(s) are reported but no contract rows were returned, "
                "so none of the per-contract figures below could be verified."
            ),
        }

    n = len(contracts)
    unidentified = [c for c in contracts if not (c.get("contract_number") or "").strip()]
    placeholder = [c for c in contracts if _is_placeholder_title(c.get("title"))]

    if len(unidentified) == n:
        reasons.append("unidentified_contracts")
        details.append(f"{n} of {n} contract record(s) carry no contract number")
    elif unidentified:
        details.append(f"{len(unidentified)} of {n} contract record(s) carry no contract number")

    if placeholder and len(placeholder) / n >= PLACEHOLDER_SHARE_THRESHOLD:
        reasons.append("placeholder_titles")
        details.append(
            f"{len(placeholder)} of {n} title(s) match a seed/probe placeholder "
            f"(e.g. {', '.join(sorted({str(c.get('title')) for c in placeholder})[:3])})"
        )

    distinct_cpi = snapshot.get("cpi_distinct_values")
    sample = snapshot.get("cpi_sample_size") or 0
    if distinct_cpi is not None and sample >= 2 and distinct_cpi <= 1:
        reasons.append("no_cpi_variance")
        details.append(
            f"all {sample} contract(s) reporting a CPI report the SAME value, so the "
            "portfolio average is one constant restated and ranks nothing"
        )

    if "unidentified_contracts" in reasons or "placeholder_titles" in reasons:
        state = DQ_SYNTHETIC
    elif reasons:
        state = DQ_DEGRADED
    else:
        state = DQ_MEASURED

    return {
        "state": state,
        "reasons": reasons,
        "detail": ("; ".join(details) + ".") if details else "",
    }


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
            "data_quality": snapshot.get("data_quality") or {},
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
        # Published beside the average so a constant restated per contract cannot
        # read as portfolio dispersion. All 43 live cpmp_evm_periods rows held two
        # distinct (cpi, spi) pairs, so "worst CPI" ranked three identical numbers.
        snapshot["cpi_sample_size"] = len(cpi_vals)
        snapshot["cpi_distinct_values"] = len(set(cpi_vals)) if cpi_vals else None
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
                        "contract": _contract_label(c),
                        "issue": issue.get("description", ""),
                        "severity": issue.get("severity"),
                    })
            except Exception:
                pass
        snapshot["top_issues"] = top_issues[:8]
    except Exception:
        snapshot["top_issues"] = []

    # Assessed LAST — it reads the CPI variance and contract rows gathered above.
    snapshot["data_quality"] = _assess_data_quality(snapshot)

    return snapshot


def _data_quality_advisory(snapshot: Dict[str, Any]) -> str:
    """The sentence that must precede any figure this brief cannot vouch for.

    Empty for a `measured` portfolio — a banner on every clean week is noise,
    and noise is how a real advisory stops being read.
    """
    dq = snapshot.get("data_quality") or {}
    state = dq.get("state")
    if not state or state == DQ_MEASURED:
        return ""

    consequence = {
        DQ_UNMEASURABLE: (
            "This brief measures nothing this week — it is not a statement that the "
            "portfolio is healthy."
        ),
        DQ_SYNTHETIC: (
            "The records below are seed/probe residue, not a live portfolio. Treat every "
            "figure in this brief as describing test data until real contracts are loaded."
        ),
        DQ_DEGRADED: (
            "The contract records are real but at least one feed behind these figures is "
            "not discriminating between contracts. Repair the feed before ranking anything."
        ),
    }.get(state, "")

    detail = dq.get("detail") or ""
    return f"DATA QUALITY — {state.upper()}: {consequence} {detail}".strip()


def _generate_narrative(snapshot: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Try LLM narrative; fall back to deterministic template.

    The advisory is prepended to WHICHEVER body is produced. It is deterministic
    and is never handed to the model to restate — a caveat an LLM may paraphrase
    away is not a caveat.
    """
    body = _try_llm_narrative(snapshot) or _deterministic_narrative(snapshot)
    advisory = _data_quality_advisory(snapshot)
    if advisory and not body.startswith("DATA QUALITY"):
        return f"{advisory} {body}"
    return body


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
        contract = (issues[0].get("contract") or "").strip()
        top = f"Top open issue: {issues[0].get('issue', '')}"
        lines.append(f"{top} ({contract})" if contract else top)

    advisory = _data_quality_advisory(snapshot)
    if advisory:
        # The all-clear is withheld: "nothing to report" and "nothing measurable
        # to report from" are different weeks and must never share a sentence.
        return " ".join([advisory] + lines)

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
        f"<tr><td>{_contract_label(c)}</td><td>{(c.get('title') or '—')[:40]}</td>"
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

    dq = snapshot.get("data_quality") or {}
    dq_state = dq.get("state")
    dq_block = ""
    if dq_state and dq_state != DQ_MEASURED:
        dq_color = "#dc3545" if dq_state in (DQ_UNMEASURABLE, DQ_SYNTHETIC) else "#fd7e14"
        dq_reasons = ", ".join(dq.get("reasons") or []) or "—"
        dq_block = (
            "<div class='dq' style='border-left:4px solid " + dq_color + ";'>"
            "<strong style='color:" + dq_color + ";'>Data Quality — " + dq_state.upper() + "</strong>"
            "<div style='margin-top:6px;'>" + (dq.get("detail") or "") + "</div>"
            "<div style='margin-top:6px;font-size:11px;color:#666;'>Reasons: " + dq_reasons + "</div>"
            "</div>"
        )

    # Published beside the average: an average over one repeated value ranks
    # nothing, and the stat tile is where that has to be visible.
    distinct = snapshot.get("cpi_distinct_values")
    sample = snapshot.get("cpi_sample_size") or 0
    cpi_note = (
        f"Avg Portfolio CPI ({distinct} distinct value{'' if distinct == 1 else 's'} / {sample})"
        if distinct is not None else "Avg Portfolio CPI"
    )

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
.dq{{background:#fff;padding:14px 20px;border-radius:4px;margin:16px 0;font-size:13px;line-height:1.5;}}
.footer{{font-size:11px;color:#888;margin-top:32px;text-align:center;}}
</style></head>
<body>
<div class="banner">CUI // SP-CTI &nbsp;&nbsp; ICDEV™ PMO Weekly Report &nbsp;&nbsp; {report_date}</div>
<h1>Portfolio Executive Brief</h1>
{dq_block}
<div class="narrative">{narrative}</div>

<h2>Portfolio Health</h2>
<div class="stat-grid">
  <div class="stat"><div class="stat-val">{snapshot.get("total_contracts",0)}</div><div class="stat-lbl">Total Contracts</div></div>
  <div class="stat"><div class="stat-val" style="color:#28a745;">{health.get("green",0)}</div><div class="stat-lbl">Green</div></div>
  <div class="stat"><div class="stat-val" style="color:#ffc107;">{health.get("yellow",0)}</div><div class="stat-lbl">Yellow</div></div>
  <div class="stat"><div class="stat-val" style="color:#dc3545;">{health.get("red",0)}</div><div class="stat-lbl">Red</div></div>
  <div class="stat"><div class="stat-val">{cpi}</div><div class="stat-lbl">{cpi_note}</div></div>
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
        # One value, used by both the tags and the card body — the pointer a
        # reader follows must be the path the report was actually written to.
        report_path = str(REPORTS_DIR / f"pmo_weekly_{report_date}.html")
        tags = json.dumps({
            "report_date": report_date,
            "total_contracts": snapshot.get("total_contracts", 0),
            "red": health.get("red", 0),
            "yellow": health.get("yellow", 0),
            "overdue_deliverables": snapshot.get("overdue_deliverables", 0),
            "critical_options": snapshot.get("critical_options", 0),
            "report_path": report_path,
            "data_quality": (snapshot.get("data_quality") or {}).get("state"),
            "data_quality_reasons": (snapshot.get("data_quality") or {}).get("reasons") or [],
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
                _card_description(narrative, report_path),
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
