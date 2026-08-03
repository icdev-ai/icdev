# CUI // SP-CTI
"""Unit tests for tools/innovation/benchmark_compare.py (xbm-cmp-01).

The scouting half of the platform could already say what an external project
was doing. It could not say what that meant for ICDEV, which is the only part
that generates action. These tests pin the properties that make the comparison
trustworthy rather than merely present:

  1. Every tracked project yields exactly one verdict naming an ICDEV
     subsystem and its measured local state. Nothing is silently dropped — an
     unroutable project is reported as ``unmapped``, not skipped.
  2. ``ahead — no adaptation needed`` is reachable, and is produced by the
     shipped catalogue for at least one subsystem. A benchmark tool that can
     only report deficits misrepresents the platform, and this is the test
     that stops it from drifting into one.
  3. An unreadable database yields ``not_assessed``, never ``unfed``. The
     comparator must not manufacture a "built but unfed" gap out of its own
     blindness.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.innovation import benchmark_compare as bc  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _spec(**overrides):
    """A minimal subsystem spec: built, fed, nothing outstanding."""
    spec = {
        "title": "Test subsystem",
        "map_section": 1,
        # A directory that really exists, so compare() paths that measure disk
        # produce a built subsystem rather than an artefact of the fixture.
        "modules": ["tools/innovation"],
        "min_modules": 3,
        "tables": ["thing_rows"],
        "data_expected": True,
        "adaptations": [],
        "caveats": [],
    }
    spec.update(overrides)
    return spec


def _catalog(**subsystems):
    return {
        "version": 1,
        "category_fallback": {"legacy_cat": "alpha"},
        "subsystems": subsystems or {"alpha": _spec()},
    }


def _assess(spec, modules=5, rows=None, **kwargs):
    """Assess a spec with both measurements injected — no disk, no database."""
    return bc.assess_subsystem(
        "alpha",
        spec,
        modules={"total": modules, "per_path": {}, "missing": []},
        row_counts={"thing_rows": 10} if rows is None else rows,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The verdict this tool exists to be able to say
# ---------------------------------------------------------------------------
def test_ahead_is_produced_when_built_fed_and_nothing_outstanding():
    result = _assess(_spec())
    assert result["verdict"] == bc.VERDICT_AHEAD
    assert result["verdict_label"] == "ahead — no adaptation needed"
    assert result["adaptation_needed"] is False
    assert result["local_state"]["code_state"] == bc.CODE_BUILT
    assert result["local_state"]["data_state"] == bc.DATA_POPULATED


def test_shipped_catalogue_actually_produces_an_ahead_subsystem():
    """The real args file, measured against the real tree, must yield ahead.

    This is the acceptance criterion, asserted against what ships rather than
    against a fixture. The database half is skipped so the assertion holds on a
    machine with no database; a subsystem that reaches ahead without needing
    row evidence reaches it with row evidence too.
    """
    catalog = bc.load_catalog()
    assert catalog.get("subsystems"), "args/benchmark_subsystems.yaml did not load"

    report = bc.compare(catalog=catalog, targets=[], use_db=False)
    ahead = [s for s in report["subsystems"] if s["verdict"] == bc.VERDICT_AHEAD]
    assert ahead, (
        "no subsystem reached 'ahead — no adaptation needed'. Either the catalogue "
        "regressed or the comparator can now only report deficits."
    )
    assert any(s["subsystem"] == "compliance_ato" for s in ahead), (
        "compliance_ato is the benchmark map's strongest measured differentiator and "
        "must remain expressible as ahead"
    )


def test_empty_tables_are_not_a_gap_when_emptiness_is_not_a_finding():
    """compliance_ato's tables fill per engagement, not by the platform running."""
    result = _assess(_spec(data_expected=False), rows={"thing_rows": 0})
    assert result["local_state"]["data_state"] == bc.DATA_NOT_EXPECTED
    assert result["verdict"] == bc.VERDICT_AHEAD


# ---------------------------------------------------------------------------
# Gaps, stated as measurement
# ---------------------------------------------------------------------------
def test_thin_module_count_is_a_gap():
    result = _assess(_spec(min_modules=10), modules=2)
    assert result["verdict"] == bc.VERDICT_GAP
    assert result["local_state"]["code_state"] == bc.CODE_THIN
    assert "2 modules against a floor of 10" in result["reasons"][0]


def test_no_modules_at_all_is_a_gap_not_an_error():
    result = _assess(_spec(), modules=0)
    assert result["verdict"] == bc.VERDICT_GAP
    assert result["local_state"]["code_state"] == bc.CODE_ABSENT


def test_built_but_unfed_is_a_gap():
    result = _assess(_spec(), rows={"thing_rows": 0})
    assert result["verdict"] == bc.VERDICT_GAP
    assert result["local_state"]["data_state"] == bc.DATA_UNFED
    assert "built but unfed" in result["reasons"][0]


