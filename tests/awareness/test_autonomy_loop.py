# CUI // SP-CTI
"""Is intervention actually falling? Measured with an honest denominator (autonomy-lrn-02).

A self-improving system that cannot show its improvement is a claim. These tests
pin the three ways this measurement could quietly become one:

  * a RATE manufactured from an empty denominator — `pct if total else 100.0`
    is the defect perfect_score_census (rem-hyg-13) drained from twelve sites,
    and every rate here must be None, never 0.0 and never 100.0, when nothing
    was measured;
  * an UNMEASURABLE folded into clean — a fleet where no process could be
    assessed has an UNKNOWN stale count, not a stale count of 0, and a
    headline cannot read `falling` over sections that were never measured;
  * a TREND asserted from one point — a delta is None whenever either side is
    missing, and the baselines are carried as recorded, never recomputed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import autonomy_loop as al  # noqa: E402

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeClaim:
    claim_id: str
    tier: str = "propose"
    tags: List[str] = field(default_factory=list)


def _pr(number, *, created, merged=None):
    return {"number": number, "createdAt": created.isoformat(),
            "mergedAt": merged.isoformat() if merged else None,
            "state": "MERGED" if merged else "OPEN"}


# --------------------------------------------------------------------------- #
# 1. The one place a rate is computed
# --------------------------------------------------------------------------- #
def test_rate_over_an_empty_denominator_is_none_not_a_perfect_score():
    assert al._rate(0, 0) is None
    assert al._rate(None, 10) is None
    assert al._rate(0, 0) != 100.0
    assert al._rate(0, 0) != 0.0


def test_rate_over_a_real_denominator_is_a_number():
    assert al._rate(27, 232, 1) == 11.6
    assert al._rate(0, 10) == 0.0, "a MEASURED zero is a real answer"


# --------------------------------------------------------------------------- #
# 2. Claims — seeded from a REAL incident, verified against the board
# --------------------------------------------------------------------------- #
def test_incident_refs_come_from_card_shaped_tags_or_an_explicit_field():
    assert al.incident_refs(FakeClaim("a", tags=["compliance", "rem-hyg-09"])) == ["rem-hyg-09"]
    assert al.incident_refs(FakeClaim("b", tags=["evidence-quality"])) == []
    explicit = SimpleNamespace(incident="autonomy-lrn-01", tags=["autonomy-lrn-01", "x"])
    assert al.incident_refs(explicit) == ["autonomy-lrn-01"], "explicit first, tag not duplicated"


def test_declared_is_the_registry_s_word_and_verified_is_the_board_s():
    registry = [FakeClaim("a", tags=["rem-hyg-09"]),
                FakeClaim("b", tags=["cch-obs-03"]),
                FakeClaim("c", tags=["evidence-quality"])]
    got = al.measure_claims(registry, board_ids=["rem-hyg-09"])
    assert got["registered"] == 3
    assert got["incident_declared"] == 2
    assert got["incident_verified_on_board"] == 1, "cch-obs-03 is declared but not on this board"
    assert got["unreferenced_claims"] == ["c"]
    assert got["incident_share_pct"] == 66.7
    assert got["verified_share_pct"] == 33.3


def test_an_unreadable_board_leaves_verified_none_while_declared_still_counts():
    got = al.measure_claims([FakeClaim("a", tags=["rem-hyg-09"])], board_ids=None)
    assert got["incident_declared"] == 1
    assert got["incident_verified_on_board"] is None
    assert got["verified_share_pct"] is None
    assert "unreadable" in got["reason"]


def test_an_empty_registry_is_unmeasurable_with_no_rates():
    got = al.measure_claims([], board_ids=[])
    assert got["state"] == al.UNMEASURABLE
    assert got["incident_share_pct"] is None
    assert got["verified_share_pct"] is None


# --------------------------------------------------------------------------- #
# 3. Live catches — unmeasurable until a run is persisted; a snapshot is a snapshot
# --------------------------------------------------------------------------- #
def test_live_catches_are_unmeasurable_without_a_persisted_history():
    got = al.measure_live_catches(reflex_row=None, snapshot=None)
    assert got["state"] == al.UNMEASURABLE
    assert got["caught_live"] is None
    assert got["snapshot_now"] is None


def test_a_reflex_run_count_and_a_clean_snapshot_do_not_make_caught_live_zero():
    snapshot = {"counts": {"agrees": 4, "disagrees": 0, "unmeasurable": 0},
                "results": [{"claim_id": "x", "verdict": "agrees"}]}
    reflex = {"reflex_name": "claim_verifier", "total_runs": 12, "last_run_at": "2026-08-21"}
    got = al.measure_live_catches(reflex, snapshot)
    assert got["caught_live"] is None, "a snapshot of 0 disagreements is not 0 caught live"
    assert got["verifier_reflex"]["total_runs"] == 12
    assert got["snapshot_now"]["disagrees"] == 0
    assert got["snapshot_now"]["disagreeing"] == []


# --------------------------------------------------------------------------- #
# 4. Duplicate dispatch
# --------------------------------------------------------------------------- #
def test_duplicate_stats_count_branches_not_prs():
    old = NOW - timedelta(days=30)
    branches = [
        [_pr(1, created=old, merged=old), _pr(2, created=old + timedelta(days=1), merged=old + timedelta(days=2))],
        [_pr(3, created=old)],
        [_pr(4, created=old, merged=old), _pr(5, created=old), _pr(6, created=old)],
    ]
    got = al.duplicate_stats(branches)
    assert got["branches"] == 3 and got["prs"] == 6
    assert got["with_multiple_prs"] == 2
    assert got["with_two_merged"] == 1
    assert got["multiple_pr_rate_pct"] == 66.7
    assert got["two_merged_rate_pct"] == 33.3


def test_duplicate_window_is_keyed_on_the_first_pr_and_states_its_censoring():
    old = NOW - timedelta(days=30)
    recent = NOW - timedelta(days=2)
    prs = {
        "kanban/old": [_pr(1, created=old), _pr(2, created=old + timedelta(days=1))],
        "kanban/new": [_pr(3, created=recent)],
        "kanban/new2": [_pr(4, created=recent), _pr(5, created=recent + timedelta(hours=1))],
    }
    got = al.measure_duplicates(prs, window_start=NOW - timedelta(days=7))
    assert got["state"] == al.MEASURED
    assert got["lifetime"]["branches"] == 3 and got["lifetime"]["with_multiple_prs"] == 2
    assert got["window"]["branches"] == 2 and got["window"]["with_multiple_prs"] == 1
    assert got["window"]["multiple_pr_rate_pct"] == 50.0
    assert "lower bound" in got["window"]["censoring"]


def test_duplicate_rate_is_none_when_the_forge_did_not_answer_or_answered_nothing():
    for prs in (None, {}):
        got = al.measure_duplicates(prs, window_start=NOW - timedelta(days=7))
        assert got["state"] == al.UNMEASURABLE
        assert got["lifetime"] is None and got["window"] is None
    empty_window = al.measure_duplicates(
        {"kanban/old": [_pr(1, created=NOW - timedelta(days=30))]},
        window_start=NOW - timedelta(days=7))
    assert empty_window["window"]["branches"] == 0
    assert empty_window["window"]["multiple_pr_rate_pct"] is None, "no branches in window -> None, not 0.0"


# --------------------------------------------------------------------------- #
# 5. Admission — the gate's own survey, plus what it actually persisted
# --------------------------------------------------------------------------- #
def test_admission_rates_are_rederived_and_recorded_refusals_kept_apart():
    survey = {"state": "measured", "dispatches": 200, "fires": 6, "right": 5, "wrong": 1}
    got = al.measure_admission(survey, recorded_refusals=0, mode="report")
    assert got["state"] == al.MEASURED
    assert got["fire_rate_pct"] == 3.0
    assert got["wrong_of_fires_pct"] == 16.7
    assert got["wrong_of_dispatches_pct"] == 0.5
    assert got["recorded_refusals"] == 0
    assert "report mode" in got["recorded_refusals_note"]


def test_admission_with_no_survey_or_an_unmeasurable_one_has_no_rates():
    got = al.measure_admission(None, recorded_refusals=None, mode="report")
    assert got["state"] == al.UNMEASURABLE
    assert got["fire_rate_pct"] is None and got["fires"] is None
    unmeasurable = {"state": "unmeasurable", "reason": "no recorded scheduler dispatches",
                    "dispatches": 0, "fires": None}
    got = al.measure_admission(unmeasurable, recorded_refusals=0, mode="report")
    assert got["state"] == al.UNMEASURABLE
    assert got["fire_rate_pct"] is None
    assert got["reason"] == "no recorded scheduler dispatches"


def test_a_measured_survey_with_zero_fires_has_no_wrong_of_fires_rate():
    survey = {"state": "measured", "dispatches": 50, "fires": 0, "right": 0, "wrong": 0}
    got = al.measure_admission(survey, recorded_refusals=0, mode="report")
    assert got["fire_rate_pct"] == 0.0, "a measured zero fire rate is a real answer"
    assert got["wrong_of_fires_pct"] is None, "0 of 0 fires is not a rate"


# --------------------------------------------------------------------------- #
# 6. Stale daemons — and for how long
# --------------------------------------------------------------------------- #
class _GitLog:
    def __init__(self, stdout="", returncode=0, raise_exc=None):
        self.stdout, self.returncode, self.raise_exc = stdout, returncode, raise_exc
        self.calls = []

    def __call__(self, args, root):
        self.calls.append(args)
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(stdout=self.stdout, returncode=self.returncode)


def test_stale_since_is_the_earliest_commit_touching_the_closure():
    runner = _GitLog("2026-08-20T01:00:00+00:00\n2026-08-21T03:00:00+00:00\n")
    got = al.stale_since("abc123", "origin/main", ["tools/a.py", "tools/b.py"], runner=runner)
    assert got == datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)
    args = runner.calls[0]
    assert args[:3] == ["log", "--format=%cI", "--reverse"]
    assert "abc123..origin/main" in args
    assert args[args.index("--") + 1:] == ["tools/a.py", "tools/b.py"]


def test_stale_since_is_none_when_git_cannot_answer():
    assert al.stale_since("abc", "origin/main", ["x.py"], runner=_GitLog(returncode=128)) is None
    assert al.stale_since("abc", "origin/main", ["x.py"], runner=_GitLog(raise_exc=OSError("no git"))) is None
    assert al.stale_since("abc", "origin/main", [], runner=_GitLog("2026-08-20T01:00:00+00:00")) is None
    assert al.stale_since(None, "origin/main", ["x.py"], runner=_GitLog("2026-08-20T01:00:00+00:00")) is None


def _staleness(*processes):
    return {"state": "measured", "processes": list(processes)}


def test_a_fleet_where_nothing_could_be_assessed_has_an_unknown_stale_count():
    rep = _staleness(
        {"session_id": "a", "verdict": "unmeasurable", "reason": "no recorded code version"},
        {"session_id": "b", "verdict": "unmeasurable", "reason": "no recorded code version"},
    )
    got = al.measure_fleet(rep, identity_columns_present=False, now=NOW, runner=_GitLog())
    assert got["state"] == al.UNMEASURABLE
    assert got["live_processes"] == 2
    assert got["stale"] is None and got["current"] is None, "unknown, never 0"
    assert got["stale_rate_pct"] is None
    assert "20260821024132" in got["reason"], "names the migration, which is the fix"


def test_the_other_cause_of_an_unassessable_fleet_is_named_differently():
    rep = _staleness({"session_id": "a", "verdict": "unmeasurable", "reason": "no recorded code version"})
    got = al.measure_fleet(rep, identity_columns_present=True, now=NOW, runner=_GitLog())
    assert got["state"] == al.UNMEASURABLE
    assert "restart" in got["reason"] and "20260821024132" not in got["reason"]


def test_a_stale_process_reports_how_long_and_whether_that_is_a_lower_bound():
    rep = _staleness(
        {"session_id": "daemon", "module": "tools.genesis.daemon", "pid": 1, "verdict": "stale",
         "code_version": "abc123", "code_dirty": 0, "changed_count": 30,
         "changed_in_closure": [f"tools/f{i}.py" for i in range(25)],
         "reason": "30 file(s) it imports changed since it booted"},
        {"session_id": "sched", "module": "tools.kanban.sched", "pid": 2, "verdict": "current",
         "code_version": "abc123", "changed_count": 0, "changed_in_closure": []},
    )
    runner = _GitLog((NOW - timedelta(hours=5, minutes=30)).isoformat() + "\n")
    got = al.measure_fleet(rep, identity_columns_present=True, now=NOW, runner=runner)
    assert got["state"] == al.MEASURED
    assert got["assessed"] == 2 and got["stale"] == 1 and got["current"] == 1
    assert got["stale_rate_pct"] == 50.0
    stale = next(p for p in got["processes"] if p["verdict"] == "stale")
    assert stale["stale_for_seconds"] == 5 * 3600 + 30 * 60
    assert stale["stale_for"] == "5h 30m"
    assert stale["stale_for_is_lower_bound"] is True, "25 of 30 files listed -> the earliest over a subset is a lower bound"
    current = next(p for p in got["processes"] if p["verdict"] == "current")
    assert current["stale_for_seconds"] is None


def test_a_detector_that_could_not_run_passes_its_reason_through():
    got = al.measure_fleet({"state": "unmeasurable", "reason": "registry unreadable: boom"},
                           identity_columns_present=None, now=NOW)
    assert got["state"] == al.UNMEASURABLE
    assert got["reason"] == "registry unreadable: boom"
    assert got["stale"] is None and got["stale_rate_pct"] is None
    none_live = al.measure_fleet({"state": "no_live_processes"}, identity_columns_present=True, now=NOW)
    assert none_live["live_processes"] == 0 and none_live["stale"] is None


# --------------------------------------------------------------------------- #
# 7. Trend and headline
# --------------------------------------------------------------------------- #
def test_trend_needs_two_points():
    assert al.trend(None, 11.6)["delta_pct_points"] is None
    assert al.trend(7.0, None)["direction"] is None
    down = al.trend(7.0, 11.6)
    assert down == {"baseline_pct": 11.6, "current_pct": 7.0, "delta_pct_points": -4.6,
                    "direction": "down", "improving": True}
    assert al.trend(11.6, 11.6)["improving"] is False
    assert al.trend(12.0, 11.6)["improving"] is False


def test_headline_cannot_be_falling_over_unmeasured_sections():
    trends = {"a": al.trend(7.0, 11.6), "b": al.trend(None, 2.99)}
    got = al.headline(trends, unmeasured_sections=["live_catches", "stale_daemons"])
    assert got["verdict"] == "falling_where_measured"
    assert got["measured_trends"] == ["a"] and got["unmeasured_trends"] == ["b"]
    assert got["unmeasured_sections"] == ["live_catches", "stale_daemons"]
    clean = al.headline({"a": al.trend(7.0, 11.6)}, unmeasured_sections=[])
    assert clean["verdict"] == "falling"


def test_headline_is_not_falling_when_any_measured_trend_worsens_and_none_when_nothing_measured():
    worse = al.headline({"a": al.trend(7.0, 11.6), "b": al.trend(4.5, 2.99)}, unmeasured_sections=[])
    assert worse["verdict"] == "not_falling"
    nothing = al.headline({"a": al.trend(None, 11.6)}, unmeasured_sections=["x"])
    assert nothing["verdict"] is None


# --------------------------------------------------------------------------- #
# 8. The whole report on a deployment with NO operating history
# --------------------------------------------------------------------------- #
class _DeadConn:
    """A connection where every query raises — the shape of a fresh worktree's DB."""

    def execute(self, *_a, **_k):
        raise RuntimeError("relation does not exist")

    def close(self):
        pass


