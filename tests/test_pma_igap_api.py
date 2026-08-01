# CUI // SP-CTI
"""Flask integration tests for INT coverage map and collection requirements endpoints.

Tests 8 REST endpoints added to tools/dashboard/api/cpmp.py (pma-igap-04):
  POST /api/cpmp/contracts/<id>/coverage
  GET  /api/cpmp/contracts/<id>/coverage
  GET  /api/cpmp/contracts/<id>/coverage/gaps
  GET  /api/cpmp/contracts/<id>/coverage/persistent
  POST /api/cpmp/contracts/<id>/coverage/<cid>/generate-reqs
  GET  /api/cpmp/coverage/<cid>/requirements
  PUT  /api/cpmp/requirements/<rid>
  GET  /api/cpmp/contracts/<id>/requirements

Acceptance criteria:
  - /coverage/gaps returns only 'gap' and 'partial' records (not 'covered')
  - PUT /requirements/<rid> with status='tasked' sets updated_at and status='tasked'
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

# Ensure the repo root is on sys.path (same as conftest.py)
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Minimal schema needed by these tests
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS audit_trail (
    id          TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    event_type  TEXT,
    actor       TEXT,
    action      TEXT NOT NULL DEFAULT '',
    details     TEXT,
    session_id  TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cpmp_contracts (
    id              TEXT PRIMARY KEY,
    contract_number TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    agency          TEXT NOT NULL DEFAULT '',
    classification  TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS cpmp_int_coverage (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT NOT NULL,
    discipline      TEXT NOT NULL DEFAULT '',
    coverage_area   TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'gap',
    confidence      REAL DEFAULT 0.0,
    source_type     TEXT DEFAULT '',
    notes           TEXT,
    last_assessed   TEXT,
    persistent_since TEXT,
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    classification  TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS cpmp_collection_requirements (
    id               TEXT PRIMARY KEY,
    coverage_id      TEXT NOT NULL,
    contract_id      TEXT NOT NULL,
    requirement_text TEXT NOT NULL DEFAULT '',
    discipline       TEXT NOT NULL DEFAULT '',
    priority         TEXT NOT NULL DEFAULT 'medium',
    status           TEXT NOT NULL DEFAULT 'open',
    ai_generated     INTEGER DEFAULT 0,
    tasked_to        TEXT,
    tasked_at        TEXT,
    satisfied_at     TEXT,
    notes            TEXT,
    metadata         TEXT DEFAULT '{}',
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now')),
    classification   TEXT DEFAULT 'CUI'
);
"""


# ---------------------------------------------------------------------------
# Thin connection wrapper matching StorageCursor's public interface
# ---------------------------------------------------------------------------

class _FakeConn:
    """Wraps a sqlite3.Connection to satisfy cpmp.py's _get_db() interface."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def set_security_context(self, ctx) -> None:  # rls no-op in tests
        pass

    def execute(self, sql: str, params=()):
        return self._c.execute(sql, params)

    def executemany(self, sql: str, params):
        return self._c.executemany(sql, params)

    def commit(self) -> None:
        self._c.commit()

    def close(self) -> None:
        self._c.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    """File-based SQLite DB shared across all tests in this module."""
    path = tmp_path_factory.mktemp("pma_igap") / "test.db"
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA)
    raw.commit()
    raw.close()
    return str(path)


def _open(db_path: str) -> _FakeConn:
    """Open a new SQLite connection to the test DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return _FakeConn(conn)


@pytest.fixture(scope="module")
def client(db_path):
    """Flask test client with _get_db patched to use the test SQLite DB."""
    from tools.dashboard.api.cpmp import cpmp_api

    app = Flask(__name__)
    app.config["TESTING"] = True
    @app.before_request
    def _inject_fake_auth_0():
        from flask import g
        g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

    app.register_blueprint(cpmp_api)

    with patch("tools.dashboard.api.cpmp._get_db", side_effect=lambda: _open(db_path)):
        with app.test_client() as c:
            yield c


# Contract and coverage IDs reused across tests
CONTRACT_ID = "ctr-igap-test-001"
COV_GAP_ID = "cov-gap-001"
COV_PARTIAL_ID = "cov-partial-001"
COV_COVERED_ID = "cov-covered-001"
COV_PERSISTENT_ID = "cov-persistent-001"


