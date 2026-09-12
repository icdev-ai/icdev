# CUI // SP-CTI
"""QA Agent Runner — Playwright E2E execution + coverage gap detection for ACE QA Agent.

Invoked by the qa_agent ACE co-worker via run_tool.  All public functions return
plain dicts/lists so they are trivially JSON-serialisable for the agent loop.

CLI usage:
    python tools/testing/qa_agent_runner.py --run [--canvas CANVAS_KEY] [--json]
    python tools/testing/qa_agent_runner.py --run --deadline-seconds 1800 --batch-size 4
    python tools/testing/qa_agent_runner.py --discover-gaps [--json]
    python tools/testing/qa_agent_runner.py --status RUN_ID [--json]

The suite is BATCHED by spec file rather than run as one invocation, because a
single `npx playwright test` killed at a wall-clock deadline emits no JSON
report at all — so a partial sweep returned nothing, and `--run` reported one
synthetic TestFailure(test_name="timeout") whatever the suite actually did.
Every batch that finishes has a real report, and the spec files that did not
run are NAMED (`spec_files_not_run` / `spec_files_no_report`) rather than
silently absent.

Note: Playwright shuts down a webServer it started, so with no dashboard
already listening each batch pays that startup again. Point the run at a
running dashboard, or set ICDEV_NO_SERVER=1, to avoid it.

A sampler runs beside every batch (qa-fail-5cacee65f1d03c8c) so a sweep whose
timeouts fell inside a HOST STALL can say so: each failure carries
`during_stall` (True / False / None -- unmeasured is never "no stall") and each
batch a `stall_census`. The verdict never moves the run's status. See the
"Host-stall sampling" section below for what it measures and what it cannot.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
from collections.abc import Mapping
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

_QA_SCREENSHOT_DIR = "playwright/screenshots/qa-agent"
_E2E_SPEC_GLOB = "tests/e2e/*.spec.ts"
_COMPONENT_REGISTRY_PATH = "args/component_registry.yaml"

#: Whole-sweep wall-clock budget. playwright.config.ts records the measured
#: duration of the full suite in its own comment — "full suite on PostgreSQL
#: 732 passed, 55 failed, 25 skipped (41.5m)" — against 838 tests at
#: `workers: 1`, `fullyParallel: false`. The previous 1200s (20 min) was under
#: half of that, so --run ALWAYS hit subprocess.TimeoutExpired.
_DEADLINE_SECONDS = 3600

#: Spec files per `npx playwright test` invocation. Each batch writes its own
#: report, so this is the granularity at which a deadline-bounded sweep still
#: yields real results.
_BATCH_SIZE = 6

#: Do not start another batch with less than this much of the deadline left —
#: it would only be killed, producing no report for those spec files.
_MIN_BATCH_SECONDS = 90

#: Statuses a run can end in. `no_tests` and `incomplete` exist because the old
#: code called both of them `passed`.
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_NO_TESTS = "no_tests"
STATUS_INCOMPLETE = "incomplete"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TestFailure:
    test_name: str = ""
    spec_file: str = ""
    error_message: str = ""
    screenshot_path: str = ""
    severity: str = "high"
    #: Playwright's own `startTime` (UTC ISO) and `duration` (ms) for the
    #: result that failed -- the window a stall sample is intersected with.
    #: Empty / None when the report carried neither; never 0.
    started_at: str = ""
    duration_ms: Optional[int] = None
    #: True: a measured stall overlaps this test's window. False: the batch
    #: was sampled and none does. None: UNMEASURED. See the stall section.
    during_stall: Optional[bool] = None
    stall_detail: str = ""


@dataclass
class QARunResult:
    run_id: str = ""
    trigger: str = "manual"
    canvas_filter: str = ""
    status: str = "running"
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    screenshot_count: int = 0
    #: `failed` minus the failures the parser could name. Non-zero means the
    #: report held a shape the walker did not understand; it is never folded
    #: into `passed`.
    failures_unparsed: int = 0
    failures: List[TestFailure] = field(default_factory=list)
    report_path: str = ""

    #: Coverage bookkeeping. Three lists, never merged — each sends you to a
    #: different fix. `not_run` means the deadline stopped us before the batch
    #: (or mid-batch); `no_report` means the batch RAN and produced nothing
    #: parseable, which is an infrastructure fault, not missing coverage.
    spec_files_total: int = 0
    spec_files_run: List[str] = field(default_factory=list)
    spec_files_not_run: List[str] = field(default_factory=list)
    spec_files_no_report: List[str] = field(default_factory=list)
    batches: List[Dict[str, Any]] = field(default_factory=list)

    #: When the runner began and finished, in the text form PostgreSQL's
    #: CURRENT_TIMESTAMP writes into these TEXT columns. Empty means NOT
    #: MEASURED (a result built by hand): `record_run` then leaves the start to
    #: the column default and the finish NULL rather than guess either.
    started_at: str = ""
    completed_at: str = ""

    #: Host-stall sampling (qa-fail-5cacee65f1d03c8c). `failures_during_stall`
    #: is None -- never 0 -- when no batch was sampled; the run-level
    #: `stall_summary` rolls the per-batch `stall_census` records up.
    failures_during_stall: Optional[int] = None
    failures_stall_unmeasured: int = 0
    stall_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failures"] = [asdict(f) for f in self.failures]
        return d


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def discover_coverage_gaps() -> List[Dict[str, Any]]:
    """Return canvases from component_registry.yaml lacking any tests/e2e/<key>*.spec.ts.

    Mirrors the pattern in icdev/tools/ace/canvas_role_gap.py::detect_gaps().
    """
    registry_path = PROJECT_ROOT / _COMPONENT_REGISTRY_PATH
    if not registry_path.exists():
        logger.warning("qa_agent_runner: component_registry.yaml not found at %s", registry_path)
        return []

    try:
        import yaml  # type: ignore[import-untyped]
        with open(registry_path, encoding="utf-8") as fh:
            registry = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.error("qa_agent_runner: cannot parse component_registry.yaml: %s", exc)
        return []

    canvases = registry.get("canvases", []) or []
    child_apps = registry.get("child_apps", []) or []
    all_components = canvases + child_apps

    existing_specs = set(glob.glob(str(PROJECT_ROOT / _E2E_SPEC_GLOB)))
    existing_keys: set[str] = set()
    for spec_path in existing_specs:
        basename = os.path.basename(spec_path).replace(".spec.ts", "")
        existing_keys.add(basename)

    gaps: List[Dict[str, Any]] = []
    for component in all_components:
        key = str(component.get("key") or "").strip()
        enabled = bool(component.get("enabled", True))
        if not key or not enabled:
            continue
        has_spec = any(k == key or k.startswith(key) or key in k for k in existing_keys)
        if not has_spec:
            gaps.append({
                "canvas_key": key,
                "display_name": component.get("display_name") or key,
                "route": component.get("route") or f"/{key}",
                "enabled": enabled,
            })

    return gaps


def generate_spec_stub(canvas_key: str, display_name: str, route: str) -> str:
    """Return a TypeScript Playwright spec for a previously-uncovered canvas.

    Covers: route HTTP status < 400, DOM load, CUI banner presence, IQE widget.
    Follows the pattern in tests/e2e/canvas_smoke.spec.ts.
    """
    safe_name = canvas_key.replace("-", "_")
    return f"""import {{ test, expect }} from '@playwright/test';

