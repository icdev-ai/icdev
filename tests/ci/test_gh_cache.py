# CUI // SP-CTI
"""A cached, measured `gh` seam -- READS ONLY (kpr-watch-17).

`PRWatcher.run_forever` ends every iteration in `time.sleep(interval)` with
``--interval 30``, and each iteration shells out to `gh` twice (``poll_once``
and ``_sweep_unlinked_prs``), both GraphQL. On a board with zero open PRs and
zero non-terminal tasks -- the live state 2026-09-09 -- that is ~120 iterations
an hour asking the forge to confirm nothing changed.

NOT AN HTTP PROXY, and the reason is the point: caching in front of
api.github.com needs HTTPS_PROXY with a MITM CA, or GH_HOST impersonating
GitHub. Both require TLS interception and a trusted root certificate on a host
whose whole posture is air-gap and least privilege -- a cache bought at the
price of a credential interception point. The seam is already the PROCESS
BOUNDARY.

THE INVARIANT EVERY TEST HERE DEFENDS: a cache that serves a WRITE, or serves a
FAILURE, is worse than no cache. The first replays an action; the second turns
one rate-limit refusal into a minute of them.
"""
from __future__ import annotations

import pathlib

import pytest

from tools.ci import gh_cache


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _runner(results):
    """A runner that hands back queued results and counts its calls."""
    calls = []

    def run(argv, **kw):
        calls.append(list(argv))
        return results[min(len(calls) - 1, len(results) - 1)]

    run.calls = calls  # type: ignore[attr-defined]
    return run


READ = ["gh", "pr", "list", "--state", "open", "--json", "number"]
READ2 = ["gh", "api", "repos/o/r/pulls"]


# ───────────────────────────────────────────────────── reads only, ever
@pytest.mark.parametrize("verb", sorted(gh_cache.WRITE_VERBS))
def test_no_write_verb_is_ever_cacheable(verb):
    assert gh_cache.is_read_only(["gh", "pr", verb, "123"]) is False


@pytest.mark.parametrize("flag", sorted(gh_cache.WRITE_FLAGS))
def test_no_write_flag_is_ever_cacheable(flag):
    assert gh_cache.is_read_only(["gh", "api", "repos/o/r/pulls", flag, "x"]) is False


def test_the_vocabulary_cannot_be_quietly_emptied():
    """One definition, and it must keep naming the verbs that matter.

    `merge_readiness`'s own read-only guard enumerated these as test literals;
    moving them to a module gives them one home, and this asserts that home
    cannot be hollowed out without a test going red.
    """
    for verb in ("merge", "push", "close", "delete", "create", "edit"):
        assert verb in gh_cache.WRITE_VERBS, verb
    for flag in ("-X", "--method", "-f", "-F", "--input"):
        assert flag in gh_cache.WRITE_FLAGS, flag


def test_a_plain_read_is_cacheable():
    assert gh_cache.is_read_only(READ) is True
    assert gh_cache.is_read_only(READ2) is True


def test_a_write_argv_is_never_served_from_cache(tmp_path):
    """Even having been seen before, a write always re-executes."""
    argv = ["gh", "pr", "merge", "123", "--merge"]
    runner = _runner([_Proc(0, "merged"), _Proc(0, "merged again")])
    a = gh_cache.run_gh(argv, runner=runner, cache_dir=tmp_path)
    b = gh_cache.run_gh(argv, runner=runner, cache_dir=tmp_path)
    assert len(runner.calls) == 2, "a write was served from cache"
    assert a.stdout == "merged" and b.stdout == "merged again"


# ─────────────────────────────────────────────────────────── the caching
def test_a_repeated_read_is_served_from_cache(tmp_path):
    runner = _runner([_Proc(0, '[{"number": 1}]')])
    first = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    second = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    assert len(runner.calls) == 1, "the second read re-executed"
    assert first.stdout == second.stdout


def test_a_different_argv_is_a_different_answer(tmp_path):
    """The key is the FULL argv: two callers asking different questions
    must never share a reply."""
    runner = _runner([_Proc(0, "A"), _Proc(0, "B")])
    a = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    b = gh_cache.run_gh(READ2, runner=runner, cache_dir=tmp_path)
    assert (a.stdout, b.stdout) == ("A", "B")
    assert len(runner.calls) == 2


def test_an_expired_entry_re_executes(tmp_path):
    runner = _runner([_Proc(0, "old"), _Proc(0, "new")])
    gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path, ttl=0)
    again = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path, ttl=0)
    assert len(runner.calls) == 2
    assert again.stdout == "new"


def test_a_failure_is_never_cached(tmp_path):
    """A rate-limit refusal cached for 60s turns one refusal into a minute.

    This is the case the whole card came from, so it is asserted directly.
    """
    refusal = _Proc(1, "", "GraphQL: API rate limit already exceeded for user ID 1")
    runner = _runner([refusal, _Proc(0, "recovered")])
    first = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    assert first.returncode == 1
    second = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    assert len(runner.calls) == 2, "a failed call was cached"
    assert second.stdout == "recovered"


