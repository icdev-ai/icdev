# CUI // SP-CTI
"""crx-test-09 -- the sweep must not call a FLAKE an order dependence.

`test_original_retention.py` failed on CI shard 2, passed alone, and the card
written from that evidence named the carrier as shared state surviving between
tests. It was not. The test read a database row that the ingest thread writes
AFTER the result the test waited for, so it could lose at any moment, in any
order, under any partition -- and the shard move only changed its timing.

A sweep that reports "red in the suite, green alone" as order dependence would
have sent the next reader to look for a module-level cache that does not exist.
So the classification, not the shuffling, is what these tests pin: a file that
fails even one of its OWN solo repeats is `flaky_alone` and can never be
reported `order_dependent`, and a red under one shuffle that does not reproduce
when that same shuffle re-runs is `order_suspect`, not a finding.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.ci import order_dependence_sweep as ods

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" tests="4">
<testcase classname="tests.a" file="tests/a.py" name="ok"/>
<testcase classname="tests.a" file="tests/a.py" name="bad"><failure message="boom">t</failure></testcase>
<testcase classname="tests.b" file="tests/b.py" name="fine"/>
<testcase classname="tests.c" file="tests/c.py" name="broken"><error message="import">e</error></testcase>
</testsuite></testsuites>
"""


class TestTheJunitReportIsReadPerFile:
    def test_any_failure_or_error_makes_the_whole_file_failed(self, tmp_path: Path):
        xml = tmp_path / "r.xml"
        xml.write_text(JUNIT, encoding="utf-8")
        out = ods._outcomes(xml, ["tests/a.py", "tests/b.py", "tests/c.py"])
        assert out == {"tests/a.py": "failed", "tests/b.py": "passed", "tests/c.py": "failed"}

    def test_a_file_the_report_never_mentions_is_absent_not_passed(self, tmp_path: Path):
        """A collection error can kill the run before a file is reached. Reading
        that silence as green is how a sweep reports a clean subsystem it never
        measured."""
        xml = tmp_path / "r.xml"
        xml.write_text(JUNIT, encoding="utf-8")
        out = ods._outcomes(xml, ["tests/a.py", "tests/never_ran.py"])
        assert out["tests/never_ran.py"] == "absent"

    def test_a_missing_or_unparsable_report_is_absent_for_every_target(self, tmp_path: Path):
        assert ods._outcomes(tmp_path / "nope.xml", ["tests/a.py"]) == {"tests/a.py": "absent"}
        bad = tmp_path / "bad.xml"
        bad.write_text("<testsuites", encoding="utf-8")
        assert ods._outcomes(bad, ["tests/a.py"]) == {"tests/a.py": "absent"}

    def test_windows_separators_in_a_target_still_match_the_report(self, tmp_path: Path):
        xml = tmp_path / "r.xml"
        xml.write_text(JUNIT, encoding="utf-8")
        out = ods._outcomes(xml, [str(Path("tests") / "b.py")])
        assert out == {"tests/b.py": "passed"}


def _sweep_with(monkeypatch, plan, orders=None):
    """Drive `sweep` over a stubbed pytest. ``plan`` maps a tuple of targets to a
    list of per-run outcome dicts, consumed in order.

    ``orders`` pins the permutations so a test states the order it means rather
    than depending on what `random.Random(seed)` happens to produce.
    """
    calls = {"n": 0}
    state: dict = {}

    def fake_run(root, targets, timeout):
        key = tuple(targets)
        seq = state.setdefault(key, list(plan[key]))
        calls["n"] += 1
        return 0, seq.pop(0) if len(seq) > 1 else seq[0], ""

    monkeypatch.setattr(ods, "_run_pytest", fake_run)
    if orders is not None:
        monkeypatch.setattr(ods, "permutations",
                            lambda files, count, seed: [list(o) for o in orders])
    return calls


class TestAFlakeIsNeverReportedAsOrderDependence:
    """The crx-test-09 defect itself. `test_original_retention.py` raced its own
    ingest thread: it could fail alone, and it did."""

    def test_a_file_that_fails_one_solo_repeat_is_flaky_alone(self, monkeypatch):
        a, b = "tests/a.py", "tests/b.py"
        _sweep_with(monkeypatch, {
            (a,): [{a: "passed"}, {a: "failed"}, {a: "passed"}],   # green, red, green ALONE
            (b,): [{b: "passed"}],
            (a, b): [{a: "failed", b: "passed"}],
            (b, a): [{a: "failed", b: "passed"}],
        }, orders=[(a, b), (b, a)])
        rep = ods.sweep(Path("."), [a, b], permutation_count=2, repeats=3, seed=1, log=lambda *_: None)
        verdicts = {e["file"]: e["verdict"] for e in rep["results"]}
        assert verdicts[a] == ods.FLAKY_ALONE, "a self-flake must never read as ordering"
        assert rep["counts"][ods.ORDER_DEPENDENT] == 0
        assert rep["order_dependent"] == []

    def test_a_file_red_in_every_solo_repeat_is_alone_red_not_ordering(self, monkeypatch):
        a = "tests/a.py"
        _sweep_with(monkeypatch, {(a,): [{a: "failed"}]}, orders=[(a,)])
        rep = ods.sweep(Path("."), [a], permutation_count=1, repeats=2, seed=1, log=lambda *_: None)
        assert rep["results"][0]["verdict"] == ods.ALONE_RED