// QA Agent generated spec — {display_name}
// Canvas key: {canvas_key}  Route: {route}

test.describe('{display_name} QA Smoke', () => {{
  test('{canvas_key} loads without error', async ({{ page }}) => {{
    const response = await page.goto('{route}');
    expect(response?.status()).toBeLessThan(400);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('body')).not.toContainText('Traceback');
    await expect(page.locator('body')).not.toContainText('Internal Server Error');
    await page.screenshot({{
      path: '{_QA_SCREENSHOT_DIR}/{canvas_key}_smoke.png',
      fullPage: true,
    }});
  }});

  test('{canvas_key} has CUI classification banner', async ({{ page }}) => {{
    await page.goto('{route}');
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').textContent();
    expect(body).toContain('CUI');
  }});

  test('{canvas_key} IQE widget present', async ({{ page }}) => {{
    await page.goto('{route}');
    await page.waitForLoadState('domcontentloaded');
    const iqe = page.locator('[id*="iqe"], [class*="iqe-query"], [data-iqe]');
    await expect(iqe.first()).toBeVisible({{ timeout: 5000 }}).catch(() => {{
      // IQE widget optional — log absence but do not fail
      console.warn('{safe_name}: IQE widget not found on {route}');
    }});
  }});
}});
"""

# ---------------------------------------------------------------------------
# Host-stall sampling (qa-fail-5cacee65f1d03c8c)
# ---------------------------------------------------------------------------
#
# Run qa-1789161604 (2026-09-11) timed out 4 of 849 tests, all four inside
# windows where the isolated server's request log went SILENT for 20-59 s
# (thirteen such gaps in the run) and a hand-run sampler beside batches 6-11
# saw its own 5 s loop stretch to 12 s and 14 s while /api/health still
# answered in 0.10 s. Every one of the four pages answered in under 2 s minutes
# later. The host, not the app, was stalling -- and the runner recorded that
# sweep IDENTICALLY to four product defects: `status=failed failed=4`, twice
# the wall clock of the same suite the day before, and no field anywhere that
# could say so.
#
# So a sampler now runs beside EVERY batch and each failure carries a verdict:
#   during_stall True   a stall sample's covered interval overlaps the test's
#                       own [startTime, startTime + duration] window
#   during_stall False  the batch WAS sampled and no stall sample overlaps
#   during_stall None   UNMEASURED -- sampler off, no sample in the batch, or
#                       a result that carries no timing. Never folded into
#                       False: "nobody looked" is not "no stall".
# THE VERDICT NEVER MOVES THE STATUS. A stall EXPLAINS a timeout; it does not
# excuse it -- a sweep with four timeouts inside a stall is still a red sweep,
# and reading it green would be the "re-run to get green" the card forbids.
#
# TWO SIGNALS, NEVER MERGED, because they send a reader to different places:
#   host_stalled  the sampler thread's `Event.wait(interval)` returned late by
#                 more than _STALL_HOST_OVERSHOOT_SECONDS. This process was not
#                 scheduled. Measured on the incident: 7-9 s over a 5 s
#                 interval; normal overshoot on this host is milliseconds.
#   health_slow   /api/health took >= _STALL_HEALTH_SLOW_SECONDS. The server
#                 answered, slowly -- the app or its database, not the host.
# `unreachable` (no answer at all) is NOT a stall kind: Playwright starts and
# stops its own webServer per batch unless ICDEV_NO_SERVER is set, so a probe
# before the server is up is expected. It is counted, apart.
#
# WHAT IT CANNOT SAY, named: the sampler lives in the runner's own process, so
# `host_stalled` proves THIS process was starved and infers the host from that
# -- on the incident the two coincided (the hand sampler was a separate
# process and stretched identically), but a reader should treat it as "the
# host did not run us" evidence, not a CPU measurement. It does not read the
# server's request log (another process's stdout), so the 20 s+ SILENCE that
# was the incident's primary evidence is not re-derived here. And the census
# lives in the run report JSON `report_path` names, under a disposable
# `.tmp/`: `ace_qa_runs` has no column for it and this card adds no migration,
# so the TABLE row still cannot say "stall" -- the sweep report written from
# the JSON can, which is what the card asked for.
#
# Kill switch ICDEV_QA_STALL_SAMPLER=0 (reported as `disabled_by_env`, never
# silent); ICDEV_QA_STALL_SAMPLE_SECONDS overrides the interval.

#: Seconds between samples. The incident was measured at 5 s.
_STALL_SAMPLE_SECONDS = 5.0
#: A sleep that returns this much late is a host stall. Measured incident:
#: 7-9 s overshoot; ordinary jitter: milliseconds. Sits well inside both.
_STALL_HOST_OVERSHOOT_SECONDS = 2.0
#: A /api/health answer this slow is a slow SERVER (it answers in ~0.1 s idle
#: and answered 0.10 s DURING the host stall).
_STALL_HEALTH_SLOW_SECONDS = 2.0
#: How long one probe may wait for /api/health before it is recorded as slow.
_STALL_PROBE_TIMEOUT_SECONDS = 15.0

_ENV_STALL_SAMPLER = "ICDEV_QA_STALL_SAMPLER"
_ENV_STALL_SAMPLE_SECONDS = "ICDEV_QA_STALL_SAMPLE_SECONDS"

SAMPLE_OK = "ok"
SAMPLE_HOST_STALLED = "host_stalled"
SAMPLE_HEALTH_SLOW = "health_slow"
SAMPLE_UNREACHABLE = "unreachable"
#: The kinds that count as a stall for a failure's verdict. `unreachable` is
#: deliberately absent -- see the section comment.
STALL_KINDS = (SAMPLE_HOST_STALLED, SAMPLE_HEALTH_SLOW)


def resolve_e2e_base_url(environ: Optional[Mapping[str, str]] = None) -> str:
    """The origin the E2E suite navigates to -- the Python mirror of
    tests/e2e/fixtures/base_url.ts::resolveBaseUrl, same three variables in
    the same order. That file is the ONE resolver for every spec and the
    config; this is the one for the runner, and it exists because the sampler
    must probe the server the suite is talking to, not a second guess at it.
    """
    env = os.environ if environ is None else environ
    url = (
        env.get("ICDEV_E2E_BASE_URL")
        or env.get("ICDEV_DASHBOARD_URL")
        or f"http://localhost:{env.get('ICDEV_DASHBOARD_PORT') or '5050'}"
    )
    return url.rstrip("/")


def probe_url(base_url: str) -> str:
    """The URL the sampler probes -- <base>/api/health, with `localhost`
    connected as 127.0.0.1.

    MEASURED on this host (Windows 11, 2026-09-11): getaddrinfo("localhost")
    lists ::1 before 127.0.0.1, the dashboard binds IPv4 only, and the ::1
    attempt takes 2.05 s to be REFUSED before the fallback answers in 0.05 s
    -- so a probe of `http://localhost:5050` costs 2.08 s every time, exactly
    the _STALL_HEALTH_SLOW_SECONDS threshold, and a default run (no
    ICDEV_E2E_BASE_URL) would read EVERY sample as `health_slow`. That is the
    resolver's cost, not the server's, and the sampler exists to measure the
    server. Chromium races both families and does not pay it. Any other host
    is probed as given; the census records `probe_url` either way.

    NOT route_smoke.resolve_base, on purpose: that resolver pins the first
    family that answers and CACHES an unchanged answer per process, so a
    sampler whose first probe precedes a Playwright-managed webServer would pin
    `localhost` for the whole run and pay the penalty on every later sample.
    The fail-safe it provides is kept another way: `probe_health` retries the
    base AS GIVEN when 127.0.0.1 refuses, so an IPv6-only bind still answers.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(base_url)
    if parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts).rstrip("/") + "/api/health"


