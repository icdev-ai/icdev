# CUI // SP-CTI
"""exa-live-02 — check_capability_liveness: zero consumption fails the gate.

``check_reflex_registry`` and ``tests/test_reflex_dispatch_parity.py`` were
written only because the reflex version of this bug shipped three times. These
tests pin the generalised shape, and in particular the two distinctions that
decide whether a gate like this survives contact with a real repository:

* **never consumed vs. idle this window.** A reflex on a quarterly schedule has
  not failed. Counting it as a defect is how the gate earns its way into being
  ignored, so the pass/fail decision reads the LIFETIME pass only.
* **inert vs. unmeasurable.** On a fresh worktree or an ephemeral CI database
  every declared capability looks inert because nothing has been recorded. That
  is a fact about the database, not the tree, and it must never fabricate a
  failure.

The decision logic is exercised through ``_evaluate_capability_liveness``, which
takes both consumption reports as data — the point being to test the verdict
without needing the populated database the verdict is conditioned on.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.workflow.coherence_checker import (  # noqa: E402
    CHECK_REGISTRY,
    _evaluate_capability_liveness,
    _liveness_check_result,
    _load_liveness_gate,
    check_capability_liveness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE_PATH = _REPO_ROOT / "args" / "liveness_gate.yaml"

_GATE = {
    "window_days": 30,
    "lifetime_days": 36500,
    "evidence_anchor": {"table": "audit_trail", "min_rows": 1000},
    "grandfathered": {"reflex": 0, "mcp_dispatch_tool": 466},
}

# Comfortably above the anchor floor: the database has an operating history, so
# a zero is a real zero.
_POPULATED = 80_000


def _cls(name: str, declared: int, inert: int, **extra):
    """One entry of a capability_consumption report's ``classes`` list."""
    payload = {
        "capability_class": name,
        "declared": declared,
        "inert": inert,
        "consumed": declared - inert,
        "telemetry_available": True,
        "unmeasured_reason": None,
        "telemetry_table": f"{name}_audit",
        # Deliberately truncated the way capability_consumption truncates it, so
        # any logic that reached for the names instead of the count would break.
        "inert_units": [f"{name}-{i}" for i in range(min(inert, 40))],
    }
    payload.update(extra)
    return payload


def _report(*classes):
    return {"classes": list(classes)}


# ---------------------------------------------------------------------------
# The gate decision
# ---------------------------------------------------------------------------


def test_within_budget_passes():
    """Every class at or under its grandfathered count is a pass."""
    window = _report(_cls("reflex", 74, 0), _cls("mcp_dispatch_tool", 470, 466))
    lifetime = _report(_cls("reflex", 74, 0), _cls("mcp_dispatch_tool", 470, 466))

    result = _evaluate_capability_liveness(window, lifetime, _POPULATED, _GATE)

    assert result["verdict"] == "pass"
    assert result["over_budget"] == []
    assert _liveness_check_result(result, 30).status == "pass"


def test_new_never_consumed_unit_fails_the_gate():
    """A newly declared capability nothing consumes pushes its class over budget.

    This is the whole point: registering a 471st MCP tool that no surface can
    dispatch is not free.
    """
    window = _report(_cls("reflex", 74, 0), _cls("mcp_dispatch_tool", 471, 467))
    lifetime = _report(_cls("reflex", 74, 0), _cls("mcp_dispatch_tool", 471, 467))

    result = _evaluate_capability_liveness(window, lifetime, _POPULATED, _GATE)

    assert result["verdict"] == "fail"
    assert [c["capability_class"] for c in result["over_budget"]] == ["mcp_dispatch_tool"]

    check = _liveness_check_result(result, 30)
    assert check.status == "fail"
    assert "467" in check.missing[0] and "466" in check.missing[0]
    # The remedy must not read as "raise the number".
    assert "Do not raise the budget" in check.message


def test_reflex_with_zero_runs_fails_even_though_its_class_is_busy():
    """73 of 74 reflexes running does not excuse the one that never has.

    The class-level event total stays healthy while a single declared unit sits
    at zero — exactly the shape (xbm-wake-01/02, hgx-obs-02) that shipped three
    times.
    """
    lifetime = _report(_cls("reflex", 74, 1))

    result = _evaluate_capability_liveness(lifetime, lifetime, _POPULATED, _GATE)

    assert result["verdict"] == "fail"
    assert result["over_budget"][0]["never_consumed"] == 1


# ---------------------------------------------------------------------------
# Low cadence is not the same defect
# ---------------------------------------------------------------------------


def test_low_cadence_unit_is_not_a_finding():
    """Consumed at some point, quiet this window — reported, never counted.

    Twelve reflexes on a schedule longer than the 30d window are inert in the
    window pass and consumed in the lifetime pass. Budget is 0, so if the gate
    read the window pass this would fail.
    """
    window = _report(_cls("reflex", 74, 12))
    lifetime = _report(_cls("reflex", 74, 0))

    result = _evaluate_capability_liveness(window, lifetime, _POPULATED, _GATE)

    assert result["verdict"] == "pass"
    reflex = result["classes"][0]
    assert reflex["never_consumed"] == 0
    assert reflex["idle_this_window"] == 12
    assert "12 unit(s) consumed previously but idle" in _liveness_check_result(result, 30).message


