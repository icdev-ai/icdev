# CUI // SP-CTI
"""RED-phase TDD tests for ABAC enforcement on the cross-agency transfer API.

Story: Given a cleared analyst at a partner agency, when they request a
cross-domain data pull, the MCIP system must enforce ABAC with mandatory
classification markings on all returned data objects.

Tests target POST /transfers — verifying that:
  - Cleared analysts with correct entitlements + clearance are permitted.
  - Uncleared users are denied (HTTP 403) and receive no data objects.
  - Permitted responses include mandatory classification_marking on every object.
  - Subject attributes may be supplied in the request body under ``subject``.
  - Resource attributes are derived from the transfer body (classification, compartments).

NIST 800-53: AC-3, AC-4, AC-4(4), AU-2, SC-7(5).
"""

from __future__ import annotations

import json
import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# App fixture — minimal Flask app with only the blueprint under test
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Minimal Flask app registering the cross-agency transfer blueprint."""
    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    from tools.dashboard.api.cross_agency_transfer import cross_agency_transfer_api
    flask_app.register_blueprint(cross_agency_transfer_api, url_prefix="/api")

    return flask_app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Request body helpers
# ---------------------------------------------------------------------------

def _cleared_analyst_subject():
    return {
        "user_id": "analyst-001",
        "role": "cleared_analyst",
        "agency": "partner_agency",
        "clearance_level": 3,       # SECRET (scale: PUBLIC=0,CUI=1,ECI=2,SECRET=3)
        "compartments": ["COI_INTEL"],
        "entitlements": ["cross_domain_pull"],
    }


def _uncleared_user_subject():
    return {
        "user_id": "user-002",
        "role": "viewer",
        "agency": "internal",
        "clearance_level": 1,       # CUI only
        "compartments": [],
        "entitlements": [],
    }


def _base_transfer_body(**kwargs):
    body = {
        "transfer_id": "xfr-001",
        "source_agency": "IC_PARTNER",
        "target_agency": "MCIP",
        "data_type": "intel_report",
        "actor": "analyst-001",
        "data_classification": "SECRET",
        "compartments": ["COI_INTEL"],
        "data_objects": [
            {"id": "rec-001", "content": "Operational briefing A"},
            {"id": "rec-002", "content": "Mission summary B"},
        ],
    }
    body.update(kwargs)
    return body


# ---------------------------------------------------------------------------
# TestABACEnforcement — integration tests for the API endpoint
# ---------------------------------------------------------------------------

class TestABACEnforcement:
    def test_cleared_analyst_gets_200(self, client):
        """A cleared analyst with correct subject attributes must receive HTTP 200."""
        body = _base_transfer_body(subject=_cleared_analyst_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_uncleared_user_gets_403(self, client):
        """An uncleared user must be denied with HTTP 403."""
        body = _base_transfer_body(subject=_uncleared_user_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_denied_response_contains_no_data_objects(self, client):
        """A denied transfer response must not include any data objects."""
        body = _base_transfer_body(subject=_uncleared_user_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        data = resp.get_json()
        assert resp.status_code == 403
        assert data.get("data_objects", []) == []

    def test_denied_response_includes_reason(self, client):
        """A denied transfer response must include a reason field."""
        body = _base_transfer_body(subject=_uncleared_user_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "reason" in data or "error" in data

    def test_permitted_response_data_objects_have_classification_marking(self, client):
        """Every data object in a permitted response must carry classification_marking."""
        body = _base_transfer_body(subject=_cleared_analyst_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "data_objects" in data, "Permitted response must include data_objects"
        for obj in data["data_objects"]:
            assert "classification_marking" in obj, (
                f"Object {obj.get('id')} missing mandatory classification_marking"
            )

    def test_classification_marking_matches_resource_classification(self, client):
        """classification_marking on each returned object must equal the resource classification."""
        body = _base_transfer_body(
            subject=_cleared_analyst_subject(),
            data_classification="SECRET",
        )
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        for obj in data["data_objects"]:
            assert obj["classification_marking"] == "SECRET"

    def test_no_subject_treated_as_uncleared(self, client):
        """A request with no subject field must be denied (treated as uncleared)."""
        body = _base_transfer_body()
        # Remove subject if present
        body.pop("subject", None)
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_missing_entitlement_denied(self, client):
        """Subject with clearance but missing cross_domain_pull entitlement must be denied."""
        subject = _cleared_analyst_subject()
        subject["entitlements"] = []     # strip entitlement
        body = _base_transfer_body(subject=subject)
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_insufficient_clearance_denied(self, client):
        """Subject with entitlement but CUI-only clearance must be denied for SECRET resource."""
        subject = _cleared_analyst_subject()
        subject["clearance_level"] = 1    # CUI, resource needs SECRET=2
        body = _base_transfer_body(subject=subject, data_classification="SECRET")
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_missing_compartment_denied(self, client):
        """Subject lacking required compartment must be denied."""
        subject = _cleared_analyst_subject()
        subject["compartments"] = []     # missing COI_INTEL required by resource
        body = _base_transfer_body(
            subject=subject,
            compartments=["COI_INTEL"],
        )
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_empty_data_objects_permitted_returns_empty_list(self, client):
        """A permitted transfer with no data_objects returns empty marked list."""
        body = _base_transfer_body(subject=_cleared_analyst_subject(), data_objects=[])
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("data_objects") == []

    def test_permitted_response_includes_success_true(self, client):
        """Permitted transfer response must include success=True."""
        body = _base_transfer_body(subject=_cleared_analyst_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("success") is True

    def test_permitted_response_includes_abac_permit_true(self, client):
        """Permitted transfer response must include abac_permitted=True."""
        body = _base_transfer_body(subject=_cleared_analyst_subject())
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data.get("abac_permitted") is True

    def test_original_fields_preserved_in_marked_objects(self, client):
        """Mandatory classification marking must not alter any original data fields."""
        data_objects = [{"id": "rec-001", "content": "Briefing", "sensitivity": "high"}]
        body = _base_transfer_body(
            subject=_cleared_analyst_subject(),
            data_objects=data_objects,
        )
        resp = client.post(
            "/api/transfers",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert resp.status_code == 200
        result_objs = resp.get_json()["data_objects"]
        assert len(result_objs) == 1
        obj = result_objs[0]
        assert obj["id"] == "rec-001"
        assert obj["content"] == "Briefing"
        assert obj["sensitivity"] == "high"
        assert obj["classification_marking"] == "SECRET"
