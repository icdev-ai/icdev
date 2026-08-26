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
and the whole gather is capped by ``deadline_seconds``. The per-app timeout is
DERIVED from the remaining budget rather than declared beside it: a per-app
timeout larger than the page deadline is incoherent, because one app could then
outlast the page it is rendering.

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

NOTHING HERE KNOWS ABOUT SUBPROCESSES. ``probe`` is injected, so the bounding
logic is testable without spawning eight interpreters, and the caller keeps
owning how a Genesis daemon is actually invoked.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from typing import Any, Callable, Dict, Mapping

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
#: Chosen against the two numbers that bracket it: the fan-out costs 0.89s
#: concurrently on this machine (measured -- the slowest single app), and the nav smoke
#: test's client budget is 10s. 8.0 leaves the page room to be slow under load
#: and still answer, and leaves the render itself two seconds of the client's
#: budget. Raising it past the client's timeout would put the unbounded page
#: straight back.
DEFAULT_DEADLINE_SECONDS = 8.0

#: Cap on concurrent probes. Each is an interpreter start; a registry that grew
#: to fifty entries must not spawn fifty at once.
MAX_WORKERS = 8

ProbeFn = Callable[[str, Mapping[str, Any], float], Any]


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


def gather_fleet_status(
    apps: Mapping[str, Mapping[str, Any]],
    probe: ProbeFn,
    *,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    max_workers: int = MAX_WORKERS,
) -> Dict[str, Dict[str, Any]]:
    """Probe every available app concurrently, bounded by ``deadline_seconds``.

    ``apps`` is the ``tools.genesis.apps_registry.load_genesis_apps`` mapping.
    ``probe(key, cfg, timeout)`` returns the app's status payload; it is called
    ONLY for an app whose ``available`` flag is true.

    Returns one entry per app in registry order, each carrying the probe's
    payload plus ``_name``, ``_available`` and ``_probe_state``. Never raises:
    a page that cannot render because a status probe misbehaved is a worse
    failure than a page that renders and says what it could not measure.
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

    started = time.monotonic()
    # Every probe starts at ~t0, so "remaining budget" and "the deadline" are
    # the same number here. Computing it from the clock anyway keeps the
    # invariant true if a future caller staggers the submissions.
    executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(to_probe))))
    try:
        for key, cfg in to_probe:
            remaining = deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                continue  # stays UNMEASURED -- never submitted, never guessed at
            pending[key] = executor.submit(probe, key, cfg, remaining)

        for key, future in pending.items():
            remaining = deadline_seconds - (time.monotonic() - started)
            entry = out[key]
            try:
                payload = future.result(timeout=max(0.0, remaining))
            except _FutureTimeout:
                # The probe is still running. It is NOT offline and it is NOT
                # disabled -- nobody knows.
                log.warning("genesis_fleet: probe for %s did not answer inside the budget", key)
                continue
            except Exception as exc:  # noqa: BLE001 -- a bad probe must not break the page
                entry["_probe_state"] = ERROR
                entry["error"] = str(exc)
                continue

            state = _classify(payload)
            if isinstance(payload, Mapping):
                entry.update(payload)
            elif payload is not None:
                entry["error"] = f"probe returned {type(payload).__name__}"
                state = ERROR
            entry["_name"] = str(apps[key].get("name") or key)
            entry["_probe_state"] = state
            entry["_available"] = state == OK
    finally:
        # Do NOT block on a hung probe: the whole point is that the page's cost
        # is the deadline. The worker drains on its own once the subprocess
        # timeout fires.
        executor.shutdown(wait=False)

    return out
# CUI // SP-CTI
