# CUI // SP-CTI
"""Deterministic source-data → VizSpec mapper for slide decks.

This is the "data-driven, not decorative" guarantee. It turns the *real*
gathered source data (kanban burndown, project progress, canvas status,
genesis activity) into concrete chart/table/KPI slides — with no LLM in the
loop, so the numbers are never fabricated.

The engine appends these slides to the LLM-written narrative (before the outro)
so a deck always contains genuine visuals regardless of LLM behaviour.

Each produced slide is a plain dict compatible with pptx_builder:
  {
    "title": str, "slide_type": "data", "speaker_notes": str,
    "chart"|"table"|"kpis"|"diagram": <spec.to_dict()>,
  }
"""
from __future__ import annotations

from typing import Any

from tools.viz.spec import ChartSpec, Series, TableSpec, KpiSpec, KpiTile, DashboardSpec


def _kanban_slides(k: dict[str, Any]) -> list[dict]:
    out: list[dict] = []
    projects = [p for p in k.get("projects", []) if isinstance(p, dict)]

    # KPI tiles: portfolio-level totals (real counts).
    n_proj = k.get("total_projects", len(projects))
    in_prog = k.get("in_progress_tasks", 0)
    backlog = k.get("backlog_tasks", 0)
    kpis = KpiSpec(title="Portfolio at a Glance", tiles=[
        KpiTile("Active Projects", str(n_proj)),
        KpiTile("In Progress", str(in_prog), unit=" tasks"),
        KpiTile("Backlog", str(backlog), unit=" tasks"),
    ])

    ranked = sorted(projects, key=lambda p: p.get("progress_pct", 0), reverse=True)[:8]
    cats = [str(p.get("key", p.get("name", "?")))[:12] for p in ranked]
    vals = [float(p.get("progress_pct", 0)) for p in ranked]
    ip = [float(p.get("in_progress", 0)) for p in ranked]
    bl = [float(p.get("backlog", 0)) for p in ranked]

    completion = ChartSpec(title="Project Completion (%)", chart_type="bar",
                           categories=cats, series=[Series("Complete", vals)], unit="%")
    workload = ChartSpec(title="Workload by Project", chart_type="column", categories=cats,
                         series=[Series("In Progress", ip), Series("Backlog", bl)], unit="tasks")

    # ── Dashboard overview slide (Tableau-style multi-tile) ──────────────────
    tiles: list[dict] = [{"spec": kpis.to_dict(), "w": 12}]
    if ranked:
        tiles.append({"spec": completion.to_dict(), "w": 6})
        if any(ip) or any(bl):
            tiles.append({"spec": workload.to_dict(), "w": 6})
    leader = ranked[0] if ranked else None
    out.append({
        "title": "Portfolio Dashboard",
        "slide_type": "data",
        "speaker_notes": k.get("summary", "Current portfolio status across active ICDEV™ projects."),
        "insight": (f"{n_proj} active projects — {in_prog} tasks in flight, {backlog} in backlog."
                    + (f" {leader.get('key', '').upper()} leads at {leader.get('progress_pct', 0)}% complete."
                       if leader else "")),
        "dashboard": DashboardSpec(title="Portfolio Dashboard", tiles=tiles).to_dict(),
    })

    # ── Focused story-point slides with insights ─────────────────────────────
    if ranked:
        lagging = min(ranked, key=lambda p: p.get("progress_pct", 0))
        out.append({
            "title": "Project Completion",
            "slide_type": "data",
            "speaker_notes": "Percent-complete by project, derived from live Kanban task counts.",
            "insight": (f"{leader.get('key', '').upper()} is furthest along ({leader.get('progress_pct', 0)}%); "
                        f"{lagging.get('key', '').upper()} trails at {lagging.get('progress_pct', 0)}%."),
            "chart": completion.to_dict(),
        })
        if any(ip) or any(bl):
            busiest = max(ranked, key=lambda p: p.get("in_progress", 0))
            out.append({
                "title": "Active Workload",
                "slide_type": "data",
                "speaker_notes": "In-progress versus backlog task counts per project.",
                "insight": (f"{busiest.get('key', '').upper()} carries the most active work "
                            f"({int(busiest.get('in_progress', 0))} in progress)."),
                "chart": workload.to_dict(),
            })
    return out


def _canvas_slides(c: dict[str, Any]) -> list[dict]:
    canvases = c.get("canvases") or c.get("active_canvases") or []
    rows: list[list[str]] = []
    for item in canvases[:12]:
        if isinstance(item, dict):
            name = str(item.get("name", item.get("key", "")))
            status = str(item.get("status", "active"))
            rows.append([name, status])
        elif isinstance(item, str):
            rows.append([item, "active"])
    if not rows:
        return []
    return [{
        "title": "Active Canvases",
        "slide_type": "data",
        "speaker_notes": c.get("summary", "ICDEV™ active design canvases and their status."),
        "insight": f"{len(rows)} active design canvases across the platform.",
        "table": TableSpec(title="Active Canvases",
                           headers=["Canvas", "Status"], rows=rows).to_dict(),
    }]


def build_data_slides(raw: dict[str, Any], max_slides: int = 4) -> list[dict]:
    """Build deterministic data-driven slides from gathered sources.

    Returns at most ``max_slides`` slides, each carrying a real VizSpec.
    Safe with partial/empty data — returns ``[]`` when nothing maps.
    """
    slides: list[dict] = []
    if not isinstance(raw, dict):
        return slides

    if isinstance(raw.get("kanban"), dict):
        slides.extend(_kanban_slides(raw["kanban"]))
    if isinstance(raw.get("canvases"), dict):
        slides.extend(_canvas_slides(raw["canvases"]))

    return slides[:max_slides]
