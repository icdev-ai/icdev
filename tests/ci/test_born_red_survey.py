"""rem-hyg-14 — a test that was red from birth must be reported, not seeded.

The defect these cover is a MISSING BRANCH, not a wrong one. The drift reflex's
ladder is::

    if was is None:            seed, report nothing
    elif was == 'pass' ...:    regression

so the first observation of a permanently-broken file lands in the seeding
branch and the file is never mentioned again. Every assertion below is about
telling that case apart from the two it currently gets merged into: a file that
regressed (already reported) and a file nobody has run (not a clean bill).
"""
# CUI // SP-CTI

from __future__ import annotations

import importlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

born_red_survey = importlib.import_module("tools.ci.born_red_survey")


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _obs(status, first_status=None, ever_passed=None, days_ago=10.0, detail="1 failed"):
    return {
        "status": status,
        "first_status": first_status,
        "ever_passed": ever_passed,
        "first_seen": (NOW - timedelta(days=days_ago)).isoformat(),
        "last_checked": NOW.isoformat(),
        "last_detail": detail,
    }


class TestClassify:
    """Five states, and the three failing ones must never collapse into one."""

    def test_no_observation_is_unobserved_not_clean(self):
        assert born_red_survey.classify(None) == "unobserved"
        assert born_red_survey.classify({}) == "unobserved"

    def test_first_observation_was_a_failure_is_born_red(self):
        assert born_red_survey.classify(_obs("fail", first_status="fail",
                                             ever_passed=0)) == "born_red"

    def test_seen_passing_once_is_a_regression_not_born_red(self):
        # The drift reflex already files this one; reporting it here too would
        # double-file the same finding.
        assert born_red_survey.classify(_obs("fail", first_status="pass",
                                             ever_passed=1)) == "regressed"

    def test_the_latch_outranks_the_first_verdict(self):
        # Seeded 'fail', later observed passing, broken again. `first_status`
        # alone would call this born red; it is a regression.
        assert born_red_survey.classify(_obs("fail", first_status="fail",
                                             ever_passed=1)) == "regressed"

    def test_a_row_with_no_recorded_history_says_so(self):
        # Pre-migration rows. Guessing either way would invent evidence.
        assert born_red_survey.classify(_obs("fail", first_status=None,
                                             ever_passed=None)) == "history_unknown"

    def test_passing_is_not_this_tools_business(self):
        assert born_red_survey.classify(_obs("pass", first_status="pass",
                                             ever_passed=1)) == "passing"