def probe_health(base_url: str, timeout: float = _STALL_PROBE_TIMEOUT_SECONDS) -> Optional[float]:
    """Seconds for GET <base>/api/health to answer, or None if nothing answered.

    An HTTP error IS an answer (the server is up). A timeout is an answer that
    came too late -- returned as the elapsed time so it classifies as slow,
    never as absent; a server that is up and starved must not read the same
    as one that is down.
    """
    swapped = probe_url(base_url)
    as_given = base_url.rstrip("/") + "/api/health"
    elapsed = _time_get(swapped, timeout)
    if elapsed is None and swapped != as_given:
        # 127.0.0.1 refused. An IPv6-only bind answers on the base as given;
        # a server that is down refuses both and stays `unreachable`.
        elapsed = _time_get(as_given, timeout)
    return elapsed


def _time_get(url: str, timeout: float) -> Optional[float]:
    """Seconds for one GET to answer (any status), or None if nothing did."""
    import socket
    import urllib.error
    import urllib.request

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read(4096)
        return time.perf_counter() - t0
    except urllib.error.HTTPError:
        return time.perf_counter() - t0
    except (TimeoutError, socket.timeout):
        return time.perf_counter() - t0
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
            return time.perf_counter() - t0
        return None
    except (OSError, ValueError):
        return None


def classify_sample(
    health_seconds: Optional[float],
    sleep_overshoot_seconds: float,
    *,
    host_overshoot_seconds: float = _STALL_HOST_OVERSHOOT_SECONDS,
    health_slow_seconds: float = _STALL_HEALTH_SLOW_SECONDS,
) -> str:
    """One kind per sample. The host verdict is asked FIRST: a starved host
    also makes the probe slow, and "the host did not run us" is the finding
    that says where to look."""
    if sleep_overshoot_seconds >= host_overshoot_seconds:
        return SAMPLE_HOST_STALLED
    if health_seconds is None:
        return SAMPLE_UNREACHABLE
    if health_seconds >= health_slow_seconds:
        return SAMPLE_HEALTH_SLOW
    return SAMPLE_OK


@dataclass
class StallSample:
    #: When the probe finished, UTC ISO text (for a reader) and epoch (for the
    #: overlap arithmetic, on the same host clock Playwright stamps startTime).
    at: str
    at_epoch: float
    #: When the sleep before this sample BEGAN. The sample's covered interval
    #: is [window_start_epoch, at_epoch]: a stall recorded here happened
    #: somewhere inside it.
    window_start_epoch: float
    health_seconds: Optional[float]
    sleep_overshoot_seconds: float
    kind: str