@pytest.fixture(scope="module", autouse=True)
def seed_data(db_path):
    """Seed a contract and coverage records for query tests."""
    raw = sqlite3.connect(db_path)
    raw.execute(
        "INSERT OR IGNORE INTO cpmp_contracts (id, contract_number, title, agency) VALUES (?,?,?,?)",
        (CONTRACT_ID, "N00014-26-C-0001", "Test INT Contract", "DARPA"),
    )
    # Three coverage records with different statuses
    for cov_id, status, persistent_since in [
        (COV_GAP_ID,       "gap",     None),
        (COV_PARTIAL_ID,   "partial", None),
        (COV_COVERED_ID,   "covered", None),
        (COV_PERSISTENT_ID,"gap",     "2026-05-01T00:00:00"),
    ]:
        raw.execute(
            "INSERT OR IGNORE INTO cpmp_int_coverage "
            "(id, contract_id, discipline, coverage_area, status, persistent_since) "
            "VALUES (?,?,?,?,?,?)",
            (cov_id, CONTRACT_ID, "SIGINT", "Eastern Europe", status, persistent_since),
        )
    raw.commit()
    raw.close()


# ---------------------------------------------------------------------------
# 1. POST /api/cpmp/contracts/<id>/coverage — upsert coverage record
# ---------------------------------------------------------------------------

