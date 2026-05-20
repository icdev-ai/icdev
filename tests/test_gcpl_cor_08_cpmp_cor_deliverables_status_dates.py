# CUI // SP-CTI
"""Tests for GET /api/cpmp/cor/contracts/<id>/deliverables — returns status and dates.

Verifies:
 1. Successful GET returns 200.
 2. Response is JSON.
 3. Each deliverable item contains a 'status' field.
 4. Each deliverable item contains a 'due_date' field.
 5. Each deliverable item contains a 'submitted_date' field.
 6. Each deliverable item contains an 'accepted_date' field.
 7. Each deliverable item contains a 'rejected_date' field.
 8. Response envelope has a 'status' key.
 9. Response envelope 'status' value is 'ok'.
10. Response envelope has a 'total' key.
11. Response envelope has a 'deliverables' key.
12. 'deliverables' value is a list.
13. 'total' matches length of the 'deliverables' list.
14. A deliverable with status='accepted' surfaces as 'accepted'.
15. A deliverable with status='overdue' surfaces as 'overdue'.
16. A deliverable with status='in_progress' surfaces as 'in_progress'.
17. 'days_overdue' is present for an overdue deliverable.
18. 'internal_cost_details' is stripped from each deliverable item.
19. 'internal_notes' is stripped from each deliverable item.
20. COR not assigned to contract returns 403.
21. Authenticated user with no email returns 400.
22. Empty deliverables list returns 200 with total=0.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-cor-test-0008"
_COR_EMAIL = "cor.officer@agency.mil"

_DELIVERABLE_ACCEPTED = {
    "id": "deliv-cor-08-001",
    "contract_id": _CONTRACT_ID,
    "cdrl_number": "A001",
    "did_number": "DI-MGMT-80330",
    "title": "Monthly Status Report",
    "description": "Monthly performance status report",
    "type": "report",
    "frequency": "monthly",
    "due_date": "2027-01-31",
    "submitted_date": "2027-01-30",
    "accepted_date": "2027-02-01",
    "rejected_date": None,
    "rejection_reason": None,
    "status": "accepted",
    "days_overdue": 0,
    "reviewer": "cor@agency.mil",
    "notes": "Accepted on time",
    "metadata": "{}",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2027-02-01T00:00:00",
    "classification": "CUI",
}

_DELIVERABLE_IN_PROGRESS = {
    "id": "deliv-cor-08-002",
    "contract_id": _CONTRACT_ID,
    "cdrl_number": "A002",
    "did_number": "DI-MISC-80711",
    "title": "Software Design Document",
    "description": "Software architecture and design documentation",
    "type": "software",
    "frequency": "one_time",
    "due_date": "2027-06-30",
    "submitted_date": None,
    "accepted_date": None,
    "rejected_date": None,
    "rejection_reason": None,
    "status": "in_progress",
    "days_overdue": 0,
    "reviewer": None,
    "notes": None,
    "metadata": "{}",
    "created_at": "2026-01-15T00:00:00",
    "updated_at": "2026-05-20T00:00:00",
    "classification": "CUI",
    # sensitive fields that must be stripped by _sanitize_for_cor
    "internal_cost_details": {"labor_cost": 150_000.0},
    "internal_notes": "PM eyes-only: behind schedule",
}

_DELIVERABLE_OVERDUE = {
    "id": "deliv-cor-08-003",
    "contract_id": _CONTRACT_ID,
    "cdrl_number": "A003",
    "did_number": "DI-TEST-81002",
    "title": "Test Results Report",
    "type": "test_result",
    "frequency": "one_time",
    "due_date": "2026-03-31",
    "submitted_date": None,
    "accepted_date": None,
    "rejected_date": None,
    "rejection_reason": None,
    "status": "overdue",
    "days_overdue": 50,
    "reviewer": None,
    "notes": None,
    "metadata": "{}",
    "created_at": "2026-01-01T00:00:00",
    "updated_at": "2026-05-01T00:00:00",
    "classification": "CUI",
}

_LIST_RESULT_TWO = {
    "status": "ok",
    "total": 2,
    "deliverables": [_DELIVERABLE_ACCEPTED, _DELIVERABLE_IN_PROGRESS],
}

_LIST_RESULT_OVERDUE = {
    "status": "ok",
    "total": 1,
    "deliverables": [_DELIVERABLE_OVERDUE],
}

_LIST_RESULT_EMPTY = {
    "status": "ok",
    "total": 0,
    "deliverables": [],
}


# ---------------------------------------------------------------------------
# Flask test app
# ---------------------------------------------------------------------------


def _build_cpmp_api_app(cor_email=_COR_EMAIL, role="cor") -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(cpmp_api)

    @flask_app.before_request
    def _inject_user():
        from flask import g

        g.current_user = {"email": cor_email, "role": role, "id": "user-cor-test-0008"}

    return flask_app


def _mock_db():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(client, contract_id=_CONTRACT_ID):
    return client.get(f"/api/cpmp/cor/contracts/{contract_id}/deliverables")


def _call_success(api_app, deliverables_result=None):
    """Issue GET with mocks patched for a successful COR deliverables access."""
    result = deliverables_result if deliverables_result is not None else _LIST_RESULT_TWO
    with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
        with patch("tools.govcon.contract_manager.list_deliverables", return_value=result):
            with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
                with api_app.test_client() as c:
                    resp = _get(c)
    return resp


# ---------------------------------------------------------------------------
# Tests: status and date fields present on each deliverable
# ---------------------------------------------------------------------------


class TestCORDeliverablesReturnsStatusAndDates:
    """GET /api/cpmp/cor/contracts/<id>/deliverables must return status and date
    fields for each deliverable item."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_200(self, api_app):
        resp = _call_success(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = _call_success(api_app)
        assert resp.content_type.startswith("application/json")

    def test_deliverable_has_status_field(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for deliv in data["deliverables"]:
            assert "status" in deliv, f"Deliverable {deliv.get('id')} is missing 'status'"

    def test_deliverable_has_due_date_field(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for deliv in data["deliverables"]:
            assert "due_date" in deliv, f"Deliverable {deliv.get('id')} is missing 'due_date'"

    def test_deliverable_has_submitted_date_field(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for deliv in data["deliverables"]:
            assert "submitted_date" in deliv, (
                f"Deliverable {deliv.get('id')} is missing 'submitted_date'"
            )

    def test_deliverable_has_accepted_date_field(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for deliv in data["deliverables"]:
            assert "accepted_date" in deliv, (
                f"Deliverable {deliv.get('id')} is missing 'accepted_date'"
            )

    def test_deliverable_has_rejected_date_field(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for deliv in data["deliverables"]:
            assert "rejected_date" in deliv, (
                f"Deliverable {deliv.get('id')} is missing 'rejected_date'"
            )

    def test_accepted_deliverable_status_value(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        accepted = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-001")
        assert accepted["status"] == "accepted"

    def test_accepted_deliverable_due_date_value(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        accepted = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-001")
        assert accepted["due_date"] == "2027-01-31"

    def test_accepted_deliverable_submitted_date_value(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        accepted = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-001")
        assert accepted["submitted_date"] == "2027-01-30"

    def test_accepted_deliverable_accepted_date_value(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        accepted = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-001")
        assert accepted["accepted_date"] == "2027-02-01"

    def test_in_progress_deliverable_status_value(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        wip = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-002")
        assert wip["status"] == "in_progress"

    def test_overdue_deliverable_status_value(self, api_app):
        resp = _call_success(api_app, deliverables_result=_LIST_RESULT_OVERDUE)
        data = resp.get_json()
        overdue = data["deliverables"][0]
        assert overdue["status"] == "overdue"

    def test_overdue_deliverable_days_overdue_present(self, api_app):
        resp = _call_success(api_app, deliverables_result=_LIST_RESULT_OVERDUE)
        data = resp.get_json()
        overdue = data["deliverables"][0]
        assert "days_overdue" in overdue
        assert overdue["days_overdue"] == 50


# ---------------------------------------------------------------------------
# Tests: response envelope shape
# ---------------------------------------------------------------------------


class TestCORDeliverablesResponseEnvelope:
    """Response envelope must include status, total, and deliverables keys."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_envelope_has_status_key(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_envelope_status_is_ok(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_envelope_has_total_key(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_envelope_has_deliverables_key(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "deliverables" in data

    def test_deliverables_value_is_list(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert isinstance(data["deliverables"], list)

    def test_total_matches_deliverables_count(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["deliverables"])

    def test_empty_contract_returns_200_with_zero_total(self, api_app):
        resp = _call_success(api_app, deliverables_result=_LIST_RESULT_EMPTY)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["deliverables"] == []


# ---------------------------------------------------------------------------
# Tests: sanitization of hidden fields within deliverable items
# ---------------------------------------------------------------------------


class TestCORDeliverablesSanitization:
    """_sanitize_for_cor must strip COR_HIDDEN_FIELDS from within deliverable items."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_internal_cost_details_stripped_from_deliverable(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        wip = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-002")
        assert "internal_cost_details" not in wip, (
            "internal_cost_details must be stripped from deliverable items"
        )

    def test_internal_notes_stripped_from_deliverable(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        wip = next(d for d in data["deliverables"] if d["id"] == "deliv-cor-08-002")
        assert "internal_notes" not in wip, (
            "internal_notes must be stripped from deliverable items"
        )

    def test_raw_json_lacks_internal_cost_details_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"internal_cost_details"' not in raw, (
            "The raw JSON response must not contain the key 'internal_cost_details'"
        )


# ---------------------------------------------------------------------------
# Tests: access control
# ---------------------------------------------------------------------------


class TestCORDeliverablesAccessControl:
    """Access-control gates must fire before deliverables are fetched."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_unassigned_cor_returns_403(self, api_app):
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[]):
            with api_app.test_client() as c:
                resp = _get(c)
        assert resp.status_code == 403

    def test_unassigned_cor_response_has_message(self, api_app):
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[]):
            with api_app.test_client() as c:
                resp = _get(c)
        data = resp.get_json()
        assert "message" in data

    def test_no_email_returns_400(self):
        app_no_email = _build_cpmp_api_app(cor_email="")
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
            with app_no_email.test_client() as c:
                resp = _get(c)
        assert resp.status_code == 400
