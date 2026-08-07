# CUI // SP-CTI
"""`icdev runtime top` and the Runtime Performance panel, over one database.

Both read ``runtime_invocations`` (migration 341); the CLI landed first
(obs-cov-02-d3) and the panel second (obs-cov-02-d4), and the panel was built to
call the CLI's ``normalise()`` precisely so the two could not drift. Nothing
enforced that: ``tests/test_runtime_top_cli.py`` seeds its own database and
checks the terminal, ``tests/test_runtime_invocations_panel.py`` seeds its own
and checks the HTTP payload, and both would stay green through a change that
made one of them answer a different question — a second rollup query written
into the endpoint, a limit clamped on one side only, a rounding rule applied to
the JSON but not the table.

These tests seed once and read twice. Every assertion is a comparison between
the two readers, or between one reader and the seed; none of them re-states an
implementation detail of either.

Fixtures are in ``conftest.py``; the seed's contents are in
``_runtime_expectations.py`` so that "what is true" is stated in one place
rather than inferred from whichever reader ran first.
"""
from __future__ import annotations

import sys

import pytest
from _runtime_expectations import (
    EXPECTED,
    STILL_RUNNING,
    TOTAL_CALLS,
    TOTAL_ERRORS,
    by_key,
    shared,
)

_ENDPOINT = "/api/runtime-invocations/summary"


def _panel_rows(client, query=""):
    payload = client.get(f"{_ENDPOINT}{query}").get_json()
    assert payload["ok"] is True, payload
    return payload["rows"]


# --------------------------------------------------------------- the seed

def test_the_recorder_wrote_what_both_readers_will_be_asked_about(seeded, cli):
    """Anchor: if the seed is wrong, every comparison below is vacuous."""
    rows = by_key(cli.json("--limit", "100"))
    assert set(rows) == set(EXPECTED)
    for key, (calls, errors) in EXPECTED.items():
        assert (rows[key]["calls"], rows[key]["errors"]) == (calls, errors), key


# ------------------------------------------------------ CLI vs. the panel

def test_cli_and_panel_report_the_same_rollup(seeded, cli, panel_client):
    """The acceptance criterion: a mismatch on any shared field fails here."""
    from_cli = by_key(cli.json("--limit", "100"))
    from_panel = by_key(_panel_rows(panel_client, "?limit=100"))

    assert set(from_cli) == set(from_panel)
    for key in from_cli:
        assert shared(from_cli[key]) == shared(from_panel[key]), key


def test_cli_and_panel_agree_on_a_still_running_group(seeded, cli, panel_client):
    """NULL avg/max is not zero milliseconds — on either reader.

    Called out separately because it is the one value the two are most likely
    to diverge on: the panel serialises it to JSON null and the CLI prints a
    dash, and a "helpful" coalesce on one side would claim an instant call.
    """
    from_cli = by_key(cli.json("--limit", "100"))[STILL_RUNNING]
    from_panel = by_key(_panel_rows(panel_client, "?limit=100"))[STILL_RUNNING]

    assert from_cli["avg_ms"] is None and from_cli["max_ms"] is None
    assert from_panel["avg_ms"] is None and from_panel["max_ms"] is None


def test_cli_and_panel_agree_under_a_surface_filter(seeded, cli, panel_client):
    from_cli = by_key(cli.json("--surface", "mcp"))
    from_panel = by_key(_panel_rows(panel_client, "?surface=mcp"))

    assert {surface for surface, _ in from_cli} == {"mcp"}
    assert set(from_cli) == set(from_panel)
    for key in from_cli:
        assert shared(from_cli[key]) == shared(from_panel[key]), key


@pytest.mark.parametrize("surface", ["mcp", "agent", "persona", "role"])
def test_every_surface_filters_identically_including_the_silent_one(
    surface, seeded, cli, panel_client
):
    """`role` recorded nothing, and both readers must say so the same way.

    A surface with no rows is the finding migration 341 exists to surface, so
    "returns nothing" has to be a shared answer rather than one reader's empty
    list and the other's error.
    """
    from_cli = by_key(cli.json("--surface", surface, "--limit", "100"))
    from_panel = by_key(_panel_rows(panel_client, f"?surface={surface}&limit=100"))
    assert set(from_cli) == set(from_panel)