class TestOrderDependenceIsConfirmedBeforeItIsReported:
    def test_green_alone_and_red_under_an_order_that_reproduces_is_a_finding(self, monkeypatch):
        a, b = "tests/a.py", "tests/b.py"
        _sweep_with(monkeypatch, {
            (a,): [{a: "passed"}],
            (b,): [{b: "passed"}],
            (a, b): [{a: "passed", b: "failed"}],
            (b, a): [{a: "passed", b: "failed"}],
        }, orders=[(a, b), (b, a)])
        rep = ods.sweep(Path("."), [a, b], permutation_count=2, repeats=1, seed=1, log=lambda *_: None)
        verdicts = {e["file"]: e["verdict"] for e in rep["results"]}
        assert verdicts[b] == ods.ORDER_DEPENDENT
        assert rep["order_dependent"] == [b]

    def test_a_red_that_does_not_reproduce_under_the_same_order_is_only_a_suspect(self, monkeypatch):
        """One red under one shuffle is a flake that happened in company. Calling
        it a finding spends someone's afternoon looking for shared state."""
        a, b = "tests/a.py", "tests/b.py"
        _sweep_with(monkeypatch, {
            (a,): [{a: "passed"}],
            (b,): [{b: "passed"}],
            # first run of this order is red; the confirm run is green
            (a, b): [{a: "passed", b: "failed"}, {a: "passed", b: "passed"}],
            (b, a): [{a: "passed", b: "passed"}],
        }, orders=[(a, b), (b, a)])
        rep = ods.sweep(Path("."), [a, b], permutation_count=2, repeats=1, seed=1, log=lambda *_: None)
        verdicts = {e["file"]: e["verdict"] for e in rep["results"]}
        assert verdicts[b] == ods.SUSPECT
        assert rep["counts"][ods.ORDER_DEPENDENT] == 0

    def test_an_absent_file_counts_as_not_green(self, monkeypatch):
        """`absent` means the run never reported on it. That is a finding to
        confirm, not a pass."""
        a, b = "tests/a.py", "tests/b.py"
        _sweep_with(monkeypatch, {
            (a,): [{a: "passed"}],
            (b,): [{b: "passed"}],
            (a, b): [{a: "passed", b: "absent"}],
            (b, a): [{a: "passed", b: "absent"}],
        }, orders=[(a, b), (b, a)])
        rep = ods.sweep(Path("."), [a, b], permutation_count=2, repeats=1, seed=1, log=lambda *_: None)
        assert {e["file"]: e["verdict"] for e in rep["results"]}[b] == ods.ORDER_DEPENDENT


class TestThePermutationsAreReproducible:
    def test_the_same_seed_gives_the_same_orders(self):
        files = [f"tests/t{i}.py" for i in range(8)]
        assert ods.permutations(files, 3, 7) == ods.permutations(files, 3, 7)

    def test_a_different_seed_gives_different_orders(self):
        files = [f"tests/t{i}.py" for i in range(8)]
        assert ods.permutations(files, 3, 7) != ods.permutations(files, 3, 8)

    def test_every_permutation_is_the_whole_set(self):
        files = [f"tests/t{i}.py" for i in range(8)]
        for order in ods.permutations(files, 4, 0):
            assert sorted(order) == sorted(files)


class TestTheBisectNamesTheNeighbour:
    def test_it_shrinks_to_the_last_file_of_the_smallest_reproducing_prefix(self, monkeypatch):
        order = ["tests/p0.py", "tests/p1.py", "tests/p2.py", "tests/p3.py", "tests/victim.py"]

        def fake_run(root, targets, timeout):
            # only p3 immediately before the victim reproduces it
            failed = "tests/p3.py" in targets
            return 0, {t: ("failed" if (t == "tests/victim.py" and failed) else "passed")
                       for t in targets}, ""

        monkeypatch.setattr(ods, "_run_pytest", fake_run)
        got = ods.bisect_predecessor(Path("."), order, "tests/victim.py", 10, log=lambda *_: None)
        assert got == "tests/p3.py"

    def test_no_reproducing_prefix_returns_none_rather_than_guessing(self, monkeypatch):
        order = ["tests/p0.py", "tests/victim.py"]
        monkeypatch.setattr(ods, "_run_pytest",
                            lambda r, t, to: (0, {x: "passed" for x in t}, ""))
        assert ods.bisect_predecessor(Path("."), order, "tests/victim.py", 10,
                                      log=lambda *_: None) is None


class TestASweepThatRanNothingIsNotASweepThatFoundNothing:
    def test_no_matching_file_exits_two(self, capsys):
        assert ods.main(["--match", "no-such-subsystem-anywhere"]) == 2

    def test_the_gated_filter_only_returns_files_that_exist(self):
        root = Path(ods.REPO_ROOT)
        got = ods.gated_files(root, "document_intelligence")
        assert got, "the document_intelligence suite is gated; an empty list is a bug here"
        assert all((root / rel).is_file() for rel in got)
        assert all("document_intelligence" in rel for rel in got)

    def test_the_file_this_card_is_about_is_in_the_swept_set(self):
        assert "tests/document_intelligence/test_original_retention.py" in \
            ods.gated_files(Path(ods.REPO_ROOT), "document_intelligence")


class TestTheGateOnlyFiresOnAConfirmedFinding:
    @pytest.mark.parametrize("counts,expected", [
        ({ods.ORDER_DEPENDENT: 1}, 1),
        ({ods.ORDER_DEPENDENT: 0, ods.FLAKY_ALONE: 3, ods.ALONE_RED: 2}, 0),
    ])
    def test_gate_exit_code(self, monkeypatch, counts, expected):
        monkeypatch.setattr(ods, "sweep", lambda *a, **k: {
            "root": ".", "files": 1, "permutations": 1, "repeats": 1, "seed": 0,
            "counts": counts, "order_dependent": [], "results": [], "elapsed_seconds": 0.0,
        })
        assert ods.main(["--files", "tests/a.py", "--gate", "--json"]) == expected
