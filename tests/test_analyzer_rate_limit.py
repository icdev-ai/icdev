#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for per-analyzer rate limits and sandbox enforcement (anz-rate-01).

The acceptance criteria this file exists to hold:
  1. exceeding an analyzer's rate limit QUEUES or REPORTS — it never drops the
     analyzer from the fan-out
  2. every ported analyzer has a sandbox-coverage decision recorded

(1) is the one worth being pedantic about. A limiter that returns "denied" and
a dispatcher that omits the analyzer would produce a result indistinguishable
from "the analyzer ran and found nothing" — the same failure shape as the
`citation_type` bug the contract file was written to avoid. So the tests below
assert on the *presence* of a report, not merely on the refusal.

The clock is injected rather than slept through: proving that an hour-wide
window rolls must not take an hour, and a test that sleeps is a test that goes
flaky on a loaded CI runner.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tools.analyzers.contract import load_contract, parse_contract
from tools.analyzers.dispatch import REPORT_STATUSES, capabilities, dispatch
from tools.analyzers.rate_limit import AnalyzerRateLimiter, get_limiter, reset_limiter
from tools.analyzers.sandbox import (
    EXECUTION_MODES,
    IN_PROCESS,
    SANDBOXED,
    SandboxUnavailable,
    SandboxUnsupported,
    resolve_execution_mode,
    run_sandboxed,
    strict_sandbox_enabled,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# A self-contained contract, same shape as tests/test_analyzer_dispatch.py.
# Its analyzers live in this module so no real DB, feed or network is touched.
# ---------------------------------------------------------------------------

_BASE_CONTRACT: Dict[str, Any] = {
    "version": 1,
    "observable_types": {
        "ip": {"description": "IPv4 or IPv6 address.", "consumers": ["tests"]},
        "cve": {"description": "CVE identifier.", "consumers": ["tests"]},
    },
    "taxonomy": {
        "levels": {"info": "Neutral.", "malicious": "Hostile."},
        "namespaces": {"SECURITY": "tools/security/."},
    },
    "sandbox_postures": {
        "sandboxed": "Runs in sandbox_execute.",
        "sandboxed_on_demand": "In-process by default; sandboxed when strict.",
        "trusted_first_party": "First-party data only.",
        "bypass_documented": "Deliberately unsandboxed.",
    },
    "defaults": {
        "rate_limit": {"max_calls": 60, "per_seconds": 3600},
        "sandbox": "trusted_first_party",
        "timeout_seconds": 5,
        "enabled": True,
    },
    "analyzers": [],
}

_MODULE = __name__

#: Every observable value the counting analyzer was called with. Proves whether
#: a rate-limited call actually reached the analyzer or not.
CALLS: List[Any] = []


def counting_analyzer(observable):
    CALLS.append(observable)
    return {
        "observable": observable,
        "taxonomy": [{"predicate": "ioc-match", "level": "malicious", "value": observable}],
    }


def _decl(key: str, accepts: list, **overrides: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "key": key,
        "kind": "analyzer",
        "display_name": key,
        "description": f"test analyzer {key}",
        "module": _MODULE,
        "entrypoint": "counting_analyzer",
        "accepts": accepts,
        "taxonomy": {
            "namespace": "SECURITY",
            "predicates": ["ioc-match"],
            "levels": ["info", "malicious"],
        },
        "sandbox": "trusted_first_party",
    }
    body.update(overrides)
    return body


def _contract(*declarations: Dict[str, Any]):
    data = dict(_BASE_CONTRACT)
    data["analyzers"] = list(declarations)
    return parse_contract(data, REPO_ROOT / "args" / "analyzer_contract.yaml")


class FakeClock:
    """A monotonic clock the test advances by hand.

    ``sleep`` advances it rather than blocking, so a queueing test exercises
    the real wait loop in :meth:`AnalyzerRateLimiter.acquire` at full speed.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture(autouse=True)
def _reset_state():
    CALLS.clear()
    reset_limiter()
    yield
    CALLS.clear()
    reset_limiter()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock) -> AnalyzerRateLimiter:
    return AnalyzerRateLimiter(time_source=clock.time, sleep=clock.sleep)


# ---------------------------------------------------------------------------
# The limiter itself
# ---------------------------------------------------------------------------


def test_calls_within_the_window_are_allowed(limiter):
    for _ in range(3):
        assert limiter.acquire("alpha", 3, 60).allowed


def test_the_call_after_the_ceiling_is_refused_with_a_retry_after(limiter):
    for _ in range(3):
        limiter.acquire("alpha", 3, 60)
    decision = limiter.acquire("alpha", 3, 60)
    assert decision.allowed is False
    assert decision.remaining == 0
    # The window opened at t=1000 and is 60s wide, so the first slot frees at
    # t=1060 — the refusal must say so rather than just saying "no".
    assert decision.retry_after_seconds == pytest.approx(60.0)


def test_remaining_counts_down_to_zero(limiter):
    assert limiter.acquire("alpha", 3, 60).remaining == 2
    assert limiter.acquire("alpha", 3, 60).remaining == 1
    assert limiter.acquire("alpha", 3, 60).remaining == 0


def test_the_window_slides_rather_than_resetting_on_a_boundary(clock, limiter):
    """A fixed window would let 2x the ceiling through across a boundary."""
    for _ in range(3):
        limiter.acquire("alpha", 3, 60)
    clock.now += 59.0
    assert limiter.acquire("alpha", 3, 60).allowed is False, "window reset early"
    clock.now += 1.5  # now past the first call's 60s lifetime
    assert limiter.acquire("alpha", 3, 60).allowed is True


def test_limits_are_tracked_per_analyzer_not_globally(limiter):
    """One greedy analyzer must not starve the rest — the whole point."""
    for _ in range(3):
        limiter.acquire("greedy", 3, 60)
    assert limiter.acquire("greedy", 3, 60).allowed is False
    assert limiter.acquire("polite", 3, 60).allowed is True


def test_queueing_waits_for_a_slot_and_then_allows(clock, limiter):
    for _ in range(2):
        limiter.acquire("alpha", 2, 60)
    decision = limiter.acquire("alpha", 2, 60, max_wait_seconds=120)
    assert decision.allowed is True
    assert decision.queued is True
    assert decision.waited_seconds == pytest.approx(60.0, abs=0.5)


def test_queueing_gives_up_at_the_deadline_and_reports(clock, limiter):
    for _ in range(2):
        limiter.acquire("alpha", 2, 60)
    decision = limiter.acquire("alpha", 2, 60, max_wait_seconds=5)
    assert decision.allowed is False
    assert decision.waited_seconds == pytest.approx(5.0, abs=0.5)
    assert decision.retry_after_seconds > 0


def test_a_refusal_explains_itself_in_words(limiter):
    limiter.acquire("alpha", 1, 60)
    detail = limiter.acquire("alpha", 1, 60).detail()
    assert "rate limit" in detail and "alpha" in detail and "retry after" in detail


def test_snapshot_reports_state_without_consuming_a_slot(limiter):
    limiter.acquire("alpha", 5, 60)
    before = limiter.snapshot("alpha", 5, 60)
    after = limiter.snapshot("alpha", 5, 60)
    assert before["used"] == after["used"] == 1
    assert after["remaining"] == 4


def test_reset_clears_the_window(limiter):
    limiter.acquire("alpha", 1, 60)
    assert limiter.acquire("alpha", 1, 60).allowed is False
    limiter.reset("alpha")
    assert limiter.acquire("alpha", 1, 60).allowed is True


def test_a_nonpositive_ceiling_is_treated_as_unlimited_not_a_deadlock(limiter):
    """A zero ceiling must not silently block an analyzer forever."""
    assert limiter.acquire("alpha", 0, 60, max_wait_seconds=10).allowed is True


def test_the_process_wide_limiter_is_a_singleton():
    assert get_limiter() is get_limiter()


# ---------------------------------------------------------------------------
# AC1 — exceeding a limit queues or reports, and NEVER drops
# ---------------------------------------------------------------------------


def test_a_rate_limited_analyzer_is_reported_not_omitted(limiter):
    contract = _contract(_decl("alpha", ["ip"], rate_limit={"max_calls": 1, "per_seconds": 60}))

    first = dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    assert [r.status for r in first.reports] == ["ok"]

    second = dispatch("ip", "198.51.100.2", contract=contract, limiter=limiter)
    # The analyzer is still in `reports`. This is the assertion that matters:
    # an omitted report would be indistinguishable from "found nothing".
    assert [r.analyzer for r in second.reports] == ["alpha"]
    assert [r.status for r in second.reports] == ["rate_limited"]


def test_a_rate_limited_analyzer_is_not_moved_to_excluded(limiter):
    """`excluded` means "never dispatched". Rate limiting is not that."""
    contract = _contract(_decl("alpha", ["ip"], rate_limit={"max_calls": 1, "per_seconds": 60}))
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    result = dispatch("ip", "198.51.100.2", contract=contract, limiter=limiter)
    assert result.excluded == ()
    assert result.counts["dispatched"] == 1


def test_rate_limiting_makes_the_result_partial_with_a_named_reason(limiter):
    contract = _contract(_decl("alpha", ["ip"], rate_limit={"max_calls": 1, "per_seconds": 60}))
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    result = dispatch("ip", "198.51.100.2", contract=contract, limiter=limiter)
    assert result.partial is True
    assert result.partial_reasons == {"rate_limited": ["alpha"]}


def test_a_rate_limited_report_carries_retry_after_so_it_can_be_requeued(limiter):
    contract = _contract(_decl("alpha", ["ip"], rate_limit={"max_calls": 1, "per_seconds": 60}))
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    result = dispatch("ip", "198.51.100.2", contract=contract, limiter=limiter)
    report = result.reports[0]
    assert report.retry_after_seconds > 0
    assert report.to_dict()["retry_after_seconds"] > 0


def test_a_rate_limited_analyzer_is_never_actually_called(limiter):
    """Refused means refused — the quota must protect the downstream API."""
    contract = _contract(_decl("alpha", ["ip"], rate_limit={"max_calls": 1, "per_seconds": 60}))
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    dispatch("ip", "198.51.100.2", contract=contract, limiter=limiter)
    assert CALLS == ["198.51.100.1"]


def test_one_analyzers_exhausted_quota_does_not_starve_the_others(limiter):
    contract = _contract(
        _decl("greedy", ["ip"], rate_limit={"max_calls": 1, "per_seconds": 60}),
        _decl("polite", ["ip"], rate_limit={"max_calls": 10, "per_seconds": 60}),
    )
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    result = dispatch("ip", "198.51.100.2", contract=contract, limiter=limiter)
    statuses = {r.analyzer: r.status for r in result.reports}
    assert statuses == {"greedy": "rate_limited", "polite": "ok"}


def test_queueing_lets_a_limited_analyzer_run_instead_of_reporting(clock, limiter):
    contract = _contract(
        _decl(
            "alpha",
            ["ip"],
            rate_limit={"max_calls": 1, "per_seconds": 60},
            timeout_seconds=600,
        )
    )
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    result = dispatch(
        "ip", "198.51.100.2", contract=contract, limiter=limiter, rate_limit_wait_seconds=120
    )
    assert [r.status for r in result.reports] == ["ok"]
    assert result.reports[0].queued_seconds > 0
    assert CALLS == ["198.51.100.1", "198.51.100.2"]


def test_the_queue_is_capped_by_the_analyzers_own_timeout_budget(clock, limiter):
    """Queueing past the budget would burn quota on an abandoned result."""
    contract = _contract(
        _decl(
            "alpha",
            ["ip"],
            rate_limit={"max_calls": 1, "per_seconds": 600},
            timeout_seconds=5,
        )
    )
    dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    result = dispatch(
        "ip",
        "198.51.100.2",
        contract=contract,
        limiter=limiter,
        rate_limit_wait_seconds=10_000,
    )
    assert [r.status for r in result.reports] == ["rate_limited"]
    # It waited, but only up to the budget — not the 10 000s it was offered.
    assert result.reports[0].queued_seconds <= 6.0
    assert CALLS == ["198.51.100.1"]


def test_quota_is_not_spent_by_an_analyzer_that_never_ran(limiter):
    """An unimportable analyzer must not consume the window it never reached."""
    contract = _contract(
        _decl(
            "ghost",
            ["ip"],
            entrypoint="no_such_function",
            rate_limit={"max_calls": 1, "per_seconds": 60},
        )
    )
    result = dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)
    assert [r.status for r in result.reports] == ["unavailable"]
    assert limiter.snapshot("ghost", 1, 60)["used"] == 0


def test_reading_capabilities_consumes_no_quota(limiter):
    payload = capabilities("cve", limiter=limiter)
    entries = payload["observable_types"]["cve"]["analyzers"]
    assert entries, "the shipped contract declares a cve analyzer"
    for entry in entries:
        assert entry["rate_limit"]["max_calls"] > 0
        assert entry["rate_limit_state"]["used"] == 0


def test_rate_limited_is_in_the_closed_status_vocabulary():
    assert "rate_limited" in REPORT_STATUSES
    assert "sandbox_unavailable" in REPORT_STATUSES


# ---------------------------------------------------------------------------
# AC2 — sandbox posture is enforced, not merely declared
# ---------------------------------------------------------------------------


def test_trusted_and_bypass_postures_run_in_process():
    for posture in ("trusted_first_party", "bypass_documented"):
        decl = _contract(_decl("alpha", ["ip"], sandbox=posture)).analyzers[0]
        assert resolve_execution_mode(decl, strict=True) == IN_PROCESS


def test_a_sandboxed_posture_always_resolves_to_the_sandbox():
    decl = _contract(_decl("alpha", ["ip"], sandbox="sandboxed")).analyzers[0]
    assert resolve_execution_mode(decl, strict=False) == SANDBOXED


def test_on_demand_is_in_process_until_strict_mode_is_set():
    decl = _contract(_decl("alpha", ["ip"], sandbox="sandboxed_on_demand")).analyzers[0]
    assert resolve_execution_mode(decl, strict=False) == IN_PROCESS
    assert resolve_execution_mode(decl, strict=True) == SANDBOXED


def test_strict_mode_is_read_from_the_platform_wide_env_flag(monkeypatch):
    monkeypatch.delenv("ICDEV_STRICT_SANDBOX", raising=False)
    assert strict_sandbox_enabled() is False
    monkeypatch.setenv("ICDEV_STRICT_SANDBOX", "1")
    assert strict_sandbox_enabled() is True


def test_an_unknown_posture_fails_closed_to_the_sandbox():
    """A posture nobody wrote a rule for must not get the permissive default."""

    class Unknown:
        key = "alpha"
        sandbox = "invented_posture"

    assert resolve_execution_mode(Unknown(), strict=False) == SANDBOXED


def test_every_execution_mode_is_in_the_closed_set():
    for posture in _BASE_CONTRACT["sandbox_postures"]:
        decl = _contract(_decl("alpha", ["ip"], sandbox=posture)).analyzers[0]
        assert resolve_execution_mode(decl, strict=True) in EXECUTION_MODES


def test_a_sandboxed_analyzer_is_never_run_in_process_when_the_sandbox_is_down(
    monkeypatch, limiter
):
    """Fail closed: no sandbox must mean no run, not a silent downgrade."""
    import tools.analyzers.dispatch as dispatch_module

    def _no_sandbox(*args, **kwargs):
        raise SandboxUnavailable("Docker is not installed")

    monkeypatch.setattr(dispatch_module, "run_sandboxed", _no_sandbox)

    contract = _contract(_decl("alpha", ["ip"], sandbox="sandboxed"))
    result = dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter)

    assert [r.status for r in result.reports] == ["sandbox_unavailable"]
    assert result.partial is True
    assert CALLS == [], "a sandboxed analyzer ran in-process — the gate leaked"


def test_a_sandboxed_analyzer_is_routed_through_the_sandbox(monkeypatch, limiter):
    routed = []

    def _fake_sandbox(decl, kwargs, **rest):
        routed.append((decl.key, dict(kwargs)))
        return {"observable": kwargs.get("observable")}

    import tools.analyzers.dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "run_sandboxed", _fake_sandbox)

    contract = _contract(_decl("alpha", ["ip"], sandbox="sandboxed"))
    result = dispatch("ip", "198.51.100.9", contract=contract, limiter=limiter)

    assert [r.status for r in result.reports] == ["ok"]
    assert routed == [("alpha", {"observable": "198.51.100.9"})]
    assert CALLS == [], "the entrypoint was also called in-process"


def test_the_report_records_the_posture_and_the_mode_it_resolved_to(limiter):
    contract = _contract(_decl("alpha", ["ip"], sandbox="sandboxed_on_demand"))
    result = dispatch("ip", "198.51.100.1", contract=contract, limiter=limiter, strict_sandbox=False)
    report = result.reports[0]
    assert report.sandbox_posture == "sandboxed_on_demand"
    assert report.execution_mode == IN_PROCESS
    assert report.to_dict()["execution_mode"] == IN_PROCESS


def test_unmarshallable_arguments_are_misdeclared_not_a_crash():
    """A live connection cannot cross the boundary; say so by name."""

    class Decl:
        key = "alpha"
        module = _MODULE
        entrypoint = "counting_analyzer"

    with pytest.raises(SandboxUnsupported) as excinfo:
        run_sandboxed(Decl(), {"conn": object()})
    assert "JSON-serializable" in str(excinfo.value)


def test_sandbox_enforcement_reuses_the_platform_executor():
    """Not a second isolation path — the same object sandbox_execute uses."""
    source = (REPO_ROOT / "tools" / "analyzers" / "sandbox.py").read_text(encoding="utf-8")
    assert "from tools.security.sandbox_executor import SandboxExecutor" in source
    for forbidden in ("subprocess", "os.system", "docker.from_env"):
        assert forbidden not in source, f"sandbox.py builds its own isolation via {forbidden}"


# ---------------------------------------------------------------------------
# AC2 — every ported analyzer has a recorded sandbox-coverage decision
# ---------------------------------------------------------------------------


def _sandbox_coverage_text() -> str:
    return (REPO_ROOT / "docs" / "security" / "sandbox-coverage.md").read_text(encoding="utf-8")


def test_every_declared_analyzer_has_a_sandbox_coverage_decision():
    """OPT-58: a ported analyzer with no recorded decision is not ported."""
    text = _sandbox_coverage_text()
    missing = [decl.key for decl in load_contract().analyzers if f"`{decl.key}`" not in text]
    assert not missing, (
        "analyzers declared in args/analyzer_contract.yaml with no decision in "
        f"docs/security/sandbox-coverage.md: {missing}"
    )


def test_each_recorded_decision_uses_the_documented_vocabulary():
    """The doc's four decisions, spelled as the doc spells them."""
    text = _sandbox_coverage_text()
    legal = ("sandboxed-on-demand", "trusted-first-party", "bypass-documented", "sandboxed")
    for decl in load_contract().analyzers:
        # The row for this analyzer, from its key to the end of the line.
        rows = [line for line in text.splitlines() if f"`{decl.key}`" in line]
        assert rows, f"no sandbox-coverage row for {decl.key}"
        assert any(
            any(word in row for word in legal) for row in rows
        ), f"{decl.key} has a row but no decision from the documented vocabulary"


def test_the_recorded_decision_matches_the_declared_posture():
    """The doc and the contract must not drift into disagreeing."""
    text = _sandbox_coverage_text()
    # doc spelling uses hyphens, the contract uses underscores
    for decl in load_contract().analyzers:
        expected = decl.sandbox.replace("_", "-")
        rows = [line for line in text.splitlines() if f"`{decl.key}`" in line]
        assert any(
            re.search(rf"\*\*{re.escape(expected)}\*\*", row) for row in rows
        ), (
            f"{decl.key} declares sandbox={decl.sandbox!r} in "
            f"args/analyzer_contract.yaml but docs/security/sandbox-coverage.md "
            f"does not record **{expected}** for it"
        )


# NOTE: docs/ is mirrored into icdev/data/docs/ by
# tools/installer/sync_package_tree.py at release time, NOT by the parity gate
# in args/mirror_parity.yaml (which lists tools/ roots only). That packaged copy
# is already behind canonical for unrelated reasons, so this file deliberately
# does not assert equality on it — doing so would newly gate a generated tree
# and turn main red for a packaging lag that has nothing to do with anz-rate-01.


# ---------------------------------------------------------------------------
# Mirror parity (tools/analyzers is a mirrored root)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["rate_limit.py", "sandbox.py"])
def test_new_modules_are_mirrored_into_the_icdev_package(name):
    canonical = REPO_ROOT / "tools" / "analyzers" / name
    mirror = REPO_ROOT / "icdev" / "tools" / "analyzers" / name
    assert mirror.is_file(), "tools/analyzers is a mirrored root (args/mirror_parity.yaml)"
    assert mirror.read_text(encoding="utf-8") == canonical.read_text(encoding="utf-8")
