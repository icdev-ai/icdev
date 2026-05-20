# CUI // SP-CTI
"""Tests for GET /api/cpmp/cor/contracts — unmatched COR email returns 200 with empty array.

Verifies:
 1. Non-matched COR email returns HTTP 200 (not 404 or 500).
 2. Response Content-Type is application/json.
 3. Response is not HTTP 404.
 4. Response is not HTTP 500.
 5. Response is not HTTP 403.
 6. Response body has a "status" key.
 7. Response status value is "ok".
 8. Response body has a "contracts" key.
 9. The "contracts" value is a list.
10. The "contracts" list is empty.
11. Response body has a "total" key.
12. The "total" value is 0.
13. "total" matches len(contracts).
14. Response body does not contain an "error" status.
15. Response body does not contain a top-level "message" key indicating failure.
16. Random UUID email returns 200 with empty contracts.
17. gov.mil-domain email with no match returns 200 with empty contracts.
18. Contractor-domain email with no match returns 200 with empty contracts.
19. contracts is not None.
20. contracts list is iterable with zero items.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UNMATCHED_EMAIL = "nobody.assigned@no-match.example"
_GOVMIL_EMAIL = "unregistered.cor@agency.mil"
_CONTRACTOR_EMAIL = "ghost.officer@contractor.com"
_UUID_EMAIL = "a1b2c3d4-0000-0000-0000-000000000000@example.invalid"

# ---------------------------------------------------------------------------
# Flask test app
# ---------------------------------------------------------------------------


def _build_app(cor_email=_UNMATCHED_EMAIL, role="cor") -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(cpmp_api)

    @flask_app.before_request
    def _inject_user():
        from flask import g

        g.current_user = {"email": cor_email, "role": role, "id": "user-cor-test-0013"}

    return flask_app


def _mock_db():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    return conn


def _get_empty(api_app):
    """GET /api/cpmp/cor/contracts with _get_cor_contracts returning []."""
    with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[]):
        with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
            with api_app.test_client() as c:
                return c.get("/api/cpmp/cor/contracts")


# ---------------------------------------------------------------------------
# Tests: HTTP status behaviour
# ---------------------------------------------------------------------------


class TestCORUnmatchedEmailHTTP:
    """Unmatched COR email must not raise an error — it simply has no contracts."""

    @pytest.fixture()
    def api_app(self):
        return _build_app()

    def test_unmatched_email_returns_200(self, api_app):
        resp = _get_empty(api_app)
        assert resp.status_code == 200, (
            "Unmatched COR email must return 200, not an error status"
        )

    def test_response_content_type_is_json(self, api_app):
        resp = _get_empty(api_app)
        assert resp.content_type.startswith("application/json"), (
            "Response must be application/json"
        )

    def test_is_not_404(self, api_app):
        resp = _get_empty(api_app)
        assert resp.status_code != 404, (
            "Unmatched email must not produce a 404 — an empty result set is valid"
        )

    def test_is_not_500(self, api_app):
        resp = _get_empty(api_app)
        assert resp.status_code != 500, (
            "Unmatched email must not cause a server error"
        )

    def test_is_not_403(self, api_app):
        resp = _get_empty(api_app)
        assert resp.status_code != 403, (
            "Having no assigned contracts must not be treated as an access-denied condition"
        )


# ---------------------------------------------------------------------------
# Tests: response body shape
# ---------------------------------------------------------------------------


class TestCORUnmatchedEmailResponseShape:
    """Response body must describe a valid empty result, not an error."""

    @pytest.fixture()
    def api_app(self):
        return _build_app()

    def test_response_has_status_key(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert "status" in data, "Response must include a 'status' key"

    def test_response_status_is_ok(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert data["status"] == "ok", (
            "status must be 'ok' for an empty result — not 'error' or any other value"
        )

    def test_response_has_contracts_key(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert "contracts" in data, "Response must include a 'contracts' key"

    def test_contracts_is_list(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert isinstance(data["contracts"], list), (
            "contracts must be a JSON array, not null or an object"
        )

    def test_contracts_is_empty(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert data["contracts"] == [], (
            "contracts must be an empty array when no contracts match the COR email"
        )

    def test_response_has_total_key(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert "total" in data, "Response must include a 'total' key"

    def test_total_is_zero(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert data["total"] == 0, (
            "total must be 0 when no contracts match the COR email"
        )

    def test_total_matches_contracts_length(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["contracts"]), (
            "total must equal len(contracts)"
        )

    def test_status_is_not_error(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert data.get("status") != "error", (
            "An empty contract list must not be surfaced as an error status"
        )

    def test_no_failure_message_key(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert "message" not in data, (
            "A successful empty-result response must not contain a 'message' error key"
        )

    def test_contracts_is_not_none(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert data["contracts"] is not None, (
            "contracts must be an empty list, not null"
        )

    def test_contracts_length_is_zero(self, api_app):
        resp = _get_empty(api_app)
        data = resp.get_json()
        assert len(data["contracts"]) == 0, (
            "contracts list must have zero items for an unmatched COR email"
        )


# ---------------------------------------------------------------------------
# Tests: different unmatched email variants
# ---------------------------------------------------------------------------


class TestCORUnmatchedEmailVariants:
    """Multiple distinct non-matching emails must all yield 200 with empty contracts."""

    def _check(self, email):
        app = _build_app(cor_email=email)
        resp = _get_empty(app)
        return resp

    def test_random_uuid_email_returns_200(self):
        resp = self._check(_UUID_EMAIL)
        assert resp.status_code == 200, (
            "UUID-format unmatched email must return 200"
        )

    def test_govmil_unmatched_returns_200(self):
        resp = self._check(_GOVMIL_EMAIL)
        assert resp.status_code == 200, (
            "gov.mil-domain unmatched email must return 200"
        )

    def test_contractor_domain_unmatched_returns_200(self):
        resp = self._check(_CONTRACTOR_EMAIL)
        assert resp.status_code == 200, (
            "Contractor-domain unmatched email must return 200"
        )

    def test_random_uuid_email_contracts_empty(self):
        resp = self._check(_UUID_EMAIL)
        data = resp.get_json()
        assert data["contracts"] == [], (
            "UUID-format unmatched email must produce empty contracts array"
        )

    def test_govmil_unmatched_total_is_zero(self):
        resp = self._check(_GOVMIL_EMAIL)
        data = resp.get_json()
        assert data["total"] == 0, (
            "gov.mil-domain unmatched email must produce total == 0"
        )

    def test_contractor_domain_contracts_empty(self):
        resp = self._check(_CONTRACTOR_EMAIL)
        data = resp.get_json()
        assert data["contracts"] == [], (
            "Contractor-domain unmatched email must produce empty contracts array"
        )

    def test_status_ok_across_variants(self):
        for email in (_UNMATCHED_EMAIL, _GOVMIL_EMAIL, _UUID_EMAIL):
            resp = self._check(email)
            data = resp.get_json()
            assert data["status"] == "ok", (
                f"status must be 'ok' for unmatched email {email!r}"
            )
