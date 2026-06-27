# CUI // SP-CTI
"""Tests for tools/network/federal_peering_request.py

Federal Network Peering Agreement — Step 1: Peering Request Initiation.
Uses SQLite in-memory DB so tests are self-contained and never touch PG.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.network.federal_peering_request import (
    CLASSIFICATION,
    PEERING_TYPES,
    REQUEST_STATUSES,
    STEP_NAME,
    STEP_NUMBER,
    WORKFLOW_ID,
    get_federal_peering_request,
    initiate_peering_request,
    list_federal_peering_requests,
    update_request_status,
)

# Also need PMC tables for the PMC-sync path
from tools.pmc_canvas.db.init_db import SCHEMA_SQLITE as PMC_SQLITE_SCHEMA


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite with PMC + federal tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")  # avoid FK issues in test isolation
    # PMC tables (peering_peers, peering_requests, etc.)
    conn.executescript(PMC_SQLITE_SCHEMA)
    conn.commit()
    yield conn
    conn.close()


def _minimal_request(db, **overrides):
    """Helper: create a peering request with defaults, return result dict."""
    kwargs = dict(
        conn=db,
        initiating_org="Agency Alpha Network",
        initiating_asn=64512,
        responding_org="Agency Beta Network",
        peering_type="public_bilateral",
    )
    kwargs.update(overrides)
    return initiate_peering_request(**kwargs)


# ── Happy-path tests ───────────────────────────────────────────────────────────

class TestInitiatePeeringRequest:
    def test_returns_dict_with_required_keys(self, db):
        result = _minimal_request(db)
        for key in ("id", "workflow_id", "step_number", "step_name", "status",
                    "initiating_org", "initiating_asn", "responding_org",
                    "interconnection_doc", "created_at"):
            assert key in result, f"Missing key: {key}"

    def test_status_is_initiated(self, db):
        result = _minimal_request(db)
        assert result["status"] == "initiated"

    def test_step_metadata_correct(self, db):
        result = _minimal_request(db)
        assert result["step_number"] == STEP_NUMBER
        assert result["step_name"] == STEP_NAME
        assert result["workflow_id"] == WORKFLOW_ID

    def test_doc_contains_cui_marking(self, db):
        result = _minimal_request(db)
        assert "CUI" in result["interconnection_doc"]
        assert "SP-CTI" in result["interconnection_doc"]

    def test_doc_contains_both_orgs(self, db):
        result = _minimal_request(db)
        doc = result["interconnection_doc"]
        assert "Agency Alpha Network" in doc
        assert "Agency Beta Network" in doc

    def test_doc_contains_asn(self, db):
        result = _minimal_request(db)
        assert "AS64512" in result["interconnection_doc"]

    def test_doc_contains_section_headers(self, db):
        result = _minimal_request(db)
        doc = result["interconnection_doc"]
        assert "INITIATING PARTY" in doc
        assert "RESPONDING PARTY" in doc
        assert "PROPOSED INTERCONNECTION" in doc
        assert "MISSION JUSTIFICATION" in doc
        assert "STANDARD REQUIREMENTS" in doc

    def test_doc_contains_next_step_reference(self, db):
        result = _minimal_request(db)
        assert "Step 2" in result["interconnection_doc"]

    def test_cage_code_in_doc(self, db):
        result = _minimal_request(db, initiating_cage_code="1ABC2")
        assert "1ABC2" in result["interconnection_doc"]

    def test_agency_name_in_doc(self, db):
        result = _minimal_request(db, initiating_agency="Department of Defense")
        assert "Department of Defense" in result["interconnection_doc"]

    def test_mission_justification_in_doc(self, db):
        result = _minimal_request(
            db, mission_justification="Redundant path for classified payload delivery."
        )
        assert "classified payload delivery" in result["interconnection_doc"]

    def test_interconnection_points_in_doc(self, db):
        points = [{"name": "LINX", "location": "London, UK", "type": "ix"}]
        result = _minimal_request(db, proposed_interconnection_points=points)
        assert "LINX" in result["interconnection_doc"]

    def test_responding_asn_in_doc(self, db):
        result = _minimal_request(db, responding_asn=64513)
        assert "AS64513" in result["interconnection_doc"]

    def test_record_persisted_in_db(self, db):
        result = _minimal_request(db)
        retrieved = get_federal_peering_request(db, result["id"])
        assert retrieved is not None
        assert retrieved["id"] == result["id"]
        assert retrieved["initiating_org"] == "Agency Alpha Network"

    def test_pmc_request_created(self, db):
        result = _minimal_request(db, responding_asn=64513)
        # PMC request ID should be populated when PMC tables exist
        assert result.get("pmc_request_id"), "Expected pmc_request_id to be set"

    def test_all_peering_types_accepted(self, db):
        for pt in PEERING_TYPES:
            result = initiate_peering_request(
                conn=db,
                initiating_org=f"Org_{pt}",
                initiating_asn=64520,
                responding_org="Org_B",
                peering_type=pt,
            )
            assert result["status"] == "initiated"


class TestValidation:
    def test_missing_initiating_org_raises(self, db):
        with pytest.raises(ValueError, match="initiating_org"):
            initiate_peering_request(
                conn=db,
                initiating_org="   ",
                initiating_asn=64512,
                responding_org="Org B",
            )

    def test_missing_responding_org_raises(self, db):
        with pytest.raises(ValueError, match="responding_org"):
            initiate_peering_request(
                conn=db,
                initiating_org="Org A",
                initiating_asn=64512,
                responding_org="",
            )

    def test_invalid_peering_type_raises(self, db):
        with pytest.raises(ValueError, match="peering_type"):
            initiate_peering_request(
                conn=db,
                initiating_org="Org A",
                initiating_asn=64512,
                responding_org="Org B",
                peering_type="invalid_type",
            )


class TestGetRequest:
    def test_get_existing(self, db):
        r = _minimal_request(db)
        got = get_federal_peering_request(db, r["id"])
        assert got is not None
        assert got["id"] == r["id"]

    def test_get_nonexistent_returns_none(self, db):
        assert get_federal_peering_request(db, "does-not-exist") is None

    def test_interconnection_points_deserialized(self, db):
        points = [{"name": "DE-CIX", "location": "Frankfurt", "type": "ix"}]
        r = _minimal_request(db, proposed_interconnection_points=points)
        got = get_federal_peering_request(db, r["id"])
        assert isinstance(got["proposed_interconnection_points"], list)
        assert got["proposed_interconnection_points"][0]["name"] == "DE-CIX"


class TestListRequests:
    def test_list_all(self, db):
        _minimal_request(db)
        _minimal_request(db, initiating_org="Org X", initiating_asn=65000)
        rows = list_federal_peering_requests(db)
        assert len(rows) >= 2

    def test_filter_by_workflow_id(self, db):
        _minimal_request(db)
        rows = list_federal_peering_requests(db, workflow_id=WORKFLOW_ID)
        assert all(r["workflow_id"] == WORKFLOW_ID for r in rows)

    def test_filter_by_status(self, db):
        _minimal_request(db)
        rows = list_federal_peering_requests(db, status="initiated")
        assert all(r["status"] == "initiated" for r in rows)

    def test_filter_unknown_status_returns_empty(self, db):
        _minimal_request(db)
        rows = list_federal_peering_requests(db, status="nonexistent")
        assert rows == []


class TestUpdateStatus:
    def test_update_to_valid_status(self, db):
        r = _minimal_request(db)
        result = update_request_status(db, r["id"], "sent")
        assert result["status"] == "sent"
        # Verify in DB
        got = get_federal_peering_request(db, r["id"])
        assert got["status"] == "sent"

    def test_all_valid_statuses_accepted(self, db):
        for status in REQUEST_STATUSES:
            r = _minimal_request(db, initiating_asn=64512 + REQUEST_STATUSES.index(status))
            update_request_status(db, r["id"], status)

    def test_invalid_status_raises(self, db):
        r = _minimal_request(db)
        with pytest.raises(ValueError, match="Invalid status"):
            update_request_status(db, r["id"], "bogus_status")


class TestConstants:
    def test_classification_contains_cui(self):
        assert "CUI" in CLASSIFICATION

    def test_peering_types_non_empty(self):
        assert len(PEERING_TYPES) >= 4

    def test_request_statuses_include_lifecycle(self):
        for expected in ("initiated", "sent", "accepted", "rejected"):
            assert expected in REQUEST_STATUSES

    def test_workflow_id_matches_task(self):
        assert WORKFLOW_ID == "digitize-wfl-7f07"

    def test_step_number_is_one(self):
        assert STEP_NUMBER == 1
