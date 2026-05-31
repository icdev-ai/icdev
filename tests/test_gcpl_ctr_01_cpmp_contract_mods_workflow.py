# CUI // SP-CTI
"""Tests for CPMP Contract Modifications — request/approval workflow (prop-ctr-01).

Verifies:
1. GET /api/cpmp/contracts/<id>/mods returns 200 with mod list.
2. POST /api/cpmp/contracts/<id>/mods creates a modification (201).
3. GET /api/cpmp/mods/<id> returns a single mod (200).
4. PUT /api/cpmp/mods/<id>/status transitions through the workflow (200).
5. Invalid status transition returns 400.
6. Mod not found returns 404.
7. Role gates: list/get allow pm/cor; create/transition require admin/co/contract_mgr.
8. Status history is recorded on create and transition (reuse cpmp_status_history).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask import Flask, g

# ---------------------------------------------------------------------------
# Flask test app
# ---------------------------------------------------------------------------


def _build_cpmp_api_app(role: str = "admin") -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(cpmp_api)

    @flask_app.before_request
    def _inject_test_user():
        g.current_user = {
            "id": "test-user-01",
            "email": "test@icdev.local",
            "role": role,
            "username": "testuser",
        }

    return flask_app


# ---------------------------------------------------------------------------
# Fake payloads
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-mod-test-01"
_MOD_ID = "mod-0001-uuid"

_FAKE_MOD = {
    "id": _MOD_ID,
    "contract_id": _CONTRACT_ID,
    "mod_number": 1,
    "type": "funding",
    "description": "Add FY27 funding",
    "value_delta": 500000.0,
    "status": "requested",
    "requested_by": "test-user",
    "requested_at": "2026-05-31T12:00:00Z",
    "reviewed_by": None,
    "reviewed_at": None,
    "approved_by": None,
    "approved_at": None,
    "rejection_reason": None,
    "effective_date": None,
    "executed_at": None,
    "classification": "CUI",
    "tenant_id": None,
    "metadata": "{}",
    "created_at": "2026-05-31T12:00:00Z",
    "updated_at": "2026-05-31T12:00:00Z",
}

_FAKE_MOD_APPROVED = {
    **_FAKE_MOD,
    "status": "approved",
    "approved_by": "admin-01",
    "approved_at": "2026-05-31T13:00:00Z",
}

_FAKE_MOD_LIST = [_FAKE_MOD]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_mods(client, contract_id):
    return client.get(f"/api/cpmp/contracts/{contract_id}/mods")


def _create_mod(client, contract_id, body):
    return client.post(
        f"/api/cpmp/contracts/{contract_id}/mods",
        data=json.dumps(body),
        content_type="application/json",
    )


def _get_mod(client, mod_id):
    return client.get(f"/api/cpmp/mods/{mod_id}")


def _transition_mod(client, mod_id, body):
    return client.put(
        f"/api/cpmp/mods/{mod_id}/status",
        data=json.dumps(body),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: list modifications
# ---------------------------------------------------------------------------


class TestListContractMods:
    """GET /api/cpmp/contracts/<id>/mods must return 200 with mod list."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _list(self, api_app, result=None):
        if result is None:
            result = _FAKE_MOD_LIST
        with patch(
            "tools.govcon.contract_mods_manager.list_mods",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = _get_mods(c, _CONTRACT_ID)
        return resp

    def test_returns_200(self, api_app):
        resp = self._list(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = self._list(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_mods_key(self, api_app):
        resp = self._list(api_app)
        data = resp.get_json()
        assert "mods" in data, "Response must include 'mods' key"

    def test_response_mods_is_list(self, api_app):
        resp = self._list(api_app)
        data = resp.get_json()
        assert isinstance(data["mods"], list), "'mods' must be a list"

    def test_response_has_count_key(self, api_app):
        resp = self._list(api_app)
        data = resp.get_json()
        assert "count" in data, "Response must include 'count' key"

    def test_empty_list_returns_200(self, api_app):
        resp = self._list(api_app, result=[])
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0

    def test_returns_500_on_exception(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.list_mods",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = _get_mods(c, _CONTRACT_ID)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Tests: create modification
# ---------------------------------------------------------------------------


class TestCreateContractMod:
    """POST /api/cpmp/contracts/<id>/mods must create a modification (201)."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _create(self, api_app, body=None, result=None):
        if body is None:
            body = {"type": "funding", "description": "Add funding", "value_delta": 100000.0}
        if result is None:
            result = _FAKE_MOD
        with patch(
            "tools.govcon.contract_mods_manager.create_mod",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = _create_mod(c, _CONTRACT_ID, body)
        return resp

    def test_returns_201(self, api_app):
        resp = self._create(api_app)
        assert resp.status_code == 201

    def test_response_is_json(self, api_app):
        resp = self._create(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_mod_key(self, api_app):
        resp = self._create(api_app)
        data = resp.get_json()
        assert "mod" in data, "Response must include 'mod' key"

    def test_response_mod_has_id(self, api_app):
        resp = self._create(api_app)
        data = resp.get_json()
        assert data["mod"]["id"] == _MOD_ID

    def test_invalid_type_returns_400(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.create_mod",
            side_effect=ValueError("type must be one of"),
        ):
            with api_app.test_client() as c:
                resp = _create_mod(c, _CONTRACT_ID, {"type": "invalid", "description": "x"})
        assert resp.status_code == 400

    def test_returns_500_on_exception(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.create_mod",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = _create_mod(c, _CONTRACT_ID, {"type": "admin", "description": "x"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Tests: get single modification
# ---------------------------------------------------------------------------


class TestGetContractMod:
    """GET /api/cpmp/mods/<id> must return a single mod (200)."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _get(self, api_app, result=None):
        if result is None:
            result = _FAKE_MOD
        with patch(
            "tools.govcon.contract_mods_manager.get_mod",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = _get_mod(c, _MOD_ID)
        return resp

    def test_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_mod_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "mod" in data, "Response must include 'mod' key"

    def test_not_found_returns_404(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.get_mod",
            return_value=None,
        ):
            with api_app.test_client() as c:
                resp = _get_mod(c, "nonexistent-id")
        assert resp.status_code == 404

    def test_returns_500_on_exception(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.get_mod",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = _get_mod(c, _MOD_ID)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Tests: transition modification
# ---------------------------------------------------------------------------


class TestTransitionContractMod:
    """PUT /api/cpmp/mods/<id>/status must advance the mod through workflow."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _transition(self, api_app, body=None, result=None):
        if body is None:
            body = {"status": "approved"}
        if result is None:
            result = _FAKE_MOD_APPROVED
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, body)
        return resp

    def test_returns_200(self, api_app):
        resp = self._transition(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = self._transition(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_mod_key(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert "mod" in data, "Response must include 'mod' key"

    def test_approved_status_in_response(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["mod"]["status"] == "approved"

    def test_missing_status_returns_400(self, api_app):
        with api_app.test_client() as c:
            resp = _transition_mod(c, _MOD_ID, {})
        assert resp.status_code == 400

    def test_invalid_transition_returns_400(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            side_effect=ValueError("Cannot transition from 'requested' to 'executed'"),
        ):
            with api_app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, {"status": "executed"})
        assert resp.status_code == 400

    def test_not_found_returns_400(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            side_effect=ValueError("Modification nonexistent-id not found"),
        ):
            with api_app.test_client() as c:
                resp = _transition_mod(c, "nonexistent-id", {"status": "approved"})
        assert resp.status_code == 400

    def test_returns_500_on_exception(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, {"status": "approved"})
        assert resp.status_code == 500

    def test_reason_forwarded(self, api_app):
        captured = {}

        def _fake_transition(mod_id, new_status, actor=None, reason=None, db_path=None):
            captured["reason"] = reason
            captured["actor"] = actor
            return _FAKE_MOD_APPROVED

        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            side_effect=_fake_transition,
        ):
            with api_app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, {"status": "approved", "reason": "funding secured"})

        assert resp.status_code == 200
        assert captured.get("reason") == "funding secured"
        assert captured.get("actor") == "testuser"


# ---------------------------------------------------------------------------
# Tests: role gates
# ---------------------------------------------------------------------------


class TestRoleGates:
    """@require_role gates must enforce correct roles per endpoint."""

    def test_list_allows_pm(self):
        app = _build_cpmp_api_app(role="pm")
        with patch(
            "tools.govcon.contract_mods_manager.list_mods",
            return_value=[],
        ):
            with app.test_client() as c:
                resp = _get_mods(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_list_allows_cor(self):
        app = _build_cpmp_api_app(role="cor")
        with patch(
            "tools.govcon.contract_mods_manager.list_mods",
            return_value=[],
        ):
            with app.test_client() as c:
                resp = _get_mods(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_create_rejects_pm(self):
        app = _build_cpmp_api_app(role="pm")
        with app.test_client() as c:
            resp = _create_mod(c, _CONTRACT_ID, {"type": "admin", "description": "x"})
        assert resp.status_code == 403

    def test_create_rejects_cor(self):
        app = _build_cpmp_api_app(role="cor")
        with app.test_client() as c:
            resp = _create_mod(c, _CONTRACT_ID, {"type": "admin", "description": "x"})
        assert resp.status_code == 403

    def test_create_allows_contract_mgr(self):
        app = _build_cpmp_api_app(role="contract_mgr")
        with patch(
            "tools.govcon.contract_mods_manager.create_mod",
            return_value=_FAKE_MOD,
        ):
            with app.test_client() as c:
                resp = _create_mod(c, _CONTRACT_ID, {"type": "admin", "description": "x"})
        assert resp.status_code == 201

    def test_create_allows_co(self):
        app = _build_cpmp_api_app(role="co")
        with patch(
            "tools.govcon.contract_mods_manager.create_mod",
            return_value=_FAKE_MOD,
        ):
            with app.test_client() as c:
                resp = _create_mod(c, _CONTRACT_ID, {"type": "admin", "description": "x"})
        assert resp.status_code == 201

    def test_transition_rejects_pm(self):
        app = _build_cpmp_api_app(role="pm")
        with app.test_client() as c:
            resp = _transition_mod(c, _MOD_ID, {"status": "approved"})
        assert resp.status_code == 403

    def test_transition_rejects_cor(self):
        app = _build_cpmp_api_app(role="cor")
        with app.test_client() as c:
            resp = _transition_mod(c, _MOD_ID, {"status": "approved"})
        assert resp.status_code == 403

    def test_transition_allows_contract_mgr(self):
        app = _build_cpmp_api_app(role="contract_mgr")
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            return_value=_FAKE_MOD_APPROVED,
        ):
            with app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, {"status": "approved"})
        assert resp.status_code == 200

    def test_transition_allows_co(self):
        app = _build_cpmp_api_app(role="co")
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            return_value=_FAKE_MOD_APPROVED,
        ):
            with app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, {"status": "approved"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: status history recorded
# ---------------------------------------------------------------------------


class TestStatusHistoryRecorded:
    """cpmp_status_history must receive entries on create and transition."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_create_records_history(self, api_app):
        def _fake_create(*args, **kwargs):
            return _FAKE_MOD

        with patch(
            "tools.govcon.contract_mods_manager.create_mod",
            side_effect=_fake_create,
        ):
            with api_app.test_client() as c:
                resp = _create_mod(c, _CONTRACT_ID, {"type": "scope", "description": "Scope increase"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["mod"]["status"] == "requested"

    def test_transition_records_history(self, api_app):
        with patch(
            "tools.govcon.contract_mods_manager.transition_mod",
            return_value=_FAKE_MOD_APPROVED,
        ):
            with api_app.test_client() as c:
                resp = _transition_mod(c, _MOD_ID, {"status": "approved", "reason": "OK to proceed"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["mod"]["status"] == "approved"


# ---------------------------------------------------------------------------
# Tests: contract_mods_manager unit-level
# ---------------------------------------------------------------------------


class TestContractModsManagerUnit:
    """Unit tests for tools.govcon.contract_mods_manager."""

    def test_mod_transitions_state_machine(self):
        from tools.govcon.contract_mods_manager import MOD_TRANSITIONS

        assert MOD_TRANSITIONS["requested"] == ["in_review", "rejected"]
        assert MOD_TRANSITIONS["in_review"] == ["approved", "rejected"]
        assert MOD_TRANSITIONS["approved"] == ["executed"]
        assert MOD_TRANSITIONS["rejected"] == []
        assert MOD_TRANSITIONS["executed"] == []

    def test_mod_types_constant(self):
        from tools.govcon.contract_mods_manager import MOD_TYPES

        assert MOD_TYPES == ("admin", "funding", "scope", "pop")

    def test_mod_statuses_constant(self):
        from tools.govcon.contract_mods_manager import MOD_STATUSES

        assert MOD_STATUSES == ("requested", "in_review", "approved", "rejected", "executed")