def _walk_pct(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_pct(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_pct(v, f"{path}[{i}]")
    elif path.endswith("_pct") or path.endswith("_pct_points"):
        yield path, obj


def test_a_deployment_with_no_history_reports_unmeasurable_and_manufactures_no_rate():
    rep = al.collect(window_days=7, conn=_DeadConn(), prs_by_branch={}, use_forge=True,
                     live_verify=False, now=NOW, registry=[],
                     staleness={"state": "no_live_processes"})
    for section in ("claims", "live_catches", "duplicate_dispatch", "admission", "stale_daemons"):
        assert rep[section]["state"] == al.UNMEASURABLE, section
    assert rep["headline"]["verdict"] is None
    assert sorted(rep["headline"]["unmeasured_sections"]) == sorted(
        ["claims", "live_catches", "duplicate_dispatch", "admission", "stale_daemons"])
    # The carried baselines are the ONLY numbers allowed to survive: they are
    # recorded facts, dated, not measurements of this deployment.
    numbers = [(p, v) for p, v in _walk_pct(rep)
               if v is not None and not p.startswith(".baselines")
               and not p.endswith(".baseline_pct")]
    assert numbers == [], f"a rate was manufactured from nothing: {numbers}"


def test_the_baselines_are_carried_as_recorded_and_dated():
    rep = al.collect(window_days=7, conn=_DeadConn(), prs_by_branch={}, live_verify=False,
                     now=NOW, registry=[], staleness={"state": "no_live_processes"})
    assert rep["baselines"]["duplicate_dispatch"]["rate_pct"] == 11.6
    assert rep["baselines"]["duplicate_dispatch"]["measured_on"] == "2026-08-20"
    assert rep["baselines"]["admission"]["fire_rate_pct"] == 2.99
    for name, t in rep["trend"].items():
        assert t["baseline_pct"] is not None, name
        assert t["current_pct"] is None and t["delta_pct_points"] is None, name


def test_the_forge_is_not_consulted_when_told_not_to(monkeypatch):
    from tools.kanban import dispatch_admission as da

    def _boom():
        raise AssertionError("gh must not be called with use_forge=False")

    monkeypatch.setattr(da, "_all_kanban_prs", _boom)
    rep = al.collect(window_days=7, conn=_DeadConn(), use_forge=False, live_verify=False,
                     now=NOW, registry=[], staleness={"state": "no_live_processes"})
    assert rep["duplicate_dispatch"]["state"] == al.UNMEASURABLE
    assert rep["admission"]["state"] == al.UNMEASURABLE


def test_render_never_crashes_on_an_all_unmeasurable_report():
    rep = al.collect(window_days=7, conn=_DeadConn(), prs_by_branch={}, live_verify=False,
                     now=NOW, registry=[], staleness={"state": "no_live_processes"})
    text = al.render(rep)
    assert "UNMEASURABLE" in text
    assert "100.0%" not in text and "100%" not in text


# --------------------------------------------------------------------------- #
# 9. Report only — no gate
# --------------------------------------------------------------------------- #
def test_there_is_no_gate_flag():
    import argparse

    captured = {}

    def _fake_parse(self, args=None, namespace=None):
        captured["opts"] = [a.option_strings for a in self._actions]
        raise SystemExit(0)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(argparse.ArgumentParser, "parse_args", _fake_parse)
        with pytest.raises(SystemExit):
            al.main([])
    flat = [o for opts in captured["opts"] for o in opts]
    assert "--gate" not in flat, "it measures the board and the fleet, not a diff (kpr-fix-03)"
