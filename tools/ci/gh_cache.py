#!/usr/bin/env python3
# CUI // SP-CTI
"""A cached, measured `gh` seam -- READS ONLY (kpr-watch-17).

WHY THIS EXISTS
---------------
`PRWatcher.run_forever` ends every iteration in ``time.sleep(interval)`` with
``--daemon --interval 30``, and each iteration shells out to `gh` TWICE
(``poll_once`` and ``_sweep_unlinked_prs``), both GraphQL. On a board with ZERO
open PRs and ZERO non-terminal tasks -- the live state 2026-09-09 -- that is
~120 iterations an hour asking the forge to confirm nothing has changed.

NOT AN HTTP PROXY, AND THE REASON IS THE DESIGN
-----------------------------------------------
Caching in front of ``api.github.com`` means either ``HTTPS_PROXY`` with a MITM
certificate authority, or ``GH_HOST`` pointed at something impersonating GitHub.
Both require TLS interception and a trusted root certificate installed on the
host -- on a platform whose stated posture is air-gap and least privilege, that
buys a cache at the price of a credential interception point, and it would sit
in the path of every token this machine sends. The seam is already the PROCESS
BOUNDARY: every call in this tree is ``subprocess.run([gh, ...])``. Caching
there needs no certificate, no network component, and is directly testable.

WHAT IT DOES NOT CLAIM
----------------------
The GraphQL refusals measured 2026-09-09 named a USER ID while
``gh api rate_limit`` read ``{"limit":5000,"remaining":5000,"used":0}``
immediately before AND after -- a SECONDARY limit, not budget exhaustion.
NOTHING HERE PROVES fewer calls would have prevented it: the cause was never
measured, because measuring it needs the API that was refusing. This reduces
calls and MAKES THE MEASUREMENT POSSIBLE. It is not the fix for that refusal and
must not be described as one.

THREE RULES, AND EACH IS A DEFECT IF BROKEN
-------------------------------------------
* READS ONLY. A cache that serves a WRITE replays an action. The vocabulary
  below is the one ``tests/test_merge_readiness.py::test_cli_is_read_only``
  enumerated as literals; it lives here now so there is ONE statement of it, and
  a test asserts it cannot be quietly emptied.
* A FAILURE IS NEVER CACHED. A rate-limit refusal held for 60s turns ONE refusal
  into a minute of them -- the exact failure this card came from.
* THE KEY IS THE FULL ARGV. Two callers asking different questions must never
  share an answer, so nothing is normalised away.

IT COUNTS, and that is half the value. The API that reports usage is the API
being refused, so instrumenting the CALLER is the only way to learn who spends
what. ``hit_rate_pct`` is None -- never 0.0 or 100.0 -- over an empty
denominator (args/perfect_score_gate.yaml, ratcheted to 0).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence

#: Sub-commands and API verbs that CHANGE something. Taken from
#: `tests/test_merge_readiness.py::test_cli_is_read_only`, which enumerated them
#: as test literals; one home, so the rule cannot be stated twice and drift.
WRITE_VERBS = frozenset({
    "merge", "push", "close", "edit", "delete", "comment",
    "create", "squash", "checkout", "commit", "ready",
})

#: `gh api` is read-only ONLY while it stays a GET. Any of these turns the same
#: argv shape into an actor.
WRITE_FLAGS = frozenset({"-X", "--method", "-f", "-F", "--input", "--field",
                         "--raw-field"})

#: Short by design: it BOUNDS HOW LONG A STATE CHANGE CAN BE HIDDEN to under
#: one floor interval (30s), and that is the whole justification.
#:
#: IT DOES *NOT* DEDUPE THE TWO CALLS AN ITERATION MAKES. kpr-watch-17 shipped
#: a comment here claiming it did; kpr-watch-18 withdrew that. `poll_once` ->
#: `_auto_merge_unlinked` asks `--limit 100` for twelve fields, and
#: `_open_pr_index` asks `--limit 200` for `url,files,mergeable,isDraft`. The
#: KEY IS THE FULL ARGV -- deliberately, so two callers asking different
#: questions never share an answer -- so those two hash apart and BOTH always
#: reach the forge. The rule that makes the cache safe is the same rule that
#: makes it useless for that pair, and no TTL can change it.
#:
#: THE REPEATED IDENTICAL ASK IS `_open_pr_index()`, invoked at SEVEN sites in
#: pr_watcher.py (1259, 1371, 3435, 4805, 4806, 4818, 4819) with one argv --
#: and 4805/4806 and 4818/4819 are same-line double calls, once for the `in`
#: test and once for the value, INSIDE the per-unlinked-PR loop. That is what
#: this cache collapses.
#:
#: ITS LIVE SAVING IS UNMEASURED, and saying so is the point: the board held
#: ZERO open PRs when this was written, so that loop body never ran. Two
#: figures are MEASURED and nothing else may be claimed -- the MECHANISM
#: (three identical asks -> 1 forge call, 2 hits, 66.7%) and the BACKOFF
#: (120 -> 10 iterations per idle hour, 91.7% fewer).
#:
#: AND THE BACKOFF OUTRUNS THIS TTL BY DESIGN. `next_poll_interval` passes 60s
#: at idle_streak 2, so on an idle board every entry expires before the next
#: poll and the cache contributes NOTHING there -- the backoff is doing all of
#: that 91.7%. Do not read the two as additive.
DEFAULT_TTL_SECONDS = int(os.environ.get("ICDEV_GH_CACHE_TTL", "60") or 60)

#: Off switch. The cache is a convenience over a working call path; a
#: deployment that wants every call to hit the forge says so here.
ENABLED = (os.environ.get("ICDEV_GH_CACHE", "1") or "1").strip().lower() not in (
    "0", "false", "no", "off")

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = _REPO_ROOT / ".tmp" / "gh_cache"

#: Process-local counters. Deliberately NOT persisted: two processes spending
#: the budget are two facts, and summing them into one file would lose which
#: caller spent what -- the one thing this instrumentation exists to learn.
_STATS: Dict[str, int] = {"hit": 0, "miss": 0, "refusal": 0, "uncacheable": 0}


class GhResult:
    """The subset of ``CompletedProcess`` every caller here reads."""

    __slots__ = ("returncode", "stdout", "stderr", "cached")

    def __init__(self, returncode: int, stdout: str, stderr: str,
                 cached: bool = False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cached = cached


def is_read_only(argv: Sequence[str]) -> bool:
    """Is this argv safe to cache -- i.e. does it change nothing?

    FAIL CLOSED: anything unrecognised is treated as a write. A new `gh`
    sub-command that mutates must not become cacheable by not being listed.
    """
    words = [str(a) for a in argv]
    for word in words:
        if word in WRITE_FLAGS:
            return False
        # `--method=POST` and friends
        head = word.split("=", 1)[0]
        if head in WRITE_FLAGS:
            return False
        if word in WRITE_VERBS:
            return False
    return True


def _key(argv: Sequence[str]) -> str:
    payload = json.dumps([str(a) for a in argv], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry_path(cache_dir: pathlib.Path, key: str) -> pathlib.Path:
    return cache_dir / ("%s.json" % key)


def _read_entry(path: pathlib.Path, ttl: int) -> Optional[GhResult]:
    """A cached reply, or None. Never raises: a bad entry is a miss."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(raw["at"])
        if age > ttl:
            return None
        return GhResult(int(raw["rc"]), str(raw["out"]), str(raw["err"]),
                        cached=True)
    except Exception:  # noqa: BLE001 - a corrupt or absent entry is a miss
        return None


