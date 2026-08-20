"""JUnit -> per-file duration snapshot (crx-test-07).

The interesting half is ATTRIBUTION. pytest's JUnit XML carries no `file`
attribute — only a dotted `classname`, which is ambiguous on its own: `tests.a.b`
is either `tests/a/b.py` or a class `b` in `tests/a.py`. Getting it wrong is
silent twice over: the weight lands on a file that does not exist, and the real
file stays unmeasured and gets the median imputed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.ci.shard_timings import (
    attribute,
    balance,
    build_snapshot,
    dotted,
    main,
    parse_junit,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _xml(cases) -> str:
    body = "".join(
        f'<testcase classname="{c}" name="{n}" time="{t}" />' for c, n, t in cases)
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite>{body}</testsuite></testsuites>'


class TestParse:
    def test_sums_every_case_per_classname(self):
        got = parse_junit(_xml([
            ("tests.test_a", "one", "1.5"),
            ("tests.test_a", "two", "2.5"),
            ("tests.test_b", "three", "0.25"),
        ]))
        assert got == {"tests.test_a": 4.0, "tests.test_b": 0.25}

    def test_a_missing_time_contributes_zero_rather_than_aborting(self):
        xml = ('<testsuites><testsuite>'
               '<testcase classname="tests.test_a" name="x" />'
               '<testcase classname="tests.test_a" name="y" time="3" />'
               '</testsuite></testsuites>')
        assert parse_junit(xml) == {"tests.test_a": 3.0}

    def test_an_unparseable_time_contributes_zero(self):
        assert parse_junit(_xml([("tests.test_a", "x", "NaNsense")])) == {
            "tests.test_a": 0.0}

    def test_a_case_with_no_classname_is_skipped(self):
        xml = ('<testsuites><testsuite><testcase name="x" time="9" />'
               '</testsuite></testsuites>')
        assert parse_junit(xml) == {}


class TestDotted:
    @pytest.mark.parametrize("path,want", [
        ("tests/test_a.py", "tests.test_a"),
        ("tests/cortex/test_b.py", "tests.cortex.test_b"),
        ("tests/cortex/", "tests.cortex"),
    ])
    def test_conversion(self, path, want):
        assert dotted(path) == want


class TestAttribution:
    def test_a_class_qualified_classname_lands_on_its_file(self):
        files, unattributed = attribute(
            {"tests.cortex.test_chat_routing.TestChatRouting": 12.0},
            ["tests/cortex/test_chat_routing.py"])
        assert files == {"tests/cortex/test_chat_routing.py": 12.0}
        assert unattributed == {}

    def test_the_longest_matching_target_wins(self):
        """`tests.a.b` must not be attributed to `tests/a.py` when
        `tests/a/b.py` is also gated — the ambiguity the allowlist resolves."""
        files, _ = attribute(
            {"tests.a.b": 5.0},
            ["tests/a.py", "tests/a/b.py"])
        assert files == {"tests/a/b.py": 5.0}

    def test_a_shallow_target_still_wins_when_it_is_the_only_one(self):
        files, _ = attribute({"tests.a.B": 5.0}, ["tests/a.py"])
        assert files == {"tests/a.py": 5.0}

    def test_an_ungated_classname_is_counted_never_guessed(self):
        files, unattributed = attribute(
            {"tests.somewhere_else.test_x": 7.0}, ["tests/a.py"])
        assert files == {}
        assert unattributed == {"tests.somewhere_else.test_x": 7.0}

    def test_several_classes_in_one_file_are_summed(self):
        files, _ = attribute(
            {"tests.a.TestOne": 1.0, "tests.a.TestTwo": 2.0, "tests.a": 0.5},
            ["tests/a.py"])
        assert files == {"tests/a.py": 3.5}


class TestBuildSnapshot:
    def test_records_coverage_and_provenance(self, tmp_path):
        report = tmp_path / "r.xml"
        report.write_text(_xml([("tests.test_a", "x", "4")]), encoding="utf-8")
        doc = build_snapshot([report], ["tests/test_a.py", "tests/test_b.py"],
                             "2026-08-20T00:00:00Z", "unit-test")
        assert doc["durations"] == {"tests/test_a.py": 4.0}
        assert doc["gated_unmeasured"] == ["tests/test_b.py"]
        assert doc["reports_read"] == ["r.xml"]
        assert doc["source"] == "unit-test"
        assert doc["generated_at"] == "2026-08-20T00:00:00Z"

    def test_an_unreadable_report_is_named_not_swallowed(self, tmp_path):
        good = tmp_path / "good.xml"
        good.write_text(_xml([("tests.test_a", "x", "1")]), encoding="utf-8")
        bad = tmp_path / "bad.xml"
        bad.write_text("<not-xml", encoding="utf-8")
        doc = build_snapshot([good, bad], ["tests/test_a.py"], "t", "s")
        assert doc["reports_read"] == ["good.xml"]
        assert len(doc["reports_unreadable"]) == 1
        assert "bad.xml" in doc["reports_unreadable"][0]

    def test_durations_are_sorted_for_a_minimal_diff(self, tmp_path):
        report = tmp_path / "r.xml"
        report.write_text(_xml([
            ("tests.test_z", "x", "1"), ("tests.test_a", "x", "1"),
        ]), encoding="utf-8")
        doc = build_snapshot([report], ["tests/test_a.py", "tests/test_z.py"], "t", "s")
        assert list(doc["durations"]) == ["tests/test_a.py", "tests/test_z.py"]

    def test_reports_from_several_shards_are_merged(self, tmp_path):
        for i, cls in enumerate(("tests.test_a", "tests.test_b")):
            (tmp_path / f"s{i}.xml").write_text(
                _xml([(cls, "x", "2")]), encoding="utf-8")
        doc = build_snapshot(sorted(tmp_path.glob("*.xml")),
                             ["tests/test_a.py", "tests/test_b.py"], "t", "s")
        assert doc["durations"] == {"tests/test_a.py": 2.0, "tests/test_b.py": 2.0}
        assert doc["total_seconds"] == 4.0


class TestRefusesToWriteNothing:
    def test_an_empty_snapshot_would_silently_revert_every_shard(self, tmp_path, capsys):
        """Writing a durations-less snapshot over a good one reverts the whole
        pipeline to round-robin while looking like a successful refresh."""
        (tmp_path / "args" / "ci_test_files").mkdir(parents=True)
        (tmp_path / "args" / "ci_test_files" / "core.txt").write_text(
            "tests/test_a.py\n", encoding="utf-8")
        report = tmp_path / "r.xml"
        report.write_text(_xml([("tests.nothing_gated", "x", "5")]), encoding="utf-8")
        rc = main(["--root", str(tmp_path), "--from-junit", str(report), "--write"])
        assert rc == 1
        assert not (tmp_path / "args" / "ci_test_timings" / "snapshot.json").exists()
        assert "refusing to write an empty snapshot" in capsys.readouterr().err


class TestCliRoundTrip:
    def test_write_then_load_then_partition(self, tmp_path):
        (tmp_path / "args" / "ci_test_files").mkdir(parents=True)
        (tmp_path / "args" / "ci_test_files" / "core.txt").write_text(
            "tests/test_a.py\ntests/test_b.py\n", encoding="utf-8")
        report = tmp_path / "r.xml"
        report.write_text(_xml([
            ("tests.test_a", "x", "90"), ("tests.test_b", "y", "1"),
        ]), encoding="utf-8")

        assert main(["--root", str(tmp_path), "--from-junit", str(report),
                     "--write", "--generated-at", "2026-08-20T00:00:00Z",
                     "--source", "unit-test"]) == 0
        written = tmp_path / "args" / "ci_test_timings" / "snapshot.json"
        assert written.is_file()
        doc = json.loads(written.read_text(encoding="utf-8"))
        assert doc["durations"] == {"tests/test_a.py": 90.0, "tests/test_b.py": 1.0}

        report_2 = balance(tmp_path, shards=2)
        assert report_2["method"] == "duration"
        assert report_2["estimated_seconds"] == [90.0, 1.0]

    def test_balance_without_timings_is_the_round_robin_baseline(self, tmp_path):
        (tmp_path / "args" / "ci_test_files").mkdir(parents=True)
        (tmp_path / "args" / "ci_test_files" / "core.txt").write_text(
            "tests/a.py\ntests/b.py\ntests/c.py\ntests/d.py\n", encoding="utf-8")
        report = balance(tmp_path, shards=2, use_timings=False)
        assert report["method"] == "round_robin"
        assert report["counts"] == [2, 2]
        assert report["estimated_seconds"] is None

    def test_no_action_flags_is_an_error_not_a_silent_success(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            main(["--root", str(tmp_path)])
        assert exc.value.code == 2


class TestAgainstTheLiveSnapshot:
    def test_the_committed_snapshot_measures_the_committed_allowlist(self):
        from tools.ci.gated_test_list import load_timings, resolve
        durations = load_timings(REPO_ROOT)["durations"]
        gated = set(resolve("core", REPO_ROOT))
        # Not "every gated file is measured" — a PR that adds a test legitimately
        # lands one the snapshot has never seen, and the median imputation is
        # exactly for that. What must hold is that the snapshot is ABOUT this
        # allowlist and has not drifted into describing a tree that no longer
        # exists.
        assert durations, "the committed snapshot measures nothing"
        overlap = gated & set(durations)
        assert len(overlap) >= 0.5 * len(gated), (
            f"only {len(overlap)} of {len(gated)} gated targets are measured — "
            "the snapshot has gone stale; run the shard-timings workflow")
