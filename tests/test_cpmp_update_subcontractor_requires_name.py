# CUI // SP-CTI
"""Tests for update_subcontractor company_name validation (FAR 52.219-9).

`create_subcontractor` rejects a nameless subcontractor (#1520), because a row
with no company name is a phantom: `flow_down_complete` defaults to 0, so
`check_flowdown` reports it as non-compliant forever and the CPMP advisor
raises a "Subcontractor Compliance" card naming a sub nobody can issue a cure
notice to.

`update_subcontractor` listed `company_name` in `updatable` and validated
nothing, so the same invariant was enforced on only half the write path:
``PUT /api/cpmp/subcontractors/<id>`` with ``{"company_name": ""}`` blanked the
name of an existing row and recreated the phantom the create-side fix exists to
prevent.

Verifies:
1. Blank / whitespace-only / None / non-string company_name is rejected.
2. A rejected update performs no UPDATE and does not commit.
3. Omitting company_name entirely still updates the other fields (the guard
   must not turn every partial update into a required-field error).
4. A valid company_name still updates, stored stripped of whitespace.
5. The PUT route surfaces a validation rejection as 400, and a missing
   subcontractor as 404 — a validation error reported as 404 tells the caller
   the row does not exist, which is the opposite of what happened.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUB_ID = "sub-test-0001"


# ---------------------------------------------------------------------------
# Fake connection — records every statement so we can assert "no UPDATE"
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, sub_exists=True):
        self.statements = []
        self._sub_exists = sub_exists
        self.committed = False
        self.closed = False

    def set_security_context(self, _ctx):
        pass

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        cursor = MagicMock()
        if "FROM cpmp_subcontractors" in sql:
            cursor.fetchone.return_value = (
                {"id": _SUB_ID, "status": "active"} if self._sub_exists else None
            )
        else:
            cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        return cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def updates_of(self, table):
        return [s for s, _ in self.statements if "UPDATE " + table in s]


def _update(data, sub_exists=True):
    """Call update_subcontractor against a fake connection."""
    from tools.govcon import subcontractor_tracker

    conn = _FakeConn(sub_exists=sub_exists)
    with patch.object(subcontractor_tracker, "_get_db", return_value=conn):
        result = subcontractor_tracker.update_subcontractor(_SUB_ID, data)
    return result, conn


# ---------------------------------------------------------------------------
# Tests: a blank name may not be written over an existing one
# ---------------------------------------------------------------------------


class TestUpdateRejectsBlankCompanyName:
    """company_name, when supplied, must be a non-empty string."""

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "\t\n", None, 12345, [], {}],
        ids=["blank", "whitespace", "tabs_newlines", "none", "int", "list", "dict"],
    )
    def test_rejected_with_error_status(self, value):
        result, _ = _update({"company_name": value})
        assert result["status"] == "error"

    def test_rejection_message_names_the_field(self):
        result, _ = _update({"company_name": ""})
        assert "company_name" in result["message"]

    def test_rejection_performs_no_update(self):
        _, conn = _update({"company_name": ""})
        assert conn.updates_of("cpmp_subcontractors") == []

    def test_rejection_does_not_commit(self):
        _, conn = _update({"company_name": ""})
        assert conn.committed is False

    def test_rejection_closes_the_connection(self):
        _, conn = _update({"company_name": ""})
        assert conn.closed is True

    def test_blank_name_alongside_valid_fields_rejects_the_whole_update(self):
        """A partial write that blanks the name is still a phantom."""
        _, conn = _update({"company_name": "", "cmmc_level": 2})
        assert conn.updates_of("cpmp_subcontractors") == []


# ---------------------------------------------------------------------------
# Tests: the guard must not break ordinary partial updates
# ---------------------------------------------------------------------------


class TestUpdateWithoutCompanyName:
    """company_name is required only when the caller actually supplies it."""

    def test_other_fields_update_normally(self):
        result, _ = _update({"flow_down_complete": 1})
        assert result["status"] == "ok"

    def test_other_fields_issue_an_update(self):
        _, conn = _update({"flow_down_complete": 1})
        assert len(conn.updates_of("cpmp_subcontractors")) == 1

    def test_empty_body_still_reports_no_updatable_fields(self):
        result, _ = _update({})
        assert result["status"] == "error"
        assert "No updatable fields" in result["message"]


# ---------------------------------------------------------------------------
# Tests: the happy path still works
# ---------------------------------------------------------------------------


class TestUpdateHappyPath:
    def test_valid_name_succeeds(self):
        result, _ = _update({"company_name": "Acme LLC"})
        assert result["status"] == "ok"

    def test_company_name_is_stripped(self):
        _, conn = _update({"company_name": "  Acme LLC  "})
        for sql, params in conn.statements:
            if "UPDATE cpmp_subcontractors" in sql:
                assert "Acme LLC" in params
                assert "  Acme LLC  " not in params
                return
        pytest.fail("no UPDATE of cpmp_subcontractors recorded")

    def test_missing_subcontractor_still_rejected(self):
        result, _ = _update({"company_name": "Acme LLC"}, sub_exists=False)
        assert result["status"] == "error"
        assert "not found" in result["message"]


# ---------------------------------------------------------------------------
# Tests: the PUT route distinguishes 400 from 404
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


class TestUpdateSubcontractorRoute:
    """PUT /api/cpmp/subcontractors/<id> — 400 on validation, 404 on missing."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _put(self, api_app, body, tracker_result):
        with patch(
            "tools.govcon.subcontractor_tracker.update_subcontractor",
            return_value=tracker_result,
        ):
            with api_app.test_client() as c:
                return c.put(f"/api/cpmp/subcontractors/{_SUB_ID}", json=body)

    def test_blank_name_returns_400(self, api_app):
        resp = self._put(
            api_app,
            {"company_name": ""},
            {
                "status": "error",
                "message": "company_name must be a non-empty string",
            },
        )
        assert resp.status_code == 400

    def test_missing_subcontractor_returns_404(self, api_app):
        resp = self._put(
            api_app,
            {"company_name": "Acme LLC"},
            {"status": "error", "message": f"Subcontractor {_SUB_ID} not found"},
        )
        assert resp.status_code == 404

    def test_valid_update_returns_200(self, api_app):
        resp = self._put(
            api_app,
            {"company_name": "Acme LLC"},
            {"status": "ok", "sub_id": _SUB_ID, "updated_fields": ["company_name"]},
        )
        assert resp.status_code == 200
