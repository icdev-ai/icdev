# CUI // SP-CTI
"""Reproduce-or-drop for dynamic findings (oss-poc-01).

The card's success criterion, verbatim: *a seeded, deliberately-fixed
vulnerability is confirmed by a replay that then FAILS once the fix is applied —
the reproduction must be proven to discriminate, not merely to run.*

That is the whole design. A replay that runs is not evidence; a replay that
reports the finding present both before and after the fix is insensitive to the
thing it claims to detect. Confirming on one would repeat `oss-meas-01`'s
mistake — a real number from an instrument that could not have produced a
different one.
"""
from __future__ import annotations

import pytest

from tools.security.reproduction import (
    CONFIRMED,
    UNCONFIRMED,
    Finding,
    Reproduction,
    gate_blocking_findings,
    triage,
    verify_discriminates,
)


def _repro(**kw) -> Reproduction:
    base = dict(
        kind="http",
        steps=[{"method": "GET", "path": "/api/usage", "headers": {}}],
        expectation="200 with another tenant's rows",
    )
    base.update(kw)
    return Reproduction(**base)


# ── The success criterion ────────────────────────────────────────────────────


class _SeededApp:
    """A deliberately vulnerable surface with a switchable fix.

    Stands in for the real thing: the replay is a real callable whose answer
    genuinely depends on whether the vulnerability is present.
    """

    def __init__(self) -> None:
        self.authz_enforced = False

    def fetch_usage(self, tenant: str) -> list:
        rows = [{"tenant": "acme"}, {"tenant": "globex"}]
        if self.authz_enforced:
            return [r for r in rows if r["tenant"] == tenant]
        return rows                                   # the vulnerability


def test_seeded_vulnerability_is_confirmed_and_the_replay_stops_firing_when_fixed():
    """The card's stated acceptance, end to end."""
    app = _SeededApp()

    def replay() -> bool:
        # "finding present" == we can see another tenant's data
        return any(r["tenant"] != "acme" for r in app.fetch_usage("acme"))

    result = verify_discriminates(
        replay=replay,
        apply_fix=lambda: setattr(app, "authz_enforced", True),
        revert_fix=lambda: setattr(app, "authz_enforced", False),
    )

    assert result["before"] is True, "replay must reproduce against the vulnerable state"
    assert result["after"] is False, "replay must STOP firing once the fix is applied"
    assert result["discriminates"] is True

    finding = Finding(title="Cross-tenant usage rows", reproduction=_repro())
    assert finding.confirm(result["discriminates"]) == CONFIRMED
    assert finding.blocks_gate is True


def test_a_replay_that_cannot_fail_is_refused():
    """The failure mode this module exists to prevent.

    A check that reports the finding present regardless of the fix is not
    evidence of a vulnerability — it is evidence the check is insensitive.
    """
    app = _SeededApp()
    result = verify_discriminates(
        replay=lambda: True,                       # always fires
        apply_fix=lambda: setattr(app, "authz_enforced", True),
    )
    assert result["discriminates"] is False
    assert "insensitive" in result["reason"]

    finding = Finding(title="Always-true check", reproduction=_repro())
    assert finding.confirm(result["discriminates"]) == UNCONFIRMED
    assert finding.blocks_gate is False


def test_a_replay_that_never_reproduces_is_not_a_finding():
    result = verify_discriminates(replay=lambda: False, apply_fix=lambda: None)
    assert result["discriminates"] is False
    assert "did not reproduce" in result["reason"]


def test_replay_erroring_after_the_fix_still_counts_as_discrimination():
    """An exception once the fix lands means it stopped reporting the finding."""
    state = {"fixed": False}

    def replay():
        if state["fixed"]:
            raise ConnectionError("endpoint now requires auth")
        return True

    result = verify_discriminates(
        replay=replay, apply_fix=lambda: state.update(fixed=True)
    )
    assert result["discriminates"] is True