def test_data_floor_catches_demo_sized_row_counts():
    """The benchmark map called four rows 'nothing running through it'.

    A floor of zero would have scored that as fed and lost the finding.
    """
    unfed = _assess(_spec(data_floor=50), rows={"thing_rows": 12})
    assert unfed["local_state"]["data_state"] == bc.DATA_UNFED
    fed = _assess(_spec(data_floor=50), rows={"thing_rows": 500})
    assert fed["local_state"]["data_state"] == bc.DATA_POPULATED


# ---------------------------------------------------------------------------
# Parity — an external concept ICDEV has not adopted
# ---------------------------------------------------------------------------
def test_open_adaptation_blocks_ahead():
    spec = _spec(adaptations=[{"name": "Event spec", "source": "OpenLineage", "status": "open"}])
    result = _assess(spec)
    assert result["verdict"] == bc.VERDICT_PARITY
    assert result["adaptation_needed"] is True
    assert result["outstanding_adaptations"][0]["source"] == "OpenLineage"


def test_deferred_adaptation_also_blocks_ahead():
    """Deferred means unadopted-and-unscheduled, not adopted."""
    spec = _spec(adaptations=[{"name": "Policy packs", "source": "Checkov", "status": "deferred"}])
    assert _assess(spec)["verdict"] == bc.VERDICT_PARITY


def test_done_adaptation_does_not_block_ahead_and_is_kept_on_the_record():
    spec = _spec(
        adaptations=[{"name": "Analyzer contract", "source": "TheHive Cortex", "status": "done"}]
    )
    result = _assess(spec)
    assert result["verdict"] == bc.VERDICT_AHEAD
    assert result["adopted_adaptations"][0]["name"] == "Analyzer contract"


def test_caveats_are_reported_but_do_not_move_the_verdict():
    """Worktree leak is real, internal, and not something an external project has."""
    spec = _spec(caveats=["Worktree reclamation is unbounded"])
    result = _assess(spec)
    assert result["verdict"] == bc.VERDICT_AHEAD
    assert result["caveats"] == ["Worktree reclamation is unbounded"]


# ---------------------------------------------------------------------------
# Honesty about what was not measured
# ---------------------------------------------------------------------------
def test_unreadable_database_yields_not_assessed_never_unfed():
    result = _assess(_spec(), rows={})          # table declared, nothing readable
    assert result["local_state"]["data_state"] == bc.DATA_NOT_ASSESSED
    assert result["verdict"] != bc.VERDICT_GAP
    assert any("not assessed" in limit for limit in result["measurement_limits"])


def test_missing_module_directory_is_recorded_as_a_limit():
    result = bc.assess_subsystem(
        "alpha",
        _spec(),
        modules={"total": 5, "per_path": {}, "missing": ["tools/gone"]},
        row_counts={"thing_rows": 10},
    )
    assert any("tools/gone" in limit for limit in result["measurement_limits"])


def test_never_benchmarked_subsystem_says_its_clean_sheet_is_unexamined():
    result = _assess(_spec(map_section=None))
    assert result["verdict"] == bc.VERDICT_AHEAD
    assert any("absent review" in limit for limit in result["measurement_limits"])


def test_unowned_subsystem_is_out_of_scope_not_a_gap():
    spec = _spec(owned=False, modules=[], tables=[], data_expected=False)
    result = bc.assess_subsystem(
        "alpha", spec, modules={"total": 0, "per_path": {}, "missing": []}, row_counts={}
    )
    assert result["verdict"] == bc.VERDICT_OUT_OF_SCOPE


def test_count_rows_rolls_back_so_one_bad_probe_does_not_hide_the_rest():
    """On PostgreSQL a failed statement aborts the transaction.

    Without the rollback every later probe fails too, and a report full of
    existing tables reads as a report full of missing ones.
    """

    class _Cursor:
        def __init__(self, owner):
            self.owner = owner

        def execute(self, sql):
            if "missing_table" in sql:
                raise RuntimeError('relation "missing_table" does not exist')
            if self.owner.aborted:
                raise RuntimeError("current transaction is aborted")

        def fetchone(self):
            return (7,)

    class _Conn:
        def __init__(self):
            self.aborted = False
            self.rollbacks = 0

        def cursor(self):
            return _Cursor(self)

        def rollback(self):
            self.rollbacks += 1
            self.aborted = False

    conn = _Conn()
    conn.aborted = False
    measured = bc.measure_tables(["missing_table", "real_table"], conn=conn)
    assert measured["rows"]["missing_table"] is None
    assert measured["rows"]["real_table"] == 7
    assert conn.rollbacks == 1


# ---------------------------------------------------------------------------
# Routing — every tracked project yields a verdict
# ---------------------------------------------------------------------------
def test_explicit_subsystem_tag_routes():
    catalog = _catalog()
    key, route, declared = bc.resolve_subsystem({"repo": "a/b", "subsystem": "alpha"}, catalog)
    assert (key, route, declared) == ("alpha", bc.ROUTE_TAG, "alpha")


def test_category_fallback_routes_a_watchlist_written_before_the_tag_existed():
    catalog = _catalog()
    key, route, _ = bc.resolve_subsystem({"repo": "a/b", "category": "legacy_cat"}, catalog)
    assert (key, route) == ("alpha", bc.ROUTE_CATEGORY_FALLBACK)


