# CUI // SP-CTI
"""The /activity runtime-invocations panel renders the rollup honestly.

The panel and ``icdev runtime top`` read the same store, so the arithmetic is
covered in ``tests/test_invocation_store.py``. What is covered HERE is the
rendering, and specifically the two ways a telemetry panel lies:

  * a failed query rendered as an empty table ("nothing ran"), and
  * a NULL duration rendered as ``0`` ("instantaneous").

``activity.html`` is rendered against a stub ``base.html`` rather than by
booting the dashboard: importing ``tools.dashboard.app`` triggers air-gap
detection, which socket-probes local LLM servers and stalls the suite (see
``tests/dashboard/test_home_tile_gating.py``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "tools/dashboard/templates"
APP = ROOT / "tools/dashboard/app.py"

#: Enough of base.html to render the content block and nothing else.
_STUB_BASE = "<html><body>{% block content %}{% endblock %}"\
             "{% block scripts %}{% endblock %}</body></html>"


@pytest.fixture()
def render():
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": _STUB_BASE}),
        FileSystemLoader(str(TEMPLATES)),
    ]), autoescape=True)
    env.globals["url_for"] = lambda *a, **k: "#"
    template = env.get_template("activity.html")
    return lambda **ctx: template.render(**ctx)


def _row(**over):
    base = {"surface": "mcp", "name": "rag_search", "names": 2, "calls": 4,
            "errors": 1, "running": 0, "completed": 4, "error_rate_pct": 25.0,
            "avg_ms": 162.5, "max_ms": 300}
    base.update(over)
    return base


def _panel(html):
    """The panel only — so an assertion cannot accidentally match the event table."""
    start = html.index("Runtime Invocations")
    return html[start:html.index("<h2", start + 10)]


def _table(html, table_id):
    """One table of the panel, by id."""
    start = html.index(f'id="{table_id}"')
    return html[start:html.index("</table>", start)]


# ------------------------------------------------------------------ happy path

def test_panel_shows_calls_errors_and_durations(render):
    html = _panel(render(inv_by_surface=[_row()], inv_slowest=[], inv_failing=[],
                         inv_error=None))
    for expected in ("mcp", "162.5", "300", "25.0%"):
        assert expected in html, f"missing {expected!r}"


def test_slow_and_failing_tools_are_named(render):
    html = render(
        inv_by_surface=[_row()],
        inv_slowest=[_row(name="sbom_generate", avg_ms=8950.0, max_ms=9100)],
        inv_failing=[_row(name="broken_tool", errors=3, error_rate_pct=75.0)],
        inv_error=None,
    )
    assert "sbom_generate" in html and "9100" in html
    assert "broken_tool" in html and "75.0%" in html


def test_no_failures_says_so_rather_than_rendering_an_empty_table(render):
    html = render(inv_by_surface=[_row()], inv_slowest=[], inv_failing=[],
                  inv_error=None)
    assert "No failures recorded" in html


# ------------------------------------------------- the two ways a panel lies

def test_a_query_failure_is_shown_not_swallowed(render):
    """UndefinedColumn from RLS injection must not read as 'nothing ran'."""
    html = _panel(render(inv_by_surface=[], inv_slowest=[], inv_failing=[],
                         inv_error='column "tenant_id" does not exist'))
    assert "Rollup unavailable" in html
    assert "tenant_id" in html
    assert "No invocations recorded yet" not in html, "an error is not an idle board"


def test_an_empty_table_is_distinguished_from_a_broken_one(render):
    html = _panel(render(inv_by_surface=[], inv_slowest=[], inv_failing=[],
                         inv_error=None))
    assert "No invocations recorded yet" in html
    assert "Rollup unavailable" not in html


def test_an_unfinished_surface_shows_a_dash_not_zero_ms(render):
    """All-running surface: avg/max are None. `0` would read as instantaneous."""
    html = _panel(render(
        inv_by_surface=[_row(surface="role", calls=2, errors=0, running=2,
                             completed=0, error_rate_pct=0.0,
                             avg_ms=None, max_ms=None)],
        inv_slowest=[], inv_failing=[], inv_error=None,
    ))
    assert "None" not in html, "a raw None leaked into the page"
    # The last two cells of the surface row are avg_ms and max_ms. Asserting on
    # them specifically, because `errors` and `running` legitimately render 0.
    cells = re.findall(r"<td[^>]*>\s*([^<]*?)\s*</td>",
                       _table(html, "runtime-invocations-surface"))
    assert cells[-2:] == ["—", "—"], f"avg/max should be em dashes, got {cells[-2:]}"


# ------------------------------------------------------------- route wiring

def test_activity_route_supplies_the_panel_context():
    """Guards against a future edit dropping the context and blanking the panel."""
    src = APP.read_text(encoding="utf-8")
    route = src[src.index('@app.route("/activity")'):]
    route = route[:route.index('@app.route("/usage")')]

    assert "InvocationStore" in route, "the panel must read the shared store"
    for name in ("inv_by_surface", "inv_slowest", "inv_failing", "inv_error"):
        assert name in route, f"{name} not passed to the template"
    assert "last_error" in route, "a failed rollup must reach inv_error"


def test_the_panel_points_at_the_cli_equivalent():
    """Someone reading the panel should learn the command that prints it."""
    assert "icdev runtime top" in (TEMPLATES / "activity.html").read_text(encoding="utf-8")
