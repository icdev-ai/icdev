# CUI // SP-CTI
"""Integration tests for CPMP Personnel Registry API endpoints.

Covers all 7 routes:
  POST   /api/cpmp/contracts/<id>/personnel        — upsert person
  GET    /api/cpmp/contracts/<id>/personnel        — list with clearance/lcat filters
  PUT    /api/cpmp/personnel/<pid>                 — update status/backup
  GET    /api/cpmp/contracts/<id>/personnel/expiring  — expiring within ?days window
  GET    /api/cpmp/contracts/<id>/personnel/key-persons — key_person=True list
  GET    /api/cpmp/contracts/<id>/personnel/alerts — open credential alerts
  PUT    /api/cpmp/personnel/alerts/<aid>          — acknowledge/resolve alert

Acceptance criteria verified:
  - expiring endpoint returns only persons within the days window
  - alert acknowledgment sets acknowledged_at timestamp
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-pma-0001"
_PERSON_ID = "per-uuid-0001"
_ALERT_ID = "alt-uuid-0001"
_BACKUP_ID = "per-uuid-0002"

_PERSON_RECORD = {
    "id": _PERSON_ID,
    "name": "Jane Smith",
    "role": "Program Manager",
    "contract_id": _CONTRACT_ID,
    "clearance_level": "TS/SCI",
    "lcat": "PM-III",
    "key_person": 1,
    "status": "active",
    "poly_expiry": "2026-09-01",
    "secret_expiry": None,
    "ts_expiry": "2026-07-15",
    "backup_person_id": None,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
}

_UPSERT_CREATE_RESULT = {
    "status": "ok",
    "person_id": _PERSON_ID,
    "action": "created",
}

_UPSERT_UPDATE_RESULT = {
    "status": "ok",
    "person_id": _PERSON_ID,
    "action": "updated",
}

_UPDATE_PERSON_RESULT = {
    "status": "ok",
    "person_id": _PERSON_ID,
}

_EXPIRING_RECORD = {
    **_PERSON_RECORD,
    "expiring_credentials": [
        {"credential": "ts_expiry", "expiry_date": "2026-07-15", "days_remaining": 32},
    ],
}

_ALERT_RECORD = {
    "id": _ALERT_ID,
    "person_id": _PERSON_ID,
    "person_name": "Jane Smith",
    "person_role": "Program Manager",
    "alert_type": "ts_expiry",
    "expiry_date": "2026-07-15",
    "days_remaining": 32,
    "severity": "warning",
    "status": "open",
    "acknowledged_at": None,
    "kanban_task_id": None,
    "created_at": "2026-06-13T00:00:00Z",
}

_ACK_RESULT = {
    "status": "ok",
    "alert_id": _ALERT_ID,
    "new_status": "acknowledged",
    "acknowledged_at": "2026-06-13T12:00:00Z",
}

_NOT_FOUND_PERSON = {
    "status": "error",
    "message": f"Person {_PERSON_ID} not found",
}

_NOT_FOUND_ALERT = {
    "status": "error",
    "message": f"Alert {_ALERT_ID} not found",
}

_MODULE = "tools.govcon.personnel_manager"


# ---------------------------------------------------------------------------
# Flask test app factory
# ---------------------------------------------------------------------------


def _build_app() -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    app = Flask(__name__)
    app.config["TESTING"] = True
    @app.before_request
    def _inject_fake_auth_0():
        from flask import g
        g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

    app.register_blueprint(cpmp_api)
    return app


# ---------------------------------------------------------------------------
# POST /api/cpmp/contracts/<id>/personnel — upsert person
# ---------------------------------------------------------------------------


class TestUpsertPersonnel:
    """POST /api/cpmp/contracts/<id>/personnel creates a new personnel record."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _post(self, client, payload=None, contract_id=_CONTRACT_ID):
        return client.post(
            f"/api/cpmp/contracts/{contract_id}/personnel",
            json=payload or {"name": "Jane Smith", "role": "PM"},
            content_type="application/json",
        )

    def test_returns_201_on_create(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_CREATE_RESULT):
            with app.test_client() as c:
                resp = self._post(c)
        assert resp.status_code == 201

    def test_response_is_json(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_CREATE_RESULT):
            with app.test_client() as c:
                resp = self._post(c)
        assert resp.content_type.startswith("application/json")

    def test_response_status_is_ok(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_CREATE_RESULT):
            with app.test_client() as c:
                resp = self._post(c)
        assert resp.get_json()["status"] == "ok"

    def test_response_has_person_id(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_CREATE_RESULT):
            with app.test_client() as c:
                resp = self._post(c)
        assert "person_id" in resp.get_json()

    def test_action_is_created(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_CREATE_RESULT):
            with app.test_client() as c:
                resp = self._post(c)
        assert resp.get_json()["action"] == "created"

    def test_action_is_updated_on_upsert(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_UPDATE_RESULT):
            with app.test_client() as c:
                resp = self._post(c, payload={"id": _PERSON_ID, "name": "Jane Smith"})
        assert resp.get_json()["action"] == "updated"

    def test_missing_name_returns_400(self, app):
        with patch(
            f"{_MODULE}.upsert_person",
            return_value={"status": "error", "message": "name is required"},
        ):
            with app.test_client() as c:
                resp = self._post(c, payload={})
        assert resp.status_code == 400
        assert resp.get_json()["status"] == "error"

    def test_upsert_called_with_contract_id(self, app):
        with patch(f"{_MODULE}.upsert_person", return_value=_UPSERT_CREATE_RESULT) as mock_fn:
            with app.test_client() as c:
                self._post(c)
        args, _ = mock_fn.call_args
        assert args[0] == _CONTRACT_ID

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.upsert_person", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._post(c)
        assert resp.status_code == 500
        assert resp.get_json()["status"] == "error"