# ── Gate weight ──────────────────────────────────────────────────────────────


def test_unconfirmed_findings_never_block_a_gate():
    """The core rule: an unreproducible claim must not carry a demonstrated
    one's weight."""
    f = Finding(title="Suspected IDOR")
    assert f.status == UNCONFIRMED
    assert f.blocks_gate is False
    assert gate_blocking_findings([f]) == []


def test_a_finding_starts_unconfirmed_by_construction():
    assert Finding(title="x").status == UNCONFIRMED


def test_status_cannot_be_promoted_without_going_through_confirm():
    """Only confirm() can produce a gate-blocking finding."""
    f = Finding(title="x", reproduction=_repro())
    assert f.confirm(discriminated=False) == UNCONFIRMED
    assert f.confirm(discriminated=True) == CONFIRMED


# ── Reproduction quality ─────────────────────────────────────────────────────


def test_a_finding_with_no_reproduction_cannot_be_confirmed():
    f = Finding(title="Prose claim only")
    assert f.confirm(discriminated=True) == UNCONFIRMED


@pytest.mark.parametrize(
    "kw,why",
    [
        ({"steps": []}, "no steps to replay"),
        ({"expectation": ""}, "nothing to check against"),
        ({"expectation": "   "}, "whitespace expectation"),
    ],
)
def test_an_empty_reproduction_is_not_replayable(kw, why):
    f = Finding(title="x", reproduction=_repro(**kw))
    assert f.reproduction.is_replayable is False, why
    assert f.confirm(discriminated=True) == UNCONFIRMED, why


def test_agent_traces_are_a_valid_reproduction_kind():
    """oss-browse-01's action trace is replayable evidence, same as HTTP."""
    r = Reproduction(
        kind="agent_trace",
        steps=[{"action": "navigate", "url": "http://localhost:5050/usage"},
               {"action": "read_state"}],
        expectation="another tenant's rows visible in the indexed elements",
    )
    assert r.is_replayable


def test_fingerprint_is_stable_and_content_derived():
    a, b = _repro(), _repro()
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != _repro(expectation="different").fingerprint()


# ── Batch triage ─────────────────────────────────────────────────────────────


def test_without_a_replay_harness_nothing_is_confirmed():
    """Correct default: a run that cannot replay produces LEADS, not findings."""
    out = triage([Finding(title="a"), Finding(title="b", reproduction=_repro())])
    assert out["confirmed"] == 0
    assert out["unconfirmed"] == 2
    assert out["gate_blocking"] == []


def test_triage_separates_confirmed_from_unreproducible():
    findings = [
        Finding(title="real", reproduction=_repro()),
        Finding(title="ghost", reproduction=_repro()),
    ]

    def replay_for(f):
        if f.title == "real":
            return {"discriminates": True, "before": True, "after": False, "reason": ""}
        return {"discriminates": False, "before": False, "after": None,
                "reason": "did not reproduce"}

    out = triage(findings, replay_for=replay_for)
    assert out["confirmed"] == 1
    assert out["not_reproducible"] == 1
    assert len(out["gate_blocking"]) == 1


def test_a_crashing_replay_harness_downgrades_rather_than_promotes():
    """Fail toward 'unconfirmed', never toward a gate block."""
    def boom(f):
        raise RuntimeError("harness died")

    out = triage([Finding(title="x", reproduction=_repro())], replay_for=boom)
    assert out["confirmed"] == 0
    assert out["unconfirmed"] == 1


def test_discrimination_evidence_is_retained_on_the_finding():
    """A reviewer must be able to see WHY it was confirmed."""
    f = Finding(title="real", reproduction=_repro())
    triage([f], replay_for=lambda _f: {
        "discriminates": True, "before": True, "after": False, "reason": ""
    })
    assert f.evidence["discrimination"]["before"] is True
    assert f.evidence["discrimination"]["after"] is False
