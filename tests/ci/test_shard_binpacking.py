"""Timing-aware shard partitioning (crx-test-07).

The invariants here are the ones whose violation reports GREEN. A partition that
drops a file does not turn CI red — the suite simply never runs that file — and
a partition that is not stable across processes puts a file on shard 2 in one
job and shard 4 in another, so some files run twice and others never while every
shard exits 0. Neither is observable downstream, so both are asserted directly.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci.gated_test_list import (
    AllowlistError,
    entry_duration,
    load_timings,
    partition,
    parse_timing_snapshot,
    shard,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _entries(n: int) -> list:
    return [f"tests/test_{i:03d}.py" for i in range(n)]


def _snapshot(root: Path, name: str, generated_at: str, durations: dict) -> Path:
    d = root / "args" / "ci_test_timings"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps({"generated_at": generated_at, "durations": durations}),
                 encoding="utf-8")
    return p


class TestLossless:
    """`sum(len(shard_i)) == len(resolved)` — the assertion the task names."""

    def test_every_entry_lands_exactly_once(self):
        entries = _entries(37)
        durations = {e: float(i * i % 17) for i, e in enumerate(entries)}
        shards, _ = partition(entries, 4, None, durations)
        packed = [e for s in shards for e in s]
        assert sorted(packed) == sorted(entries)
        assert sum(len(s) for s in shards) == len(entries)

    def test_shards_are_disjoint(self):
        entries = _entries(37)
        durations = {e: float(i % 7) for i, e in enumerate(entries)}
        shards, _ = partition(entries, 5, None, durations)
        seen = set()
        for s in shards:
            assert not (seen & set(s)), "a file landed on two shards"
            seen |= set(s)
        assert seen == set(entries)

    def test_one_shard_returns_everything(self):
        entries = _entries(11)
        shards, _ = partition(entries, 1, None, {e: 1.0 for e in entries})
        assert shards[0] == entries

    def test_more_shards_than_entries_leaves_empties_not_losses(self):
        # `check()` reports an empty shard as an ERROR; the partition itself must
        # still be lossless rather than raising, so the error names the real cause.
        entries = _entries(3)
        shards, _ = partition(entries, 5, None, {e: 1.0 for e in entries})
        assert sum(len(s) for s in shards) == 3
        assert sorted(e for s in shards for e in s) == entries


class TestDeterminism:
    """No builtin `hash()`: PYTHONHASHSEED is randomised per process."""

    def test_repeated_calls_agree(self):
        entries = _entries(40)
        durations = {e: float((i * 31) % 23) for i, e in enumerate(entries)}
        first, _ = partition(entries, 4, None, durations)
        for _ in range(5):
            again, _ = partition(entries, 4, None, durations)
            assert again == first

    def test_stable_across_processes_with_different_hash_seeds(self, tmp_path):
        """The failure this guards is invisible: two shards computed in two
        processes disagree about which files exist, so files silently go unrun
        and every shard still exits 0."""
        entries = _entries(60)
        durations = {e: float((i * 7) % 13) for i, e in enumerate(entries)}
        payload = json.dumps({"entries": entries, "durations": durations})
        script = (
            "import json,sys;"
            "sys.path.insert(0, %r);" % str(REPO_ROOT) +
            "from tools.ci.gated_test_list import partition;"
            "d=json.loads(sys.stdin.read());"
            "s,_=partition(d['entries'],4,None,d['durations']);"
            "print(json.dumps(s))"
        )
        results = []
        for seed in ("0", "1", "12345"):
            proc = subprocess.run(
                [sys.executable, "-c", script], input=payload, capture_output=True,
                text=True, env={**_clean_env(), "PYTHONHASHSEED": seed})
            assert proc.returncode == 0, proc.stderr
            results.append(json.loads(proc.stdout))
        assert results[0] == results[1] == results[2]

    def test_tie_on_equal_weights_is_the_round_robin_order(self):
        # All-equal weights must not depend on dict iteration order; the
        # `(load, index)` tie-break pins them to the lowest free shard.
        entries = _entries(8)
        durations = {e: 1.0 for e in entries}
        packed, _ = partition(entries, 4, None, durations)
        plain, _ = partition(entries, 4, None, None)
        assert packed == plain


class TestBinPacking:
    def test_the_heavy_file_is_isolated(self):
        """The measured shape: one file at 39% of the suite."""
        entries = _entries(10)
        durations = {e: 1.0 for e in entries}
        durations[entries[3]] = 100.0
        shards, report = partition(entries, 4, None, durations)
        assert report["method"] == "duration"
        heavy = [s for s in shards if entries[3] in s][0]
        assert heavy == [entries[3]], "the heavy file should not carry passengers"

    def test_packing_beats_round_robin_on_makespan(self):
        entries = _entries(24)
        # Descending weights: round-robin gives shard 1 every 4th-heaviest.
        durations = {e: float(100 - i * 4) for i, e in enumerate(entries)}
        packed, packed_report = partition(entries, 4, None, durations)
        plain, _ = partition(entries, 4, None, None)

        def makespan(shards):
            return max(sum(durations[e] for e in s) for s in shards)

        assert makespan(packed) < makespan(plain)
        assert packed_report["estimated_spread_pct"] < 5.0

    def test_lower_bound_is_the_heaviest_indivisible_unit(self):
        entries = _entries(10)
        durations = {e: 1.0 for e in entries}
        durations[entries[7]] = 50.0
        _, report = partition(entries, 4, None, durations)
        assert report["lower_bound_seconds"] == 50.0
        assert report["heaviest_unit"] == entries[7]
        # At the floor: raising the shard count buys nothing, which is what the
        # task explicitly warns against doing.
        assert report["at_lower_bound"] is True

    def test_estimated_seconds_sum_to_the_measured_total(self):
        entries = _entries(20)
        durations = {e: float(i + 1) for i, e in enumerate(entries)}
        _, report = partition(entries, 4, None, durations)
        assert sum(report["estimated_seconds"]) == pytest.approx(
            sum(durations.values()), rel=1e-3)


class TestUnknownFiles:
    """A NEW test file is absent from the snapshot and must still be placed."""

    def test_an_unmeasured_file_is_never_dropped(self):
        entries = _entries(9)
        durations = {e: 5.0 for e in entries[:6]}  # last three unmeasured
        shards, report = partition(entries, 3, None, durations)
        packed = {e for s in shards for e in s}
        assert packed == set(entries)
        assert report["imputed_units"] == 3
        assert report["measured_entries"] == 6

    def test_an_unmeasured_file_is_weighted_at_the_median(self):
        entries = _entries(5)
        durations = {entries[0]: 1.0, entries[1]: 3.0, entries[2]: 100.0}
        # median of (1, 3, 100) is 3.0; two unmeasured files -> 3.0 each.
        _, report = partition(entries, 1, None, durations)
        assert report["estimated_seconds"][0] == pytest.approx(1 + 3 + 100 + 3 + 3)

    def test_placement_of_an_unmeasured_file_is_deterministic(self):
        entries = _entries(12)
        durations = {e: float(i) for i, e in enumerate(entries) if i % 2 == 0}
        first, _ = partition(entries, 3, None, durations)
        for _ in range(4):
            again, _ = partition(entries, 3, None, durations)
            assert again == first

    def test_nothing_measured_degrades_to_round_robin(self):
        entries = _entries(9)
        packed, report = partition(entries, 3, None, {"tests/other.py": 5.0})
        plain, _ = partition(entries, 3, None, None)
        assert report["method"] == "round_robin"
        assert packed == plain


class TestDirectoryTargets:
    """`core.txt` allows a trailing-slash directory target. None is listed today,
    so this path would otherwise ship untested — and its failure mode is a whole
    directory silently weighted at one file's median."""

    def test_a_directory_sums_the_files_beneath_it(self):
        durations = {
            "tests/cortex/test_a.py": 10.0,
            "tests/cortex/test_b.py": 5.0,
            "tests/other/test_c.py": 100.0,
        }
        seconds, was_measured = entry_duration("tests/cortex/", durations, 1.0)
        assert (seconds, was_measured) == (15.0, True)

    def test_an_unmeasured_directory_falls_back_to_the_default(self):
        seconds, was_measured = entry_duration(
            "tests/empty/", {"tests/other/test_c.py": 100.0}, 7.0)
        assert (seconds, was_measured) == (7.0, False)

    def test_a_directory_target_partitions_by_its_summed_weight(self):
        entries = ["tests/heavy/", "tests/test_a.py", "tests/test_b.py"]
        durations = {
            "tests/heavy/test_1.py": 50.0,
            "tests/heavy/test_2.py": 50.0,
            "tests/test_a.py": 1.0,
            "tests/test_b.py": 1.0,
        }
        shards, _ = partition(entries, 2, None, durations)
        assert ["tests/heavy/"] in shards
        assert sorted(e for s in shards for e in s) == sorted(entries)


