# CUI // SP-CTI
"""A CDRL that will never be delivered needs a terminal, documented disposition.

Every path through DELIVERABLE_TRANSITIONS used to end at 'accepted' (the
government took delivery) or loop forever ('rejected' -> 'resubmitted' ->
'government_review'). Nothing expressed "this CDRL is not going to be
delivered" — descoped by contract mod, superseded, or created in error. So a
dead obligation had three bad exits: leave it (counts as overdue forever, and
the cpmp_monitor reflex files a fresh alarm card against it every cycle),
DELETE it (destroys the cpmp_status_history trail, and no delete API exists),
or flip it to 'accepted' (a false assertion of government acceptance that also
feeds CPARS quality scoring).

'cancelled' is the honest fourth exit. It must be a DISPOSITION, not a mute
button, which is what most of these tests are actually pinning:

1.  It is terminal and reachable from the states a live obligation sits in.
2.  It is NOT reachable from 'accepted' — reversing delivery is a mod concern.
3.  Cancelling with no reason is REFUSED.
4.  The reason lands in the append-only cpmp_status_history.
5.  A cancelled CDRL stops counting as overdue for the advisor...
6.  ...and compute_overdue_deliverables does not sweep it back to 'overdue',
    which is the failure that would have made the whole disposition inert.
7.  The YAML config and the Python default agree — the YAML OVERRIDES the
    Python default at import, so a fix applied to only one of them is dead.
"""
import uuid
from datetime import date, timedelta

import pytest

import tools.govcon.contract_manager as cm


# ---------------------------------------------------------------------------
# Fixtures — a contract with one overdue deliverable
# ---------------------------------------------------------------------------


@pytest.fixture
def cpmp_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the real CPMP tables.

    Built by running tools.govcon.init_db.init_cpmp_tables rather than by
    pasting DDL in here, so the test cannot pass against a table shape that no
    longer exists. Points ICDEV_DB_PATH at tmp_path — these tests WRITE, and the
    live board's cpmp_contracts is what this whole card is about.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "cpmp_test.db"))
    from tools.govcon.init_db import init_cpmp_tables

    init_cpmp_tables()
    yield


@pytest.fixture
def contract_id(cpmp_db):
    result = cm.create_contract({"contract_number": "TEST-CANCEL-001", "title": "Cancellation Test"})
    assert result["status"] == "ok"
    return result["contract_id"]


@pytest.fixture
def overdue_deliverable(contract_id):
    """A CDRL whose due date is in the past — computed, never a literal.

    A hardcoded date in a fixture is what put the permanently-overdue rows on
    the live board in the first place (they were future-dated when written).
    """
    past = (date.today() - timedelta(days=30)).isoformat()
    result = cm.create_deliverable(
        contract_id, {"cdrl_number": "A001", "title": "Descoped CDRL", "due_date": past}
    )
    assert result["status"] == "ok"
    return result["deliverable_id"]


def _status(deliverable_id):
    return cm.get_deliverable(deliverable_id)["deliverable"]["status"]


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


class TestCancelledIsTerminalAndReachable:
    def test_cancelled_is_terminal(self):
        assert cm.DELIVERABLE_TRANSITIONS["cancelled"] == []

    @pytest.mark.parametrize(
        "state", ["not_started", "in_progress", "draft_complete", "internal_review", "submitted", "overdue", "rejected"]
    )
    def test_reachable_from_live_obligation_states(self, state):
        assert "cancelled" in cm.DELIVERABLE_TRANSITIONS[state], (
            f"a CDRL sitting in '{state}' is a live obligation with no way to be descoped"
        )

    def test_not_reachable_from_accepted(self):
        """Un-accepting a delivered CDRL is a contract mod, not a status flip."""
        assert cm.DELIVERABLE_TRANSITIONS["accepted"] == []

    def test_cancelled_is_a_closed_status(self):
        assert "cancelled" in cm.CLOSED_DELIVERABLE_STATUSES


class TestConfigAndDefaultAgree:
    """args/govcon_config.yaml OVERRIDES the Python default via _CFG.get()."""

    def test_yaml_defines_cancelled_transitions(self):
        import yaml

        with open(cm._CONFIG_PATH) as f:
            configured = yaml.safe_load(f)["cpmp"]["deliverable_transitions"]
        assert "cancelled" in configured, "the YAML wins at import; a Python-only fix is inert"
        assert configured["cancelled"] == []
        assert configured["accepted"] == []
        for state in ("not_started", "in_progress", "overdue", "rejected"):
            assert "cancelled" in configured[state]

    def test_loaded_map_is_the_yaml_map(self):
        """Pins that the module actually loaded config, not the fallback dict."""
        import yaml

        with open(cm._CONFIG_PATH) as f:
            configured = yaml.safe_load(f)["cpmp"]["deliverable_transitions"]
        assert cm.DELIVERABLE_TRANSITIONS == configured


# ---------------------------------------------------------------------------
# Cancelling is a documented act
# ---------------------------------------------------------------------------