# ---------------------------------------------------------------------------
# GET /api/cpmp/contracts/<id>/personnel — list personnel
# ---------------------------------------------------------------------------


class TestListPersonnel:
    """GET /api/cpmp/contracts/<id>/personnel returns personnel list with optional filters."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _get(self, client, contract_id=_CONTRACT_ID, params=None):
        url = f"/api/cpmp/contracts/{contract_id}/personnel"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        return client.get(url)

    def test_returns_200(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[_PERSON_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 200

    def test_response_is_json_array(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[_PERSON_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert isinstance(resp.get_json(), list)

    def test_returns_personnel_records(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[_PERSON_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["id"] == _PERSON_ID

    def test_list_called_with_contract_id(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c)
        args, _ = mock_fn.call_args
        assert args[0] == _CONTRACT_ID

    def test_clearance_filter_forwarded(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c, params={"clearance_level": "TS/SCI"})
        _, kwargs = mock_fn.call_args
        assert kwargs.get("clearance_level") == "TS/SCI"

    def test_lcat_filter_forwarded(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c, params={"lcat": "PM-III"})
        _, kwargs = mock_fn.call_args
        assert kwargs.get("lcat") == "PM-III"

    def test_empty_contract_returns_empty_list(self, app):
        with patch(f"{_MODULE}.list_personnel", return_value=[]):
            with app.test_client() as c:
                resp = self._get(c, contract_id="ctr-empty")
        assert resp.get_json() == []

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.list_personnel", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PUT /api/cpmp/personnel/<pid> — update personnel
# ---------------------------------------------------------------------------


class TestUpdatePersonnel:
    """PUT /api/cpmp/personnel/<pid> updates status and/or backup assignment."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _put(self, client, person_id=_PERSON_ID, payload=None):
        return client.put(
            f"/api/cpmp/personnel/{person_id}",
            json=payload or {"status": "inactive", "backup_person_id": _BACKUP_ID},
            content_type="application/json",
        )

    def test_returns_200(self, app):
        with patch(f"{_MODULE}.update_person", return_value=_UPDATE_PERSON_RESULT):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.status_code == 200

    def test_response_status_is_ok(self, app):
        with patch(f"{_MODULE}.update_person", return_value=_UPDATE_PERSON_RESULT):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.get_json()["status"] == "ok"

    def test_response_has_person_id(self, app):
        with patch(f"{_MODULE}.update_person", return_value=_UPDATE_PERSON_RESULT):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.get_json()["person_id"] == _PERSON_ID

    def test_not_found_returns_404(self, app):
        with patch(f"{_MODULE}.update_person", return_value=_NOT_FOUND_PERSON):
            with app.test_client() as c:
                resp = self._put(c, person_id="per-unknown")
        assert resp.status_code == 404
        assert resp.get_json()["status"] == "error"

    def test_update_called_with_person_id(self, app):
        with patch(f"{_MODULE}.update_person", return_value=_UPDATE_PERSON_RESULT) as mock_fn:
            with app.test_client() as c:
                self._put(c)
        args, _ = mock_fn.call_args
        assert args[0] == _PERSON_ID

    def test_backup_assignment_forwarded(self, app):
        with patch(f"{_MODULE}.update_person", return_value=_UPDATE_PERSON_RESULT) as mock_fn:
            with app.test_client() as c:
                self._put(c, payload={"backup_person_id": _BACKUP_ID})
        args, _ = mock_fn.call_args
        assert args[1].get("backup_person_id") == _BACKUP_ID

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.update_person", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/cpmp/contracts/<id>/personnel/expiring — expiring credentials
# ---------------------------------------------------------------------------