class TestPins:
    def test_a_pinned_group_stays_together_under_bin_packing(self):
        entries = _entries(12)
        durations = {e: float(i + 1) for i, e in enumerate(entries)}
        groups = [[entries[0], entries[11]]]
        shards, _ = partition(entries, 4, groups, durations)
        home = [i for i, s in enumerate(shards) if entries[0] in s][0]
        assert entries[11] in shards[home]

    def test_a_pin_group_absent_from_the_list_is_ignored(self):
        entries = _entries(6)
        durations = {e: 1.0 for e in entries}
        shards, _ = partition(entries, 2, [["tests/not_listed.py"]], durations)
        assert sorted(e for s in shards for e in s) == entries


class TestSnapshotLoading:
    def test_newest_generated_at_wins_per_path(self, tmp_path):
        _snapshot(tmp_path, "snapshot.json", "2026-08-01T00:00:00Z",
                  {"tests/a.py": 1.0, "tests/b.py": 2.0})
        _snapshot(tmp_path, "crx-test-07.json", "2026-08-20T00:00:00Z",
                  {"tests/a.py": 99.0})
        loaded = load_timings(tmp_path)
        assert loaded["durations"] == {"tests/a.py": 99.0, "tests/b.py": 2.0}
        assert loaded["warnings"] == []

    def test_an_older_fragment_cannot_undo_a_fresh_measurement(self, tmp_path):
        _snapshot(tmp_path, "aaa-old.json", "2026-01-01T00:00:00Z", {"tests/a.py": 99.0})
        _snapshot(tmp_path, "snapshot.json", "2026-08-20T00:00:00Z", {"tests/a.py": 1.0})
        assert load_timings(tmp_path)["durations"] == {"tests/a.py": 1.0}

    def test_a_malformed_snapshot_warns_and_never_raises(self, tmp_path):
        d = tmp_path / "args" / "ci_test_timings"
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text("{not json", encoding="utf-8")
        loaded = load_timings(tmp_path)
        assert loaded["durations"] == {}
        assert len(loaded["warnings"]) == 1
        assert "snapshot.json" in loaded["warnings"][0]

    def test_a_missing_directory_is_normal(self, tmp_path):
        loaded = load_timings(tmp_path)
        assert loaded == {"durations": {}, "sources": [], "warnings": []}

    @pytest.mark.parametrize("bad", [
        '[]',
        '{"durations": []}',
        '{"durations": {"tests/a.py": "slow"}}',
        '{"durations": {"tests/a.py": -1}}',
        '{"durations": {"tests/a.py": true}}',
    ])
    def test_rejected_snapshot_shapes(self, bad):
        with pytest.raises(ValueError):
            parse_timing_snapshot(bad)

    def test_windows_separators_are_normalised(self):
        _, durations = parse_timing_snapshot(
            r'{"durations": {"tests\\cortex\\test_a.py": 3.0}}')
        assert durations == {"tests/cortex/test_a.py": 3.0}