def test_a_corrupt_cache_entry_re_executes_rather_than_raising(tmp_path):
    runner = _runner([_Proc(0, "fresh")])
    gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    for f in tmp_path.glob("*.json"):
        f.write_text("{not json", encoding="utf-8")
    out = gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    assert out.stdout == "fresh"


# ───────────────────────────────────────────────────────── it MEASURES
def test_hits_and_misses_are_counted(tmp_path):
    """The API that reports usage is the API being refused, so instrumenting
    the CALLER is the only way to know who spends what."""
    gh_cache.reset_stats()
    runner = _runner([_Proc(0, "x")])
    gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    st = gh_cache.stats()
    assert st["miss"] == 1 and st["hit"] == 1


def test_an_uncacheable_call_is_counted_apart_from_a_miss(tmp_path):
    """A write and a cache miss are different events and never one number."""
    gh_cache.reset_stats()
    gh_cache.run_gh(["gh", "pr", "merge", "1"],
                    runner=_runner([_Proc(0, "")]), cache_dir=tmp_path)
    st = gh_cache.stats()
    assert st["uncacheable"] == 1
    assert st["miss"] == 0


def test_a_refusal_is_counted_apart_from_a_miss(tmp_path):
    gh_cache.reset_stats()
    runner = _runner([_Proc(1, "", "API rate limit already exceeded")])
    gh_cache.run_gh(READ, runner=runner, cache_dir=tmp_path)
    st = gh_cache.stats()
    assert st["refusal"] == 1


def test_stats_start_empty_and_never_report_a_rate_over_nothing():
    gh_cache.reset_stats()
    st = gh_cache.stats()
    assert st["hit"] == 0 and st["miss"] == 0
    assert st["hit_rate_pct"] is None, (
        "a hit rate over an empty denominator must be None, never 0.0 or 100.0")


# ══════════════════════════════════════════════════════════════════════════
# Adaptive backoff: the loop stops polling a board with nothing on it.
# ══════════════════════════════════════════════════════════════════════════
#
# NOT webhooks -- GitHub must reach an inbound endpoint and this host is behind
# NAT; the Actions side already fires on `check_suite`. The local win is simply
# not asking a forge to re-confirm an empty board 120 times an hour.

from tools.ci import pr_watcher as pw  # noqa: E402


def test_a_busy_board_polls_at_exactly_the_floor():
    """No behaviour change while there is anything to watch."""
    assert pw.next_poll_interval(30, 480, idle_streak=0) == 30


def test_an_idle_board_backs_off_geometrically():
    seq = [pw.next_poll_interval(30, 480, idle_streak=n) for n in range(1, 6)]
    assert seq == [60, 120, 240, 480, 480], seq


def test_the_ceiling_is_never_exceeded():
    assert pw.next_poll_interval(30, 480, idle_streak=99) == 480


def test_the_floor_is_restored_the_moment_work_appears():
    """Any action, any PR-bearing task, any error resets it -- so a PR opened
    during a backed-off window is picked up within one ceiling, not later."""
    assert pw.next_poll_interval(30, 480, idle_streak=5) == 480
    assert pw.next_poll_interval(30, 480, idle_streak=0) == 30


def test_a_ceiling_below_the_floor_never_polls_slower_than_the_floor():
    """A misconfigured ceiling must not silently stop the watcher."""
    assert pw.next_poll_interval(60, 10, idle_streak=4) == 60


@pytest.mark.parametrize("floor", [1, 5, 30, 45])
def test_the_floor_is_always_honoured(floor):
    assert pw.next_poll_interval(floor, 600, idle_streak=0) == floor


def test_the_declared_ceiling_lives_in_config_not_in_code():
    """A watcher that quietly stopped looking is indistinguishable from one
    with nothing to see -- so the bound is declared where a reader finds it."""
    import yaml

    from tools.ci.pr_watcher import CONFIG_PATH

    cfg = yaml.safe_load(pathlib.Path(CONFIG_PATH).read_text(encoding="utf-8")) or {}
    assert "idle_backoff_max_seconds" in cfg, (
        "the backoff ceiling is not declared in args/pr_watcher_config.yaml")


def test_only_the_read_runner_is_cached_not_the_actors():
    """`_pr_list_runner` is a READ; merge and close are ACTS.

    `is_read_only` would refuse to cache an act anyway, but routing one through
    something named "cache" invites a future edit to make it one -- so this
    pins which seam got wired.
    """
    import subprocess

    w = pw.PRWatcher.__new__(pw.PRWatcher)
    pw.PRWatcher.__init__(w)
    assert w._auto_merge_runner is subprocess.run
    assert w._gh_close_runner is subprocess.run
    assert w._pr_list_runner is not subprocess.run


def test_an_injected_runner_still_wins():
    """Tests inject their own runner; the cache must not displace it."""
    sentinel = lambda *a, **k: None  # noqa: E731
    w = pw.PRWatcher.__new__(pw.PRWatcher)
    pw.PRWatcher.__init__(w, pr_list_runner=sentinel)
    assert w._pr_list_runner is sentinel
