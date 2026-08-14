# CUI // SP-CTI
"""Tests for POST /api/govcon/opportunities/<id>/auto-compliance — direct API returns 200.

Verifies:
1. POST returns 200 with JSON on success.
2. Response includes required keys: status, matrix, L_compliant, M_partial, N_gap,
   compliance_rate, compliance_items_created.
3. Grade counts are internally consistent (L + M + N == total_requirements).
4. Endpoint handles arbitrary opportunity IDs.
5. Non-ok populate result (no requirements) still returns 200.
6. Endpoint returns 500 + error key when populate_compliance_matrix raises.
7. The endpoint is RBAC-gated: 401 unauthenticated, 403 for a non-write role,
   and a denied caller reaches neither the populator nor the database.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests._govcon_api_app import NON_WRITE_ROLE, WRITE_ROLE, build_govcon_api_app

# ---------------------------------------------------------------------------
# Fake return values from tools.govcon.compliance_populator.populate_compliance_matrix
# ---------------------------------------------------------------------------

_OPP_ID = "opp-compliance-test-01"

_FAKE_POPULATE_RESULT = {
    "status": "ok",
    "opportunity_id": _OPP_ID,
    "total_requirements": 3,
    "L_compliant": 1,
    "M_partial": 1,
    "N_gap": 1,
    "compliance_rate": 0.3333,
    "populated_items": 2,
    "matrix": [
        {
            "shall_id": 1,
            "statement": "The system shall support ATO boundary monitoring.",
            "domain": "devsecops",
            "statement_type": "functional",
            "best_capability": "DevSecOps Pipeline",
            "best_capability_id": "cap-001",
            "coverage_score": 0.92,
            "grade": "L",
            "evidence": "CI/CD gates enforce ATO boundaries at every deployment.",
        },
        {
            "shall_id": 2,
            "statement": "The system shall provide multi-factor authentication.",
            "domain": "security",
            "statement_type": "functional",
            "best_capability": "IAM Integration",
            "best_capability_id": "cap-002",
            "coverage_score": 0.55,
            "grade": "M",
            "evidence": "MFA enforced via SAML2/OIDC for all user roles.",
        },
        {
            "shall_id": 3,
            "statement": "The system shall generate weekly executive dashboards.",
            "domain": "reporting",
            "statement_type": "functional",
            "best_capability": "Dashboard Engine",
            "best_capability_id": "cap-003",
            "coverage_score": 0.25,
            "grade": "N",
            "evidence": "",
        },
    ],
}

_FAKE_POPULATE_NO_REQS = {
    "status": "no_requirements",
    "opportunity_id": "opp-empty-01",
    "message": "No shall statements found for this opportunity.",
}


# ---------------------------------------------------------------------------
# Mock DB connection helper
# ---------------------------------------------------------------------------


def _make_mock_conn():
    mock_conn = MagicMock()
    # Simulate no existing compliance items → INSERT path taken for each matrix item
    mock_conn.execute.return_value.fetchone.return_value = None
    return mock_conn


# ---------------------------------------------------------------------------
# API test helper: minimal Flask app with govcon_api blueprint only
# ---------------------------------------------------------------------------


def _build_api_test_app(role=WRITE_ROLE):
    """App carrying the real blueprint, with an authenticated caller.

    The endpoint is ``@require_role(*GOVCON_WRITE_ROLES)`` (prop-fix-09); an app
    with no ``g.current_user`` answers 401 to every one of these assertions. The
    gate is authenticated past, never stripped — see ``TestAutoComplianceRBAC``.
    """
    return build_govcon_api_app(role=role)


# ---------------------------------------------------------------------------
# API tests: POST /api/govcon/opportunities/<id>/auto-compliance
# ---------------------------------------------------------------------------


class TestAutoComplianceAPIEndpoint:
    """POST /api/govcon/opportunities/<id>/auto-compliance returns 200 on success."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _post(self, api_app, opp_id=_OPP_ID, result=None):
        if result is None:
            result = _FAKE_POPULATE_RESULT
        mock_conn = _make_mock_conn()
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            return_value=result,
        ):
            with patch("tools.dashboard.api.govcon._get_db", return_value=mock_conn):
                with api_app.test_client() as c:
                    resp = c.post(f"/api/govcon/opportunities/{opp_id}/auto-compliance")
        return resp

    def test_post_returns_200(self, api_app):
        resp = self._post(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._post(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_matrix_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "matrix" in data

    def test_response_has_l_compliant_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "L_compliant" in data

    def test_response_has_m_partial_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "M_partial" in data

    def test_response_has_n_gap_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "N_gap" in data

    def test_response_has_compliance_rate_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "compliance_rate" in data

    def test_response_has_compliance_items_created_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "compliance_items_created" in data, (
            "Endpoint must add compliance_items_created after batch-inserting matrix items"
        )

    def test_l_compliant_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["L_compliant"] == _FAKE_POPULATE_RESULT["L_compliant"]

    def test_m_partial_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["M_partial"] == _FAKE_POPULATE_RESULT["M_partial"]

    def test_n_gap_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["N_gap"] == _FAKE_POPULATE_RESULT["N_gap"]

    def test_grade_counts_sum_to_total_requirements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        total = data["L_compliant"] + data["M_partial"] + data["N_gap"]
        assert total == _FAKE_POPULATE_RESULT["total_requirements"]

    def test_matrix_length_matches_total_requirements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert len(data["matrix"]) == _FAKE_POPULATE_RESULT["total_requirements"]

    def test_compliance_items_created_equals_matrix_length(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        # All items new (mock returns no existing) → created count == matrix length
        assert data["compliance_items_created"] == len(data["matrix"])

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opp-xyz"):
            resp = self._post(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id}"

    def test_no_requirements_returns_200(self, api_app):
        resp = self._post(api_app, result=_FAKE_POPULATE_NO_REQS)
        assert resp.status_code == 200

    def test_no_requirements_status_in_response(self, api_app):
        resp = self._post(api_app, result=_FAKE_POPULATE_NO_REQS)
        data = resp.get_json()
        assert data["status"] == "no_requirements"

    def test_returns_500_when_populate_raises(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            side_effect=RuntimeError("compliance populator offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-compliance")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            side_effect=RuntimeError("compliance populator offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-compliance")
        data = resp.get_json()
        assert "error" in data

    def test_500_error_message_is_non_empty(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            side_effect=RuntimeError("compliance populator offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-compliance")
        data = resp.get_json()
        assert data["error"], "Error message must not be empty"


# ---------------------------------------------------------------------------
# RBAC: the gate that made every assertion above answer 401 (prop-fix-09)
# ---------------------------------------------------------------------------


class TestAutoComplianceRBAC:
    """auto-compliance is @require_role(*GOVCON_WRITE_ROLES).

    Auto-population INSERTs into proposal_compliance_matrix, so it is a write
    endpoint. These assertions exist so the gate can never be silently removed
    to make the success-path tests above go green again.
    """

    def _post(self, app):
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            return_value=_FAKE_POPULATE_RESULT,
        ):
            with patch("tools.dashboard.api.govcon._get_db", return_value=_make_mock_conn()):
                with app.test_client() as c:
                    return c.post(f"/api/govcon/opportunities/{_OPP_ID}/auto-compliance")

    def test_write_role_is_a_govcon_write_role(self):
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        assert WRITE_ROLE in GOVCON_WRITE_ROLES
        assert NON_WRITE_ROLE not in GOVCON_WRITE_ROLES

    def test_unauthenticated_is_401(self):
        resp = self._post(build_govcon_api_app(role=None))
        assert resp.status_code == 401, (
            "auto-compliance must reject an unauthenticated caller with 401"
        )

    def test_non_write_role_is_403(self):
        resp = self._post(build_govcon_api_app(role=NON_WRITE_ROLE))
        assert resp.status_code == 403, (
            f"auto-compliance must deny role '{NON_WRITE_ROLE}' with 403, not "
            f"{resp.status_code}"
        )

    def test_every_write_role_is_allowed(self):
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        for role in GOVCON_WRITE_ROLES:
            resp = self._post(build_govcon_api_app(role=role))
            assert resp.status_code == 200, f"role '{role}' should be allowed"

    def test_denied_caller_never_reaches_the_populator(self):
        """The gate must deny BEFORE the endpoint body runs.

        A 403 that still populated the matrix would leave the write done and
        only the response withheld — the denial would be cosmetic.
        """
        populate = MagicMock(return_value=_FAKE_POPULATE_RESULT)
        db = MagicMock(return_value=_make_mock_conn())
        with patch("tools.govcon.compliance_populator.populate_compliance_matrix", populate):
            with patch("tools.dashboard.api.govcon._get_db", db):
                with build_govcon_api_app(role=NON_WRITE_ROLE).test_client() as c:
                    resp = c.post(f"/api/govcon/opportunities/{_OPP_ID}/auto-compliance")
        assert resp.status_code == 403
        assert not populate.called, "a denied caller must not run the compliance populator"
        assert not db.called, "a denied caller must not open a database connection"