def _iso_utc(epoch: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class StallSampler(threading.Thread):
    """Samples the server and the sampler's own scheduling beside one batch.

    Daemon thread: a probe that hangs can never hold the sweep past its own
    deadline. Nothing it does can raise into the run -- a broken probe is
    recorded on `error` and the census reports itself unmeasured.
    """

    def __init__(
        self,
        base_url: str,
        interval_seconds: float = _STALL_SAMPLE_SECONDS,
        probe=None,
        *,
        host_overshoot_seconds: float = _STALL_HOST_OVERSHOOT_SECONDS,
        health_slow_seconds: float = _STALL_HEALTH_SLOW_SECONDS,
        probe_timeout_seconds: float = _STALL_PROBE_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(name="qa-stall-sampler", daemon=True)
        self.base_url = base_url
        self.interval = max(0.001, float(interval_seconds))
        self.probe = probe or (lambda base: probe_health(base, timeout=probe_timeout_seconds))
        self.host_overshoot_seconds = host_overshoot_seconds
        self.health_slow_seconds = health_slow_seconds
        self.probe_timeout_seconds = probe_timeout_seconds
        self.samples: List[StallSample] = []
        self.error: Optional[str] = None
        # `_halt`, NOT `_stop`: threading.Thread defines `_stop` as an internal
        # METHOD on CPython <= 3.12, and `join()` -> `_wait_for_tstate_lock()`
        # calls `self._stop()`. Assigning an Event to that name shadows the
        # method, so every join raises `TypeError: 'Event' object is not
        # callable`. It is INVISIBLE on a modern local interpreter -- 3.14 no
        # longer has that attribute, so this passed locally and failed all 12
        # sampler tests on CI, which pins python-version 3.11.
        self._halt = threading.Event()

    def run(self) -> None:  # pragma: no cover - exercised through start()/stop()
        try:
            while not self._halt.is_set():
                sleep_started = time.time()
                self._halt.wait(self.interval)
                if self._halt.is_set():
                    break
                woke = time.time()
                overshoot = max(0.0, (woke - sleep_started) - self.interval)
                health = self.probe(self.base_url)
                finished = time.time()
                self.samples.append(StallSample(
                    at=_iso_utc(finished),
                    at_epoch=finished,
                    window_start_epoch=sleep_started,
                    health_seconds=None if health is None else round(float(health), 3),
                    sleep_overshoot_seconds=round(overshoot, 3),
                    kind=classify_sample(
                        health, overshoot,
                        host_overshoot_seconds=self.host_overshoot_seconds,
                        health_slow_seconds=self.health_slow_seconds,
                    ),
                ))
        except Exception as exc:  # noqa: BLE001 - recorded, never propagated
            self.error = f"{type(exc).__name__}: {exc}"

    def stop(self, timeout: Optional[float] = None) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout if timeout is not None else self.probe_timeout_seconds + 1.0)

    def census(self) -> Dict[str, Any]:
        """What this batch's sampling measured. `measured` is False -- with a
        reason -- when there is nothing to count; counts are then None, never 0."""
        if not self.samples:
            reason = f"sampler_error: {self.error}" if self.error else "no_samples"
            return {
                "measured": False, "reason": reason, "samples": 0,
                "host_stalls": None, "health_slow": None, "unreachable": None,
                "reachable_samples": None, "health_max_seconds": None,
                "max_sleep_overshoot_seconds": None,
                "interval_seconds": self.interval, "base_url": self.base_url,
                "probe_url": probe_url(self.base_url),
            }
        kinds = [s.kind for s in self.samples]
        reachable = [s.health_seconds for s in self.samples if s.health_seconds is not None]
        out: Dict[str, Any] = {
            "measured": True,
            "samples": len(self.samples),
            "host_stalls": kinds.count(SAMPLE_HOST_STALLED),
            "health_slow": kinds.count(SAMPLE_HEALTH_SLOW),
            "unreachable": kinds.count(SAMPLE_UNREACHABLE),
            "reachable_samples": len(reachable),
            "health_max_seconds": max(reachable) if reachable else None,
            "max_sleep_overshoot_seconds": max(s.sleep_overshoot_seconds for s in self.samples),
            "interval_seconds": self.interval,
            "base_url": self.base_url,
            "probe_url": probe_url(self.base_url),
            "stalls": [
                {
                    "at": s.at or _iso_utc(s.at_epoch), "kind": s.kind,
                    "health_seconds": s.health_seconds,
                    "sleep_overshoot_seconds": s.sleep_overshoot_seconds,
                }
                for s in self.samples if s.kind in STALL_KINDS
            ],
        }
        if self.error:
            out["error"] = self.error
        return out


def _parse_playwright_start(started_at: str) -> Optional[float]:
    """Playwright's `startTime` (`2026-09-11T21:28:14.504Z`) as an epoch, or None."""
    from datetime import datetime, timezone

    text = (started_at or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _overlapping_stalls(
    started_at: str, duration_ms: Optional[int], samples: List[StallSample],
) -> Optional[List[StallSample]]:
    start = _parse_playwright_start(started_at)
    if start is None or duration_ms is None or not samples:
        return None
    end = start + max(0, int(duration_ms)) / 1000.0
    return [
        s for s in samples
        if s.kind in STALL_KINDS and s.window_start_epoch <= end and s.at_epoch >= start
    ]


def failure_during_stall(
    started_at: str, duration_ms: Optional[int], samples: List[StallSample],
) -> Optional[bool]:
    """True / False / None -- and None is "nobody measured", never "no stall".

    Overlap is between the test's own window [startTime, startTime + duration]
    and each stall sample's covered interval [sleep started, probe finished];
    a stall may have happened anywhere inside the latter, so touching counts.
    Both clocks are the same host's.
    """
    hits = _overlapping_stalls(started_at, duration_ms, samples)
    if hits is None:
        return None
    return bool(hits)


def _describe_sample(s: StallSample) -> str:
    at = s.at or _iso_utc(s.at_epoch)
    if s.kind == SAMPLE_HOST_STALLED:
        return f"{s.kind} overshoot {s.sleep_overshoot_seconds:.1f}s at {at}"
    return f"{s.kind} health {s.health_seconds:.1f}s at {at}"


def annotate_failures_with_stalls(failures: List[TestFailure], samples: List[StallSample]) -> None:
    """Write `during_stall` and a human `stall_detail` onto each failure."""
    for f in failures:
        hits = _overlapping_stalls(f.started_at, f.duration_ms, samples)
        if hits is None:
            f.during_stall = None
            if not samples:
                f.stall_detail = "unmeasured: sampler took no sample during this batch"
            else:
                f.stall_detail = "unmeasured: result carries no startTime/duration"
            continue
        f.during_stall = bool(hits)
        if hits:
            f.stall_detail = "; ".join(_describe_sample(s) for s in hits)
        else:
            f.stall_detail = (
                f"no stall sample overlaps the test window ({len(samples)} samples in batch)"
            )


def summarize_stalls(result: QARunResult, *, base_url: str, interval_seconds: float) -> Dict[str, Any]:
    """Roll the per-batch censuses up. A batch that was not sampled is COUNTED
    as unsampled, never averaged in, and a run where nothing was sampled
    reports None counts."""
    sampled = [
        b["stall_census"] for b in result.batches
        if isinstance(b.get("stall_census"), dict) and b["stall_census"].get("measured")
    ]
    summary: Dict[str, Any] = {
        "measured": bool(sampled),
        "batches_sampled": len(sampled),
        "batches_unsampled": len(result.batches) - len(sampled),
        "interval_seconds": interval_seconds,
        "base_url": base_url,
    }
    for key in ("samples", "host_stalls", "health_slow", "unreachable", "reachable_samples"):
        summary[key] = sum(int(c.get(key) or 0) for c in sampled) if sampled else None
    return summary


def _stall_sampler_config(env: Mapping[str, str]) -> Dict[str, Any]:
    """Read the sampler's switches ONCE per run, from the run's own env."""
    enabled = (env.get(_ENV_STALL_SAMPLER) or "1").strip().lower() not in ("0", "false", "no", "off")
    raw = env.get(_ENV_STALL_SAMPLE_SECONDS)
    interval = _STALL_SAMPLE_SECONDS
    if raw:
        try:
            interval = max(0.5, float(raw))
        except ValueError:
            logger.warning("qa_agent_runner: %s=%r is not a number; using %s",
                           _ENV_STALL_SAMPLE_SECONDS, raw, _STALL_SAMPLE_SECONDS)
    return {"enabled": enabled, "interval": interval, "base_url": resolve_e2e_base_url(env)}


# ---------------------------------------------------------------------------
# E2E execution
# ---------------------------------------------------------------------------

def _npx_cmd() -> str:
    try:
        from tools.compat.platform_utils import get_npx_cmd
        return get_npx_cmd()
    except Exception:
        return "npx.cmd" if sys.platform == "win32" else "npx"


def _now_text() -> str:
    """UTC now, spelled the way the ace_qa_runs column default spells it.

    The timestamp columns are TEXT and every existing row carries PostgreSQL's
    `2026-09-10 20:48:47.073977+00`. An ISO `T` separator would sort every row
    written from here after every older row of the same day.
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00")


def _make_run_id() -> str:
    import time
    return f"qa-{int(time.time())}"


def count_screenshot_attachments(report: dict) -> int:
    """Count the screenshots a batch report ACTUALLY recorded.

    `screenshot_count` used to be `len(glob(<run dir>/*.png))` over a directory
    NOTHING writes to. The run exported `PLAYWRIGHT_SCREENSHOT_DIR` naming it,
    and that variable had exactly ONE occurrence in the whole tree -- the write.
    No spec, config, fixture or workflow has ever read it. Playwright is
    configured `screenshot: 'on'` and writes into `outputDir`, recording each
    file's path on the per-result `attachments` -- which this module ALREADY
    reads for `TestFailure.screenshot_path`, so the evidence was in hand and the
    count looked somewhere else for it.

    So the field could not move: 16 of the 17 rows in `ace_qa_runs` read 0,
    INCLUDING the sweeps that failed 39 and 31 tests, and the one non-zero row
    (88) came from a hand-driven run that never called `run_e2e_suite`. A number
    that reports the same value for a clean sweep and a 39-failure sweep is a
    constant wearing the name of a measurement.

    Counted at ANY depth, because every spec under tests/e2e/ sits inside a
    `test.describe` and a top-level walk finds zero specs in every real report
    (the same shape `_walk_suites` was fixed for). Counted over EVERY result,
    passed included -- `screenshot: 'on'` captures one per test, so restricting
    the walk to failures would under-report by construction -- and over every
    retry attempt, each of which captured its own file.

    This counts what the reports THIS RUN COULD READ. Batches that produced no
    report contribute nothing and are named separately in
    `QARunResult.spec_files_no_report`; the two must not be read as one.
    """
    total = 0
    stack: List[dict] = list(report.get("suites") or [])
    while stack:
        suite = stack.pop()
        for spec in suite.get("specs") or []:
            for t in spec.get("tests") or []:
                for res in t.get("results") or []:
                    for att in res.get("attachments") or []:
                        ctype = (att.get("contentType") or "").lower()
                        if ctype.startswith("image/") and att.get("path"):
                            total += 1
        stack.extend(suite.get("suites") or [])
    return total


def resolve_spec_files(canvas_filter: Optional[str] = None) -> List[str]:
    """Return spec paths RELATIVE to the repo root, forward-slashed.

    A bare Playwright argument is a REGEX matched against the test file path,
    not a path. An absolute Windows path (backslashes, a drive colon) matches
    nothing: Playwright exits "No tests found" and still writes a 0/0/0 report,
    which reads exactly like a clean run.
    """
    if canvas_filter:
        pattern = str(PROJECT_ROOT / "tests" / "e2e" / f"*{canvas_filter}*.spec.ts")
    else:
        pattern = str(PROJECT_ROOT / _E2E_SPEC_GLOB)
    return sorted(
        os.path.relpath(p, str(PROJECT_ROOT)).replace(os.sep, "/")
        for p in glob.glob(pattern)
    )


def build_playwright_cmd(npx: str, rel_specs: List[str]) -> List[str]:
    """Build one `npx playwright test` argv for a batch of spec files.

    `--project=chromium` is ONE token on purpose. Split as `--project chromium`,
    the parser reads every following bare argument as a further PROJECT name,
    and the run dies with `Project(s) "<spec path>" not found`.

    No `--reporter` override: playwright.config.ts already declares the json
    reporter (whose output path honours ICDEV_PW_RUN_TAG), and a CLI
    `--reporter` REPLACES that list rather than adding to it.
    """
    return [npx, "playwright", "test", "--project=chromium", *rel_specs]


def batch_specs(rel_specs: List[str], batch_size: int) -> List[List[str]]:
    """Split spec files into fixed-size batches, preserving order."""
    size = max(1, int(batch_size))
    return [rel_specs[i:i + size] for i in range(0, len(rel_specs), size)]


def derive_status(result: QARunResult) -> str:
    """Classify a finished run. A run that measured nothing is never `passed`.

    `no_tests` (Playwright matched no test) and `incomplete` (the deadline or a
    missing report left spec files unmeasured) were both previously reported as
    `passed`, so a sweep that ran zero tests was indistinguishable from a green
    one.
    """
    if result.failed > 0:
        return STATUS_FAILED
    if result.spec_files_not_run or result.spec_files_no_report:
        return STATUS_INCOMPLETE
    if result.total == 0:
        return STATUS_NO_TESTS
    return STATUS_PASSED


def _batch_report_path(run_tag: str) -> Path:
    """Where playwright.config.ts's json reporter writes for this run tag."""
    return PROJECT_ROOT / ".tmp" / "test_runs" / f"playwright-results-{run_tag}.json"


def run_e2e_suite(
    canvas_filter: Optional[str] = None,
    trigger: str = "manual",
    deadline_seconds: int = _DEADLINE_SECONDS,
    batch_size: int = _BATCH_SIZE,
) -> QARunResult:
    """Execute the Playwright E2E suite in deadline-bounded batches.

    Returns a QARunResult with a structured failure list, the spec files that
    were measured, and the spec files that were not.
    """
    import time

    run_id = _make_run_id()

    result = QARunResult(
        run_id=run_id,
        trigger=trigger,
        canvas_filter=canvas_filter or "",
        started_at=_now_text(),
    )

    rel_specs = resolve_spec_files(canvas_filter)
    result.spec_files_total = len(rel_specs)
    if not rel_specs:
        logger.warning(
            "qa_agent_runner: no spec files matching canvas '%s'", canvas_filter or "*"
        )
        result.status = STATUS_NO_TESTS
        result.completed_at = _now_text()
        return result

    env = os.environ.copy()
    root_str = str(PROJECT_ROOT)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root_str if not existing_pp else root_str + os.pathsep + existing_pp

    npx = _npx_cmd()
    batches = batch_specs(rel_specs, batch_size)
    stall_cfg = _stall_sampler_config(env)
    disabled_census = {"measured": False, "reason": "disabled_by_env"}
    started = time.time()
    deadline = started + max(1, int(deadline_seconds))

    logger.info(
        "qa_agent_runner: starting run_id=%s canvas_filter=%s specs=%d batches=%d deadline=%ds",
        run_id, canvas_filter, len(rel_specs), len(batches), deadline_seconds,
    )

    for idx, batch in enumerate(batches):
        remaining = deadline - time.time()
        if remaining < _MIN_BATCH_SECONDS:
            for pending in batches[idx:]:
                result.spec_files_not_run.extend(pending)
            result.batches.append({
                "batch": idx, "status": "deadline_skipped", "files": list(batch),
            })
            break

        run_tag = f"{run_id}-b{idx}"
        benv = dict(env)
        benv["ICDEV_PW_RUN_TAG"] = run_tag
        report_path = _batch_report_path(run_tag)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        # The sampler runs beside the batch and is stopped on EVERY exit path
        # (the `finally`), so a deadline kill cannot leave a probe thread
        # sampling a server the next batch will replace.
        sampler = (
            StallSampler(stall_cfg["base_url"], interval_seconds=stall_cfg["interval"])
            if stall_cfg["enabled"] else None
        )
        t0 = time.time()
        killed = False
        if sampler is not None:
            sampler.start()
        try:
            proc = subprocess.run(
                build_playwright_cmd(npx, batch),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=benv,
                timeout=int(remaining),
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.TimeoutExpired:
            # This batch and everything after it is unmeasured, and each one is
            # NAMED — a deadline-truncated sweep that reported only what it got
            # through would read as full coverage.
            result.spec_files_not_run.extend(batch)
            for pending in batches[idx + 1:]:
                result.spec_files_not_run.extend(pending)
            killed = True
        except FileNotFoundError:
            for pending in batches[idx:]:
                result.spec_files_not_run.extend(pending)
            result.failures.append(TestFailure(
                test_name="setup",
                error_message="npx/playwright not found — install Node.js and @playwright/test",
                severity="critical",
            ))
            break
        finally:
            if sampler is not None:
                sampler.stop()
        stall_census = sampler.census() if sampler else disabled_census
        stall_samples = sampler.samples if sampler else []
        if killed:
            result.batches.append({
                "batch": idx, "status": "deadline_killed",
                "seconds": round(time.time() - t0, 1), "files": list(batch),
                "stall_census": stall_census,
            })
            break

        elapsed = round(time.time() - t0, 1)
        raw_json = _read_batch_report(report_path, proc)
        if not raw_json:
            result.spec_files_no_report.extend(batch)
            result.batches.append({
                "batch": idx, "status": "no_report", "seconds": elapsed,
                "returncode": proc.returncode, "files": list(batch),
                "error": (proc.stderr or proc.stdout or "")[:500],
                "stall_census": stall_census,
            })
            continue

        try:
            report = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            result.spec_files_no_report.extend(batch)
            result.batches.append({
                "batch": idx, "status": "unparseable_report", "seconds": elapsed,
                "returncode": proc.returncode, "files": list(batch),
                "stall_census": stall_census,
            })
            continue

        batch_failures = parse_playwright_json(raw_json)
        annotate_failures_with_stalls(batch_failures, stall_samples)
        result.failures.extend(batch_failures)
        _tally(report, result)
        result.screenshot_count += count_screenshot_attachments(report)
        result.spec_files_run.extend(batch)
        stats = report.get("stats") or {}
        batch_record = {
            "batch": idx, "status": "ok", "seconds": elapsed,
            "returncode": proc.returncode, "files": list(batch),
            "report_path": str(report_path),
            "stats": {k: stats.get(k) for k in ("expected", "unexpected", "skipped", "flaky")},
            "stall_census": stall_census,
        }
        # Report-level errors say WHY a batch ran zero tests — a webServer that
        # never came up, a config that failed to load. `no_tests` on its own is
        # a shrug, and these are different fixes from "the suite is empty".
        errors = [
            str(e.get("message") or e) for e in (report.get("errors") or [])
        ]
        if errors:
            batch_record["errors"] = errors[:5]
        result.batches.append(batch_record)

    # `failed` was tallied from Playwright's `stats.unexpected`; `failures` is
    # what the parser could NAME. If the two disagree the gap is reported, never
    # resolved in favour of the parser -- a parser blind spot must not turn a
    # red sweep green.
    result.failures_unparsed = max(0, result.failed - len(result.failures))
    if result.failures_unparsed:
        logger.warning(
            "qa_agent_runner: run_id=%s Playwright reports %d unexpected but only %d "
            "were parsed into failures (%d unnamed)",
            run_id, result.failed, len(result.failures), result.failures_unparsed,
        )
    # The stall verdicts are ROLLED UP, never folded into the status: a stall
    # explains a timeout and does not excuse it. `failures_during_stall` is
    # None when no batch was sampled -- a sweep nobody measured must not read
    # as "0 failures during a stall".
    result.stall_summary = summarize_stalls(
        result, base_url=stall_cfg["base_url"], interval_seconds=stall_cfg["interval"],
    )
    result.failures_during_stall = (
        sum(1 for f in result.failures if f.during_stall is True)
        if result.stall_summary["measured"] else None
    )
    result.failures_stall_unmeasured = sum(1 for f in result.failures if f.during_stall is None)
    # Derive the verdict BEFORE persisting it. The two lines used to run the
    # other way round, so the file `report_path` sends a reader to carried the
    # `running` the result was constructed with -- for every sweep ever taken.
    result.completed_at = _now_text()
    result.status = derive_status(result)
    write_run_report(result)

    logger.info(
        "qa_agent_runner: run_id=%s status=%s total=%d passed=%d failed=%d "
        "specs_run=%d/%d not_run=%d no_report=%d",
        run_id, result.status, result.total, result.passed, result.failed,
        len(result.spec_files_run), result.spec_files_total,
        len(result.spec_files_not_run), len(result.spec_files_no_report),
    )
    return result


def _read_batch_report(report_path: Path, proc: "subprocess.CompletedProcess[str]") -> Optional[str]:
    """Return a batch's raw JSON report, or None if it produced none."""
    if report_path.exists():
        return report_path.read_text(encoding="utf-8", errors="replace")
    stdout = (proc.stdout or "").strip()
    return stdout if stdout.startswith("{") else None


def write_run_report(result: QARunResult) -> Path:
    """Persist the aggregated run to .tmp/ace/qa/<run_id>-results.json."""
    out = PROJECT_ROOT / ".tmp" / "ace" / "qa" / f"{result.run_id}-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Stamped BEFORE serialising, so the artifact names itself: a reader holding
    # only the file can say which run it is. This function is the ONE owner of
    # the field -- the caller used to assign it from the return value, i.e.
    # after the bytes were already written with `report_path: ""`.
    result.report_path = str(out)
    try:
        out.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    except OSError as exc:
        # A path that was never written must not be advertised as the report.
        result.report_path = ""
        logger.error("qa_agent_runner: cannot write run report %s: %s", out, exc)
    return out


def _tally(report: dict, result: QARunResult) -> None:
    """Accumulate one batch report's stats onto the run total."""
    stats = report.get("stats") or {}
    expected = int(stats.get("expected") or 0)
    unexpected = int(stats.get("unexpected") or 0)
    skipped = int(stats.get("skipped") or 0)
    result.total += expected + unexpected + skipped
    result.passed += expected
    result.skipped += skipped
    # `failed` is Playwright's OWN count. It used to be `len(result.failures)`
    # assigned after the loop, and on the 2026-08-22 sweep (task-qa-sweep-3c7b8b3d)
    # that read 0 against 31 `unexpected` across 11 batch reports -- the parser
    # walked one suite level and every spec sits under a `test.describe`, so
    # `status` was `passed` with thirty-one red tests inside it.
    result.failed += unexpected


def parse_playwright_json(raw_json: str) -> List[TestFailure]:
    """Parse Playwright JSON reporter output into TestFailure objects."""
    try:
        report = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    failures: List[TestFailure] = []
    _walk_suites(report.get("suites") or [], "", "", failures)
    return failures


def _walk_suites(
    suites: List[dict], parent_title: str, parent_file: str, failures: List[TestFailure],
) -> None:
    """Collect failed specs from a Playwright suite tree, at ANY depth.

    Playwright's JSON reporter nests: a FILE suite (title = the file name, no
    specs of its own) holds one suite per `test.describe`, which holds the
    specs. Every spec under tests/e2e/ is inside a describe block, so a walk of
    the top level alone finds zero specs in every real report -- measured on
    the 2026-08-22 sweep: 6 file suites per batch, 0 specs each, 1 child each.
    The innermost suite names the test (`Auth flow > login page loads`); the
    file is inherited from whichever ancestor carries one.
    """
    for suite in suites or []:
        suite_title = suite.get("title") or parent_title or "unknown"
        spec_file = suite.get("file") or parent_file or ""
        for spec in suite.get("specs") or []:
            spec_title = spec.get("title", "unknown")
            test_name = f"{suite_title} > {spec_title}"
            for t in spec.get("tests") or []:
                t_results = t.get("results") or []
                if not t_results:
                    continue
                last = t_results[-1]
                status = last.get("status") or ""
                if status in ("passed", "expected", "skipped"):
                    continue
                err = last.get("error") or {}
                error_msg = err.get("message") or err.get("snippet") or "Test failed"
                screenshot_path = ""
                for att in last.get("attachments") or []:
                    ctype = (att.get("contentType") or "").lower()
                    if ctype.startswith("image/") and att.get("path"):
                        screenshot_path = att["path"]
                        break

                severity = "critical" if any(k in test_name.lower() for k in ("auth", "rls", "login", "permission")) else "high"
                duration = last.get("duration")
                failures.append(TestFailure(
                    test_name=test_name,
                    spec_file=spec_file or spec.get("file") or "",
                    error_message=error_msg[:1000],
                    screenshot_path=screenshot_path,
                    severity=severity,
                    started_at=str(last.get("startTime") or ""),
                    duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
                ))
        _walk_suites(suite.get("suites") or [], suite_title, spec_file, failures)


# ---------------------------------------------------------------------------
# Kanban task filing
# ---------------------------------------------------------------------------

def failure_task_id(run_id: str, test_name: str) -> str:
    """The card id a failure files under: `qa-fail-` + sha256(run:test)[:16].
    ONE derivation, read by the filer and by the failure-row writer, so a row's
    `kanban_task_id` can never name a card the filer would not have created."""
    return "qa-fail-" + hashlib.sha256(f"{run_id}:{test_name}".encode()).hexdigest()[:16]


def file_failure_tasks(
    failures: List[TestFailure],
    run_id: str,
    instance_id: str = "",
) -> List[str]:
    """Create kanban tasks for each failure. Returns list of inserted task IDs."""
    if not failures:
        return []

    try:
        from tools.kanban.task_factory import create_tasks
    except ImportError:
        logger.error("qa_agent_runner: cannot import task_factory — skipping kanban filing")
        return []

    specs = []
    for f in failures:
        task_id = failure_task_id(run_id, f.test_name)
        idem_key = task_id[len("qa-fail-"):]
        desc_lines = [
            f"**Test**: {f.test_name}",
            f"**Spec file**: {f.spec_file}",
            f"**Error**: {f.error_message}",
            f"**Screenshot**: {f.screenshot_path or 'none captured'}",
            f"**Run ID**: {run_id}",
        ]
        if instance_id:
            desc_lines.append(f"**ACE Instance**: {instance_id}")
        specs.append({
            "id": task_id,
            "title": f"[QA] {f.test_name[:80]}",
            "description": "\n".join(desc_lines),
            # NOT "bug". create_tasks refuses it outright — VALID_TASK_TYPES is
            # {build, run, fix, research, deploy, test, chore} and the raise
            # happens before any insert, so every call filed nothing at all.
            "task_type": "fix",
            "priority": "critical" if f.severity == "critical" else "high",
            "status": "backlog",
            "idempotency_key": idem_key,
        })

    created = create_tasks(specs)
    logger.info("qa_agent_runner: filed %d kanban tasks for run %s", len(created), run_id)
    return created


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def record_run(result: QARunResult) -> str:
    """Persist QA run to ace_qa_runs (append-only). Returns run_id."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
    except ImportError:
        from tools.db.storage import get_canvas_connection  # type: ignore[no-reattr]

    # Both times are NAMED. Left to the column default, `started_at` recorded
    # the moment of this INSERT -- which happens after the sweep -- and
    # `completed_at` stayed NULL on every row. An unmeasured start (a result
    # built by hand) still falls back to the default; an unmeasured finish is
    # NULL, never a guess.
    started_at = result.started_at or None
    completed_at = result.completed_at or None
    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.execute(
            """INSERT INTO ace_qa_runs
               (id, trigger, canvas_filter, status,
                total_tests, passed, failed, screenshot_count, report_path,
                started_at, completed_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                       COALESCE(%s, CURRENT_TIMESTAMP), %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                result.run_id, result.trigger, result.canvas_filter, result.status,
                result.total, result.passed, result.failed,
                result.screenshot_count, result.report_path,
                started_at, completed_at,
            ),
        )
        conn.commit()
    except Exception as exc:
        # ONE dialect. A SQLite `?` retry used to follow this statement: on
        # SQLite it never ran (storage translates `%s`), and on PostgreSQL it
        # could only raise -- so what it did was log ITS error instead of this.
        logger.error("qa_agent_runner: record_run failed: %s", exc)
    finally:
        conn.close()
    return result.run_id


def record_failure(
    failure: TestFailure,
    run_id: str,
    kanban_task_id: str = "",
) -> str:
    """Persist one failure to ace_qa_failures (append-only). Returns failure_id."""
    failure_id = hashlib.sha256(f"{run_id}:{failure.test_name}".encode()).hexdigest()[:24]

    try:
        from icdev.tools.db.storage import get_canvas_connection
    except ImportError:
        from tools.db.storage import get_canvas_connection  # type: ignore[no-reattr]

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.execute(
            """INSERT INTO ace_qa_failures
               (id, run_id, test_name, spec_file, error_message,
                screenshot_path, severity, kanban_task_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                failure_id, run_id, failure.test_name, failure.spec_file,
                failure.error_message, failure.screenshot_path,
                failure.severity, kanban_task_id,
            ),
        )
        conn.commit()
    except Exception as exc:
        # ONE dialect -- see record_run.
        logger.error("qa_agent_runner: record_failure failed: %s", exc)
    finally:
        conn.close()
    return failure_id


def get_run_status(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a run record by ID. Returns None if not found."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
    except ImportError:
        from tools.db.storage import get_canvas_connection  # type: ignore[no-reattr]

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        # ONE dialect. A `?` re-ask for a missing run raised on PostgreSQL and
        # was logged as a failure; "not found" is an answer, not an error.
        row = conn.execute(
            "SELECT * FROM ace_qa_runs WHERE id = %s", (run_id,)
        ).fetchone()
        if row is None:
            return None
        # A psycopg2 RealDictRow IS a mapping and carries no `.cursor`, so the
        # description walk found no keys and EVERY PostgreSQL lookup fell into
        # the degraded `raw` branch below -- the CLI printed `status=None
        # total=None` for a row that says `failed / 840 / 831 / 1`. The fields
        # were in hand and were stringified away. sqlite3.Row exposes `keys()`
        # and the same mapping protocol, so both drivers are read the same way.
        if isinstance(row, Mapping) or hasattr(row, "keys"):
            return dict(row)
        keys = [d[0] for d in (row.cursor.description if hasattr(row, "cursor") else [])]
        if not keys:
            # Never silently: a row nothing could read is reported as such.
            logger.warning(
                "qa_agent_runner: get_run_status could not name the columns of a "
                "%s row for %s", type(row).__name__, run_id,
            )
            return {"id": run_id, "raw": str(row)}
        return dict(zip(keys, row))
    except Exception as exc:
        logger.error("qa_agent_runner: get_run_status failed: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _print_stall_summary(result: QARunResult) -> None:
    """The stall lines of the human report. Unmeasured says so BY NAME; a
    sweep whose sampler never ran must not print a reassuring zero."""
    ss = result.stall_summary or {}
    if not ss.get("measured"):
        reason = ss.get("reason") or (
            "no batch was sampled" if ss else "sampler did not run"
        )
        print(f"  STALLS: not measured ({reason})")
        return
    batches = int(ss.get("batches_sampled") or 0) + int(ss.get("batches_unsampled") or 0)
    print(
        f"  STALLS: batches sampled {ss.get('batches_sampled')}/{batches}, "
        f"host stalls {ss.get('host_stalls')}, slow health {ss.get('health_slow')}, "
        f"unreachable {ss.get('unreachable')} "
        f"(every {ss.get('interval_seconds')}s at {ss.get('base_url')})"
    )
    if result.failures:
        print(
            f"  {result.failures_during_stall} of {len(result.failures)} failures inside "
            f"a measured stall; {result.failures_stall_unmeasured} unmeasured"
        )


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QA Agent Runner — Playwright E2E execution for ACE qa_agent role"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Execute Playwright E2E suite")
    group.add_argument("--discover-gaps", action="store_true", help="Find canvases without E2E specs")
    group.add_argument("--status", metavar="RUN_ID", help="Fetch status of a previous run")
    parser.add_argument("--canvas", metavar="CANVAS_KEY", help="Limit --run to specs matching this canvas key")
    parser.add_argument("--trigger", default="manual", help="Trigger label (default: manual)")
    parser.add_argument(
        "--deadline-seconds", type=int, default=_DEADLINE_SECONDS,
        help=f"Whole-sweep wall-clock budget (default: {_DEADLINE_SECONDS}; "
             "the full suite measures ~41.5m)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=_BATCH_SIZE,
        help=f"Spec files per Playwright invocation (default: {_BATCH_SIZE})",
    )
    parser.add_argument(
        "--record", dest="record", action="store_true", default=True,
        help="Persist the run to ace_qa_runs (default: on)",
    )
    parser.add_argument(
        "--no-record", dest="record", action="store_false",
        help="Run the suite without persisting it",
    )
    parser.add_argument(
        "--file-failures", action="store_true",
        help="File one kanban `fix` card per failure (default: off — one shared "
             "cause becomes N cards and N duplicate PRs, so filing is opt-in)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.run:
        result = run_e2e_suite(
            canvas_filter=args.canvas,
            trigger=args.trigger,
            deadline_seconds=args.deadline_seconds,
            batch_size=args.batch_size,
        )
        # A sweep the CLI does not persist is a sweep nobody can cite an hour
        # later: `--run` measured 770 tests and wrote NOTHING to ace_qa_runs,
        # so every caller that needed step 3/4 of the QA card had to hand-write
        # a driver around these same two seams. Both outcomes are REPORTED —
        # `recorded` is None when recording was not attempted, which is never
        # the same as an attempt that failed.
        persistence: Dict[str, Any] = {
            "recorded": None, "record_error": None,
            "filed_tasks": None, "file_failures_error": None,
            "recorded_failures": None, "record_failures_error": None,
        }
        if args.record:
            try:
                persistence["recorded"] = record_run(result)
            except Exception as exc:
                persistence["record_error"] = repr(exc)
                logger.error("qa_agent_runner: record_run failed: %s", exc)
        if result.failures and args.file_failures:
            try:
                persistence["filed_tasks"] = file_failure_tasks(
                    result.failures, run_id=result.run_id
                )
            except Exception as exc:
                persistence["file_failures_error"] = repr(exc)
                logger.error("qa_agent_runner: file_failure_tasks failed: %s", exc)
        # `record_failure` had a definition and NO call site here, so
        # `ace_qa_failures` held 0 rows for a 4-failure sweep and 0 for a clean
        # one (2026-09-11 sweep report, s8). One row per failure, after the
        # cards, so a row can name the card that was ACTUALLY created for it --
        # a deduped or refused card is "" on its row, never the derived id.
        if args.record:
            written = 0
            try:
                filed = set(persistence["filed_tasks"] or [])
                for f in result.failures:
                    derived = failure_task_id(result.run_id, f.test_name)
                    record_failure(
                        f, run_id=result.run_id,
                        kanban_task_id=derived if derived in filed else "",
                    )
                    written += 1
                persistence["recorded_failures"] = written
            except Exception as exc:
                persistence["recorded_failures"] = written
                persistence["record_failures_error"] = repr(exc)
                logger.error("qa_agent_runner: record_failure failed: %s", exc)

        if args.json:
            payload = result.to_dict()
            payload["persistence"] = persistence
            print(json.dumps(payload, indent=2))
        else:
            print(f"Run {result.run_id}: {result.status} ({result.passed}/{result.total} passed)")
            print(f"  recorded: {persistence['recorded'] or persistence['record_error'] or 'not attempted'}")
            if result.failures:
                print(f"  filed tasks: {persistence['filed_tasks'] if persistence['filed_tasks'] is not None else persistence['file_failures_error'] or 'not attempted (--file-failures is off)'}")
            print(f"  spec files: {len(result.spec_files_run)}/{result.spec_files_total} measured")
            for f in result.failures:
                tag = {
                    True: "DURING MEASURED STALL",
                    False: "no stall in window",
                    None: "STALL UNMEASURED",
                }[f.during_stall]
                print(f"  FAIL: {f.test_name} — {f.error_message[:120]}")
                print(f"        [{tag}: {f.stall_detail or 'no sampler ran'}]")
            _print_stall_summary(result)
            # Name the unmeasured spec files. A truncated sweep that printed
            # only what it got through would read as full coverage.
            for label, files in (
                ("NOT RUN (deadline)", result.spec_files_not_run),
                ("NO REPORT", result.spec_files_no_report),
            ):
                for path in files:
                    print(f"  {label}: {path}")
            for b in result.batches:
                for err in b.get("errors") or []:
                    print(f"  BATCH {b['batch']} ERROR: {err[:160]}")
        return 0 if result.status == STATUS_PASSED else 1

    if args.discover_gaps:
        gaps = discover_coverage_gaps()
        if args.json:
            print(json.dumps(gaps, indent=2))
        else:
            if gaps:
                print(f"Coverage gaps found: {len(gaps)}")
                for g in gaps:
                    print(f"  {g['canvas_key']} ({g['display_name']}) — {g['route']}")
            else:
                print("No coverage gaps detected.")
        return 0

    if args.status:
        row = get_run_status(args.status)
        if row is None:
            print(json.dumps({"error": f"run_id not found: {args.status}"}) if args.json else f"Not found: {args.status}")
            return 1
        if args.json:
            print(json.dumps(row, indent=2, default=str))
        else:
            print(f"Run {row.get('id')}: status={row.get('status')} "
                  f"total={row.get('total_tests')} passed={row.get('passed')} failed={row.get('failed')}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