class TestSurveyRanking:
    """Ranked by how long it has been failing, with the basis stated."""

    @pytest.fixture(autouse=True)
    def _no_git_or_backlog(self, monkeypatch):
        monkeypatch.setattr(born_red_survey, "default_branch_ref", lambda root: "origin/main")

    def _run(self, monkeypatch, files, observations, landings):
        monkeypatch.setattr(born_red_survey, "effective_backlog", lambda root: files)
        monkeypatch.setattr(
            born_red_survey, "landed_at",
            lambda root, rel, ref=None: landings.get(
                rel, {"commit": None, "landed_at": None}),
        )
        return born_red_survey.survey(root=Path("."), now=NOW,
                                      observations=observations)

    def test_longest_standing_first(self, monkeypatch):
        report = self._run(
            monkeypatch,
            ["tests/a.py", "tests/b.py"],
            {"tests/a.py": _obs("fail", "fail", 0, days_ago=2),
             "tests/b.py": _obs("fail", "fail", 0, days_ago=2)},
            {"tests/a.py": {"commit": "aaa", "landed_at": (NOW - timedelta(days=5)).isoformat()},
             "tests/b.py": {"commit": "bbb", "landed_at": (NOW - timedelta(days=44)).isoformat()}},
        )
        assert [r["path"] for r in report["findings"]] == ["tests/b.py", "tests/a.py"]
        assert report["findings"][0]["red_days"] == pytest.approx(44.0, abs=0.2)

    def test_the_two_durations_are_never_merged(self, monkeypatch):
        report = self._run(
            monkeypatch, ["tests/a.py"],
            {"tests/a.py": _obs("fail", "fail", 0, days_ago=3)},
            {"tests/a.py": {"commit": "aaa",
                            "landed_at": (NOW - timedelta(days=40)).isoformat()}},
        )
        row = report["findings"][0]
        # Proven span and the upper bound are both carried, and the rank says
        # which one it used.
        assert row["observed_red_days"] == pytest.approx(3.0, abs=0.2)
        assert row["file_age_days"] == pytest.approx(40.0, abs=0.2)
        assert row["red_days_basis"] == "file_age_upper_bound"

    def test_no_git_history_falls_back_to_the_proven_span(self, monkeypatch):
        report = self._run(
            monkeypatch, ["tests/a.py"],
            {"tests/a.py": _obs("fail", "fail", 0, days_ago=3)},
            {},
        )
        row = report["findings"][0]
        assert row["red_days_basis"] == "observed_only"
        assert row["red_days"] == pytest.approx(3.0, abs=0.2)

    def test_regressions_are_not_findings_here(self, monkeypatch):
        report = self._run(
            monkeypatch, ["tests/a.py"],
            {"tests/a.py": _obs("fail", "pass", 1)}, {},
        )
        assert report["findings"] == []
        assert report["counts"]["regressed"] == 1

    def test_unobserved_is_counted_and_never_folded_into_clean(self, monkeypatch):
        report = self._run(
            monkeypatch, ["tests/a.py", "tests/b.py"],
            {"tests/a.py": _obs("pass", "pass", 1)}, {},
        )
        assert report["counts"]["unobserved"] == 1
        assert report["unobserved"] == 1
        assert report["state"] == "clean"
        # The human table must SAY the uninspected files exist.
        assert "never run 1" in born_red_survey.render(report)

    def test_the_population_always_adds_up(self, monkeypatch):
        report = self._run(
            monkeypatch, ["tests/a.py", "tests/b.py", "tests/c.py"],
            {"tests/a.py": _obs("pass", "pass", 1)}, {},
        )
        assert report["observed"] + report["unobserved"] == report["backlog_total"]
        assert sum(report["counts"].values()) == report["backlog_total"]

    def test_a_file_measured_this_run_leaves_unobserved_exactly_once(
            self, monkeypatch):
        """`--run` inspects a file, so it is no longer uninspected — but it was
        never counted as unobserved either, and subtracting it a second time
        understated the uninspected population by the number just inspected."""
        monkeypatch.setattr(born_red_survey, "effective_backlog",
                            lambda root: ["tests/a.py", "tests/b.py", "tests/c.py"])
        monkeypatch.setattr(born_red_survey, "landed_at",
                            lambda root, rel, ref=None: {"commit": None, "landed_at": None})
        monkeypatch.setattr(
            born_red_survey, "measure",
            lambda root, rel, timeout=0, db_dir=None: {
                "status": "pass", "detail": "ok", "returncode": 0, "seconds": 0.1})
        report = born_red_survey.survey(root=Path("."), now=NOW, run_limit=1,
                                        observations={})
        assert report["measured_now"] == 1
        assert report["observed"] == 1
        assert report["unobserved"] == 2
        assert report["observed"] + report["unobserved"] == report["backlog_total"]

    def test_nothing_recorded_is_unmeasurable_not_zero(self, monkeypatch):
        report = self._run(monkeypatch, ["tests/a.py", "tests/b.py"], {}, {})
        assert report["state"] == "unmeasurable"
        # None, never 0 — "measured clean" and "never measured" justify
        # opposite decisions.
        assert report["born_red_count"] is None
        assert report["counts"]["born_red"] is None
        assert report["counts"]["unobserved"] == 2
        assert "UNMEASURABLE" in born_red_survey.render(report)