def test_idle_and_never_consumed_are_reported_separately():
    """A class can carry both, and only the never-consumed half is gated."""
    window = _report(_cls("mcp_dispatch_tool", 470, 470))
    lifetime = _report(_cls("mcp_dispatch_tool", 470, 466))

    result = _evaluate_capability_liveness(window, lifetime, _POPULATED, _GATE)

    entry = result["classes"][0]
    assert entry["never_consumed"] == 466  # at budget → no failure
    assert entry["idle_this_window"] == 4
    assert result["verdict"] == "pass"


def test_declared_count_race_cannot_produce_a_negative_idle_count():
    """The two passes run moments apart; a class declared FROM a table can grow."""
    window = _report(_cls("skill_optimizer", 122, 100))
    lifetime = _report(_cls("skill_optimizer", 130, 108))
    gate = dict(_GATE, grandfathered={"skill_optimizer": 200})

    result = _evaluate_capability_liveness(window, lifetime, _POPULATED, gate)

    assert result["classes"][0]["idle_this_window"] == 0


# ---------------------------------------------------------------------------
# Unmeasurable is never reported as zero
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("corpus_rows", [None, 0, 999])
def test_database_without_history_warns_instead_of_failing(corpus_rows):
    """A fresh worktree / ephemeral CI database must not fabricate findings."""
    lifetime = _report(_cls("mcp_dispatch_tool", 470, 470), _cls("reflex", 74, 74))

    result = _evaluate_capability_liveness(lifetime, lifetime, corpus_rows, _GATE)

    assert result["verdict"] == "no_history"
    check = _liveness_check_result(result, 30)
    assert check.status == "warn"
    assert check.missing == []


def test_unmeasurable_class_warns_and_does_not_count_as_inert():
    """A class with no telemetry table is a measurement gap, not 470 findings."""
    unmeasurable = {
        "capability_class": "mcp_dispatch_tool",
        "declared": 0,
        "inert": 0,
        "consumed": 0,
        "telemetry_available": False,
        "unmeasured_reason": "studio_mcp_dispatch_audit does not exist",
        "telemetry_table": "studio_mcp_dispatch_audit",
        "inert_units": [],
    }
    report = _report(_cls("reflex", 74, 0), unmeasurable)

    result = _evaluate_capability_liveness(report, report, _POPULATED, _GATE)

    assert result["verdict"] == "warn"
    assert result["over_budget"] == []
    assert result["unmeasurable"] == ["mcp_dispatch_tool: studio_mcp_dispatch_audit does not exist"]

    check = _liveness_check_result(result, 30)
    assert check.status == "warn"
    assert "studio_mcp_dispatch_audit does not exist" in check.extra[0]


def test_shrinking_backlog_is_surfaced_as_a_ratchet():
    """Wiring a capability up should tell you the budget can come down."""
    report = _report(_cls("mcp_dispatch_tool", 470, 460))

    result = _evaluate_capability_liveness(report, report, _POPULATED, _GATE)

    assert result["verdict"] == "pass"
    check = _liveness_check_result(result, 30)
    assert "can be lowered to 460" in check.extra[0]


# ---------------------------------------------------------------------------
# The gate file
# ---------------------------------------------------------------------------


def test_gate_file_forbids_raising_a_count():
    """The never-raise rule must be stated in the file, not only in review."""
    header = _GATE_PATH.read_text(encoding="utf-8")
    assert "NEVER raise one" in header
    assert "Lower a count" in header


def test_gate_file_budgets_are_non_negative_integers():
    raw = yaml.safe_load(_GATE_PATH.read_text(encoding="utf-8"))
    budgets = raw["grandfathered"]
    assert budgets, "the gate file must carry the measured backlog, not an empty map"
    for name, allowed in budgets.items():
        assert isinstance(allowed, int) and allowed >= 0, f"{name}: {allowed!r}"


def test_gate_file_covers_every_measurable_capability_class():
    """A class with no entry gets a budget of 0 — fine for a new class, wrong for
    one whose backlog was measured. Keep the two in sync deliberately."""
    from tools.awareness.capability_consumption import PROBES

    budgets = yaml.safe_load(_GATE_PATH.read_text(encoding="utf-8"))["grandfathered"]
    assert set(PROBES) == set(budgets)


def test_missing_gate_file_fails_closed(monkeypatch, tmp_path):
    """An unreadable allowlist must make the gate stricter, never looser."""
    import tools.workflow.coherence_checker as cc

    monkeypatch.setattr(cc, "_LIVENESS_GATE_PATH", tmp_path / "nope.yaml")
    gate = _load_liveness_gate()

    assert gate["grandfathered"] == {}
    report = _report(_cls("reflex", 74, 1))
    assert _evaluate_capability_liveness(report, report, _POPULATED, gate)["verdict"] == "fail"


def test_real_gate_file_loads():
    gate = _load_liveness_gate()
    assert gate["window_days"] > 0
    assert gate["lifetime_days"] >= 3650
    assert gate["evidence_anchor"]["table"] == "audit_trail"
    assert gate["grandfathered"]["reflex"] == 0


# ---------------------------------------------------------------------------
# Registration and end-to-end behaviour
# ---------------------------------------------------------------------------


def test_check_is_registered():
    assert CHECK_REGISTRY["capability_liveness"] is check_capability_liveness


def test_check_never_fails_on_this_tree():
    """The check must land GREEN, and must not go red just because the test
    database is empty — pass on the populated platform database, warn on the
    SQLite database conftest pins tests to."""
    result = check_capability_liveness()
    assert result.status in ("pass", "warn"), result.message