def test_unroutable_project_is_reported_not_dropped():
    catalog = _catalog()
    report = bc.compare(
        catalog=catalog,
        targets=[{"repo": "who/knows", "subsystem": "nonexistent"}],
        use_db=False,
    )
    row = report["projects"][0]
    assert row["verdict"] == bc.VERDICT_UNMAPPED
    assert row["declared"] == "nonexistent"
    assert "nonexistent" in row["reasons"][0]
    assert report["summary"]["unmapped_projects"] == ["who/knows"]


def test_every_tracked_project_yields_exactly_one_row():
    catalog = _catalog(alpha=_spec(), beta=_spec(title="Beta"))
    targets = [
        {"repo": "a/one", "subsystem": "alpha"},
        {"repo": "a/two", "subsystem": "beta"},
        {"name": "Closed Source Co", "url": "https://x", "trackable": False, "subsystem": "alpha"},
        {"repo": "a/four", "subsystem": "nope"},
    ]
    report = bc.compare(catalog=catalog, targets=targets, use_db=False)
    assert len(report["projects"]) == len(targets)
    assert report["summary"]["projects_tracked"] == 4
    for row in report["projects"]:
        assert row["verdict"] in bc.VERDICT_LABELS
        if row["verdict"] != bc.VERDICT_UNMAPPED:
            assert row["subsystem"]
            assert row["local_state"]["modules"] >= 0


def test_untrackable_manual_review_entry_still_gets_a_verdict():
    """Cortex.io has no public repo; it still benchmarks a subsystem."""
    catalog = _catalog()
    report = bc.compare(
        catalog=catalog,
        targets=[{"name": "Cortex.io", "url": "https://cortex.io", "trackable": False, "subsystem": "alpha"}],
        use_db=False,
    )
    row = report["projects"][0]
    assert row["project"] == "Cortex.io"
    assert row["trackable"] is False
    assert row["subsystem"] == "alpha"


def test_subsystem_nobody_watches_is_surfaced():
    catalog = _catalog(alpha=_spec(), beta=_spec(title="Beta"))
    report = bc.compare(
        catalog=catalog, targets=[{"repo": "a/one", "subsystem": "alpha"}], use_db=False
    )
    assert report["summary"]["subsystems_unwatched"] == ["beta"]


def test_no_db_run_declares_that_it_skipped_the_data_half():
    report = bc.compare(catalog=_catalog(), targets=[], use_db=False)
    assert report["summary"]["database"] == "skipped"
    markdown = bc.render_markdown(report)
    assert "--no-db" in markdown


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_markdown_names_the_subsystem_and_its_measured_state():
    catalog = _catalog()
    report = bc.compare(
        catalog=catalog,
        targets=[{"repo": "back/stage", "subsystem": "alpha"}],
        use_db=False,
    )
    markdown = bc.render_markdown(report)
    assert "back/stage" in markdown
    assert "`alpha`" in markdown
    assert "modules" in markdown


def test_markdown_calls_out_the_ahead_subsystems_by_name():
    report = bc.compare(
        catalog=_catalog(),
        targets=[],
        use_db=False,
        row_counts={"thing_rows": 10},
    )
    markdown = bc.render_markdown(report)
    assert "Ahead — no adaptation needed" in markdown


def test_cli_json_runs_without_a_database(capsys, tmp_path):
    rc = bc.main(["--json", "--no-db"])
    assert rc == 0
    payload = capsys.readouterr().out
    assert '"verdict"' in payload
    assert '"ahead"' in payload


def test_cli_reports_an_empty_catalogue_rather_than_pretending(tmp_path, capsys):
    empty = tmp_path / "empty.yaml"
    empty.write_text("subsystems: {}\n", encoding="utf-8")
    assert bc.main(["--json", "--no-db", "--catalog", str(empty)]) == 1


def test_module_counting_ignores_dunder_init_and_records_missing_dirs(tmp_path):
    (tmp_path / "tools" / "real").mkdir(parents=True)
    (tmp_path / "tools" / "real" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "real" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "real" / "b.py").write_text("", encoding="utf-8")

    measured = bc.count_modules(["tools/real", "tools/gone"], base_dir=tmp_path)
    assert measured["total"] == 2
    assert measured["missing"] == ["tools/gone"]


def test_catalogue_entries_are_well_formed():
    """Every shipped subsystem must declare what the comparator reads."""
    catalog = bc.load_catalog()
    for key, spec in catalog["subsystems"].items():
        assert spec.get("title"), f"{key} has no title"
        assert "map_section" in spec, f"{key} does not say whether it was benchmarked"
        assert isinstance(spec.get("modules", []), list), f"{key} modules must be a list"
        for item in spec.get("adaptations") or []:
            assert item.get("name"), f"{key} has an unnamed adaptation"
            assert item.get("source"), f"{key} adaptation {item.get('name')!r} names no source"
            assert str(item.get("status")) in {"open", "deferred", "done"}, (
                f"{key} adaptation {item.get('name')!r} has an unknown status"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