class TestBirthConfirmation:
    """Three outcomes. Two of them are findings and the third is neither."""

    def _fake(self, monkeypatch, returncode, stdout="1 failed, 9 passed in 1s"):
        monkeypatch.setattr(born_red_survey, "_git",
                            lambda root, *a, **k: subprocess.CompletedProcess(
                                a, 0, "", ""))
        monkeypatch.setattr(Path, "is_file", lambda self: True)

        def _run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")

        monkeypatch.setattr(born_red_survey.subprocess, "run", _run)

    def test_failing_at_the_landing_commit_confirms(self, monkeypatch, tmp_path):
        monkeypatch.setattr(born_red_survey, "_scratch_root", lambda: tmp_path)
        self._fake(monkeypatch, 1)
        res = born_red_survey.confirm_at_birth(Path("."), "tests/a.py", "abc123def456")
        assert res["verdict"] == "confirmed_born_red"

    def test_passing_at_the_landing_commit_is_its_own_finding(self, monkeypatch, tmp_path):
        monkeypatch.setattr(born_red_survey, "_scratch_root", lambda: tmp_path)
        self._fake(monkeypatch, 0, "20 passed in 2s")
        res = born_red_survey.confirm_at_birth(Path("."), "tests/a.py", "abc123def456")
        # It worked when it landed and broke later, silently. NOT born red.
        assert res["verdict"] == "passed_at_birth"

    @pytest.mark.parametrize("code", [2, 3, 4, 5])
    def test_a_tree_that_could_not_run_it_confirms_nothing(self, monkeypatch,
                                                           tmp_path, code):
        # A collection error on a six-week-old tree is a statement about that
        # checkout's dependencies, not about the test.
        monkeypatch.setattr(born_red_survey, "_scratch_root", lambda: tmp_path)
        self._fake(monkeypatch, code, "ERROR collecting")
        res = born_red_survey.confirm_at_birth(Path("."), "tests/a.py", "abc123def456")
        assert res["verdict"] == "birth_unrunnable"
        assert res["reason"]


