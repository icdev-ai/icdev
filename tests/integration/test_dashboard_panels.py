# CUI // SP-CTI
"""Dashboard panels against the API they actually fetch, on a real database.

``tests/test_runtime_invocations_panel.py`` covers the endpoint's logic with the
blueprint mounted by hand. What it cannot cover is the seam the Runtime
Performance panel really depends on: the template hardcodes
``/api/runtime-invocations/summary``, and if the mount moves — or the payload
loses a key the template reads — the page renders "Could not load runtime
telemetry" and every test in the repo still passes.

So the URL and the field list here are PARSED OUT OF THE TEMPLATE rather than
retyped. A test that restates the path it is checking cannot notice the path
changing; one that reads it from the file the browser will run does.

The database is the same fixture the CLI comparison uses (``conftest.py``), so
the panel's per-surface breakdown is checked against the CLI's rollup rather
than against a second hand-written expectation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from _runtime_expectations import TOTAL_CALLS, by_key

from tools.observability import invocation_recorder

_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "tools/dashboard/templates/monitoring/_runtime_performance.html"
)


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _fetch_url() -> str:
    """The URL the panel's JavaScript fetches, straight out of the template."""
    match = re.search(r"fetch\(\s*'([^']+)'", _template_text())
    assert match, "panel no longer fetches anything — did the loader change?"
    return match.group(1)


# ------------------------------------------------- the panel reaches its API

def test_the_panel_fetches_a_path_the_app_actually_serves(seeded, panel_client):
    """Registration is load-bearing for the UI, not only for API clients.

    ``register_api_blueprints`` mounts this blueprint at ``/api/v1/...`` with an
    ``/api/...`` legacy alias. The template uses the alias. If that alias is
    dropped as "legacy", nothing in the API tier fails — the panel just stops
    loading.
    """
    response = panel_client.get(_fetch_url())
    assert response.status_code == 200, _fetch_url()

    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["available"] is True
    assert payload["rows"], "the panel would render an empty table over seeded data"


def test_the_payload_carries_every_field_the_template_reads(seeded, panel_client):
    """A key the template reads and the payload omits renders as `undefined`.

    JavaScript does not raise on a missing property, so this failure is silent
    on the page: `undefined%` in the Error column, a blank note line. Listing
    the fields here is the only place that mismatch is caught.
    """
    payload = panel_client.get(_fetch_url()).get_json()

    for key in ("ok", "available", "backend", "limit", "truncated",
                "rows", "totals", "by_surface"):
        assert key in payload, key
    for key in ("names", "calls", "errors"):
        assert key in payload["totals"], key
    for row in payload["rows"]:
        for key in ("surface", "name", "calls", "errors",
                    "error_rate", "avg_ms", "max_ms"):
            assert key in row, key
    for bucket in payload["by_surface"].values():
        assert "calls" in bucket


def test_the_panel_asks_for_a_limit_the_endpoint_accepts(seeded, panel_client):
    """The template's own ?limit=... must survive the endpoint's clamp.

    ``_MAX_LIMIT`` is 1000; a template asking for more would be silently served
    fewer names while its note line claimed the number it asked for.
    """
    requested = re.search(r"limit=(\d+)", _fetch_url())
    assert requested, "the panel stopped bounding its own request"

    payload = panel_client.get(_fetch_url()).get_json()
    assert payload["limit"] == int(requested.group(1))


# ------------------------------------------- the panel agrees with the CLI

def test_the_per_surface_breakdown_matches_the_cli_rollup(seeded, cli, panel_client):
    """The note line's per-surface counts come from the rows, not a second query."""
    payload = panel_client.get(f"{_fetch_url()}").get_json()

    from_cli = {}
    for row in cli.json("--limit", "100"):
        bucket = from_cli.setdefault(row["surface"], {"names": 0, "calls": 0, "errors": 0})
        bucket["names"] += 1
        bucket["calls"] += row["calls"]
        bucket["errors"] += row["errors"]

    for surface, bucket in from_cli.items():
        assert payload["by_surface"][surface] == bucket, surface
    assert sum(b["calls"] for b in payload["by_surface"].values()) == TOTAL_CALLS