class TestShardProjection:
    def test_shard_is_the_matching_slice_of_partition(self):
        entries = _entries(20)
        durations = {e: float(i) for i, e in enumerate(entries)}
        shards, _ = partition(entries, 4, None, durations)
        for k in range(1, 5):
            assert shard(entries, k, 4, None, durations) == shards[k - 1]

    def test_a_shard_preserves_list_order(self):
        entries = _entries(30)
        durations = {e: float((i * 13) % 11) for i, e in enumerate(entries)}
        for k in range(1, 5):
            got = shard(entries, k, 4, None, durations)
            assert got == [e for e in entries if e in set(got)]

    @pytest.mark.parametrize("index,total", [(0, 4), (5, 4), (-1, 3)])
    def test_out_of_range_index_raises(self, index, total):
        with pytest.raises(ValueError):
            shard(_entries(8), index, total)

    def test_zero_shards_raises(self):
        with pytest.raises(ValueError):
            partition(_entries(4), 0)


class TestLivePartition:
    """Against the real committed allowlist and snapshot, not a fixture."""

    def test_the_real_list_partitions_losslessly(self):
        from tools.ci.gated_test_list import load_shard_pins, resolve
        entries = resolve("core", REPO_ROOT)
        durations = load_timings(REPO_ROOT)["durations"]
        for n in (2, 4, 6):
            shards, report = partition(entries, n, load_shard_pins(REPO_ROOT), durations)
            assert sorted(e for s in shards for e in s) == sorted(entries)
            assert report["entries"] == len(entries)

    def test_the_committed_snapshot_is_readable(self):
        loaded = load_timings(REPO_ROOT)
        assert loaded["warnings"] == [], loaded["warnings"]
        assert loaded["durations"], "the committed snapshot measures nothing"


def _clean_env():
    import os
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)
    return env


def test_a_broken_partition_is_caught_not_shipped(monkeypatch):
    """The losslessness assertion must actually fire, not merely exist.

    Simulated by making the unit-key map lose an entry, which is exactly what a
    partition bug looks like: the shards are internally consistent and 20% of the
    suite silently never runs.
    """
    import tools.ci.gated_test_list as gtl

    entries = _entries(10)
    real = gtl._unit_keys

    def lossy(items, groups=None):
        key_of, ordered = real(items, groups)
        return key_of, ordered[:-2]  # two units vanish

    monkeypatch.setattr(gtl, "_unit_keys", lossy)
    with pytest.raises((AllowlistError, KeyError)):
        gtl.partition(entries, 3, None, {e: 1.0 for e in entries})