class TestCancellationRequiresAReason:
    def test_cancel_without_reason_is_refused(self, overdue_deliverable):
        result = cm.transition_deliverable(overdue_deliverable, "cancelled", changed_by="pm")
        assert result["status"] == "error"
        assert "reason" in result["message"].lower()

    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_blank_reason_is_refused(self, overdue_deliverable, blank):
        result = cm.transition_deliverable(overdue_deliverable, "cancelled", changed_by="pm", reason=blank)
        assert result["status"] == "error"

    def test_refusal_does_not_change_status(self, overdue_deliverable):
        cm.transition_deliverable(overdue_deliverable, "cancelled", changed_by="pm")
        assert _status(overdue_deliverable) == "not_started"

    def test_cancel_with_reason_succeeds(self, overdue_deliverable):
        result = cm.transition_deliverable(
            overdue_deliverable, "cancelled", changed_by="pm", reason="Descoped by mod P00003"
        )
        assert result["status"] == "ok"
        assert result["new_status"] == "cancelled"
        assert _status(overdue_deliverable) == "cancelled"

    def test_reason_is_recorded_in_status_history(self, overdue_deliverable):
        cm.transition_deliverable(
            overdue_deliverable, "cancelled", changed_by="pm", reason="Descoped by mod P00003"
        )
        history = cm.get_deliverable(overdue_deliverable)["deliverable"]["status_history"]
        entry = next(h for h in history if h["new_status"] == "cancelled")
        assert entry["reason"] == "Descoped by mod P00003"
        assert entry["changed_by"] == "pm"

    def test_cancel_clears_days_overdue(self, contract_id, overdue_deliverable):
        cm.compute_overdue_deliverables(contract_id)
        cm.transition_deliverable(
            overdue_deliverable, "cancelled", changed_by="pm", reason="Superseded by A002"
        )
        assert cm.get_deliverable(overdue_deliverable)["deliverable"]["days_overdue"] == 0

    def test_cancelled_is_terminal_in_practice(self, overdue_deliverable):
        cm.transition_deliverable(overdue_deliverable, "cancelled", changed_by="pm", reason="Created in error")
        result = cm.transition_deliverable(overdue_deliverable, "in_progress", changed_by="pm")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# The disposition actually sticks
# ---------------------------------------------------------------------------


class TestCancelledStopsCountingAsOverdue:
    def test_advisor_counts_it_before_cancellation(self, contract_id, overdue_deliverable):
        from tools.govcon.pmo_ai_advisor import _gather_contract_context

        assert _gather_contract_context(contract_id)["overdue_deliverables"] == 1

    def test_advisor_stops_counting_it_after_cancellation(self, contract_id, overdue_deliverable):
        from tools.govcon.pmo_ai_advisor import _gather_contract_context

        cm.transition_deliverable(
            overdue_deliverable, "cancelled", changed_by="pm", reason="Descoped by mod P00003"
        )
        assert _gather_contract_context(contract_id)["overdue_deliverables"] == 0

    def test_advisor_raises_no_overdue_issue_after_cancellation(self, contract_id, overdue_deliverable):
        from tools.govcon.pmo_ai_advisor import auto_detect_issues

        cm.transition_deliverable(
            overdue_deliverable, "cancelled", changed_by="pm", reason="Descoped by mod P00003"
        )
        issues = auto_detect_issues(contract_id).get("issues", [])
        assert not [i for i in issues if i.get("type") == "overdue_deliverables"]

    def test_sweep_does_not_resurrect_a_cancelled_deliverable(self, contract_id, overdue_deliverable):
        """The failure that would make the whole disposition inert.

        compute_overdue_deliverables runs on a schedule. If it does not exclude
        'cancelled', it flips the row straight back to 'overdue' and the alarm
        card returns on the next cpmp_monitor cycle.
        """
        cm.transition_deliverable(
            overdue_deliverable, "cancelled", changed_by="pm", reason="Descoped by mod P00003"
        )
        cm.compute_overdue_deliverables(contract_id)
        assert _status(overdue_deliverable) == "cancelled"

    def test_sweep_still_marks_a_genuinely_overdue_deliverable(self, contract_id, overdue_deliverable):
        """The other direction: cancellation must not blind the sweep."""
        result = cm.compute_overdue_deliverables(contract_id)
        assert result["overdue_count"] == 1
        assert _status(overdue_deliverable) == "overdue"

    def test_due_soon_count_also_excludes_cancelled(self, contract_id):
        from tools.govcon.pmo_ai_advisor import _gather_contract_context

        soon = (date.today() + timedelta(days=10)).isoformat()
        deliv = cm.create_deliverable(
            contract_id, {"cdrl_number": "A002", "title": "Upcoming", "due_date": soon}
        )["deliverable_id"]
        assert _gather_contract_context(contract_id)["due_in_30_days"] == 1
        cm.transition_deliverable(deliv, "cancelled", changed_by="pm", reason="Descoped by mod P00003")
        assert _gather_contract_context(contract_id)["due_in_30_days"] == 0


class TestApiSurface:
    """The existing PUT /deliverables/<id>/status route already forwards a reason."""

    def test_route_passes_reason_through(self):
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module("tools.dashboard.api.cpmp"))
        body = src[src.index('@cpmp_api.route("/deliverables/<deliverable_id>/status"') :]
        body = body[: body.index("\n@cpmp_api.route", 1)]
        assert 'data.get("reason")' in body
        assert "_transition(deliverable_id, new_status, changed_by, reason)" in body

    def test_route_is_role_gated(self):
        import importlib
        import inspect

        src = inspect.getsource(importlib.import_module("tools.dashboard.api.cpmp"))
        idx = src.index('@cpmp_api.route("/deliverables/<deliverable_id>/status"')
        assert "@require_role(" in src[idx : idx + 400]


def test_contract_round_trips(cpmp_db):
    """Sanity: the fixture DB really persists rows we can re-read."""
    result = cm.create_contract({"contract_number": f"T-{uuid.uuid4().hex[:6]}", "title": "Sanity"})
    assert cm.get_contract(result["contract_id"])["status"] == "ok"