def test_every_declared_surface_has_a_filter_button(seeded, panel_client):
    """A surface nobody can filter to is a surface nobody will look at.

    The buttons are hardcoded in the template while ``SURFACES`` is the source
    of truth, so a fifth surface would appear in the API response and in the
    CLI's ``--surface`` choices, and be unreachable on the page.
    """
    buttons = set(re.findall(r'data-surface="([^"]*)"', _template_text()))
    assert buttons >= set(invocation_recorder.SURFACES)
    assert "" in buttons, "the 'All' filter is gone"

    payload = panel_client.get(_fetch_url()).get_json()
    assert set(payload["by_surface"]) >= set(invocation_recorder.SURFACES)


def test_a_filter_button_selects_rows_the_endpoint_agrees_exist(seeded, panel_client):
    """Client-side filtering only works if the surface strings match exactly.

    The panel filters ``r.surface === btn.dataset.surface`` in the browser, so a
    button whose value is spelled differently from the column silently shows an
    empty table.
    """
    payload = panel_client.get(_fetch_url()).get_json()
    served = {row["surface"] for row in payload["rows"]}
    buttons = {value for value in re.findall(r'data-surface="([^"]*)"', _template_text())
               if value}
    assert served <= buttons, served - buttons


# ---------------------------------------------- the note line stays honest

def test_a_capped_response_is_flagged_rather_than_passed_off_as_a_total(
    seeded, cli, panel_client
):
    """`limit` bounds names, so the totals under a cap are not the table's totals."""
    capped = panel_client.get("/api/runtime-invocations/summary?limit=2").get_json()
    assert capped["truncated"] is True
    assert capped["totals"]["names"] == 2
    assert capped["totals"]["calls"] < TOTAL_CALLS

    full = panel_client.get("/api/runtime-invocations/summary?limit=100").get_json()
    assert full["truncated"] is False
    assert full["totals"]["calls"] == TOTAL_CALLS
    # The capped rows are the busiest ones, not an arbitrary two — same ranking
    # the CLI applies, checked against the CLI rather than re-derived.
    assert [r["name"] for r in capped["rows"]][:1] == \
        [r["name"] for r in cli.json("--limit", "100")][:1]


def test_an_unavailable_table_is_named_rather_than_rendered_as_zero(
    unmigrated_db, panel_app
):
    """`available: false` is what the note line turns into "run the migration"."""
    payload = panel_app.test_client().get(_fetch_url()).get_json()
    assert payload["ok"] is True
    assert payload["available"] is False
    assert payload["totals"] == {"names": 0, "calls": 0, "errors": 0}
    assert payload["backend"], "the note line would say 'read from undefined'"


def test_the_panel_is_included_by_the_monitoring_page(seeded):
    """An unreferenced partial renders nowhere. Cheap to check, easy to lose."""
    overview = (_TEMPLATE.parent / "overview.html").read_text(encoding="utf-8")
    assert "_runtime_performance.html" in overview


def test_rows_are_json_safe(seeded, panel_client):
    """PostgreSQL returns Decimal for avg()/sum(); Flask cannot serialise it.

    On SQLite this passes trivially — but ``get_json()`` succeeding at all is
    the assertion, and it is the one that would fail first if the endpoint ever
    stopped routing its rows through ``normalise()``.
    """
    rows = by_key(panel_client.get(_fetch_url()).get_json()["rows"])
    for key, row in rows.items():
        assert isinstance(row["calls"], int), key
        assert isinstance(row["errors"], int), key
        assert row["avg_ms"] is None or isinstance(row["avg_ms"], (int, float)), key
        assert row["max_ms"] is None or isinstance(row["max_ms"], int), key


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
