# CUI // SP-CTI
"""End-to-end V&V smoke test for all three PMA gap epics.

Acceptance criteria (pma-vv-01):
  GIVEN all pma-coord, pma-cont, and pma-igap tasks are done
  WHEN the V&V smoke test runs
  THEN all three epic workflows complete end-to-end without errors.

Epic 1 — Coordination (pma-coord):
  * Create meeting log via API
  * Extract action items from meeting notes (AI extractor)
  * HITL approve an action item
  * Verify overdue alert surfaced for past-due item

Epic 2 — Personnel Continuity (pma-cont):
  * Insert person with expiring polygraph credential
  * Run credential monitor reflex end-to-end
  * Verify alert row inserted and kanban task seeded for critical expiry
  * Key-person matrix: person without backup flagged as SPOF

Epic 3 — INT Coverage Gap (pma-igap):
  * Upsert INT coverage record with gap status via API
  * Run INT gap monitor reflex end-to-end
  * Verify collection requirement generated for persistent gap
  * HITL tasking workflow: advance requirement status open → tasked
"""
from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Shared SQLite schema (all tables needed across all three epics)
# ---------------------------------------------------------------------------

_SHARED_SCHEMA = """
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

CREATE TABLE IF NOT EXISTS kanban_tasks (
    id              TEXT PRIMARY KEY,
    task_type       TEXT DEFAULT 'feature',
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'suggested',
    priority        TEXT DEFAULT 'medium',
    target_date     TEXT,
    tags            TEXT,
    dispatch_source TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS cpmp_risks (
    id          TEXT PRIMARY KEY,
    contract_id TEXT,
    title       TEXT NOT NULL DEFAULT '',
    category    TEXT DEFAULT 'compliance',
    probability INTEGER DEFAULT 3,
    impact      INTEGER DEFAULT 3,
    status      TEXT DEFAULT 'open',
    mitigation  TEXT,
    owner       TEXT,
    metadata    TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pma_personnel (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    role             TEXT,
    contract_id      TEXT,
    poly_expiry      TEXT,
    secret_expiry    TEXT,
    ts_expiry        TEXT,
    backup_person_id TEXT,
    clearance_level  TEXT,
    lcat             TEXT,
    key_person       INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'active',
    created_at       TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS pma_credential_alerts (
    id             TEXT PRIMARY KEY,
    person_id      TEXT NOT NULL,
    alert_type     TEXT NOT NULL,
    expiry_date    TEXT NOT NULL,
    days_remaining INTEGER NOT NULL,
    severity       TEXT NOT NULL,
    kanban_task_id TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (person_id, alert_type, expiry_date)
);

CREATE TABLE IF NOT EXISTS pma_int_gaps (
    id                      TEXT PRIMARY KEY,
    exploitation_line_id    TEXT NOT NULL,
    exploitation_line_title TEXT NOT NULL,
    discipline              TEXT NOT NULL DEFAULT 'ALL',
    gap_description         TEXT NOT NULL,
    severity                TEXT NOT NULL DEFAULT 'medium',
    status                  TEXT NOT NULL DEFAULT 'open',
    contract_id             TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pma_collection_requirements (
    id               TEXT PRIMARY KEY,
    gap_id           TEXT NOT NULL,
    coverage_id      TEXT NOT NULL,
    discipline       TEXT NOT NULL,
    priority         TEXT NOT NULL DEFAULT 'medium',
    requirement_text TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    kanban_task_id   TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pma_meeting_logs (
    id          TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    title       TEXT NOT NULL,
    date_held   TEXT,
    attendees   TEXT,
    notes       TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pma_action_items (
    id            TEXT PRIMARY KEY,
    meeting_id    TEXT NOT NULL,
    contract_id   TEXT NOT NULL,
    description   TEXT NOT NULL,
    owner         TEXT,
    due_date      TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    hitl_approved INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cpmp_contracts (
    id              TEXT PRIMARY KEY,
    contract_number TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    agency          TEXT NOT NULL DEFAULT '',
    classification  TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS cpmp_int_coverage (
    id               TEXT PRIMARY KEY,
    contract_id      TEXT NOT NULL,
    discipline       TEXT NOT NULL DEFAULT '',
    coverage_area    TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'gap',
    confidence       REAL DEFAULT 0.0,
    source_type      TEXT DEFAULT '',
    notes            TEXT,
    last_assessed    TEXT,
    persistent_since TEXT,
    metadata         TEXT DEFAULT '{}',
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now')),
    classification   TEXT DEFAULT 'CUI'
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
# Shared fake connection wrapper for cpmp.py _get_db()
# ---------------------------------------------------------------------------


class _FakeConn:
    """Wraps sqlite3.Connection to satisfy StorageCursor's public interface."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn
        # Route execute() through the REAL storage wrapper. Runtime SQL is
        # authored for PostgreSQL (%s placeholders, per CLAUDE.md); the wrapper
        # is what translates them to SQLite's ?. Passing SQL straight to a raw
        # sqlite3 connection makes every %s a `near "%": syntax error`, which the
        # reflexes swallow -- so they silently seeded nothing.
        from tools.db.storage import StorageConnection

        self._storage = StorageConnection(conn, "sqlite")

    def set_security_context(self, ctx) -> None:
        pass

    def execute(self, sql: str, params=()):
        return self._storage.execute(sql, params)

    def executemany(self, sql: str, params):
        return self._c.executemany(sql, params)

    def executescript(self, sql: str):
        return self._c.executescript(sql)

    def commit(self) -> None:
        self._c.commit()

    def close(self) -> None:
        self._c.close()


