# CUI // SP-CTI
"""Tests for GET /api/cpmp/cdrl-generations — CDRL generation record schema.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'generations' key.
5. Response body contains 'total' key.
6. generations value is a list.
7. list_generations called with correct contract_id filter.
8. list_generations called with correct deliverable_id filter.
9. list_generations called with correct status filter.
10. All three filters forwarded together.
11. total equals len(generations) in response.
12. Generation record contains 'contract_id' field.
13. Generation record contains 'deliverable_id' field.
14. Generation record contains 'generation_tool' field (tool used for generation).
15. Generation record contains 'status' field.
16. Empty generations list returns total == 0.
17. Empty generations list returns generations == [].
18. Backend exception returns 500 with error status.
19. contract_id filter not forwarded when absent.
20. deliverable_id filter not forwarded when absent.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-cdrl-03"
_DELIVERABLE_ID = "deliv-cdrl-test-03"

_GEN_1 = {
    "id": "gen-cdrl-03-a",
    "contract_id": _CONTRACT_ID,
    "deliverable_id": _DELIVERABLE_ID,
    "cdrl_type": "ssp",
    "generation_tool": "tools/compliance/ssp_generator.py",
    "tool_args": "{}",
    "output_path": "data/cdrl_output/ssp_A001_20260520.json",
    "output_hash": "abc123de456f7890",
    "file_size_bytes": None,
    "status": "generated",
    "error_message": None,
    "generated_by": None,
    "reviewed_by": None,
    "approved_by": None,
    "metadata": "{}",
    "created_at": "2026-05-20T12:00:00+00:00",
    "classification": "CUI",
}

_GEN_2 = {
    "id": "gen-cdrl-03-b",
    "contract_id": _CONTRACT_ID,
    "deliverable_id": _DELIVERABLE_ID,
    "cdrl_type": "sbom",
    "generation_tool": "tools/compliance/sbom_generator.py",
    "tool_args": "{}",
    "output_path": None,
    "output_hash": None,
    "file_size_bytes": None,
    "status": "failed",
    "error_message": "Exit code 1",
    "generated_by": None,
    "reviewed_by": None,
    "approved_by": None,
    "metadata": "{}",
    "created_at": "2026-05-20T11:00:00+00:00",
    "classification": "CUI",
}

_LIST_RESULT_MULTI = {
    "status": "ok",
    "total": 2,
    "generations": [_GEN_1, _GEN_2],
}

_LIST_RESULT_EMPTY = {
    "status": "ok",
    "total": 0,
    "generations": [],
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


def _get(client, **query_params):
    qs = "&".join(f"{k}={v}" for k, v in query_params.items())
    url = "/api/cpmp/cdrl-generations"
    if qs:
        url = f"{url}?{qs}"
    return client.get(url)


# ---------------------------------------------------------------------------
# Tests: core response structure
# ---------------------------------------------------------------------------


class TestListCdrlGenerationsResponse:
    """GET /api/cpmp/cdrl-generations returns 200 with generations array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, return_value=None, **query_params):
        if return_value is None:
            return_value = _LIST_RESULT_MULTI
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=return_value,
        ) as mock_list:
            with api_app.test_client() as c:
                resp = _get(c, **query_params)
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

    def test_response_has_generations_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "generations" in data

    def test_response_has_total_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_generations_is_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["generations"], list)

    def test_total_matches_generations_length(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["generations"])


# ---------------------------------------------------------------------------
# Tests: query parameter forwarding
# ---------------------------------------------------------------------------


class TestListCdrlGenerationsFilters:
    """Query params contract_id, deliverable_id, status forwarded to list_generations."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_id_filter_forwarded(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, contract_id=_CONTRACT_ID)
        args, _ = mock_list.call_args
        assert args[0] == _CONTRACT_ID

    def test_deliverable_id_filter_forwarded(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, deliverable_id=_DELIVERABLE_ID)
        args, _ = mock_list.call_args
        assert args[1] == _DELIVERABLE_ID

    def test_status_filter_forwarded(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, status="generated")
        args, _ = mock_list.call_args
        assert args[2] == "generated"

    def test_all_three_filters_forwarded(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, contract_id=_CONTRACT_ID, deliverable_id=_DELIVERABLE_ID, status="failed")
        args, _ = mock_list.call_args
        assert args[0] == _CONTRACT_ID
        assert args[1] == _DELIVERABLE_ID
        assert args[2] == "failed"

    def test_no_contract_id_passes_none(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_EMPTY,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c)
        args, _ = mock_list.call_args
        assert args[0] is None

    def test_no_deliverable_id_passes_none(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_EMPTY,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c)
        args, _ = mock_list.call_args
        assert args[1] is None


# ---------------------------------------------------------------------------
# Tests: generation record schema fields
# ---------------------------------------------------------------------------


class TestCdrlGenerationRecordSchema:
    """Generation records expose contract_id, deliverable_id, generation_tool, status."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_generation_record_has_contract_id(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all("contract_id" in g for g in generations)

    def test_generation_record_has_deliverable_id(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all("deliverable_id" in g for g in generations)

    def test_generation_record_has_generation_tool(self, api_app):
        """generation_tool identifies the ICDEV™ tool invoked for this CDRL (tool_used)."""
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all("generation_tool" in g for g in generations)

    def test_generation_record_has_status(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all("status" in g for g in generations)

    def test_contract_id_value_is_non_empty_string(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all(isinstance(g["contract_id"], str) and g["contract_id"] for g in generations)

    def test_deliverable_id_value_is_non_empty_string(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all(isinstance(g["deliverable_id"], str) and g["deliverable_id"] for g in generations)

    def test_generation_tool_value_is_non_empty_string(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all(isinstance(g["generation_tool"], str) and g["generation_tool"] for g in generations)

    def test_status_value_is_valid_string(self, api_app):
        valid_statuses = {"generated", "reviewed", "approved", "submitted", "failed"}
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_MULTI,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        generations = resp.get_json()["generations"]
        assert all(g["status"] in valid_statuses for g in generations)


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestListCdrlGenerationsEdgeCases:
    """Edge cases: empty results, backend exceptions."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_empty_result_total_is_zero(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_EMPTY,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        data = resp.get_json()
        assert data["total"] == 0

    def test_empty_result_generations_is_empty_list(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            return_value=_LIST_RESULT_EMPTY,
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        data = resp.get_json()
        assert data["generations"] == []

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.cdrl_generator.list_generations",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"
