# CUI // SP-CTI
"""Bound the /genesis fleet probe, and keep its four outcomes apart.
(qa-fail-1474518ac97ac6a2)

``GET /genesis`` rendered by looping over ``args/genesis_apps.yaml`` and calling
``_genesis_run(key, ["--status", "--json"])`` for each entry -- a fresh Python
interpreter per app, SERIALLY, each with ``timeout=15`` and no cap on the page
itself. Eight apps are declared and all eight resolve on the development box, so
the route's worst case was 8 x 15 = 120 SECONDS. A page whose render time is
``sum(per-app timeouts)`` cannot answer inside any client's budget, and nothing
measured or reported that.

MEASURED like for like -- both shapes in ONE process against the same eight real
sibling roots, three runs each, best of three:

    old serial loop   2.38 / 1.73 / 1.69  ->  1.69s
    new bounded       0.89 / 0.89 / 0.96  ->  0.89s   (1.91x)

1.91x and not 8x, because the probes' costs are wildly uneven -- run alone they
are 1.29 / 0.08 / 0.74 / 0.65 / 0.35 / 0.35 / 0.51 / 0.07s, so the slowest one
sets the floor. That is exactly what ``max()`` instead of ``sum()`` buys, and
the speedup is the SMALLER half of the fix: the worst case goes from 120s to
``deadline_seconds``.

The nav smoke test allows a route 10s (``actionTimeout`` in
playwright.config.ts); under a loaded QA sweep it got
``TimeoutError: apiRequestContext.get``. The 10s budget was the messenger.

TWO CHANGES, AND THE SECOND IS THE ONE THAT LASTS.

**Concurrent, with a deadline.** The probes are independent subprocess spawns --
I/O-bound, GIL released -- so the fan-out costs ``max()`` rather than ``sum()``,
and the whole gather is capped by ``deadline_seconds``. Without a cache the
per-app timeout is DERIVED from the remaining budget rather than declared beside
it: a per-app timeout larger than the page deadline is incoherent when nothing
would ever read the late answer.

**Four outcomes, never merged.** The old route had two, and both were wrong at
the edges:

  ``ok``            the probe answered.
  ``root_missing``  the app is not on this machine. Not probed at all.
  ``unmeasured``    the probe was started and did not come back inside the
                    budget. NOBODY KNOWS what this app's status is. Previously
                    this landed in ``except Exception`` -> ``_available: False``
                    and rendered OFFLINE -- the same badge as an app that is not
                    installed, which is the reassurance this whole file exists
                    to refuse.
  ``error``         the probe RAN and failed -- a crash, or ``_genesis_run``'s
                    own ``{"error": "parse_failed"}``. That dict does NOT raise,
                    so the old route set ``_available: True`` on it and the
                    template, finding no ``daemon.enabled``, drew DISABLED: a
                    broken probe reading as "installed, switched off".
                    ``govchain`` and ``icdev-ft`` have no ``daemon.py`` on this
                    machine and did exactly that.

``unmeasured`` and ``error`` are kept apart because they send a reader to
different places: one is a budget or a hung daemon, the other is a daemon that
answered badly.

**A probe slower than the page answers the NEXT render
(qa-fail-007b14002f882f59).** Measured 2026-10-17 on the dev box: the
``govproposal`` probe answers in 23.45s and ``trading-engine``'s did not answer
in 40.98s. Both sit far past any page deadline, so every ``GET /genesis`` paid
the full 8.0s deadline (8.41 / 8.19 / 8.03s) -- two seconds of headroom against
the 10s client, gone under a QA sweep. And because the per-app timeout was the
page deadline, each slow probe was KILLED at 8s and could never be measured on
any render, while every load spawned all eight interpreters again.
``FleetProbeCache`` separates the two clocks: the page waits at most
``deadline_seconds``; the probe keeps its own ``probe_timeout_seconds`` in the
background and records its answer; an answer younger than ``fresh_seconds`` is
served without spawning anything; and an app has at most ONE probe in flight no
matter how often the page loads. An app that has never answered is still
``unmeasured`` -- the cache only serves what a probe actually said, and
``_age_seconds`` says how old it is.

NOTHING HERE KNOWS ABOUT SUBPROCESSES. ``probe`` is injected, so the bounding
logic is testable without spawning eight interpreters, and the caller keeps
owning how a Genesis daemon is actually invoked.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from typing import Any, Callable, Dict, Mapping, Optional

from tools.logging.icdev_logger import get_logger

log = get_logger("icdev.dashboard.genesis_fleet")

#: The probe answered.
OK = "ok"
#: The app is not checked out on this machine. Never probed.
ROOT_MISSING = "root_missing"
#: The probe was started and did not answer inside the budget. NOT "offline".
UNMEASURED = "unmeasured"
#: The probe ran and failed -- a raise, or an ``{"error": ...}`` answer.
ERROR = "error"

#: Total wall clock the whole fan-out may spend, in seconds.
#:
#: Bracketed by the fan-out's cost when every probe is healthy (0.89s measured,
#: the slowest single app) and the nav smoke test's 10s client budget. It was
#: 8.0; with two probes that never answer inside it, the page cost 8s on every
#: load (qa-fail-007b14002f882f59). 3.0 is ~3x the healthy cost and leaves the
#: render and a loaded host 7s of the client's budget. A probe that needs more
#: than this is served from ``FleetProbeCache`` once it lands, not waited for.
DEFAULT_DEADLINE_SECONDS = 3.0

#: How long a cached probe may run in the background. Independent of the page.
DEFAULT_PROBE_TIMEOUT_SECONDS = 60.0

#: An answer younger than this is served without spawning a new probe.
DEFAULT_FRESH_SECONDS = 30.0

#: Cap on concurrent probes. Each is an interpreter start; a registry that grew
#: to fifty entries must not spawn fifty at once.
MAX_WORKERS = 8

ProbeFn = Callable[[str, Mapping[str, Any], float], Any]


class FleetProbeCache:
    """Last answer per app, and the probe currently in flight per app.

    One instance lives for the life of the dashboard process. Thread-safe: the
    done-callback that records an answer runs on the probe's worker thread.
    """

    def __init__(
        self,
        *,
        probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
        fresh_seconds: float = DEFAULT_FRESH_SECONDS,
    ) -> None:
        self.probe_timeout_seconds = probe_timeout_seconds
        self.fresh_seconds = fresh_seconds
        self._lock = threading.Lock()
        self._answers: Dict[str, tuple] = {}  # key -> (payload or exception, monotonic)
        self._inflight: Dict[str, Any] = {}  # key -> Future

    def answer(self, key: str) -> Optional[tuple]:
        """``(payload_or_exception, age_seconds)``, or ``None`` if never answered."""
        with self._lock:
            hit = self._answers.get(key)
        if hit is None:
            return None
        return hit[0], time.monotonic() - hit[1]

    def future_for(self, key: str, submit: Callable[[], Any]):
        """The in-flight probe for ``key``, starting one only if none is running."""
        with self._lock:
            fut = self._inflight.get(key)
            if fut is not None and not fut.done():
                return fut
            fut = submit()
            self._inflight[key] = fut
        fut.add_done_callback(lambda f, k=key: self._record(k, f))
        return fut

    def _record(self, key: str, fut) -> None:
        exc = fut.exception()
        with self._lock:
            self._answers[key] = (exc if exc is not None else fut.result(), time.monotonic())
            if self._inflight.get(key) is fut:
                del self._inflight[key]


def _classify(payload: Any) -> str:
    """An answered probe's payload -> ``ok`` | ``root_missing`` | ``error``.

    ``_genesis_run`` reports both a missing root and a failed parse as an
    ``error`` key rather than by raising, so the payload has to be read.
    """
    if not isinstance(payload, Mapping):
        return ERROR
    err = payload.get("error")
    if not err:
        return OK
    if str(err) == ROOT_MISSING:
        return ROOT_MISSING
    return ERROR


def _apply(entry: Dict[str, Any], name: str, payload: Any) -> None:
    """Fold one probe answer -- a payload, or the exception it raised -- into ``entry``."""
    if isinstance(payload, BaseException):
        entry["_probe_state"] = ERROR
        entry["error"] = str(payload)
        return
    state = _classify(payload)
    if isinstance(payload, Mapping):
        entry.update(payload)
    elif payload is not None:
        entry["error"] = f"probe returned {type(payload).__name__}"
        state = ERROR
    entry["_name"] = name
    entry["_probe_state"] = state
    entry["_available"] = state == OK


def gather_fleet_status(
    apps: Mapping[str, Mapping[str, Any]],
    probe: ProbeFn,
    *,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    max_workers: int = MAX_WORKERS,
    cache: Optional[FleetProbeCache] = None,
) -> Dict[str, Dict[str, Any]]:
    """Probe every available app concurrently, bounded by ``deadline_seconds``.

    ``apps`` is the ``tools.genesis.apps_registry.load_genesis_apps`` mapping.
    ``probe(key, cfg, timeout)`` returns the app's status payload; it is called
    ONLY for an app whose ``available`` flag is true.

    Returns one entry per app in registry order, each carrying the probe's
    payload plus ``_name``, ``_available`` and ``_probe_state``. Never raises:
    a page that cannot render because a status probe misbehaved is a worse
    failure than a page that renders and says what it could not measure.

    With ``cache``, a probe runs under the cache's own timeout and outlives the
    page; a fresh cached answer skips the probe, and a probe that misses the
    deadline falls back to the last answer it gave (``_age_seconds`` set).
    """
    out: Dict[str, Dict[str, Any]] = {}
    pending: Dict[str, Any] = {}

    to_probe = []
    for key, cfg in apps.items():
        name = str(cfg.get("name") or key)
        if not cfg.get("available"):
            out[key] = {
                "_name": name,
                "_available": False,
                "_probe_state": ROOT_MISSING,
                # Same vocabulary ``apps_registry.root_missing`` and
                # ``_genesis_run`` already answer with, so a caller reading
                # ``error`` gets one word for one condition rather than a prose
                # variant per call site.
                "error": ROOT_MISSING,
                "root": cfg.get("root"),
            }
            continue
        # Reserve the slot now so registry order survives the fan-out.
        out[key] = {"_name": name, "_available": False, "_probe_state": UNMEASURED}
        to_probe.append((key, cfg))

    if not to_probe:
        return out

    def _from_cache(key: str, hit: tuple) -> None:
        _apply(out[key], str(apps[key].get("name") or key), hit[0])
        out[key]["_age_seconds"] = round(hit[1], 1)

    started = time.monotonic()
    # Every probe starts at ~t0, so "remaining budget" and "the deadline" are
    # the same number here. Computing it from the clock anyway keeps the
    # invariant true if a future caller staggers the submissions.
    executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(to_probe))))
    try:
        for key, cfg in to_probe:
            if cache is not None:
                hit = cache.answer(key)
                if hit is not None and hit[1] < cache.fresh_seconds:
                    _from_cache(key, hit)
                    continue
                pending[key] = cache.future_for(
                    key,
                    lambda k=key, c=cfg: executor.submit(probe, k, c, cache.probe_timeout_seconds),
                )
                continue
            remaining = deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                continue  # stays UNMEASURED -- never submitted, never guessed at
            pending[key] = executor.submit(probe, key, cfg, remaining)

        for key, future in pending.items():
            remaining = deadline_seconds - (time.monotonic() - started)
            try:
                payload = future.result(timeout=max(0.0, remaining))
            except _FutureTimeout:
                # The probe is still running. It is NOT offline and it is NOT
                # disabled -- nobody knows, unless it answered an earlier render.
                hit = cache.answer(key) if cache is not None else None
                if hit is not None:
                    _from_cache(key, hit)
                else:
                    log.warning("genesis_fleet: probe for %s did not answer inside the budget", key)
                continue
            except Exception as exc:  # noqa: BLE001 -- a bad probe must not break the page
                payload = exc
            _apply(out[key], str(apps[key].get("name") or key), payload)
    finally:
        # Do NOT block on a hung probe: the whole point is that the page's cost
        # is the deadline. The worker drains on its own once the subprocess
        # timeout fires -- and, with a cache, records what it found.
        executor.shutdown(wait=False)

    return out
# CUI // SP-CTI
