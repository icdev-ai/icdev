# [TEMPLATE: CUI // SP-CTI]
"""Tests for Bell-LaPadula MAC filtering on win-loss records/lessons
(tools/dashboard/api/proposal_genesis.py, prop-sec-02).

pg_win_loss_records/pg_win_loss_lessons are genuinely SECRET-capable
competitive intelligence (why we won/lost against a named competitor, our
own weaknesses) that classify per-row (classification column, default
CUI) -- not per-endpoint. Unlike a blanket @require_clearance("SECRET")
gate (which would hide the CUI majority of records from ordinary
capture/proposal roles), _mac_filter_by_classification() filters the
result set per-row, matching the pattern already established in
tools/dashboard/api/proposals.py::_mac_filter().
"""
import sqlite3

import pytest
from flask import Flask, g

from tools.security.security_context import SecurityContext


_SCHEMA = """
CREATE TABLE sam_gov_opportunities (
    id TEXT PRIMARY KEY, title TEXT, agency TEXT
);
CREATE TABLE pg_win_loss_records (
    id TEXT PRIMARY KEY, opportunity_id TEXT, outcome TEXT,
    competitor_name TEXT, competitor_strengths TEXT,
    our_strengths TEXT, our_weaknesses TEXT, lessons_learned TEXT,
    created_at TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
);
CREATE TABLE pg_win_loss_lessons (
    id TEXT PRIMARY KEY, win_loss_id TEXT, category TEXT, lesson TEXT,
    actionable INTEGER DEFAULT 1, applied INTEGER DEFAULT 0,
    created_at TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
);
"""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "winloss_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    import tools.dashboard.api.proposal_genesis as pg_mod
    monkeypatch.setattr(pg_mod, "DB_PATH", db_file)
    return db_file


@pytest.fixture()
def app(db):
    from tools.dashboard.api.proposal_genesis import proposal_genesis_api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(proposal_genesis_api)

    # Test-only: attach a SecurityContext directly (real auth wiring is
    # covered end-to-end in tests/test_dashboard_auth.py; these routes
    # have no @require_role gate today, so there's no auth to piggyback on).
    @app.before_request
    def _set_test_security_context():
        level = __import__("flask").request.headers.get("X-Test-Clearance")
        if level is not None:
            g.security_context = SecurityContext(clearance_level=int(level))

    return app


def _conn(db_file):
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    return c


def _seed_win_loss_records(db_file):
    conn = _conn(db_file)
    conn.executemany(
        "INSERT INTO pg_win_loss_records "
        "(id, opportunity_id, outcome, competitor_name, created_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("wl-cui", "opp-1", "lost", "Vendor A", "2026-01-01", "CUI"),
            ("wl-secret", "opp-1", "lost", "Vendor B", "2026-01-02", "SECRET"),
        ],
    )
    conn.commit()
    conn.close()


def _seed_win_loss_lessons(db_file):
    conn = _conn(db_file)
    conn.executemany(
        "INSERT INTO pg_win_loss_lessons "
        "(id, win_loss_id, category, lesson, created_at, classification) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("l-cui", "wl-cui", "technical", "Improve technical volume clarity", "2026-01-01", "CUI"),
            ("l-secret", "wl-secret", "technical", "Classified capability gap", "2026-01-02", "SECRET"),
        ],
    )
    conn.commit()
    conn.close()


class TestWinLossRecordsMacFilter:
    def test_no_security_context_returns_all_records(self, app, db):
        _seed_win_loss_records(db)
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/win-loss-records")
            data = resp.get_json()
            assert data["count"] == 2

    def test_cui_clearance_hides_secret_record(self, app, db):
        _seed_win_loss_records(db)
        with app.test_client() as client:
            resp = client.get(
                "/api/proposal-genesis/win-loss-records",
                headers={"X-Test-Clearance": "1"},  # CUI
            )
            data = resp.get_json()
            ids = {r["id"] for r in data["records"]}
            assert ids == {"wl-cui"}
            assert data["count"] == 1

    def test_secret_clearance_sees_both_records(self, app, db):
        _seed_win_loss_records(db)
        with app.test_client() as client:
            resp = client.get(
                "/api/proposal-genesis/win-loss-records",
                headers={"X-Test-Clearance": "3"},  # SECRET
            )
            data = resp.get_json()
            ids = {r["id"] for r in data["records"]}
            assert ids == {"wl-cui", "wl-secret"}
            assert data["count"] == 2


class TestWinLossLessonsMacFilter:
    def test_no_security_context_returns_all_lessons(self, app, db):
        _seed_win_loss_records(db)
        _seed_win_loss_lessons(db)
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/win-loss-lessons")
            data = resp.get_json()
            assert data["count"] == 2

    def test_cui_clearance_hides_secret_lesson(self, app, db):
        _seed_win_loss_records(db)
        _seed_win_loss_lessons(db)
        with app.test_client() as client:
            resp = client.get(
                "/api/proposal-genesis/win-loss-lessons",
                headers={"X-Test-Clearance": "1"},  # CUI
            )
            data = resp.get_json()
            ids = {l["id"] for l in data["lessons"]}
            assert ids == {"l-cui"}

    def test_secret_clearance_sees_both_lessons(self, app, db):
        _seed_win_loss_records(db)
        _seed_win_loss_lessons(db)
        with app.test_client() as client:
            resp = client.get(
                "/api/proposal-genesis/win-loss-lessons",
                headers={"X-Test-Clearance": "3"},  # SECRET
            )
            data = resp.get_json()
            ids = {l["id"] for l in data["lessons"]}
            assert ids == {"l-cui", "l-secret"}
