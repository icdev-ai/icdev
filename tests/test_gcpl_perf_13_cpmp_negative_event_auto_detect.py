# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/negative-events/auto-detect — overdue deliverable detection.

Verifies:
1. Successful POST returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'total_new_events' key.
5. Response body contains 'contract_id' key.
6. Response body contains 'detections' key.
7. detections contains 'delinquent_delivery' sub-result.
8. detections contains 'cost_overrun' sub-result.
9. detections contains 'quality_rejection' sub-result.
10. detections contains 'flowdown_failure' sub-result.
11. contract_id in response matches path parameter.
12. total_new_events equals sum of new_events across all detections.
13. Each detection sub-result contains 'status' == 'ok'.
14. Each detection sub-result contains 'new_events' key.
15. total_new_events is non-negative integer.
16. auto_detect_all called with correct contract_id.
17. Backend exception returns 500 with error status.
18. total_new_events reflects non-zero detections when overdue deliverables exist.
19. detections delinquent_delivery new_events is non-negative.
20. Response is 200 even when no new events detected (idempotent scan).
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0013"

_DETECT_RESULT_SOME = {
    "status": "ok",
    "total_new_events": 3,
    "contract_id": _CONTRACT_ID,
    "detections": {
        "delinquent_delivery": {"status": "ok", "new_events": 2, "event_type": "delinquent_delivery"},
        "cost_overrun": {"status": "ok", "new_events": 0, "event_type": "cost_overrun"},
        "quality_rejection": {"status": "ok", "new_events": 1, "event_type": "quality_rejection"},
        "flowdown_failure": {"status": "ok", "new_events": 0, "event_type": "flowdown_failure"},
    },
}

_DETECT_RESULT_NONE = {
    "status": "ok",
    "total_new_events": 0,
    "contract_id": _CONTRACT_ID,
    "detections": {
        "delinquent_delivery": {"status": "ok", "new_events": 0, "event_type": "delinquent_delivery"},
        "cost_overrun": {"status": "ok", "new_events": 0, "event_type": "cost_overrun"},
        "quality_rejection": {"status": "ok", "new_events": 0, "event_type": "quality_rejection"},
        "flowdown_failure": {"status": "ok", "new_events": 0, "event_type": "flowdown_failure"},
    },
}

_DETECT_RESULT_OVERDUE = {
    "status": "ok",
    "total_new_events": 5,
    "contract_id": _CONTRACT_ID,
    "detections": {
        "delinquent_delivery": {"status": "ok", "new_events": 5, "event_type": "delinquent_delivery"},
        "cost_overrun": {"status": "ok", "new_events": 0, "event_type": "cost_overrun"},
        "quality_rejection": {"status": "ok", "new_events": 0, "event_type": "quality_rejection"},
        "flowdown_failure": {"status": "ok", "new_events": 0, "event_type": "flowdown_failure"},
    },
}

_DETECTION_KEYS = ("delinquent_delivery", "cost_overrun", "quality_rejection", "flowdown_failure")


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


def _post(client, contract_id):
    return client.post(
        f"/api/cpmp/contracts/{contract_id}/negative-events/auto-detect",
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: successful detection
# ---------------------------------------------------------------------------


class TestPostAutoDetectSuccess:
    """POST auto-detect with overdue deliverables returns 200 with detection summary."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, detect_result=None):
        if detect_result is None:
            detect_result = _DETECT_RESULT_SOME
        with patch(
            "tools.govcon.negative_event_tracker.auto_detect_all",
            return_value=detect_result,
        ) as mock_detect:
            with api_app.test_client() as c:
                resp = _post(c, contract_id)
        return resp, mock_detect

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

    def test_response_has_total_new_events(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_new_events" in data

    def test_response_has_contract_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "contract_id" in data

    def test_response_has_detections(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "detections" in data

    def test_detections_has_delinquent_delivery(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "delinquent_delivery" in data["detections"]

    def test_detections_has_cost_overrun(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "cost_overrun" in data["detections"]

    def test_detections_has_quality_rejection(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "quality_rejection" in data["detections"]

    def test_detections_has_flowdown_failure(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "flowdown_failure" in data["detections"]

    def test_contract_id_matches_path_parameter(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["contract_id"] == _CONTRACT_ID

    def test_total_new_events_equals_sum_of_detections(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        expected = sum(
            data["detections"][k].get("new_events", 0) for k in _DETECTION_KEYS
        )
        assert data["total_new_events"] == expected

    def test_each_detection_has_ok_status(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for key in _DETECTION_KEYS:
            assert data["detections"][key]["status"] == "ok", f"{key} status not ok"

    def test_each_detection_has_new_events_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for key in _DETECTION_KEYS:
            assert "new_events" in data["detections"][key], f"{key} missing new_events"

    def test_total_new_events_is_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total_new_events"] >= 0

    def test_auto_detect_all_called_with_contract_id(self, api_app):
        _, mock_detect = self._call(api_app)
        args, _ = mock_detect.call_args
        assert args[0] == _CONTRACT_ID

    def test_total_new_events_nonzero_when_overdue_deliverables_exist(self, api_app):
        resp, _ = self._call(api_app, detect_result=_DETECT_RESULT_OVERDUE)
        data = resp.get_json()
        assert data["total_new_events"] > 0

    def test_delinquent_delivery_new_events_is_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["detections"]["delinquent_delivery"]["new_events"] >= 0


# ---------------------------------------------------------------------------
# Tests: idempotent scan — no new events
# ---------------------------------------------------------------------------


class TestPostAutoDetectNoNewEvents:
    """POST returns 200 even when no new events are detected (idempotent scan)."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_200_when_no_new_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.auto_detect_all",
            return_value=_DETECT_RESULT_NONE,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_total_new_events_zero_when_no_overdue(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.auto_detect_all",
            return_value=_DETECT_RESULT_NONE,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["total_new_events"] == 0

    def test_status_ok_when_no_new_events(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.auto_detect_all",
            return_value=_DETECT_RESULT_NONE,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Tests: edge cases — backend exception
# ---------------------------------------------------------------------------


class TestPostAutoDetectEdgeCases:
    """Backend exception returns 500 with error status."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.auto_detect_all",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID)
        assert resp.status_code == 500

    def test_backend_exception_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.auto_detect_all",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"
