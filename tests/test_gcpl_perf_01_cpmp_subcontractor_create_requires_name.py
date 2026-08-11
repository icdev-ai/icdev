# CUI // SP-CTI
"""Tests for create_subcontractor company_name validation (FAR 52.219-9).

A subcontractor row with no company name is not a subcontractor — it is a
phantom. Because `flow_down_complete` defaults to 0, every phantom row is
permanently non-compliant and `check_flowdown` reports it forever, which is how
14 rows named 'Unknown' accumulated on the live board and produced the
"[CPMP] ... Subcontractor Compliance" advisor card with no real sub behind it.

Verifies:
1. Missing company_name is rejected with status == 'error'.
2. Blank / whitespace-only company_name is rejected.
3. Non-string company_name is rejected.
4. A rejected create performs no INSERT.
5. A rejected create returns no sub_id.
6. A body whose keys do not match the API contract (e.g. the 'name' /
   'contract_value' shape) is rejected rather than defaulted into a phantom.
7. A valid company_name still creates normally.
8. company_name is stored stripped of surrounding whitespace.
9. A missing parent contract is still rejected before validation side effects.
10. The POST route surfaces the rejection as HTTP 400.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"

# The shape tests/e2e/cpmp_performance.spec.ts used to POST. None of these keys
# are read by create_subcontractor, so every column fell to its default.
_MISMATCHED_BODY = {
    "name": "GCPL Test Subcontractor LLC",
    "role": "Software Development",
    "small_business_type": "small",
    "cmmc_level": 2,
    "cybersecurity_compliant": True,
    "flowdown_verified": False,
    "contract_value": 50000,
}

_VALID_BODY = {
    "company_name": "GCPL Test Subcontractor LLC",
    "business_size": "small",
    "subcontract_value": 50000,
    "cmmc_level": 2,
}


# ---------------------------------------------------------------------------
# Fake connection — records every statement so we can assert "no INSERT"
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, contract_exists=True):
        self.statements = []
        self._contract_exists = contract_exists
        self.committed = False
        self.closed = False

    def set_security_context(self, _ctx):
        pass

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        cursor = MagicMock()
        if "FROM cpmp_contracts" in sql:
            cursor.fetchone.return_value = {"id": _CONTRACT_ID} if self._contract_exists else None
        else:
            cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        return cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def inserts_into(self, table):
        return [s for s, _ in self.statements if "INSERT INTO " + table in s]


def _create(data, contract_exists=True):
    """Call create_subcontractor against a fake connection."""
    from tools.govcon import subcontractor_tracker

    conn = _FakeConn(contract_exists=contract_exists)
    with patch.object(subcontractor_tracker, "_get_db", return_value=conn):
        result = subcontractor_tracker.create_subcontractor(_CONTRACT_ID, data)
    return result, conn


# ---------------------------------------------------------------------------
# Tests: rejection of nameless subcontractors
# ---------------------------------------------------------------------------


class TestCreateSubcontractorRequiresCompanyName:
    """company_name is documented as required — it must actually be enforced."""

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"business_size": "small"},
            {"company_name": ""},
            {"company_name": "   "},
            {"company_name": "\t\n"},
            {"company_name": None},
            {"company_name": 12345},
        ],
        ids=[
            "empty_body",
            "no_company_name_key",
            "blank",
            "whitespace",
            "tabs_newlines",
            "none",
            "non_string",
        ],
    )
    def test_rejected_with_error_status(self, data):
        result, _ = _create(data)
        assert result["status"] == "error"

    def test_rejection_message_names_the_field(self):
        result, _ = _create({})
        assert "company_name" in result["message"]

    def test_rejection_performs_no_insert(self):
        _, conn = _create({})
        assert conn.inserts_into("cpmp_subcontractors") == []

    def test_rejection_returns_no_sub_id(self):
        result, _ = _create({})
        assert "sub_id" not in result

    def test_rejection_does_not_commit(self):
        _, conn = _create({})
        assert conn.committed is False

    def test_rejection_closes_the_connection(self):
        _, conn = _create({})
        assert conn.closed is True


class TestMismatchedFieldNamesAreRejected:
    """A body using the wrong key names must not silently become a phantom row."""

    def test_mismatched_body_is_rejected(self):
        result, _ = _create(_MISMATCHED_BODY)
        assert result["status"] == "error"

    def test_mismatched_body_creates_nothing(self):
        _, conn = _create(_MISMATCHED_BODY)
        assert conn.inserts_into("cpmp_subcontractors") == []


# ---------------------------------------------------------------------------
# Tests: the happy path still works
# ---------------------------------------------------------------------------


class TestCreateSubcontractorHappyPath:
    """Validation must not break a well-formed create."""

    def test_valid_body_succeeds(self):
        result, _ = _create(_VALID_BODY)
        assert result["status"] == "ok"

    def test_valid_body_returns_sub_id(self):
        result, _ = _create(_VALID_BODY)
        assert result["sub_id"]

    def test_valid_body_inserts_one_row(self):
        _, conn = _create(_VALID_BODY)
        assert len(conn.inserts_into("cpmp_subcontractors")) == 1

    def test_company_name_is_stripped(self):
        _, conn = _create({**_VALID_BODY, "company_name": "  Acme LLC  "})
        for sql, params in conn.statements:
            if "INSERT INTO cpmp_subcontractors" in sql:
                assert "Acme LLC" in params
                assert "  Acme LLC  " not in params
                return
        pytest.fail("no INSERT into cpmp_subcontractors recorded")

    def test_missing_contract_still_rejected(self):
        result, _ = _create(_VALID_BODY, contract_exists=False)
        assert result["status"] == "error"
        assert "not found" in result["message"]


# ---------------------------------------------------------------------------
# Tests: the POST route surfaces the rejection
# ---------------------------------------------------------------------------


def _build_cpmp_api_app() -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    @flask_app.before_request
    def _inject_fake_auth_0():
        from flask import g

        g.current_user = {
            "username": "test_user",
            "role": "admin",
            "email": "test@test.mil",
            "classification": "CUI",
        }

    flask_app.register_blueprint(cpmp_api)
    return flask_app


class TestCreateSubcontractorRoute:
    """POST /api/cpmp/contracts/<id>/subcontractors returns 400 on rejection."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _post(self, api_app, body, tracker_result):
        with patch(
            "tools.govcon.subcontractor_tracker.create_subcontractor",
            return_value=tracker_result,
        ):
            with api_app.test_client() as c:
                return c.post(
                    f"/api/cpmp/contracts/{_CONTRACT_ID}/subcontractors",
                    json=body,
                )

    def test_nameless_body_returns_400(self, api_app):
        resp = self._post(
            api_app,
            {},
            {"status": "error", "message": "company_name is required"},
        )
        assert resp.status_code == 400

    def test_valid_body_returns_201(self, api_app):
        resp = self._post(api_app, _VALID_BODY, {"status": "ok", "sub_id": "sub-0001"})
        assert resp.status_code == 201
