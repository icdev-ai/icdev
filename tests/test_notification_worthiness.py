# CUI // SP-CTI
"""oss2-triage-01 — notification worthiness gate.

A scored interrupt/dispatch/file stage in front of routing (agent-chief pattern).
Pins the decision boundaries, the off-by-default inertness, and — in agent-chief's
'evaluated, not asserted' spirit — the filter distribution over a representative
event stream.
"""
from __future__ import annotations

from tools.notifications.worthiness import (
    DISPATCH,
    FILE,
    INTERRUPT,
    evaluate_stream,
    is_enabled,
    score_worthiness,
)

# A config with the gate ON, for exercising the scoring (the shipped default is OFF).
_ON = {
    "enabled": True,
    "severity_weights": {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.25, "info": 0.1},
    "event_type_modifiers": {"security_alert": 0.2, "digest": -0.35, "heartbeat": -0.45},
    "thresholds": {"interrupt": 0.75, "dispatch": 0.4},
}


def test_critical_interrupts():
    d = score_worthiness("security_alert", "critical", config=_ON)
    assert d.action == INTERRUPT
    assert d.score >= 0.75


def test_medium_dispatches():
    d = score_worthiness("kanban_blocked", "medium", config=_ON)
    assert d.action == DISPATCH


def test_routine_info_is_filed():
    d = score_worthiness("heartbeat", "info", config=_ON)
    assert d.action == FILE
    assert d.score < 0.4


def test_metadata_routine_signal_lowers_score():
    hi = score_worthiness("compliance_alert", "high", config=_ON)
    lo = score_worthiness("compliance_alert", "high", metadata={"routine": True}, config=_ON)
    assert lo.score < hi.score


def test_score_is_clamped_to_unit_interval():
    d = score_worthiness("security_alert", "critical", metadata={"actionable": True}, config=_ON)
    assert 0.0 <= d.score <= 1.0


def test_off_by_default_is_inert():
    # The shipped config has enabled:false; a decision must carry enabled=False so a
    # caller preserves today's route-everything behavior.
    d = score_worthiness("heartbeat", "info")  # loads the real shipped config
    assert d.enabled is False
    assert is_enabled() is False


def test_evaluate_stream_reports_the_filter_distribution():
    # A representative mixed stream: a flood of routine/scheduled events plus a few
    # real alerts — agent-chief's '24 in -> 1 interrupt' shape.
    stream = (
        [{"event_type": "heartbeat", "severity": "info"}] * 12
        + [{"event_type": "awareness_scan", "severity": "info", "metadata": {"routine": True}}] * 6
        + [{"event_type": "digest", "severity": "info"}] * 4
        + [{"event_type": "kanban_blocked", "severity": "medium"}] * 3
        + [{"event_type": "security_alert", "severity": "critical"}] * 2
    )
    report = evaluate_stream(stream, config={
        **_ON,
        "event_type_modifiers": {"security_alert": 0.2, "digest": -0.35, "heartbeat": -0.45, "awareness_scan": -0.25},
    })
    assert report["total"] == 27
    # the flood is filed/dispatched, not interrupting; only the real alerts interrupt
    assert report["counts"][INTERRUPT] == 2
    assert report["counts"][FILE] >= 20
    assert report["events_per_interrupt"] > 5  # heavy filtering, the whole point
