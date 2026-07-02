# CUI // SP-CTI
"""Auto data-storytelling: a typed dataset → dashboard + Story-Point slides.

Given a parsed dataset (see ``tools.viz.dataset``), this builds:
  - a Tableau-style dashboard slide with KPI tiles + filterable charts (the
    dataset is embedded so the web runtime can filter + re-aggregate live), and
  - a short sequence of focused "story point" slides, each with an auto-derived
    insight (the leading category, the spread, etc.).

No LLM — insights are computed deterministically from the real numbers.
"""
from __future__ import annotations

from typing import Any

from tools.viz import dataset as _ds
from tools.viz.spec import KpiSpec, KpiTile, ChartSpec, Series, TableSpec, DashboardSpec


def _pick_fields(d: dict) -> tuple[str | None, str | None]:
    """Choose a primary dimension (sane cardinality) and primary measure."""
    rows, cols = d["rows"], d["columns"]
    dim = None
    for c in d.get("dimensions", []):
        ci = cols.index(c)
        card = len({str(r[ci]) for r in rows if ci < len(r)})
        if 2 <= card <= 30:
            dim = c
            break
    if dim is None and d.get("dimensions"):
        dim = d["dimensions"][0]
    meas = d["measures"][0] if d.get("measures") else None
    return dim, meas


def _filter_dims(d: dict, limit: int = 3) -> list[str]:
    """Dimensions with low-enough cardinality to expose as filters."""
    rows, cols = d["rows"], d["columns"]
    out = []
    for c in d.get("dimensions", []):
        ci = cols.index(c)
        card = len({str(r[ci]) for r in rows if ci < len(r)})
        if 2 <= card <= 30:
            out.append(c)
        if len(out) >= limit:
            break
    return out


def build_dataset_slides(d: dict[str, Any]) -> list[dict]:
    """Build dashboard + story-point slides from a parsed dataset dict."""
    if not d or not d.get("columns") or not d.get("rows"):
        return []

    rows, cols = d["rows"], d["columns"]
    name = d.get("name", "Dataset")
    dim, meas = _pick_fields(d)
    agg = "sum" if meas else "count"
    slides: list[dict] = []

    # ── KPI tiles (real counts) ──────────────────────────────────────────────
    # Static spec drives the PPTX snapshot; the parallel `datasetKpi` descriptor
    # lets the web runtime recompute the same tiles live as filters change.
    tiles = [KpiTile("Records", str(len(rows)))]
    kpi_defs: list[dict] = [{"label": "Records", "kind": "count"}]
    if dim:
        di = cols.index(dim)
        tiles.append(KpiTile("Distinct " + dim, str(len({str(r[di]) for r in rows}))))
        kpi_defs.append({"label": "Distinct " + dim, "kind": "distinct", "field": dim})
    if meas:
        mi = cols.index(meas)
        total = sum(_ds._to_number(r[mi]) for r in rows if mi < len(r))
        tiles.append(KpiTile("Total " + meas, f"{total:,.0f}"))
        kpi_defs.append({"label": "Total " + meas, "kind": "sum", "field": meas})
    kpis = KpiSpec(title=name, tiles=tiles[:4])

    # ── Dashboard tiles (datasetChart → filterable + re-aggregating client-side) ──
    dash_tiles: list[dict] = [{"spec": kpis.to_dict(), "datasetKpi": {"defs": kpi_defs[:4]}, "w": 12}]
    if dim:
        dash_tiles.append({"datasetChart": {
            "title": (meas or "count") + " by " + dim, "dimension": dim,
            "measure": meas, "agg": agg, "chart_type": "column"}, "w": 7})
    second_dim = next((c for c in d.get("dimensions", []) if c != dim), None)
    if second_dim:
        dash_tiles.append({"datasetChart": {
            "title": (meas or "count") + " by " + second_dim, "dimension": second_dim,
            "measure": meas, "agg": agg, "chart_type": "donut"}, "w": 5})

    dataset_payload = {"columns": cols, "rows": rows,
                       "dimensions": d.get("dimensions", []), "measures": d.get("measures", [])}
    slides.append({
        "title": name + " — Dashboard",
        "slide_type": "data",
        "speaker_notes": f"Interactive dashboard over {len(rows)} records. Use the filters to explore.",
        "insight": f"{len(rows)} records across {len(cols)} fields. Use the filters to slice live.",
        "dashboard": DashboardSpec(title=name + " — Dashboard", tiles=dash_tiles,
                                   dataset=dataset_payload, filters=_filter_dims(d)).to_dict(),
    })

    # ── Focused story point: leading category ────────────────────────────────
    if dim:
        cats, vals = _ds.aggregate(rows, cols, dim, meas, agg)
        if cats:
            pairs = sorted(zip(cats, vals), key=lambda p: p[1], reverse=True)
            top_cats = [p[0] for p in pairs[:10]]
            top_vals = [round(p[1], 2) for p in pairs[:10]]
            lead = pairs[0]
            metric = meas or "records"
            slides.append({
                "title": (meas or "Count") + " by " + dim,
                "slide_type": "data",
                "speaker_notes": f"{metric} aggregated by {dim} ({agg}).",
                "insight": f"“{lead[0]}” leads with {lead[1]:,.0f} {metric}"
                           + (f" — {lead[1] / sum(top_vals) * 100:.0f}% of the top {len(top_vals)}."
                              if sum(top_vals) else "."),
                "chart": ChartSpec(title=(meas or "Count") + " by " + dim,
                                   chart_type="bar", categories=top_cats,
                                   series=[Series(metric, top_vals)]).to_dict(),
            })

    # ── Data sample table ────────────────────────────────────────────────────
    sample = [[str(c) for c in r[:6]] for r in rows[:8]]
    slides.append({
        "title": name + " — Sample",
        "slide_type": "data",
        "speaker_notes": "First rows of the uploaded dataset.",
        "insight": f"Showing {len(sample)} of {len(rows)} rows.",
        "table": TableSpec(title="Data Sample", headers=cols[:6], rows=sample).to_dict(),
    })
    return slides
