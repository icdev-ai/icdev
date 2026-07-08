# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/negative-events — negative event list (append-only).

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'events' key.
5. Response body contains 'total' key.
6. Response body contains 'contract_id' key.
7. events value is a list.
8. list_events called with correct contract_id.
9. contract_id in response matches the URL path parameter.
10. total equals len(events) in response.
11. No query params — list_events called with severity=None, status=None.
12. severity query param forwarded to list_events.
13. status query param forwarded to list_events.
14. Both severity and status query params forwarded together.
15. Empty result set — total is 0 and events is [].
16. Contract not found — list_events returns ok with empty events list.
17. Backend exception — returns 500 with error status.
18. Event records in list contain 'event_type' field.
19. Event records in list contain 'severity' field.
20. Events returned newest-first (NIST AU-2 append-only ordering).
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0012"
_MISSING_CONTRACT_ID = "ctr-not-found-0012"

_EVENT_1 = {
    "id": "evt-0012-a",
    "contract_id": _CONTRACT_ID,
    "event_type": "delinquent_delivery",
    "severity": "high",
    "description": "Deliverable overdue by 14 days",
    "corrective_action": None,
    "corrective_action_status": "open",
    "cpars_impact": 0.05,
    "detected_by": "pm",
    "source_entity_type": None,
    "source_entity_id": None,
    "created_at": "2026-05-20T10:00:00",
    "updated_at": "2026-05-20T10:00:00",
}

_EVENT_2 = {
    "id": "evt-0012-b",
    "contract_id": _CONTRACT_ID,
    "event_type": "cost_overrun",
    "severity": "medium",
    "description": "Cost overrun on CLIN 0002",
    "corrective_action": None,
    "corrective_action_status": "open",
    "cpars_impact": 0.03,
    "detected_by": "contracting_officer",
    "source_entity_type": None,
    "source_entity_id": None,
    "created_at": "2026-05-19T08:00:00",
    "updated_at": "2026-05-19T08:00:00",
}

_LIST_RESULT_MULTI = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "total": 2,
    "events": [_EVENT_1, _EVENT_2],
}

_LIST_RESULT_EMPTY = {
    "status": "ok",
    "contract_id": _MISSING_CONTRACT_ID,
    "total": 0,
    "events": [],
}

_LIST_RESULT_EMPTY_CONTRACT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "total": 0,
    "events": [],
}


# ---------------------------------------------------------------------------
# Flask test app
# ---------------------------------------------------------------------------


def _build_cpmp_api_app() -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    @flask_app.before_request
    def _inject_fake_auth_0():
        from flask import g
        g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

    flask_app.register_blueprint(cpmp_api)
    return flask_app


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get(client, contract_id, **query_params):
    qs = "&".join(f"{k}={v}" for k, v in query_params.items())
    url = f"/api/cpmp/contracts/{contract_id}/negative-events"
    if qs:
        url = f"{url}?{qs}"
    return client.get(url)


# ---------------------------------------------------------------------------
# Tests: core response structure
# ---------------------------------------------------------------------------


class TestGetNegativeEventList:
    """GET with valid contract_id returns 200 with events array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, return_value=None, **query_params):
        if return_value is None:
            return_value = _LIST_RESULT_MULTI
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=return_value,
        ) as mock_list:
            with api_app.test_client() as c:
                resp = _get(c, contract_id, **query_params)
        return resp, mock_list

    def test_returns_200(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_status_is_ok(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_events_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "events" in data

    def test_response_has_total_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_response_has_contract_id_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "contract_id" in data

    def test_events_is_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["events"], list)

    def test_list_called_with_correct_contract_id(self, api_app):
        _, mock_list = self._call(api_app)
        args, _ = mock_list.call_args
        assert args[0] == _CONTRACT_ID

    def test_contract_id_in_response_matches_request(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["contract_id"] == _CONTRACT_ID

    def test_total_matches_events_length(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["events"])


# ---------------------------------------------------------------------------
# Tests: query parameter forwarding
# ---------------------------------------------------------------------------


class TestGetNegativeEventFilters:
    """Query params severity and status are forwarded to list_events."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_no_filters_passes_none_to_list_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, _CONTRACT_ID)
        _, kwargs = mock_list.call_args
        assert kwargs.get("severity") is None
        assert kwargs.get("status") is None

    def test_severity_filter_forwarded_to_list_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, _CONTRACT_ID, severity="high")
        _, kwargs = mock_list.call_args
        assert kwargs.get("severity") == "high"

    def test_status_filter_forwarded_to_list_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, _CONTRACT_ID, status="open")
        _, kwargs = mock_list.call_args
        assert kwargs.get("status") == "open"

    def test_both_filters_forwarded_to_list_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, _CONTRACT_ID, severity="high", status="open")
        _, kwargs = mock_list.call_args
        assert kwargs.get("severity") == "high"
        assert kwargs.get("status") == "open"

    def test_empty_result_total_is_zero(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_EMPTY_CONTRACT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["total"] == 0
        assert data["events"] == []


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestGetNegativeEventEdgeCases:
    """Edge cases: unknown contract, backend exceptions, field presence, ordering."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_ok_with_empty_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_EMPTY,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _MISSING_CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "ok"
        assert data.get("events") == []

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_event_record_has_event_type_field(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        events = resp.get_json()["events"]
        assert all("event_type" in e for e in events)

    def test_event_record_has_severity_field(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        events = resp.get_json()["events"]
        assert all("severity" in e for e in events)

    def test_events_returned_newest_first_append_only(self, api_app):
        """NIST AU-2 append-only: list_events orders by created_at DESC (newest first)."""
        with patch(
            "tools.govcon.negative_event_tracker.list_events",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        events = resp.get_json()["events"]
        assert len(events) >= 2
        assert events[0]["created_at"] >= events[1]["created_at"]
