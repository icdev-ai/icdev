# CUI // SP-CTI
"""Tests for `icdev runtime top`.

Driven against a real SQLite database with migration 341 applied rather than a
mocked ``summary()``. The command's whole job is to render what the telemetry
table actually holds, and a mocked rollup would assert the shape of the
formatter against numbers this repo never produced.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tools.cli import runtime
from tools.observability import invocation_recorder as R

# Resolved by GLOB, not a pinned number — this migration has been renumbered
# three times (329 -> 333 -> 341) and each rename broke hardcoded paths.
_MIGRATION = next(
    (Path(__file__).resolve().parent.parent / "tools/db/migrations")
    .glob("*_runtime_invocations/up.py")
)


@pytest.fixture()
def obs_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with runtime_invocations created."""
    db = tmp_path / "obs.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_OBS_INVOCATIONS", raising=False)
    monkeypatch.setattr(R, "_table_missing", False, raising=False)

    import tools.db.storage as storage

    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    spec = importlib.util.spec_from_file_location("m341_top", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up()
    return db


@pytest.fixture()
def populated(obs_db):
    """Three names with known call/error counts, across two surfaces."""
    for _ in range(3):
        with R.record(R.SURFACE_MCP, "rag_search"):
            pass
    with pytest.raises(ValueError):
        with R.record(R.SURFACE_MCP, "rag_search"):
            raise ValueError("boom")
    with R.record(R.SURFACE_MCP, "kg_search"):
        pass
    with R.record(R.SURFACE_AGENT, "builder"):
        pass
    return obs_db


# ------------------------------------------------------------------ rendering

def test_normalise_coerces_decimal_and_null_aggregates():
    """PG hands back Decimal; a group of still-running rows hands back NULL."""
    from decimal import Decimal

    rows = runtime.normalise([
        {"surface": "mcp", "name": "a", "calls": Decimal("4"),
         "errors": Decimal("1"), "avg_ms": Decimal("12.34"), "max_ms": Decimal("40")},
        {"surface": "mcp", "name": "b", "calls": 1, "errors": 0,
         "avg_ms": None, "max_ms": None},
    ])
    assert rows[0] == {"surface": "mcp", "name": "a", "calls": 4, "errors": 1,
                       "avg_ms": 12.3, "max_ms": 40}
    assert rows[1]["avg_ms"] is None and rows[1]["max_ms"] is None
    # JSON-safety is the point of normalise(): Decimal is not serialisable.
    json.dumps(rows)


def test_render_columns_are_aligned_and_null_durations_show_a_dash():
    lines = runtime.render(runtime.normalise([
        {"surface": "mcp", "name": "a-very-long-tool-name", "calls": 2,
         "errors": 0, "avg_ms": None, "max_ms": None},
        {"surface": "agent", "name": "b", "calls": 1, "errors": 0,
         "avg_ms": 5, "max_ms": 5},
    ]))
    header, rule = lines[0], lines[1]
    assert header.startswith("SURFACE") and set(rule) <= {"-", " "}
    assert len(rule) == len(header.rstrip()) or len(rule) >= len(header)
    assert lines[2].split()[-2:] == ["-", "-"], "NULL durations must render as -"


def test_render_truncates_long_names_but_json_keeps_them(populated, capsys):
    long_name = "mcp__icdev-unified__" + "x" * 80
    with R.record(R.SURFACE_MCP, long_name):
        pass
    assert runtime.main(["top", "--no-color"]) == 0
    assert long_name not in capsys.readouterr().out, "name was not truncated"

    assert runtime.main(["top", "--json"]) == 0
    names = [r["name"] for r in json.loads(capsys.readouterr().out)]
    assert long_name in names, "--json must carry the full name"


def test_render_colours_only_rows_that_have_errors():
    rows = runtime.normalise([
        {"surface": "mcp", "name": "bad", "calls": 2, "errors": 1, "avg_ms": 1, "max_ms": 2},
        {"surface": "mcp", "name": "good", "calls": 2, "errors": 0, "avg_ms": 1, "max_ms": 2},
    ])
    bad, good = runtime.render(rows, colour=True)[2:4]
    assert "\033[31m" in bad and "\033[31m" not in good


def test_render_does_not_colour_by_substring_match():
    """`workflow_loop_create` contains "low" — it must not be painted green."""
    line = runtime.render(runtime.normalise([
        {"surface": "mcp", "name": "workflow_loop_create", "calls": 1,
         "errors": 0, "avg_ms": 1, "max_ms": 1},
    ]), colour=True)[2]
    assert "\033[" not in line


# ----------------------------------------------------------------------- CLI

def test_top_prints_a_table_with_real_summary_statistics(populated, capsys):
    assert runtime.main(["top", "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "SURFACE" in out and "CALLS" in out and "ERRORS" in out

    row = next(ln for ln in out.splitlines() if ln.startswith("mcp") and "rag_search" in ln)
    fields = row.split()
    assert fields[:4] == ["mcp", "rag_search", "4", "1"], f"wrong rollup: {row}"
    assert "6 call(s)  1 error(s)" in out


def test_top_ranks_by_call_count(populated, capsys):
    runtime.main(["top", "--no-color"])
    body = [ln for ln in capsys.readouterr().out.splitlines()
            if ln and not ln.startswith(("SURFACE", "-")) and "call(s)" not in ln]
    assert "rag_search" in body[0], "busiest name must be first"


def test_surface_filter_restricts_the_rollup(populated, capsys):
    assert runtime.main(["top", "--surface", "agent", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and {r["surface"] for r in rows} == {"agent"}


def test_limit_bounds_the_number_of_names(populated, capsys):
    assert runtime.main(["top", "--limit", "1", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_non_positive_limit_does_not_produce_an_empty_table(populated, capsys):
    """`LIMIT 0` would silently return nothing; the floor keeps it truthful."""
    assert runtime.main(["top", "--limit", "0", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_json_output_is_machine_readable(populated, capsys):
    assert runtime.main(["top", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all(
        set(r) == {"surface", "name", "calls", "errors", "avg_ms", "max_ms"} for r in rows
    )
    assert all(isinstance(r["calls"], int) and isinstance(r["errors"], int) for r in rows)


def test_empty_table_names_the_database_rather_than_looking_broken(obs_db, capsys):
    """An empty table and the WRONG database must not look identical."""
    assert runtime.main(["top", "--no-color"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no invocations recorded" in captured.err and "read from" in captured.err


def test_empty_table_still_emits_valid_json(obs_db, capsys):
    assert runtime.main(["top", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_query_failure_exits_one(obs_db, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(runtime.invocation_recorder, "summary", boom)
    assert runtime.main(["top"]) == 1
    assert "query failed" in capsys.readouterr().err


def test_bad_surface_is_rejected_by_the_parser():
    with pytest.raises(SystemExit) as exc:
        runtime.main(["top", "--surface", "not-a-surface"])
    assert exc.value.code == 2


# ------------------------------------------------------------------ dispatcher

def test_icdev_runtime_is_wired_into_the_cli_dispatcher(populated, capsys):
    """`icdev runtime top` must reach this module, not print 'unknown subcommand'."""
    from tools.cli.__main__ import main as icdev_main

    assert icdev_main(["runtime", "top", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)


def test_runtime_appears_in_the_subcommand_index(capsys):
    from tools.cli.__main__ import main as icdev_main

    assert icdev_main(["--help"]) == 0
    assert "runtime top" in capsys.readouterr().out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