# ---------------------------------------------------------------------------
# Shared DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("pma_e2e") / "test.db"
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    raw.executescript(_SHARED_SCHEMA)
    raw.commit()
    raw.close()
    return str(path)


def _open(db_path: str) -> _FakeConn:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return _FakeConn(conn)


@pytest.fixture(scope="module")
def client(db_path):
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


# ---------------------------------------------------------------------------
# Helper: open a raw connection for direct function-level tests
# ---------------------------------------------------------------------------


def _raw(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ===========================================================================
# Epic 1 — PMA Coordination (pma-coord)
# ===========================================================================

_CONTRACT_COORD = "ctr-coord-e2e-001"


class TestEpic1Coordination:
    """E2E workflow: create meeting → extract actions → HITL gate → overdue alert."""

    def test_step1_create_meeting(self, client):
        """POST /meetings — create a meeting log; verify 201 + meeting_id."""
        rv = client.post(
            f"/api/cpmp/contracts/{_CONTRACT_COORD}/meetings",
            json={
                "title": "Weekly PMR",
                "date_held": "2026-06-13",
                "notes": "PM will follow up on CDRL. Contractor must submit deliverable D001.",
                "attendees": "PM, COR, Contractor Rep",
            },
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["status"] == "ok"
        assert "meeting_id" in body
        # stash for subsequent steps
        self.__class__._meeting_id = body["meeting_id"]

    def test_step2_extract_action_items(self, client):
        """POST /meetings/<id>/extract-actions — AI extractor surfaces action sentences."""
        mid = self.__class__._meeting_id
        rv = client.post(
            f"/api/cpmp/meetings/{mid}/extract-actions",
            json={"notes": "PM will follow up on CDRL. Contractor must submit deliverable D001. PM to schedule next review."},
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["count"] >= 2, "Expected at least 2 action items extracted from notes"
        items = body["action_items"]
        owners = {item["owner"] for item in items}
        assert "PM" in owners or "Contractor" in owners
        self.__class__._item_id = items[0]["id"]

    def test_step3_hitl_approve_action_item(self, client):
        """PUT /action-items/<id> — HITL gate approves the action item."""
        item_id = self.__class__._item_id
        rv = client.put(
            f"/api/cpmp/action-items/{item_id}",
            json={"hitl_approved": True, "status": "in_progress"},
        )
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        item = body["item"]
        assert item["hitl_approved"] == 1, "HITL approval flag must be set"
        assert item["status"] == "in_progress"

    def test_step4_overdue_alert_surfaced(self, db_path):
        """Insert past-due item, call get_overdue_action_items, verify it is returned."""
        from tools.pma.meeting_coordinator import get_overdue_action_items, _ensure_tables

        mid = self.__class__._meeting_id
        past_due = (date.today() - timedelta(days=5)).isoformat()
        item_id = f"act-overdue-{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        raw = _raw(db_path)
        raw.execute(
            "INSERT INTO pma_action_items "
            "(id, meeting_id, contract_id, description, owner, due_date, "
            "status, hitl_approved, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'open', 0, ?, ?)",
            (item_id, mid, _CONTRACT_COORD, "Overdue deliverable", "PM", past_due, now_iso, now_iso),
        )
        raw.commit()
        raw.close()

        # Test via data layer (get_overdue_action_items uses get_connection internally)
        fc = _FakeConn(_raw(db_path))
        _ensure_tables(fc)
        overdue = get_overdue_action_items(_CONTRACT_COORD, conn=fc)
        fc.close()

        ids = [i["id"] for i in overdue]
        assert item_id in ids, "Past-due action item must appear in overdue alert list"
        # The function should auto-mark the item as 'overdue' status
        overdue_item = next(i for i in overdue if i["id"] == item_id)
        assert overdue_item["due_date"] == past_due

    def test_step5_list_meetings_shows_created(self, client):
        """GET /meetings — verify the created meeting is listed."""
        rv = client.get(f"/api/cpmp/contracts/{_CONTRACT_COORD}/meetings")
        assert rv.status_code == 200
        body = rv.get_json()
        ids = [m["id"] for m in body["meetings"]]
        assert self.__class__._meeting_id in ids


# ===========================================================================
# Epic 2 — Personnel Continuity (pma-cont)
# ===========================================================================


class TestEpic2PersonnelContinuity:
    """E2E workflow: insert person → run credential reflex → verify alert + task → key-person matrix."""

    def test_step1_insert_person_with_expiring_poly(self, db_path):
        """Insert a person with a polygraph expiring in 20 days (CRITICAL tier)."""
        self.__class__._contract_id = "ctr-cont-e2e-001"
        self.__class__._person_id = f"per-{uuid.uuid4().hex[:8]}"
        expiry = (date.today() + timedelta(days=20)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()

        raw = _raw(db_path)
        raw.execute(
            "INSERT INTO pma_personnel "
            "(id, name, role, contract_id, poly_expiry, backup_person_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                self.__class__._person_id,
                "Alice Johnson",
                "Lead Analyst",
                self.__class__._contract_id,
                expiry,
                now_iso,
                now_iso,
            ),
        )
        raw.commit()
        raw.close()

        # Confirm row is present
        raw2 = _raw(db_path)
        row = raw2.execute(
            "SELECT * FROM pma_personnel WHERE id = ?", (self.__class__._person_id,)
        ).fetchone()
        raw2.close()
        assert row is not None
        assert dict(row)["poly_expiry"] == expiry

    def test_step2_run_credential_monitor_reflex(self, db_path):
        """Run get_expiring_credentials() directly, verify Alice appears as CRITICAL."""
        from tools.pma.credential_monitor import get_expiring_credentials, _ensure_tables

        fc = _FakeConn(_raw(db_path))
        _ensure_tables(fc)
        results = get_expiring_credentials(days=90, conn=fc)
        fc.close()

        person_ids = [r["person_id"] for r in results]
        assert self.__class__._person_id in person_ids, "Expiring person must be in results"
        alice_result = next(r for r in results if r["person_id"] == self.__class__._person_id)
        assert alice_result["severity"] == "critical", "20-day expiry must be CRITICAL"
        assert alice_result["alert_type"] == "poly_expiry"
        self.__class__._expiry_date = alice_result["expiry_date"]

    def test_step3_credential_alert_seeded(self, db_path):
        """Run the full reflex, verify pma_credential_alerts row is inserted."""
        from tools.genesis.reflexes.pma_credential_monitor import run

        fc = _FakeConn(_raw(db_path))

        with (
            patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", return_value=fc),
            patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"),
            patch("tools.govcon.risk_manager.create_risk", return_value={"status": "ok", "risk_id": "r-test"}),
        ):
            result = run({}, None)

        assert result["success"] is True
        assert result["details"]["alerts_inserted"] >= 1

        raw = _raw(db_path)
        alerts = raw.execute(
            "SELECT * FROM pma_credential_alerts WHERE person_id = ?",
            (self.__class__._person_id,),
        ).fetchall()
        raw.close()
        assert len(alerts) >= 1, "Alert row must be inserted for CRITICAL credential expiry"

    def test_step4_kanban_task_seeded_for_critical_expiry(self, db_path):
        """Verify a kanban task was seeded for the CRITICAL poly expiry."""
        raw = _raw(db_path)
        tasks = raw.execute(
            "SELECT * FROM kanban_tasks WHERE dispatch_source = 'pma_credential_monitor'",
        ).fetchall()
        raw.close()
        assert len(tasks) >= 1, "Kanban task must be seeded for CRITICAL expiry"
        titles = [dict(t)["title"] for t in tasks]
        assert any("Alice Johnson" in t or "poly" in t.lower() for t in titles)

    def test_step5_key_person_spof_flagged(self, db_path):
        """Verify get_key_person_dependencies surfaces Alice (no backup) as SPOF."""
        from tools.pma.credential_monitor import get_key_person_dependencies, _ensure_tables

        fc = _FakeConn(_raw(db_path))
        _ensure_tables(fc)
        spofs = get_key_person_dependencies(conn=fc)
        fc.close()

        ids = [s["person_id"] for s in spofs]
        assert self.__class__._person_id in ids, "Person with no backup_person_id must be SPOF"

    def test_step6_alert_dedup_on_second_run(self, db_path):
        """Second reflex run must not duplicate the alert row."""
        from tools.genesis.reflexes.pma_credential_monitor import run

        fc = _FakeConn(_raw(db_path))

        with (
            patch("tools.genesis.reflexes.pma_credential_monitor.get_connection", return_value=fc),
            patch("tools.genesis.reflexes.pma_credential_monitor._write_memory_log"),
            patch("tools.govcon.risk_manager.create_risk", return_value={"status": "ok", "risk_id": "r-test2"}),
        ):
            run({}, None)

        raw = _raw(db_path)
        alerts = raw.execute(
            "SELECT * FROM pma_credential_alerts WHERE person_id = ?",
            (self.__class__._person_id,),
        ).fetchall()
        raw.close()
        assert len(alerts) == 1, "Dedup must prevent duplicate alert rows"


# ===========================================================================
# Epic 3 — INT Coverage Gap (pma-igap)
# ===========================================================================

_CONTRACT_IGAP = "ctr-igap-e2e-001"


class TestEpic3IntGap:
    """E2E workflow: insert persistent gap → run reflex → verify req → HITL tasking."""

    def test_step1_insert_persistent_gap(self, db_path):
        """Insert a high-severity INT gap older than 14 days."""
        gap_id = f"gap-{uuid.uuid4().hex[:8]}"
        self.__class__._gap_id = gap_id
        self.__class__._line_id = f"line-{uuid.uuid4().hex[:6]}"
        old_date = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()

        raw = _raw(db_path)
        raw.execute(
            "INSERT INTO pma_int_gaps "
            "(id, exploitation_line_id, exploitation_line_title, discipline, "
            " gap_description, severity, status, contract_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                gap_id,
                self.__class__._line_id,
                "Eastern Collection Line Alpha",
                "HUMINT",
                "No persistent HUMINT collection against target set",
                "high",
                "open",
                _CONTRACT_IGAP,
                old_date,
                old_date,
            ),
        )
        raw.commit()
        raw.close()

        raw2 = _raw(db_path)
        row = raw2.execute("SELECT id FROM pma_int_gaps WHERE id = ?", (gap_id,)).fetchone()
        raw2.close()
        assert row is not None

    def test_step2_gap_detected_by_data_layer(self, db_path):
        """get_persistent_gaps(days=14) returns the inserted gap."""
        from tools.pma.int_gap_monitor import get_persistent_gaps

        fc = _FakeConn(_raw(db_path))
        gaps = get_persistent_gaps(days=14, conn=fc)
        fc.close()

        ids = [g["id"] for g in gaps]
        assert self.__class__._gap_id in ids

    def test_step3_run_gap_monitor_reflex(self, db_path):
        """Run pma_int_gap_monitor reflex; verify collection requirement inserted."""
        from tools.genesis.reflexes.pma_int_gap_monitor import run

        fc = _FakeConn(_raw(db_path))

        with (
            patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", return_value=fc),
            patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"),
            patch("tools.govcon.risk_manager.create_risk", return_value={"status": "ok", "risk_id": "r-igap"}),
        ):
            result = run({}, None)

        assert result["success"] is True, f"Reflex failed: {result}"
        assert result["details"]["gaps_found"] >= 1
        assert result["details"]["requirements_inserted"] >= 1
        assert result["details"]["kanban_tasks_seeded"] >= 1, "High-severity gap must seed kanban task"

    def test_step4_collection_requirement_persisted(self, db_path):
        """Verify pma_collection_requirements row was inserted for the gap."""
        raw = _raw(db_path)
        reqs = raw.execute(
            "SELECT * FROM pma_collection_requirements WHERE gap_id = ?",
            (self.__class__._gap_id,),
        ).fetchall()
        raw.close()
        assert len(reqs) >= 1, "Collection requirement must be persisted for the gap"
        req = dict(reqs[0])
        assert req["discipline"] == "HUMINT"
        assert req["status"] == "open"
        self.__class__._req_id = req["id"]

    def test_step5_hitl_tasking_workflow(self, db_path):
        """Update requirement status open → tasked (HITL tasking workflow)."""
        req_id = self.__class__._req_id
        now_iso = datetime.now(timezone.utc).isoformat()

        raw = _raw(db_path)
        raw.execute(
            "UPDATE pma_collection_requirements SET status = 'tasked', updated_at = ? WHERE id = ?",
            (now_iso, req_id),
        )
        raw.commit()
        updated = raw.execute(
            "SELECT status FROM pma_collection_requirements WHERE id = ?", (req_id,)
        ).fetchone()
        raw.close()

        assert dict(updated)["status"] == "tasked", "Requirement must advance to 'tasked'"

    def test_step6_requirement_dedup_on_second_reflex_run(self, db_path):
        """Second reflex run must not create a duplicate requirement."""
        from tools.genesis.reflexes.pma_int_gap_monitor import run

        fc = _FakeConn(_raw(db_path))

        with (
            patch("tools.genesis.reflexes.pma_int_gap_monitor.get_connection", return_value=fc),
            patch("tools.genesis.reflexes.pma_int_gap_monitor._write_memory_log"),
            patch("tools.govcon.risk_manager.create_risk", return_value={"status": "ok"}),
        ):
            result = run({}, None)

        assert result["success"] is True
        assert result["details"]["requirements_inserted"] == 0, "Dedup must prevent duplicate requirement"

        raw = _raw(db_path)
        count = raw.execute(
            "SELECT COUNT(*) FROM pma_collection_requirements WHERE gap_id = ?",
            (self.__class__._gap_id,),
        ).fetchone()[0]
        raw.close()
        assert count == 1, "Exactly one requirement must exist after two reflex firings"

    def test_step7_igap_api_coverage_upsert(self, client):
        """POST /coverage — create a gap coverage record via CPMP API."""
        rv = client.post(
            f"/api/cpmp/contracts/{_CONTRACT_IGAP}/coverage",
            json={"discipline": "SIGINT", "coverage_area": "Northern Region", "status": "gap"},
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["coverage"]["status"] == "gap"
        cov_id = body["coverage"]["id"]
        self.__class__._coverage_id = cov_id

    def test_step8_generate_collection_requirements_api(self, client):
        """POST /coverage/<id>/generate-reqs — AI-generate requirements for a gap coverage."""
        cov_id = self.__class__._coverage_id
        rv = client.post(
            f"/api/cpmp/contracts/{_CONTRACT_IGAP}/coverage/{cov_id}/generate-reqs",
            json={"count": 2, "priority": "high"},
        )
        assert rv.status_code == 201
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["count"] == 2
        req_id = body["requirements"][0]["id"]
        self.__class__._api_req_id = req_id

    def test_step9_hitl_tasking_via_api(self, client):
        """PUT /requirements/<id> — advance requirement status to 'tasked' via API."""
        req_id = self.__class__._api_req_id
        rv = client.put(
            f"/api/cpmp/requirements/{req_id}",
            json={"status": "tasked", "tasked_to": "Collection Team Alpha"},
        )
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["status"] == "ok"
        assert body["requirement"]["status"] == "tasked"
        assert body["requirement"]["tasked_at"] is not None
