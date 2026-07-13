#!/usr/bin/env python3
# CUI // SP-CTI
"""prem-rpt-01 — a WHOLE dashboard can finally be exported.

bi_dashboard already had NL->chart generation, dashboard CRUD, data ingestion, ECharts
theming and a spec layer that round-trips. What it did not have was a way to get a
dashboard OUT. Export was chart-level only, and the code said so:

    if fmt == "png" and spec.kind == "chart": ...
    if fmt == "svg" and spec.kind == "chart": ...
    return {"error": f"export format {fmt} not supported for kind {spec.kind}"}, 400

One chart, one PNG. Ask for the dashboard — the thing a customer actually reads — and
you got a 400. You could build the report and never send it.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bi_dashboard.export import (  # noqa: E402
    dashboard_to_html,
    export_dashboard,
    supported_formats,
)

CHART = {"kind": "chart", "chart_type": "bar", "title": "Burn by CLIN",
         "categories": ["0001", "0002"], "series": [{"name": "Burn", "values": [120, 80]}]}
TABLE = {"kind": "table", "title": "Deliverables", "headers": ["CDRL", "Status"],
         "rows": [["A001", "accepted"], ["A002", "overdue"]]}


def _dash(*specs, classification="CUI", title="Q3 Program Health"):
    return {"title": title, "classification": classification,
            "tiles": [{"spec": s} for s in specs]}


# ---------------------------------------------------------------------------
# The gap this closes
# ---------------------------------------------------------------------------


def test_a_whole_dashboard_renders_every_tile_into_one_document():
    html = dashboard_to_html(_dash(CHART, TABLE))
    assert "Q3 Program Health" in html
    assert "viz-chart" in html      # the chart tile
    assert "viz-table" in html      # AND the table tile, in the same document
    assert "Burn by CLIN" in html
    assert "A002" in html


@pytest.mark.parametrize("fmt", supported_formats())
def test_every_supported_format_produces_real_bytes(fmt):
    out = export_dashboard(_dash(CHART, TABLE), fmt)
    assert out["format"] == fmt
    assert out["filename"].endswith(f".{fmt}")

    if fmt == "html":
        assert len(out["html"]) > 500
    else:
        raw = base64.b64decode(out[f"{fmt}_base64"])
        assert len(raw) > 1000
        # A real file, not an empty envelope.
        assert raw[:4] in (b"%PDF", b"PK\x03\x04")


def test_an_unknown_format_is_refused_with_the_ones_that_work():
    with pytest.raises(ValueError, match="unsupported dashboard export format"):
        export_dashboard(_dash(CHART), "docx")


# ---------------------------------------------------------------------------
# A report that quietly loses a tile is worse than one that says so
# ---------------------------------------------------------------------------


def test_an_unrenderable_tile_is_REPORTED_not_silently_dropped():
    """A customer counts the charts. If a tile vanishes between the screen and the PDF,
    nobody finds out until the customer asks where it went."""
    timeline = {"kind": "timeline", "title": "Milestones", "events": []}
    html = dashboard_to_html(_dash(CHART, timeline))

    assert "viz-chart" in html                    # the good tile still renders
    assert "has no renderer" in html              # and the bad one SAYS so
    assert "viz-unsupported" in html


def test_a_malformed_spec_is_reported_not_dropped():
    html = dashboard_to_html({"title": "T", "tiles": [{"spec": {"kind": "chart"}}, {}]})
    assert "viz-unsupported" in html


def test_an_empty_dashboard_says_so():
    assert "no tiles" in dashboard_to_html(_dash())


# ---------------------------------------------------------------------------
# The marking travels with the export
# ---------------------------------------------------------------------------


def test_the_classification_banner_is_on_the_export_top_AND_bottom():
    """An export LEAVES the platform — that is what an export is. The marking has to go
    with it rather than staying behind in the database row it came from. An unmarked
    export of CUI is a CUI spill in a slide deck."""
    html = dashboard_to_html(_dash(CHART, classification="SECRET"))
    # Banner in and banner out.
    assert html.count("SECRET") >= 2
    assert html.index("SECRET") < html.index("viz-chart")          # before the content
    assert html.rindex("SECRET") > html.rindex("viz-chart")        # and after it


def test_the_html_is_self_contained():
    """A customer-facing report that fetches a stylesheet from a host they cannot reach
    renders as a wall of unstyled text — and they will not tell you, they will just think
    less of it."""
    html = dashboard_to_html(_dash(CHART))
    assert "<link" not in html                 # no external stylesheet
    assert "<script src=" not in html          # no external script
    assert "<svg" in html                      # the chart is inline SVG
