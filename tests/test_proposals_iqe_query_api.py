# [TEMPLATE: CUI // SP-CTI]
"""Tests for POST /api/proposals/iqe-query (prop-iqe-01), mirroring the
pre-existing POST /api/govcon/iqe-query (prop-cap-13) — see
tools/dashboard/api/proposals.py::proposals_iqe_query.

nl_to_iqe() is monkeypatched to a deterministic stub so these tests don't
depend on LLM availability/determinism; the IQE query language itself
(parse + execute_query) and the proposals.* adapter collections run for
real against a seeded SQLite DB.
"""
import sqlite3

import pytest
from flask import Flask


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "proposals_iqe_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE proposal_opportunities (
            id TEXT PRIMARY KEY, title TEXT, agency TEXT, sub_agency TEXT,
            solicitation_number TEXT, naics_code TEXT, due_date TEXT, status TEXT,
            proposal_type TEXT, set_aside_type TEXT, estimated_value_low REAL,
            estimated_value_high REAL, win_probability REAL, capture_phase TEXT,
            capture_manager TEXT, proposal_manager TEXT, classification TEXT, created_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, title, agency, status, due_date, created_at) "
        "VALUES ('opp-1', 'Cyber IDIQ', 'DoD', 'writing', '2026-12-31', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    import tools.dashboard.api.proposals as proposals_mod

    monkeypatch.setattr(proposals_mod, "DB_PATH", db_file)
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    return db_file


@pytest.fixture()
def app(db):
    from tools.dashboard.api.proposals import proposals_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(proposals_api)
    return flask_app


def _stub_nl_to_iqe(monkeypatch, iqe_str, explanation="stub translation"):
    import tools.iqe.nl_to_iqe as nl_mod

    monkeypatch.setattr(nl_mod, "nl_to_iqe", lambda question, collections: {
        "iqe": iqe_str, "explanation": explanation,
    })


class TestProposalsIqeQuery:
    def test_requires_question(self, app):
        with app.test_client() as client:
            resp = client.post("/api/proposals/iqe-query", json={})
            assert resp.status_code == 400

    def test_translate_only_when_execute_false(self, app, monkeypatch):
        _stub_nl_to_iqe(monkeypatch, "foreach o in proposals.opportunities select *")
        with app.test_client() as client:
            resp = client.post(
                "/api/proposals/iqe-query",
                json={"question": "show all opportunities", "execute": False},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "iqe" in data
            assert "results" not in data

    def test_executes_and_returns_real_rows(self, app, monkeypatch):
        _stub_nl_to_iqe(monkeypatch, "foreach o in proposals.opportunities select *")
        with app.test_client() as client:
            resp = client.post(
                "/api/proposals/iqe-query",
                json={"question": "show all opportunities"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["row_count"] == 1
            assert data["results"][0]["id"] == "opp-1"

    def test_invalid_iqe_syntax_returns_400(self, app, monkeypatch):
        _stub_nl_to_iqe(monkeypatch, "this is not valid iqe syntax !!!")
        with app.test_client() as client:
            resp = client.post(
                "/api/proposals/iqe-query",
                json={"question": "garbage in"},
            )
            assert resp.status_code == 400
            assert "IQE syntax error" in resp.get_json()["error"]
