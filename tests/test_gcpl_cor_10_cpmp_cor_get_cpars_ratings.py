# CUI // SP-CTI
"""Tests for GET /api/cpmp/cor/contracts/<id>/cpars — returns CPARS ratings.

Verifies:
 1. Successful GET returns 200.
 2. Response is JSON.
 3. Response envelope 'status' value is 'ok'.
 4. Response contains 'contract_id' field.
 5. 'contract_id' matches request path parameter.
 6. Response contains 'total' field.
 7. 'total' is an integer.
 8. Response contains 'assessments' field.
 9. 'assessments' is a list.
10. Each assessment contains 'id' field.
11. Each assessment contains 'period_start' field.
12. Each assessment contains 'period_end' field.
13. Each assessment contains 'overall_rating' field.
14. Each assessment contains 'quality_rating' field.
15. Each assessment contains 'schedule_rating' field.
16. 'total' equals the length of 'assessments'.
17. 'subcontractor_pricing' is absent from response.
18. 'subcontractor_cost' is absent from response.
19. 'internal_cost_details' is absent from response.
20. 'internal_notes' is absent from response.
21. 'corrective_action_details' is absent from response.
22. 'billed_value' is absent from response.
23. 'ac_cumulative' is absent from response.
24. Raw JSON string does not contain the key '"internal_notes"'.
25. Raw JSON string does not contain the key '"corrective_action_details"'.
26. COR not assigned to contract returns 403.
27. 403 response includes a 'message' field.
28. Authenticated user with no email returns 400.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-cor-test-0010"
_COR_EMAIL = "cor.officer@agency.mil"

_CPARS_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "total": 2,
    "assessments": [
        {
            "id": "cpars-001",
            "contract_id": _CONTRACT_ID,
            "period_start": "2025-07",
            "period_end": "2025-12",
            "quality_rating": "Exceptional",
            "schedule_rating": "Very Good",
            "cost_rating": "Satisfactory",
            "management_rating": "Very Good",
            "small_business_rating": None,
            "overall_rating": "Very Good",
            "predicted_overall": "very_good",
            "narrative": "Contractor met all major milestones.",
            "negative_event_count": 0,
            "status": "final",
            "created_at": "2026-01-15T00:00:00Z",
            "updated_at": "2026-01-20T00:00:00Z",
            # sensitive fields — must be stripped by _sanitize_for_cor
            "internal_notes": "Flagged for follow-up review.",
            "corrective_action_details": {"plan": "none required"},
            "billed_value": 1_250_000.00,
            "ac_cumulative": 1_180_000.00,
            "internal_cost_details": {"labor": 900_000.00},
        },
        {
            "id": "cpars-002",
            "contract_id": _CONTRACT_ID,
            "period_start": "2025-01",
            "period_end": "2025-06",
            "quality_rating": "Satisfactory",
            "schedule_rating": "Satisfactory",
            "cost_rating": "Satisfactory",
            "management_rating": "Satisfactory",
            "small_business_rating": "Very Good",
            "overall_rating": "Satisfactory",
            "predicted_overall": "satisfactory",
            "narrative": None,
            "negative_event_count": 2,
            "status": "final",
            "created_at": "2025-07-10T00:00:00Z",
            "updated_at": "2025-07-15T00:00:00Z",
            "internal_notes": "Minor schedule slippage noted.",
            "corrective_action_details": None,
            "billed_value": 1_100_000.00,
            "ac_cumulative": 1_050_000.00,
            "internal_cost_details": None,
        },
    ],
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

        g.current_user = {"email": cor_email, "role": role, "id": "user-cor-test-0010"}

    return flask_app


def _mock_db():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(client, contract_id=_CONTRACT_ID):
    return client.get(f"/api/cpmp/cor/contracts/{contract_id}/cpars")


def _call_success(api_app, cpars_result=None):
    """Issue GET with mocks patched for a successful COR CPARS access."""
    result = cpars_result if cpars_result is not None else _CPARS_RESULT
    with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
        with patch("tools.govcon.cpars_predictor.list_assessments", return_value=result):
            with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
                with api_app.test_client() as c:
                    resp = _get(c)
    return resp


# ---------------------------------------------------------------------------
# Tests: HTTP status and format
# ---------------------------------------------------------------------------


class TestCORCparsHTTPResponse:
    """GET /api/cpmp/cor/contracts/<id>/cpars must return 200 with valid JSON."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_200(self, api_app):
        resp = _call_success(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = _call_success(api_app)
        assert resp.content_type.startswith("application/json")


# ---------------------------------------------------------------------------
# Tests: response envelope shape
# ---------------------------------------------------------------------------


class TestCORCparsResponseEnvelope:
    """Response envelope must include status, contract_id, total, and assessments."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_envelope_status_is_ok(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data.get("status") == "ok"

    def test_envelope_has_contract_id(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "contract_id" in data, "CPARS response must include 'contract_id'"

    def test_contract_id_matches_request(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data["contract_id"] == _CONTRACT_ID

    def test_envelope_has_total(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "total" in data, "CPARS response must include 'total'"

    def test_total_is_integer(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert isinstance(data["total"], int), "'total' must be an integer"

    def test_envelope_has_assessments(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "assessments" in data, "CPARS response must include 'assessments'"

    def test_assessments_is_list(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert isinstance(data["assessments"], list), "'assessments' must be a list"

    def test_total_matches_assessments_length(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["assessments"]), (
            "'total' must equal the length of 'assessments'"
        )


# ---------------------------------------------------------------------------
# Tests: assessment record fields
# ---------------------------------------------------------------------------


class TestCORCparsAssessmentFields:
    """Each assessment record must contain required CPARS rating fields."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_each_assessment_has_id(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data["assessments"]:
            assert "id" in assessment, "Each assessment must have an 'id' field"

    def test_each_assessment_has_period_start(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data["assessments"]:
            assert "period_start" in assessment, "Each assessment must have 'period_start'"

    def test_each_assessment_has_period_end(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data["assessments"]:
            assert "period_end" in assessment, "Each assessment must have 'period_end'"

    def test_each_assessment_has_overall_rating(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data["assessments"]:
            assert "overall_rating" in assessment, "Each assessment must have 'overall_rating'"

    def test_each_assessment_has_quality_rating(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data["assessments"]:
            assert "quality_rating" in assessment, "Each assessment must have 'quality_rating'"

    def test_each_assessment_has_schedule_rating(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data["assessments"]:
            assert "schedule_rating" in assessment, "Each assessment must have 'schedule_rating'"


# ---------------------------------------------------------------------------
# Tests: sanitization of hidden fields
# ---------------------------------------------------------------------------


class TestCORCparsSanitization:
    """_sanitize_for_cor must strip COR_HIDDEN_FIELDS from CPARS data."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_subcontractor_pricing_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "subcontractor_pricing" not in assessment, (
                "'subcontractor_pricing' must be stripped from COR CPARS response"
            )

    def test_subcontractor_cost_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "subcontractor_cost" not in assessment, (
                "'subcontractor_cost' must be stripped from COR CPARS response"
            )

    def test_internal_cost_details_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "internal_cost_details" not in assessment, (
                "'internal_cost_details' must be stripped from COR CPARS response"
            )

    def test_internal_notes_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "internal_notes" not in assessment, (
                "'internal_notes' must be stripped from COR CPARS response"
            )

    def test_corrective_action_details_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "corrective_action_details" not in assessment, (
                "'corrective_action_details' must be stripped from COR CPARS response"
            )

    def test_billed_value_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "billed_value" not in assessment, (
                "'billed_value' must be stripped from COR CPARS response"
            )

    def test_ac_cumulative_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        for assessment in data.get("assessments", []):
            assert "ac_cumulative" not in assessment, (
                "'ac_cumulative' must be stripped from COR CPARS response"
            )

    def test_raw_json_lacks_internal_notes_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"internal_notes"' not in raw, (
            "Raw JSON must not contain the key '\"internal_notes\"'"
        )

    def test_raw_json_lacks_corrective_action_details_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"corrective_action_details"' not in raw, (
            "Raw JSON must not contain the key '\"corrective_action_details\"'"
        )


# ---------------------------------------------------------------------------
# Tests: access control
# ---------------------------------------------------------------------------


class TestCORCparsAccessControl:
    """Access-control gates must fire before CPARS data is fetched."""

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