class TestLandedAt:
    def test_walks_the_default_branch_first_parent(self, monkeypatch):
        """The ADD commit is on a feature branch whose tree lacks whatever else
        merged that day — the exact reason the measured example was green alone
        and red on main. The landing MERGE is the tree that matters."""
        seen = {}

        def _git(root, *args, **kwargs):
            seen["args"] = args
            return subprocess.CompletedProcess(args, 0, "sha123|2026-07-07T21:45:08-04:00", "")

        monkeypatch.setattr(born_red_survey, "_git", _git)
        out = born_red_survey.landed_at(Path("."), "tests/a.py", "origin/main")
        assert "--first-parent" in seen["args"]
        assert "origin/main" in seen["args"]
        assert out["commit"] == "sha123"

    def test_a_path_with_no_history_reports_none_not_today(self, monkeypatch):
        monkeypatch.setattr(born_red_survey, "_git",
                            lambda root, *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
        out = born_red_survey.landed_at(Path("."), "tests/a.py")
        assert out["commit"] is None and out["landed_at"] is None


class TestReportOnly:
    def test_there_is_no_gate_flag(self):
        """kpr-fix-03: a survey with a --gate earns itself a `|| true`."""
        with pytest.raises(SystemExit):
            born_red_survey.main(["--gate"])

    def test_findings_still_exit_zero(self, monkeypatch, capsys):
        canned = {
            "ran": True, "state": "findings", "backlog_total": 1, "observed": 1,
            "unobserved": 0, "coverage_pct": 100.0,
            "counts": {k: 0 for k in born_red_survey.STATES},
            "born_red_count": 1, "broke_after_birth_count": 0,
            "confirmations": None, "measured_now": 0,
            "findings": [{"path": "tests/a.py", "state": "born_red",
                          "red_days": 42.0, "red_days_basis": "file_age_upper_bound",
                          "landed_at": None, "landed_commit": None,
                          "observed_red_days": 3.0, "detail": "1 failed"}],
        }
        monkeypatch.setattr(born_red_survey, "survey", lambda **kw: canned)
        # A survey that FOUND something is still a survey, not a gate.
        assert born_red_survey.main([]) == 0
        assert "BORN RED" in capsys.readouterr().out

    def test_an_unproducible_survey_exits_two_not_zero(self, monkeypatch):
        def _boom(**kwargs):
            raise born_red_survey.SurveyError("no backlog")

        monkeypatch.setattr(born_red_survey, "survey", _boom)
        # A survey that could not run is never a survey that found nothing.
        assert born_red_survey.main([]) == 2


class _FakeRow(dict):
    pass


class _FakeConn:
    """Records every statement so the reflex's WRITE decisions can be asserted."""

    def __init__(self, existing):
        self.existing = existing
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split()), params))
        if "SELECT path, status FROM" in sql:
            return _FakeCursor([_FakeRow(p) for p in self.existing])
        if "ORDER BY last_checked" in sql:
            return _FakeCursor([_FakeRow({"path": p["path"]}) for p in self.existing])
        return _FakeCursor([])

    def commit(self):
        pass

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class TestReflexRecordsHistory:
    """The reflex must record WHICH verdict was first, and latch a pass."""

    def _drive(self, monkeypatch, verdict_status, existing=()):
        drift = importlib.import_module("tools.genesis.reflexes.ungated_test_drift")
        conn = _FakeConn(list(existing))
        storage = importlib.import_module("tools.db.storage")
        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
        monkeypatch.setattr(drift, "_ungated_files", lambda: ["tests/a.py"])
        monkeypatch.setattr(drift, "_table_exists", lambda c: True)
        monkeypatch.setattr(drift, "_has_history_columns", lambda c: True)
        monkeypatch.setattr(drift, "_run_alone",
                            lambda rel: {"status": verdict_status,
                                         "detail": "1 failed", "seconds": 0.1})
        result = drift.run({"sample": 1, "dry_run": True})
        return conn, result

    def test_a_first_observation_that_FAILS_records_first_status_fail(self, monkeypatch):
        # Without this the row is indistinguishable from one that regressed a
        # moment ago, and born-red stays invisible for ever.
        conn, result = self._drive(monkeypatch, "fail")
        inserts = [s for s in conn.statements if s[0].startswith("INSERT OR IGNORE")]
        assert inserts, "the reflex did not seed a baseline row"
        sql, params = inserts[0]
        assert "first_status" in sql and "ever_passed" in sql
        assert params[-2:] == ("fail", 0)
        assert result["history_recorded"] is True

    def test_a_pass_latches_ever_passed(self, monkeypatch):
        conn, _ = self._drive(monkeypatch, "pass")
        updates = [s for s in conn.statements if s[0].startswith("UPDATE")]
        assert any("ever_passed = 1" in s[0] for s in updates)

    def test_a_fail_never_clears_the_latch(self, monkeypatch):
        # A file that passed once and broke is a REGRESSION; clearing the latch
        # would relabel it born-red and double-file the drift reflex's finding.
        conn, _ = self._drive(monkeypatch, "fail",
                              existing=[{"path": "tests/a.py", "status": "pass"}])
        updates = [s for s in conn.statements if s[0].startswith("UPDATE")]
        assert updates and not any("ever_passed" in s[0] for s in updates)

    def test_a_pre_migration_deployment_still_records_transitions(self, monkeypatch):
        drift = importlib.import_module("tools.genesis.reflexes.ungated_test_drift")
        conn = _FakeConn([])
        storage = importlib.import_module("tools.db.storage")
        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
        monkeypatch.setattr(drift, "_ungated_files", lambda: ["tests/a.py"])
        monkeypatch.setattr(drift, "_table_exists", lambda c: True)
        monkeypatch.setattr(drift, "_has_history_columns", lambda c: False)
        monkeypatch.setattr(drift, "_run_alone",
                            lambda rel: {"status": "fail", "detail": "x", "seconds": 0.1})
        result = drift.run({"sample": 1, "dry_run": True})
        # Naming a column the deployed table lacks raises at runtime and is
        # swallowed by the surrounding handler — the INSERT/schema-parity trap.
        inserts = [s for s in conn.statements if s[0].startswith("INSERT OR IGNORE")]
        assert inserts and "first_status" not in inserts[0][0]
        assert result["history_recorded"] is False
        assert result["success"] is True


class TestUngatedFilesReadsFragments:
    def test_a_file_promoted_by_a_core_d_fragment_is_not_ungated(self, tmp_path, monkeypatch):
        """core.txt PLUS core.d/ is the allowlist (tsg-policy-03). Reading only
        the list file reports a freshly gated file as ungated forever."""
        drift = importlib.import_module("tools.genesis.reflexes.ungated_test_drift")
        root = tmp_path
        (root / "args" / "ci_test_files" / "core.d").mkdir(parents=True)
        (root / "args" / "ci_test_files" / "core.txt").write_text(
            "tests/test_listed.py\n", encoding="utf-8")
        (root / "args" / "ci_test_files" / "core.d" / "rem-hyg-14.txt").write_text(
            "tests/test_fragment.py\n", encoding="utf-8")
        (root / "tests").mkdir()
        for name in ("test_listed.py", "test_fragment.py", "test_ungated.py"):
            (root / "tests" / name).write_text("", encoding="utf-8")
        monkeypatch.setattr(drift, "BASE_DIR", root)
        assert drift._ungated_files() == ["tests/test_ungated.py"]
