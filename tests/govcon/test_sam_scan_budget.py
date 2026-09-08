# [TEMPLATE: CUI // SP-CTI]
"""The synchronous SAM.gov scan is bounded by a wall clock, and the bound is REPORTED
(qa-fail-3805bd2c3cfc939a).

``tools/dashboard/api/govcon.py::scan_sam_gov`` calls
``tools.govcon.sam_scanner.scan_sam_gov`` SYNCHRONOUSLY inside the request and returns
its result.  The scanner loops ``for naics in naics_codes: for ntype in notice_types``
with one HTTP GET per pair -- args/govcon_config.yaml declares 8 NAICS x 4 notice types
= 32 SEQUENTIAL requests at DEFAULT_TIMEOUT = 30s each, so the worst case is 960s for
one web request, and ``tools/http/client.py`` retries 429/5xx up to ``max_retries``
on top of that.  MEASURED on this host 2026-09-08, every call failing 401 (the FAST
path): 16.76s for the loop with the database stubbed, 0.32s per call.  An independent
measurement recorded in tests/test_reflex_registration.py -- the reason the
``govcon_scan`` reflex is not registered at all -- saw a full scan "run past 30 minutes
without returning".

THE BUDGET MUST BE REAL, and that is what most of this file asserts.  A deadline that
only gates the LOOP while each request may still block for ``DEFAULT_TIMEOUT``, and
each rate-limit backoff may still sleep ``min(60, ...)``, is a budget in name only --
the same fiction as a ``subprocess.run(timeout=30)`` whose child runs on.  So the
remaining budget clamps the per-request timeout AND every sleep, and a pair is not
STARTED when too little budget is left to make a real attempt.

TRUNCATED MUST NEVER READ AS CLEAN.  A scan that stopped early reports
``complete: False`` with the unscanned pairs NAMED, because a short list of
opportunities is otherwise indistinguishable from a corpus that holds nothing.
"""
import time

import pytest

# Imported directly, never via importorskip: sam_scanner is first-party and its every
# third-party dependency (yaml, requests, the audit logger, the quota tracker) is behind
# a graceful try/except, so the module imports on any host.  A skip here would satisfy
# the gating census while asserting nothing.
from tools.govcon import sam_scanner


class _Cur:
    def fetchone(self):
        return None


class _Conn:
    """Stub connection -- these tests must never touch a real database."""

    def execute(self, *_a, **_k):
        return _Cur()

    def commit(self):
        pass

    def close(self):
        pass


_CONFIG = {
    "sam_gov": {
        "api_url": "https://example.invalid/opportunities/v2/search",
        "api_key_env": "SAM_TEST_KEY",
        "naics_codes": ["111111", "222222", "333333", "444444"],
        "notice_types": ["o", "p"],
        "rate_limit": {"delay_between_requests": 0.0},
    }
}

_ALL_PAIRS = {(n, t) for n in _CONFIG["sam_gov"]["naics_codes"] for t in _CONFIG["sam_gov"]["notice_types"]}


@pytest.fixture()
def scanner(monkeypatch):
    """sam_scanner with the database and the API key stubbed."""
    monkeypatch.setenv("SAM_TEST_KEY", "test-key")
    monkeypatch.setattr(sam_scanner, "_get_db", lambda db_path=None: _Conn())
    monkeypatch.setattr(sam_scanner, "_audit", lambda *a, **k: None)
    return sam_scanner