def test_cli_and_panel_agree_on_what_limit_bounds(seeded, cli, panel_client):
    """`limit` caps NAMES, not invocations — and caps them the same way twice."""
    from_cli = cli.json("--limit", "2")
    from_panel = _panel_rows(panel_client, "?limit=2")

    assert len(from_cli) == len(from_panel) == 2
    # rag_search is the only name with more than one call, so the busiest row is
    # not a tie and both readers must rank it first.
    assert from_cli[0]["name"] == from_panel[0]["name"] == "rag_search"
    assert shared(from_cli[0]) == shared(from_panel[0])


def test_cli_and_panel_share_the_same_limit_floor(seeded, cli, panel_client):
    """`LIMIT 0` would silently return nothing; both clamp it to one.

    Two separate clamps (`max(1, ...)` in the CLI, `_parse_limit` in the API)
    implement this, and nothing but this test ties them together.
    """
    assert len(cli.json("--limit", "0")) == 1
    assert len(_panel_rows(panel_client, "?limit=0")) == 1


def test_panel_totals_describe_the_same_calls_the_cli_counts(seeded, cli, panel_client):
    payload = panel_client.get(f"{_ENDPOINT}?limit=100").get_json()
    rows = cli.json("--limit", "100")

    assert payload["totals"] == {
        "names": len(rows),
        "calls": sum(row["calls"] for row in rows),
        "errors": sum(row["errors"] for row in rows),
    }
    assert payload["totals"]["calls"] == TOTAL_CALLS
    assert payload["totals"]["errors"] == TOTAL_ERRORS


def test_panel_error_rate_is_derived_from_the_counts_the_cli_prints(
    seeded, cli, panel_client
):
    """The one panel-only field. It must be a function of the shared ones."""
    from_cli = by_key(cli.json("--limit", "100"))
    for key, row in by_key(_panel_rows(panel_client, "?limit=100")).items():
        counts = from_cli[key]
        expected = round(counts["errors"] / counts["calls"], 4) if counts["calls"] else 0.0
        assert row["error_rate"] == expected, key


# -------------------------------------------------- the rendered terminal

def test_the_printed_table_carries_the_same_numbers_as_the_panel(
    seeded, cli, panel_client
):
    """The table is a third rendering of the rollup, and it can drift alone.

    ``--json`` and the panel share ``normalise()``; the human table does not —
    it goes through ``render()``, where a formatting change could silently
    disagree with both. Only the integer columns are compared: the table prints
    a rounded average, and asserting on that would test the rounding rule
    rather than the agreement.
    """
    panel = by_key(_panel_rows(panel_client, "?limit=100"))[("mcp", "rag_search")]
    line = next(
        text for text in cli.table("--limit", "100").out.splitlines()
        if text.startswith("mcp") and "rag_search" in text
    )
    surface, name, calls, errors, _avg, maximum = line.split()

    assert (surface, name) == ("mcp", "rag_search")
    assert int(calls) == panel["calls"]
    assert int(errors) == panel["errors"]
    assert int(maximum) == panel["max_ms"]


def test_the_printed_table_shows_a_dash_where_the_panel_sends_null(seeded, cli):
    running_surface, running_name = STILL_RUNNING
    line = next(
        text for text in cli.table("--limit", "100").out.splitlines()
        if text.startswith(running_surface) and running_name in text
    )
    assert line.split()[-2:] == ["-", "-"], line


# ------------------------------------------------- honesty about the table

def test_neither_reader_calls_a_missing_table_an_idle_runtime(
    unmigrated_db, cli, panel_app
):
    """The failure both readers were built to avoid, checked in one place.

    ``summary()`` swallows its own exceptions and returns ``[]``, so "nothing
    ran" and "the migration never ran here" are the same value — and a git
    worktree has no ``.env``, so falling into an empty SQLite file is the
    ordinary case, not an exotic one. The CLI answers by naming the backend on
    stderr; the panel answers with ``available: false``. Neither may answer
    with a confident empty table.
    """
    captured = cli.table()
    assert captured.out == ""
    assert "no invocations recorded" in captured.err
    assert "read from" in captured.err

    payload = panel_app.test_client().get(_ENDPOINT).get_json()
    assert payload["ok"] is True
    assert payload["available"] is False
    assert payload["rows"] == []
    assert payload["backend"]


def test_an_empty_but_present_table_is_not_reported_as_missing(
    runtime_db, cli, panel_client
):
    """The other half of the same distinction: migrated, but nothing ran yet."""
    assert cli.json() == []

    payload = panel_client.get(_ENDPOINT).get_json()
    assert payload["available"] is True
    assert payload["rows"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