class TestUpsertCoverage:
    def test_creates_coverage_record_201(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage",
            json={"discipline": "GEOINT", "coverage_area": "Pacific Rim", "status": "gap"},
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["coverage"]["discipline"] == "GEOINT"
        assert body["coverage"]["status"] == "gap"
        assert body["coverage"]["contract_id"] == CONTRACT_ID

    def test_rejects_invalid_status(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage",
            json={"discipline": "HUMINT", "status": "unknown_status"},
        )
        assert rv.status_code == 400

    def test_defaults_status_to_gap(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage",
            json={"discipline": "OSINT", "coverage_area": "Middle East"},
        )
        assert rv.status_code == 201
        assert rv.get_json()["coverage"]["status"] == "gap"


# ---------------------------------------------------------------------------
# 2. GET /api/cpmp/contracts/<id>/coverage — list with optional status filter
# ---------------------------------------------------------------------------

class TestListCoverage:
    def test_returns_all_records(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["count"] >= 4  # seed has 4; POST tests may add more

    def test_filters_by_status(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage?status=covered")
        assert rv.status_code == 200
        body = rv.get_json()
        for rec in body["coverage"]:
            assert rec["status"] == "covered"

    def test_ignores_invalid_status_filter(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage?status=bogus")
        assert rv.status_code == 200  # returns all, doesn't crash

    def test_empty_contract_returns_empty_list(self, client):
        rv = client.get("/api/cpmp/contracts/nonexistent-ctr/coverage")
        assert rv.status_code == 200
        assert rv.get_json()["count"] == 0


# ---------------------------------------------------------------------------
# 3. GET /api/cpmp/contracts/<id>/coverage/gaps — gap and partial only
# ---------------------------------------------------------------------------

class TestListCoverageGaps:
    def test_returns_only_gap_and_partial(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/gaps")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        for rec in body["gaps"]:
            assert rec["status"] in ("gap", "partial"), (
                f"Expected gap or partial, got {rec['status']!r}"
            )

    def test_covered_record_excluded(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/gaps")
        gap_ids = {r["id"] for r in rv.get_json()["gaps"]}
        assert COV_COVERED_ID not in gap_ids, "covered record must not appear in /gaps"

    def test_seeded_gap_and_partial_included(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/gaps")
        gap_ids = {r["id"] for r in rv.get_json()["gaps"]}
        assert COV_GAP_ID in gap_ids
        assert COV_PARTIAL_ID in gap_ids

    def test_empty_contract_returns_empty(self, client):
        rv = client.get("/api/cpmp/contracts/no-contract/coverage/gaps")
        assert rv.status_code == 200
        assert rv.get_json()["count"] == 0


# ---------------------------------------------------------------------------
# 4. GET /api/cpmp/contracts/<id>/coverage/persistent — ?days=14
# ---------------------------------------------------------------------------

class TestPersistentGaps:
    def test_persistent_record_returned(self, client):
        # COV_PERSISTENT_ID has persistent_since=2026-05-01, well over 14 days ago (today=2026-06-13)
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/persistent?days=14")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["days"] == 14
        ids = {r["id"] for r in body["persistent_gaps"]}
        assert COV_PERSISTENT_ID in ids

    def test_non_persistent_gap_excluded(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/persistent?days=14")
        ids = {r["id"] for r in rv.get_json()["persistent_gaps"]}
        # COV_GAP_ID has no persistent_since — must NOT appear
        assert COV_GAP_ID not in ids

    def test_covered_record_excluded(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/persistent?days=14")
        ids = {r["id"] for r in rv.get_json()["persistent_gaps"]}
        assert COV_COVERED_ID not in ids

    def test_default_days_14(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/persistent")
        assert rv.status_code == 200
        assert rv.get_json()["days"] == 14


# ---------------------------------------------------------------------------
# 5. POST /api/cpmp/contracts/<id>/coverage/<cid>/generate-reqs — AI generator
# ---------------------------------------------------------------------------

class TestGenerateRequirements:
    def test_returns_201_with_ai_generated_true(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_GAP_ID}/generate-reqs",
            json={"count": 3, "priority": "high"},
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["count"] == 3
        for req in body["requirements"]:
            assert req["ai_generated"] is True, "Each generated requirement must have ai_generated=True"
            assert req["status"] == "open"
            assert req["coverage_id"] == COV_GAP_ID
            assert req["contract_id"] == CONTRACT_ID
            assert req["requirement_text"] != ""

    def test_default_count_3(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_PARTIAL_ID}/generate-reqs",
            json={},
        )
        assert rv.status_code == 201
        assert rv.get_json()["count"] == 3

    def test_count_capped_at_10(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_GAP_ID}/generate-reqs",
            json={"count": 99},
        )
        assert rv.status_code == 201
        assert rv.get_json()["count"] == 10

    def test_invalid_coverage_returns_404(self, client):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/nonexistent-cov/generate-reqs",
            json={"count": 2},
        )
        assert rv.status_code == 404

    def test_requirements_persisted_in_db(self, client, db_path):
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_PARTIAL_ID}/generate-reqs",
            json={"count": 2, "priority": "medium"},
        )
        assert rv.status_code == 201
        ids = [r["id"] for r in rv.get_json()["requirements"]]
        raw = sqlite3.connect(db_path)
        raw.row_factory = sqlite3.Row
        for rid in ids:
            row = raw.execute(
                "SELECT ai_generated FROM cpmp_collection_requirements WHERE id = ?", (rid,)
            ).fetchone()
            assert row is not None, f"Requirement {rid} not persisted"
            assert row["ai_generated"] == 1
        raw.close()


# ---------------------------------------------------------------------------
# 6. GET /api/cpmp/coverage/<cid>/requirements — list collection requirements
# ---------------------------------------------------------------------------

class TestListCoverageRequirements:
    def test_returns_generated_requirements(self, client):
        # generate-reqs already ran for COV_GAP_ID in TestGenerateRequirements
        rv = client.get(f"/api/cpmp/coverage/{COV_GAP_ID}/requirements")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["count"] >= 3

    def test_empty_for_unknown_coverage(self, client):
        rv = client.get("/api/cpmp/coverage/nonexistent-cov/requirements")
        assert rv.status_code == 200
        assert rv.get_json()["count"] == 0

    def test_response_shape(self, client):
        rv = client.get(f"/api/cpmp/coverage/{COV_GAP_ID}/requirements")
        body = rv.get_json()
        if body["count"] > 0:
            req = body["requirements"][0]
            for field in ("id", "coverage_id", "contract_id", "requirement_text", "status", "ai_generated"):
                assert field in req, f"Field {field!r} missing from requirement response"


# ---------------------------------------------------------------------------
# 7. PUT /api/cpmp/requirements/<rid> — update status: open→tasked→satisfied
# ---------------------------------------------------------------------------

class TestUpdateRequirement:
    @pytest.fixture
    def req_id(self, client):
        """Create a fresh requirement and return its ID."""
        rv = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_GAP_ID}/generate-reqs",
            json={"count": 1},
        )
        assert rv.status_code == 201
        return rv.get_json()["requirements"][0]["id"]

    def test_open_to_tasked_sets_status_and_updated_at(self, client, req_id):
        rv = client.put(
            f"/api/cpmp/requirements/{req_id}",
            json={"status": "tasked", "tasked_to": "SIGINT-CELL-1"},
        )
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        req = body["requirement"]
        assert req["status"] == "tasked"
        assert req["updated_at"] is not None
        assert req["tasked_to"] == "SIGINT-CELL-1"

    def test_tasked_to_satisfied_sets_satisfied_at(self, client, req_id):
        client.put(f"/api/cpmp/requirements/{req_id}", json={"status": "tasked"})
        rv = client.put(f"/api/cpmp/requirements/{req_id}", json={"status": "satisfied"})
        assert rv.status_code == 200
        req = rv.get_json()["requirement"]
        assert req["status"] == "satisfied"
        assert req["satisfied_at"] is not None

    def test_invalid_status_returns_400(self, client, req_id):
        rv = client.put(f"/api/cpmp/requirements/{req_id}", json={"status": "revoked"})
        assert rv.status_code == 400

    def test_unknown_requirement_returns_404(self, client):
        rv = client.put("/api/cpmp/requirements/nonexistent-req", json={"status": "tasked"})
        assert rv.status_code == 404

    def test_notes_updated(self, client, req_id):
        rv = client.put(f"/api/cpmp/requirements/{req_id}", json={"notes": "Priority escalated"})
        assert rv.status_code == 200
        assert rv.get_json()["requirement"]["notes"] == "Priority escalated"


# ---------------------------------------------------------------------------
# 8. GET /api/cpmp/contracts/<id>/requirements — all reqs, filterable
# ---------------------------------------------------------------------------

class TestListContractRequirements:
    def test_returns_all_requirements_for_contract(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/requirements")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["count"] >= 1

    def test_filter_by_discipline(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/requirements?discipline=SIGINT")
        assert rv.status_code == 200
        for req in rv.get_json()["requirements"]:
            assert req["discipline"] == "SIGINT"

    def test_filter_by_status_open(self, client):
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/requirements?status=open")
        assert rv.status_code == 200
        for req in rv.get_json()["requirements"]:
            assert req["status"] == "open"

    def test_filter_by_status_tasked(self, client):
        # Create and task a requirement, then filter
        gen = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_GAP_ID}/generate-reqs",
            json={"count": 1},
        )
        rid = gen.get_json()["requirements"][0]["id"]
        client.put(f"/api/cpmp/requirements/{rid}", json={"status": "tasked"})

        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/requirements?status=tasked")
        assert rv.status_code == 200
        ids = {r["id"] for r in rv.get_json()["requirements"]}
        assert rid in ids

    def test_empty_for_unknown_contract(self, client):
        rv = client.get("/api/cpmp/contracts/no-contract-here/requirements")
        assert rv.status_code == 200
        assert rv.get_json()["count"] == 0

    def test_combined_discipline_and_status_filter(self, client):
        rv = client.get(
            f"/api/cpmp/contracts/{CONTRACT_ID}/requirements?discipline=SIGINT&status=open"
        )
        assert rv.status_code == 200
        for req in rv.get_json()["requirements"]:
            assert req["discipline"] == "SIGINT"
            assert req["status"] == "open"


# ---------------------------------------------------------------------------
# Acceptance-criteria smoke test (named per task spec)
# ---------------------------------------------------------------------------

class TestAcceptanceCriteria:
    def test_gaps_endpoint_excludes_covered_status(self, client):
        """GIVEN mixed statuses WHEN GET /coverage/gaps THEN only gap+partial returned."""
        rv = client.get(f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/gaps")
        assert rv.status_code == 200
        for rec in rv.get_json()["gaps"]:
            assert rec["status"] in ("gap", "partial"), (
                "Acceptance violation: 'covered' record appeared in /gaps response"
            )

    def test_put_requirement_status_tasked_sets_updated_at(self, client):
        """GIVEN open requirement WHEN PUT status='tasked' THEN updated_at set and status='tasked'."""
        gen = client.post(
            f"/api/cpmp/contracts/{CONTRACT_ID}/coverage/{COV_GAP_ID}/generate-reqs",
            json={"count": 1},
        )
        rid = gen.get_json()["requirements"][0]["id"]

        rv = client.put(f"/api/cpmp/requirements/{rid}", json={"status": "tasked"})
        assert rv.status_code == 200
        req = rv.get_json()["requirement"]
        assert req["status"] == "tasked"
        assert req["updated_at"] is not None and req["updated_at"] != ""