def _record_calls(monkeypatch, per_call_seconds=0.0, error=None):
    """Replace _safe_get with a recorder that consumes a fixed slice of the budget."""
    calls = []
    sleeps = []
    real_sleep = time.sleep

    def fake_get(url, headers=None, params=None, timeout=None, track_quota=True):
        calls.append({"ncode": params.get("ncode"), "ptype": params.get("ptype"), "timeout": timeout})
        if per_call_seconds:
            real_sleep(per_call_seconds)
        return (None, error) if error else ({"opportunitiesData": []}, None)

    monkeypatch.setattr(sam_scanner, "_safe_get", fake_get)
    monkeypatch.setattr(sam_scanner.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


# ---------------------------------------------------------------------------
# The default is UNCHANGED -- no existing caller's behaviour moves
# ---------------------------------------------------------------------------


def test_no_budget_scans_every_pair_and_reports_complete(scanner, monkeypatch):
    """budget_seconds=None is the CLI/reflex path: every pair, and complete is True."""
    calls, _ = _record_calls(monkeypatch)

    result = scanner.scan_sam_gov(config=_CONFIG)

    assert len(calls) == 8, "4 NAICS x 2 notice types must all be scanned when unbounded"
    assert result["complete"] is True
    assert result["budget_exhausted"] is False
    assert result["skipped_over_budget"] == []
    assert result["pairs_scanned"] == 8
    assert result["pairs_total"] == 8
    assert result["budget_seconds"] is None


# ---------------------------------------------------------------------------
# The bound, and the report of it
# ---------------------------------------------------------------------------


def test_budget_stops_the_loop_and_names_what_it_skipped(scanner, monkeypatch):
    """An exhausted budget stops issuing requests and reports the unscanned pairs BY NAME."""
    # Each call burns 0.5s of a 3s budget, so some pairs run and the rest cannot.
    calls, _ = _record_calls(monkeypatch, per_call_seconds=0.5)

    result = scanner.scan_sam_gov(config=_CONFIG, budget_seconds=3)

    assert 0 < len(calls) < 8, "a PARTIAL scan: some pairs ran, the budget stopped the rest"
    assert result["budget_exhausted"] is True
    assert result["complete"] is False, "a truncated scan must never report complete"
    assert result["pairs_scanned"] == len(calls)
    assert result["pairs_total"] == 8

    skipped = result["skipped_over_budget"]
    assert len(skipped) == 8 - len(calls), "every unscanned pair must be reported"
    # NAMED, not counted -- a bare number cannot tell an operator what was missed.
    assert all(set(s) >= {"naics", "notice_type"} for s in skipped)
    scanned_pairs = {(c["ncode"], c["ptype"]) for c in calls}
    skipped_pairs = {(s["naics"], s["notice_type"]) for s in skipped}
    assert not (scanned_pairs & skipped_pairs), "a pair is scanned or skipped, never both"
    assert scanned_pairs | skipped_pairs == _ALL_PAIRS


def test_budget_is_reported_even_when_the_scan_completes(scanner, monkeypatch):
    """A generous budget still records what the bound WAS -- the reader must not guess."""
    _record_calls(monkeypatch)

    result = scanner.scan_sam_gov(config=_CONFIG, budget_seconds=300)

    assert result["budget_seconds"] == 300
    assert result["budget_exhausted"] is False
    assert result["complete"] is True


# ---------------------------------------------------------------------------
# The budget is REAL -- it binds the request and the sleep, not only the loop
# ---------------------------------------------------------------------------


def test_per_request_timeout_is_clamped_to_the_remaining_budget(scanner, monkeypatch):
    """A 5s budget must never hand a 30s timeout to a request -- that is the fiction."""
    calls, _ = _record_calls(monkeypatch, per_call_seconds=0.05)

    scanner.scan_sam_gov(config=_CONFIG, budget_seconds=5)

    assert calls, "at least one request must have been attempted"
    for call in calls:
        assert call["timeout"] is not None, "the scan must pass an explicit per-request timeout"
        assert call["timeout"] <= 5, (
            f"per-request timeout {call['timeout']}s exceeds the whole 5s budget -- "
            "a single request could then outlast the budget on its own"
        )
    # Non-increasing headroom: a later request may not be given more time than an earlier one.
    timeouts = [c["timeout"] for c in calls]
    assert timeouts == sorted(timeouts, reverse=True)


def test_unbounded_scan_keeps_the_default_request_timeout(scanner, monkeypatch):
    """With no budget the per-request timeout is the module default, unchanged."""
    calls, _ = _record_calls(monkeypatch)

    scanner.scan_sam_gov(config=_CONFIG)

    assert calls, "at least one request must have been attempted"
    assert all(c["timeout"] == sam_scanner.DEFAULT_TIMEOUT for c in calls)


def test_rate_limit_backoff_never_sleeps_past_the_budget(scanner, monkeypatch):
    """min(60, delay * 2**n) inside a 2s budget is the same fiction as an unclamped timeout."""
    cfg = {"sam_gov": dict(_CONFIG["sam_gov"], rate_limit={"delay_between_requests": 30.0})}
    _, sleeps = _record_calls(monkeypatch, error="rate_limit exceeded")

    scanner.scan_sam_gov(config=cfg, budget_seconds=2)

    assert sleeps, "the rate-limit path must have been exercised"
    for slept in sleeps:
        assert slept <= 2, f"slept {slept}s inside a 2s budget"


# ---------------------------------------------------------------------------
# The ROUTES must ask for the bound -- a budget nothing passes is the
# declared-but-never-consumed defect one layer up from the code it governs
# ---------------------------------------------------------------------------


def _route_calls_scanner_with_a_budget(source, route_marker):
    """Does the named route hand ``budget_seconds`` to the scanner?

    Read from the SOURCE rather than by importing the dashboard app: importing it
    registers every blueprint against .env toggles and opens a live connection, which a
    unit test must not do.
    """
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != route_marker:
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and any(kw.arg == "budget_seconds" for kw in call.keywords):
                return True
        return False
    raise AssertionError(f"route function {route_marker!r} not found")


@pytest.mark.parametrize("route_func", ["scan_sam_gov", "run_pipeline"])
def test_synchronous_routes_pass_a_budget(route_func):
    """Both routes that scan SAM.gov inside a request must bound it.

    /api/govcon/sam/scan is the one the E2E test hit; /api/govcon/pipeline/run has the
    identical unbounded call, and a fix that only covered the first would leave the same
    defect live at the second site.
    """
    import pathlib

    for rel in ("tools/dashboard/api/govcon.py", "icdev/tools/dashboard/api/govcon.py"):
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert _route_calls_scanner_with_a_budget(source, route_func), (
            f"{rel}::{route_func} calls the SAM.gov scanner without budget_seconds -- "
            "it will hold a request thread for as long as SAM.gov takes"
        )


def test_a_pair_is_not_started_without_room_to_attempt_it(scanner, monkeypatch):
    """Below the minimum attempt window the scan stops rather than issuing a doomed request."""
    calls, _ = _record_calls(monkeypatch, per_call_seconds=0.05)

    result = scanner.scan_sam_gov(config=_CONFIG, budget_seconds=0.001)

    assert calls == [], "no request may be started with no budget left to make it in"
    assert result["complete"] is False
    assert result["budget_exhausted"] is True
    assert len(result["skipped_over_budget"]) == 8