def _write_entry(path: pathlib.Path, result: GhResult) -> None:
    """Best-effort. A cache that cannot be written must not break the call."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "at": time.time(), "rc": result.returncode,
            "out": result.stdout, "err": result.stderr,
        }), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def run_gh(argv: Sequence[str], *, runner=None,
           ttl: Optional[int] = None,
           cache_dir: Optional[pathlib.Path] = None,
           timeout: int = 60) -> GhResult:
    """Run one `gh` argv, serving a recent identical READ from cache.

    A write, a failure, and a cache miss are three different events and are
    counted as three different numbers.
    """
    argv = [str(a) for a in argv]
    ttl = DEFAULT_TTL_SECONDS if ttl is None else int(ttl)
    directory = pathlib.Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    if runner is None:
        runner = subprocess.run

    cacheable = ENABLED and is_read_only(argv)
    if not cacheable:
        _STATS["uncacheable"] += 1
    else:
        hit = _read_entry(_entry_path(directory, _key(argv)), ttl)
        if hit is not None:
            _STATS["hit"] += 1
            return hit

    proc = runner(argv, capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=timeout)
    result = GhResult(int(getattr(proc, "returncode", 1) or 0),
                      getattr(proc, "stdout", "") or "",
                      getattr(proc, "stderr", "") or "")

    if result.returncode != 0:
        # NEVER cached. Holding a rate-limit refusal for the TTL converts one
        # refusal into a TTL's worth of them, which is the failure this card
        # came from.
        _STATS["refusal"] += 1
        return result

    if cacheable:
        _STATS["miss"] += 1
        _write_entry(_entry_path(directory, _key(argv)), result)
    return result


def cached_runner(*, ttl: Optional[int] = None,
                  cache_dir: Optional[pathlib.Path] = None,
                  runner=None):
    """A drop-in for ``subprocess.run`` that caches READS.

    Callers pass `capture_output=True, text=True, encoding=..., errors=...` --
    the shape every `gh` shell-out in this tree already uses -- and those are
    accepted and ignored, because `run_gh` always captures text. Only `timeout`
    is honoured, since it is the one that changes behaviour.

    WIRE THIS ONLY INTO A READ SEAM. `pr_watcher` keeps `_auto_merge_runner` and
    `_gh_close_runner` on the bare `subprocess.run`: `is_read_only` would refuse
    to cache them anyway, but routing an ACT through a thing named "cache" is an
    invitation for a future edit to make it one.
    """
    def run(argv, **kw):
        return run_gh(argv, runner=runner, ttl=ttl, cache_dir=cache_dir,
                      timeout=int(kw.get("timeout") or 60))
    return run


def stats() -> Dict[str, Any]:
    """Counters plus a hit rate that refuses to exist over nothing."""
    looked = _STATS["hit"] + _STATS["miss"]
    return {
        **_STATS,
        "looked_up": looked,
        # None, never 0.0 or 100.0, over an empty denominator: a cache nobody
        # asked reads identically to one that never hits.
        "hit_rate_pct": (round(_STATS["hit"] / looked * 100, 1)
                         if looked else None),
    }


def reset_stats() -> None:
    for k in _STATS:
        _STATS[k] = 0


def purge(cache_dir: Optional[pathlib.Path] = None,
          older_than: Optional[int] = None) -> int:
    """Drop expired entries. Returns how many were removed."""
    directory = pathlib.Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    ttl = DEFAULT_TTL_SECONDS if older_than is None else int(older_than)
    removed = 0
    try:
        for path in directory.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - float(raw["at"]) > ttl:
                    path.unlink()
                    removed += 1
            except Exception:  # noqa: BLE001 - a corrupt entry is litter too
                try:
                    path.unlink()
                    removed += 1
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001 - an unreadable dir is not an error here
        pass
    return removed


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--purge", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.purge:
        print("gh cache: purged %d expired entr(ies)." % purge())
        return 0

    payload = stats()
    payload["cache_dir"] = str(DEFAULT_CACHE_DIR)
    payload["enabled"] = ENABLED
    payload["ttl_seconds"] = DEFAULT_TTL_SECONDS
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        rate = payload["hit_rate_pct"]
        print("gh cache: hit=%(hit)d miss=%(miss)d refusal=%(refusal)d "
              "uncacheable=%(uncacheable)d" % payload)
        print("  hit rate: %s" % ("not measured (nothing looked up)"
                                  if rate is None else "%.1f%%" % rate))
        print("  dir: %s  ttl: %ds  enabled: %s"
              % (payload["cache_dir"], payload["ttl_seconds"], payload["enabled"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
