"""AI Brief Banner — partial renderer for the ICDEV™ dashboard.

Returns the inner HTML for the <aside id="ai-brief"> element given a canvas
name and a live portfolio snapshot.  Every LLM-touching call routes through
pmo_ai_advisor, which already carries deterministic fallbacks, so the component
works correctly with no LLM available.
"""

from __future__ import annotations

import html
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

_LLM_TIMEOUT = 5  # seconds per LLM-touching call


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_with_timeout(fn, *args, timeout: float = _LLM_TIMEOUT, **kwargs) -> Any:
    """Run *fn* in a thread; raise FutureTimeoutError if it takes longer than *timeout* s."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn, *args, **kwargs).result(timeout=timeout)


def _esc(v: Any) -> str:
    return html.escape(str(v) if v is not None else "")


def _fmt_value(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


# ---------------------------------------------------------------------------
# Canvas-specific data collectors
# ---------------------------------------------------------------------------

def _collect_cpmp(snapshot: dict) -> list[dict]:
    """Worst-3 contracts by health → PMO recommendations per contract."""
    from tools.govcon.pmo_ai_advisor import get_pmo_recommendations
    from tools.govcon.portfolio_manager import get_portfolio_summary

    contracts = snapshot.get("contracts") or []
    if not contracts:
        try:
            raw = _call_with_timeout(get_portfolio_summary)
            contracts = (raw.get("portfolio") or {}).get("contracts") or []
        except Exception:
            contracts = []

    health_order = {"red": 0, "yellow": 1, "green": 2}
    worst3 = sorted(
        contracts,
        key=lambda c: health_order.get((c.get("health") or "green").lower(), 3),
    )[:3]

    items = []
    for contract in worst3:
        cid = contract.get("id") or contract.get("contract_id") or ""
        if not cid:
            continue
        try:
            result = _call_with_timeout(get_pmo_recommendations, cid)
            recs = result.get("recommendations") or []
            bullets = [r.get("action") or r.get("text") or str(r) for r in recs[:3]]
            summary = result.get("summary") or result.get("overall_health") or ""
        except Exception:
            bullets = []
            summary = "Recommendations unavailable."
        items.append({
            "label": contract.get("contract_number") or cid,
            "health": (contract.get("health") or "unknown").lower(),
            "summary": summary,
            "bullets": bullets,
        })
    return items


def _collect_cpmp_deliverables(snapshot: dict) -> list[dict]:
    """auto_detect_issues() across all contracts → top-3 by severity."""
    from tools.govcon.pmo_ai_advisor import auto_detect_issues
    from tools.govcon.portfolio_manager import get_portfolio_summary

    contracts = snapshot.get("contracts") or []
    if not contracts:
        try:
            raw = _call_with_timeout(get_portfolio_summary)
            contracts = (raw.get("portfolio") or {}).get("contracts") or []
        except Exception:
            contracts = []

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_issues: list[dict] = []
    for contract in contracts:
        cid = contract.get("id") or contract.get("contract_id") or ""
        if not cid:
            continue
        try:
            result = _call_with_timeout(auto_detect_issues, cid)
            for issue in result.get("issues") or []:
                issue["_contract_label"] = contract.get("contract_number") or cid
                all_issues.append(issue)
        except Exception:
            pass

    all_issues.sort(
        key=lambda i: severity_rank.get((i.get("severity") or "low").lower(), 4)
    )
    return [
        {
            "label": i.get("_contract_label", ""),
            "severity": (i.get("severity") or "low").lower(),
            "type": i.get("type") or i.get("issue_type") or "",
            "description": i.get("description") or i.get("message") or "",
        }
        for i in all_issues[:3]
    ]


def _collect_proposals(snapshot: dict) -> dict:
    """pipeline_value_rollup() + top-3 at-risk opportunities by pWin (lowest first)."""
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    try:
        rollup = _call_with_timeout(pipeline_value_rollup)
    except Exception:
        rollup = {}

    opps = [o for o in (rollup.get("opportunities") or []) if o.get("pwin_pct") is not None]
    at_risk = sorted(opps, key=lambda o: o.get("pwin_pct", 100))[:3]

    return {
        "total_weighted": rollup.get("total_weighted_pipeline_value", 0),
        "total_potential": rollup.get("total_potential_value", 0),
        "scored_count": rollup.get("scored_count", 0),
        "at_risk": [
            {
                "title": o.get("title") or o.get("opportunity_id") or "",
                "pwin_pct": o.get("pwin_pct", 0),
                "weighted_value": o.get("weighted_value", 0),
            }
            for o in at_risk
        ],
    }


def _collect_govcon(snapshot: dict) -> dict:
    """GovCon engine status + top-3 capability gaps."""
    from tools.govcon.govcon_engine import get_status
    from tools.govcon.capability_mapper import get_gaps

    try:
        status = _call_with_timeout(get_status)
    except Exception:
        status = {}

    try:
        gaps_result = _call_with_timeout(get_gaps)
        gaps = (gaps_result.get("gaps") or [])[:3]
    except Exception:
        gaps = []

    return {
        "total_opportunities": status.get("total_opportunities", 0),
        "total_requirements": status.get("total_requirements", 0),
        "approved_drafts": status.get("approved_drafts", 0),
        "total_awards": status.get("total_awards", 0),
        "gaps": [
            {
                "name": g.get("pattern_name") or g.get("pattern_id") or "",
                "domain": g.get("domain") or "",
                "grade": g.get("grade") or "N",
                "recommendation": g.get("recommendation") or "",
            }
            for g in gaps
        ],
    }


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

_HEALTH_BADGE = {
    "red": '<span class="badge bg-danger">Red</span>',
    "yellow": '<span class="badge bg-warning text-dark">Yellow</span>',
    "green": '<span class="badge bg-success">Green</span>',
}

_SEVERITY_BADGE = {
    "critical": '<span class="badge bg-danger">Critical</span>',
    "high": '<span class="badge bg-warning text-dark">High</span>',
    "medium": '<span class="badge bg-info text-dark">Medium</span>',
    "low": '<span class="badge bg-secondary">Low</span>',
}


def _render_cpmp(items: list[dict]) -> str:
    if not items:
        return '<p class="text-muted small mb-0">No contract data available.</p>'
    rows = []
    for it in items:
        badge = _HEALTH_BADGE.get(it["health"], "")
        bullet_html = "".join(
            f'<li class="small">{_esc(b)}</li>' for b in it["bullets"]
        ) or '<li class="small text-muted">No recommendations.</li>'
        rows.append(
            f'<div class="mb-2">'
            f'<span class="fw-semibold">{_esc(it["label"])}</span> {badge}'
            f'<p class="small mb-1 text-muted">{_esc(it["summary"])}</p>'
            f'<ul class="mb-0 ps-3">{bullet_html}</ul>'
            f'</div>'
        )
    return "".join(rows)


def _render_cpmp_deliverables(items: list[dict]) -> str:
    if not items:
        return '<p class="text-muted small mb-0">No active issues detected.</p>'
    rows = []
    for it in items:
        sev_badge = _SEVERITY_BADGE.get(it["severity"], "")
        rows.append(
            f'<div class="mb-2">'
            f'<span class="fw-semibold small">{_esc(it["label"])}</span> {sev_badge}'
            f'<span class="badge bg-light text-dark ms-1">{_esc(it["type"])}</span>'
            f'<p class="small mb-0 text-muted">{_esc(it["description"])}</p>'
            f'</div>'
        )
    return "".join(rows)


def _render_proposals(data: dict) -> str:
    risk_rows = "".join(
        f'<li class="small">'
        f'{_esc(o["title"])} &mdash; '
        f'<span class="text-warning fw-semibold">{_esc(o["pwin_pct"])}% pWin</span> '
        f'({_fmt_value(o["weighted_value"])} weighted)'
        f'</li>'
        for o in (data.get("at_risk") or [])
    ) or '<li class="small text-muted">No at-risk opportunities.</li>'

    return (
        f'<div class="d-flex gap-3 mb-2">'
        f'<div><span class="small text-muted">Weighted Pipeline</span><br>'
        f'<strong>{_fmt_value(data.get("total_weighted") or 0)}</strong></div>'
        f'<div><span class="small text-muted">Potential</span><br>'
        f'<strong>{_fmt_value(data.get("total_potential") or 0)}</strong></div>'
        f'<div><span class="small text-muted">Scored</span><br>'
        f'<strong>{_esc(data.get("scored_count", 0))}</strong></div>'
        f'</div>'
        f'<p class="small fw-semibold mb-1">At-Risk Opportunities</p>'
        f'<ul class="mb-0 ps-3">{risk_rows}</ul>'
    )


def _render_govcon(data: dict) -> str:
    gap_rows = "".join(
        f'<li class="small">'
        f'<span class="fw-semibold">{_esc(g["name"])}</span>'
        f'{" — " + _esc(g["domain"]) if g["domain"] else ""}'
        f' <span class="badge bg-secondary">{_esc(g["grade"])}</span>'
        f'<br><span class="text-muted">{_esc(g["recommendation"])}</span>'
        f'</li>'
        for g in (data.get("gaps") or [])
    ) or '<li class="small text-muted">No capability gaps detected.</li>'

    return (
        f'<div class="d-flex gap-3 flex-wrap mb-2">'
        f'<div><span class="small text-muted">Opportunities</span><br>'
        f'<strong>{_esc(data.get("total_opportunities", 0))}</strong></div>'
        f'<div><span class="small text-muted">Requirements</span><br>'
        f'<strong>{_esc(data.get("total_requirements", 0))}</strong></div>'
        f'<div><span class="small text-muted">Awards</span><br>'
        f'<strong>{_esc(data.get("total_awards", 0))}</strong></div>'
        f'<div><span class="small text-muted">Approved Drafts</span><br>'
        f'<strong>{_esc(data.get("approved_drafts", 0))}</strong></div>'
        f'</div>'
        f'<p class="small fw-semibold mb-1">Top Capability Gaps</p>'
        f'<ul class="mb-0 ps-3">{gap_rows}</ul>'
    )


# ---------------------------------------------------------------------------
# Canvas metadata
# ---------------------------------------------------------------------------

_CANVAS_META: dict[str, tuple[str, str]] = {
    "cpmp": ("Contract PMO", "bi-clipboard-check"),
    "cpmp_deliverables": ("Deliverables", "bi-box-seam"),
    "proposals": ("Proposals Pipeline", "bi-graph-up-arrow"),
    "govcon": ("GovCon Intelligence", "bi-globe2"),
}

_CANVAS_RENDER = {
    "cpmp": lambda snap: _render_cpmp(_collect_cpmp(snap)),
    "cpmp_deliverables": lambda snap: _render_cpmp_deliverables(_collect_cpmp_deliverables(snap)),
    "proposals": lambda snap: _render_proposals(_collect_proposals(snap)),
    "govcon": lambda snap: _render_govcon(_collect_govcon(snap)),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_ai_brief(canvas: str, snapshot: dict) -> str:
    """Return the inner HTML for the AI brief banner aside element.

    Args:
        canvas: Dashboard canvas key ('cpmp', 'cpmp_deliverables', 'proposals', 'govcon').
        snapshot: Live portfolio snapshot dict; collectors fall back to DB calls if empty.

    Returns:
        Rendered HTML string wrapping <aside id="ai-brief">...</aside>.
    """
    title, icon = _CANVAS_META.get(canvas, ("AI Brief", "bi-stars"))
    renderer = _CANVAS_RENDER.get(canvas)

    if renderer is None:
        body_html = '<p class="text-muted small mb-0">No AI brief configured for this canvas.</p>'
    else:
        try:
            body_html = renderer(snapshot)
        except (FutureTimeoutError, Exception) as exc:
            body_html = (
                f'<p class="text-warning small mb-0">'
                f'<i class="bi bi-exclamation-triangle me-1"></i>'
                f'Brief temporarily unavailable: {_esc(exc)}'
                f'</p>'
            )

    return (
        f'<aside id="ai-brief" class="ai-brief-banner card border-0 shadow-sm mb-3">'
        f'<div class="card-header d-flex align-items-center gap-2 py-2">'
        f'<i class="bi {_esc(icon)} text-primary"></i>'
        f'<span class="fw-semibold">{_esc(title)} — AI Brief</span>'
        f'<span class="badge bg-primary ms-auto">AI</span>'
        f'</div>'
        f'<div class="card-body py-2 px-3">'
        f'{body_html}'
        f'</div>'
        f'</aside>'
    )
