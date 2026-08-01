# CUI // SP-CTI
"""Tests for GET /api/cpmp/sam/awards/search — returns SAM.gov awards search results.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'awards' key.
5. 'awards' value is a list.
6. search_awards called with correct query string.
7. Response body contains 'total' key.
8. 'total' equals len(awards).
9. Response body contains 'query' key.
10. 'query' echoes the q parameter value.
11. Missing 'q' parameter returns 400.
12. Missing 'q' returns status == 'error'.
13. Empty search results returns 200 with empty awards list.
14. Empty search results returns total == 0.
15. Backend exception returns 500 with error status.
16. Award record contains 'sam_award_id' field.
17. Award record contains 'awardee_name' field.
18. Award record contains 'awarding_agency' field.
19. Award record contains 'piid' field.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QUERY = "Acme Defense"

_AWARD_1 = {
    "id": "award-cdrl-12-a",
    "sam_award_id": "SAM-2026-0001",
    "piid": "FA8650-26-C-0001",
    "awardee_name": "Acme Defense Systems LLC",
    "awardee_uei": "UEIDEF0001",
    "awardee_cage": "1ACME",
    "obligation_amount": 4500000.0,
    "base_exercised_options_value": 9000000.0,
    "award_date": "2026-01-15",
    "pop_start": "2026-02-01",
    "pop_end": "2027-01-31",
    "awarding_agency": "Department of the Air Force",
    "naics_code": "541330",
    "psc_code": "R425",
    "content_hash": "abc123de456f7890",
    "linked_contract_id": None,
    "discovered_at": "2026-05-20T10:00:00+00:00",
}

_AWARD_2 = {
    "id": "award-cdrl-12-b",
    "sam_award_id": "SAM-2026-0002",
    "piid": "FA8650-26-C-0002",
    "awardee_name": "Acme Defense Technologies Inc",
    "awardee_uei": "UEIDEF0002",
    "awardee_cage": "2ACME",
    "obligation_amount": 1200000.0,
    "base_exercised_options_value": None,
    "award_date": "2026-02-10",
    "pop_start": None,
    "pop_end": None,
    "awarding_agency": "Department of Defense",
    "naics_code": "541519",
    "psc_code": None,
    "content_hash": "def456ab789c0123",
    "linked_contract_id": "ctr-internal-001",
    "discovered_at": "2026-05-20T09:00:00+00:00",
}

_SEARCH_RESULT = {
    "status": "ok",
    "query": _QUERY,
    "total": 2,
    "awards": [_AWARD_1, _AWARD_2],
}

_EMPTY_RESULT = {
    "status": "ok",
    "query": _QUERY,
    "total": 0,
    "awards": [],
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


def _get(client, q=None):
    url = "/api/cpmp/sam/awards/search"
    if q is not None:
        url = f"{url}?q={q}"
    return client.get(url)


# ---------------------------------------------------------------------------
# Tests: successful search response
# ---------------------------------------------------------------------------


class TestSearchSamAwardsReturnsResults:
    """GET /api/cpmp/sam/awards/search?q=<keyword> returns 200 with awards array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, query=_QUERY, return_value=None):
        if return_value is None:
            return_value = _SEARCH_RESULT
        with patch(
            "tools.govcon.sam_contract_sync.search_awards",
            return_value=return_value,
        ) as mock_search:
            with api_app.test_client() as c:
                resp = _get(c, q=query)
        return resp, mock_search

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

    def test_response_has_awards_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "awards" in data

    def test_awards_is_a_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["awards"], list)

    def test_search_awards_called_with_correct_query(self, api_app):
        _, mock_search = self._call(api_app)
        args, _ = mock_search.call_args
        assert args[0] == _QUERY

    def test_response_has_total_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_total_equals_awards_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["awards"])

    def test_response_has_query_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "query" in data

    def test_query_echoes_q_parameter(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["query"] == _QUERY


# ---------------------------------------------------------------------------
# Tests: award record schema
# ---------------------------------------------------------------------------


class TestSearchSamAwardsRecordSchema:
    """Award records expose sam_award_id, awardee_name, awarding_agency, piid."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.sam_contract_sync.search_awards",
            return_value=_SEARCH_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, q=_QUERY)
        return resp.get_json()["awards"]

    def test_award_record_has_sam_award_id(self, api_app):
        awards = self._call(api_app)
        assert all("sam_award_id" in a for a in awards)

    def test_award_record_has_awardee_name(self, api_app):
        awards = self._call(api_app)
        assert all("awardee_name" in a for a in awards)

    def test_award_record_has_awarding_agency(self, api_app):
        awards = self._call(api_app)
        assert all("awarding_agency" in a for a in awards)

    def test_award_record_has_piid(self, api_app):
        awards = self._call(api_app)
        assert all("piid" in a for a in awards)


# ---------------------------------------------------------------------------
# Tests: missing q parameter
# ---------------------------------------------------------------------------


class TestSearchSamAwardsMissingQuery:
    """Missing q parameter returns 400 with error status."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_q_returns_400(self, api_app):
        with api_app.test_client() as c:
            resp = _get(c)
        assert resp.status_code == 400

    def test_missing_q_returns_error_status(self, api_app):
        with api_app.test_client() as c:
            resp = _get(c)
        data = resp.get_json()
        assert data.get("status") == "error"


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestSearchSamAwardsEdgeCases:
    """Edge cases: empty results and backend exceptions."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_empty_results_returns_200(self, api_app):
        with patch(
            "tools.govcon.sam_contract_sync.search_awards",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, q=_QUERY)
        assert resp.status_code == 200

    def test_empty_results_returns_empty_list(self, api_app):
        with patch(
            "tools.govcon.sam_contract_sync.search_awards",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, q=_QUERY)
        data = resp.get_json()
        assert data["awards"] == []

    def test_empty_results_total_is_zero(self, api_app):
        with patch(
            "tools.govcon.sam_contract_sync.search_awards",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, q=_QUERY)
        data = resp.get_json()
        assert data["total"] == 0

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.sam_contract_sync.search_awards",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, q=_QUERY)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"