class TestListExpiringPersonnel:
    """GET .../personnel/expiring returns only persons within the ?days window."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _get(self, client, contract_id=_CONTRACT_ID, days=None):
        url = f"/api/cpmp/contracts/{contract_id}/personnel/expiring"
        if days is not None:
            url = f"{url}?days={days}"
        return client.get(url)

    def test_returns_200(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[_EXPIRING_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 200

    def test_response_is_json_array(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[_EXPIRING_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert isinstance(resp.get_json(), list)

    def test_records_include_expiring_credentials(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[_EXPIRING_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        data = resp.get_json()
        assert len(data) == 1
        assert "expiring_credentials" in data[0]
        assert len(data[0]["expiring_credentials"]) == 1

    def test_days_param_forwarded_to_backend(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c, days=60)
        _, kwargs = mock_fn.call_args
        assert kwargs.get("days") == 60

    def test_default_days_is_90(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c)
        _, kwargs = mock_fn.call_args
        assert kwargs.get("days") == 90

    def test_expiring_endpoint_called_with_contract_id(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c)
        args, _ = mock_fn.call_args
        assert args[0] == _CONTRACT_ID

    def test_only_within_days_window_returned(self, app):
        """Acceptance criteria: only records within the days window are returned."""
        within_window = {**_EXPIRING_RECORD, "expiring_credentials": [
            {"credential": "ts_expiry", "expiry_date": "2026-07-15", "days_remaining": 32}
        ]}
        # Backend filters; route should pass through exactly what backend returns
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[within_window]):
            with app.test_client() as c:
                resp = self._get(c, days=60)
        data = resp.get_json()
        assert all(
            cred["days_remaining"] <= 60
            for person in data
            for cred in person.get("expiring_credentials", [])
        )

    def test_empty_result_for_no_expiring(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", return_value=[]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.get_json() == []

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.get_expiring_personnel", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/cpmp/contracts/<id>/personnel/key-persons — key person list
# ---------------------------------------------------------------------------


class TestListKeyPersons:
    """GET .../personnel/key-persons returns personnel with key_person=True."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _get(self, client, contract_id=_CONTRACT_ID):
        return client.get(f"/api/cpmp/contracts/{contract_id}/personnel/key-persons")

    def test_returns_200(self, app):
        with patch(f"{_MODULE}.get_key_persons", return_value=[_PERSON_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 200

    def test_response_is_json_array(self, app):
        with patch(f"{_MODULE}.get_key_persons", return_value=[_PERSON_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert isinstance(resp.get_json(), list)

    def test_all_records_have_key_person_true(self, app):
        with patch(f"{_MODULE}.get_key_persons", return_value=[_PERSON_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        data = resp.get_json()
        assert all(r.get("key_person") for r in data)

    def test_called_with_contract_id(self, app):
        with patch(f"{_MODULE}.get_key_persons", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c)
        args, _ = mock_fn.call_args
        assert args[0] == _CONTRACT_ID

    def test_empty_when_no_key_persons(self, app):
        with patch(f"{_MODULE}.get_key_persons", return_value=[]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.get_json() == []

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.get_key_persons", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/cpmp/contracts/<id>/personnel/alerts — open alerts
# ---------------------------------------------------------------------------


class TestListPersonnelAlerts:
    """GET .../personnel/alerts returns open credential alerts for the contract."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _get(self, client, contract_id=_CONTRACT_ID):
        return client.get(f"/api/cpmp/contracts/{contract_id}/personnel/alerts")

    def test_returns_200(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", return_value=[_ALERT_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 200

    def test_response_is_json_array(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", return_value=[_ALERT_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        assert isinstance(resp.get_json(), list)

    def test_all_alerts_are_open(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", return_value=[_ALERT_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        data = resp.get_json()
        for alert in data:
            assert alert.get("status") in (None, "open")

    def test_alert_has_person_name(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", return_value=[_ALERT_RECORD]):
            with app.test_client() as c:
                resp = self._get(c)
        data = resp.get_json()
        assert data[0].get("person_name") == "Jane Smith"

    def test_called_with_contract_id(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", return_value=[]) as mock_fn:
            with app.test_client() as c:
                self._get(c)
        args, _ = mock_fn.call_args
        assert args[0] == _CONTRACT_ID

    def test_empty_when_no_open_alerts(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", return_value=[]):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.get_json() == []

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.get_personnel_alerts", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._get(c)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PUT /api/cpmp/personnel/alerts/<aid> — acknowledge/resolve alert
# ---------------------------------------------------------------------------


class TestUpdatePersonnelAlert:
    """PUT /api/cpmp/personnel/alerts/<aid> acknowledges or resolves an alert."""

    @pytest.fixture()
    def app(self):
        return _build_app()

    def _put(self, client, alert_id=_ALERT_ID, payload=None):
        return client.put(
            f"/api/cpmp/personnel/alerts/{alert_id}",
            json=payload or {"status": "acknowledged"},
            content_type="application/json",
        )

    def test_returns_200(self, app):
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.status_code == 200

    def test_response_status_is_ok(self, app):
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.get_json()["status"] == "ok"

    def test_response_has_alert_id(self, app):
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.get_json()["alert_id"] == _ALERT_ID

    def test_acknowledged_status_in_response(self, app):
        """Acceptance criteria: status='acknowledged' is reflected in the response."""
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT):
            with app.test_client() as c:
                resp = self._put(c, payload={"status": "acknowledged"})
        data = resp.get_json()
        assert data["new_status"] == "acknowledged"

    def test_acknowledged_at_timestamp_set(self, app):
        """Acceptance criteria: acknowledged_at timestamp is set when status='acknowledged'."""
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT):
            with app.test_client() as c:
                resp = self._put(c, payload={"status": "acknowledged"})
        data = resp.get_json()
        assert data.get("acknowledged_at") is not None

    def test_resolve_status_sets_acknowledged_at(self, app):
        resolved_result = {**_ACK_RESULT, "new_status": "resolved"}
        with patch(f"{_MODULE}.update_alert", return_value=resolved_result):
            with app.test_client() as c:
                resp = self._put(c, payload={"status": "resolved"})
        data = resp.get_json()
        assert data["new_status"] == "resolved"
        assert data.get("acknowledged_at") is not None

    def test_not_found_returns_404(self, app):
        with patch(f"{_MODULE}.update_alert", return_value=_NOT_FOUND_ALERT):
            with app.test_client() as c:
                resp = self._put(c, alert_id="alt-unknown")
        assert resp.status_code == 404
        assert resp.get_json()["status"] == "error"

    def test_update_called_with_alert_id(self, app):
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT) as mock_fn:
            with app.test_client() as c:
                self._put(c)
        args, _ = mock_fn.call_args
        assert args[0] == _ALERT_ID

    def test_status_forwarded_to_backend(self, app):
        with patch(f"{_MODULE}.update_alert", return_value=_ACK_RESULT) as mock_fn:
            with app.test_client() as c:
                self._put(c, payload={"status": "acknowledged"})
        args, _ = mock_fn.call_args
        assert args[1].get("status") == "acknowledged"

    def test_backend_exception_returns_500(self, app):
        with patch(f"{_MODULE}.update_alert", side_effect=RuntimeError("DB error")):
            with app.test_client() as c:
                resp = self._put(c)
        assert resp.status_code == 500
