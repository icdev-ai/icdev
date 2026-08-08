# CUI // SP-CTI
"""Tests for the Network Migration Canvas genesis reflex (NMCE).

Regression cover for the duplicate-promotion defect: the reflex minted a fresh
``uuid4()`` task id on every run, so its ``INSERT OR IGNORE`` guard never
matched and each 24h cycle re-promoted every open finding as a brand-new
kanban card.
"""

from __future__ import annotations

import importlib

import pytest

reflex = importlib.import_module("tools.genesis.reflexes.migration_canvas")


# ── Deterministic task ids ────────────────────────────────────────────────

def test_finding_task_id_is_stable_across_calls():
    finding = {
        "type": "stale_migration_session",
        "session_id": "nmig-ac17558e9b27",
        "suggested_action": "Review or close session nmig-ac17558e9b27",
    }
    assert reflex._finding_task_id(finding) == reflex._finding_task_id(dict(finding))


def test_finding_task_id_ignores_volatile_message_and_confidence():
    """Age and confidence change every run — they must not change the id."""
    base = {
        "type": "stale_migration_session",
        "session_id": "nmig-ac17558e9b27",
        "suggested_action": "Review or close session nmig-ac17558e9b27",
    }
    day45 = dict(base, message="... 45 days ...", confidence=0.95)
    day46 = dict(base, message="... 46 days ...", confidence=0.96)
    assert reflex._finding_task_id(day45) == reflex._finding_task_id(day46)


def test_finding_task_id_differs_per_session():
    a = {"type": "stale_migration_session", "session_id": "nmig-aaa",
         "suggested_action": "Review or close session nmig-aaa"}
    b = {"type": "stale_migration_session", "session_id": "nmig-bbb",
         "suggested_action": "Review or close session nmig-bbb"}
    assert reflex._finding_task_id(a) != reflex._finding_task_id(b)


def test_finding_task_id_differs_per_protocol_on_same_session():
    """Stale protocol plans share a session_id — the protocol disambiguates."""
    vlan = {"type": "stale_protocol_plan", "session_id": "nmig-aaa",
            "suggested_action": "Complete or approve vlan migration plan for session nmig-aaa"}
    bgp = {"type": "stale_protocol_plan", "session_id": "nmig-aaa",
           "suggested_action": "Complete or approve bgp migration plan for session nmig-aaa"}
    assert reflex._finding_task_id(vlan) != reflex._finding_task_id(bgp)


def test_finding_task_id_matches_board_prefix():
    finding = {"type": "eol_no_migration", "device_id": "dev-1",
               "suggested_action": "Start network migration session for core-rtr-01"}
    tid = reflex._finding_task_id(finding)
    assert tid.startswith("mc-reflex-")
    assert len(tid) == len("mc-reflex-") + 8


# ── Terminal status vocabulary ────────────────────────────────────────────

def test_terminal_statuses_cover_canonical_canvas_vocabulary():
    """A session closed the way the canvas closes it must silence the reflex.

    blueprint.py and network_migration.py both treat 'complete' and 'archived'
    as closed; the reflex previously used the non-existent spelling 'completed'
    for its EOL check, so a completed migration still counted as active.
    """
    from tools.migration_canvas.constants import NET_SESSION_TERMINAL_STATUSES

    assert "complete" in NET_SESSION_TERMINAL_STATUSES
    assert "archived" in NET_SESSION_TERMINAL_STATUSES
    assert set(reflex._TERMINAL_SESSION_STATUSES) >= {"complete", "archived"}


def test_terminal_statuses_are_a_subset_of_the_allowed_status_vocabulary():
    from tools.migration_canvas.constants import (
        NET_SESSION_STATUSES,
        NET_SESSION_TERMINAL_STATUSES,
    )

    assert set(NET_SESSION_TERMINAL_STATUSES) <= set(NET_SESSION_STATUSES)
    assert "in_progress" in NET_SESSION_STATUSES


# ── Promotion is idempotent across runs ───────────────────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingConn:
    """Minimal connection double capturing INSERTs issued by the reflex."""

    def __init__(self, rows_by_table, inserts):
        self._rows_by_table = rows_by_table
        self.inserts = inserts

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("INSERT"):
            self.inserts.append(params)
            return _FakeCursor([])
        for table, rows in self._rows_by_table.items():
            if table in sql:
                if "status NOT IN" in sql:
                    excluded = set(params or ())
                    rows = [r for r in rows if r.get("status") not in excluded]
                return _FakeCursor(rows)
        return _FakeCursor([])

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def stale_session_row():
    return {
        "id": "nmig-ac17558e9b27",
        "src_model": "8101-32H",
        "tgt_model": "",
        "status": "in_progress",
        "updated_at": "2026-06-18T02:34:11.529300+00:00",
        "created_at": "2026-06-18T02:34:11.529300+00:00",
    }


def _patch_conns(monkeypatch, session_rows, inserts):
    mc_db = importlib.import_module("tools.migration_canvas.db.init_db")
    storage = importlib.import_module("tools.db.storage")
    netmig = importlib.import_module("tools.migration_canvas.network_migration")

    monkeypatch.setattr(
        mc_db, "get_connection",
        lambda: _RecordingConn({"mc_net_sessions": session_rows,
                                "mc_net_protocol_plans": []}, inserts),
    )
    monkeypatch.setattr(
        storage, "get_connection",
        lambda *a, **k: _RecordingConn({}, inserts),
    )
    # EOL check reads the network inventory — keep it empty for this test.
    monkeypatch.setattr(netmig, "_nc_conn", lambda: _RecordingConn({"ni_devices": []}, inserts))
    monkeypatch.setattr(netmig, "_mc_conn", lambda: _RecordingConn({"mc_net_sessions": []}, inserts))


def test_repeated_runs_promote_the_same_task_id(monkeypatch, stale_session_row):
    """Two reflex cycles over unchanged data must not mint two card ids."""
    inserts: list = []
    _patch_conns(monkeypatch, [stale_session_row], inserts)

    first = reflex.run({}, None)
    second = reflex.run({}, None)

    assert first["success"] and second["success"]
    task_ids = {p[0] for p in inserts if p and str(p[0]).startswith("mc-reflex-")}
    assert len(task_ids) == 1, f"expected one stable card id, got {task_ids}"


def test_patch_rejects_a_status_outside_the_vocabulary(monkeypatch):
    """Closing a session with an unknown status must fail loudly.

    The reflex treats anything non-terminal as still open, so a session
    "closed" as e.g. 'done' would keep generating a card every cycle.
    """
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    from flask import Flask

    from tools.migration_canvas.blueprint import create_migration_blueprint

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(create_migration_blueprint())
    client = app.test_client()

    rejected = client.patch("/api/network-migration/nmig-nonexistent",
                            json={"status": "done"})
    assert rejected.status_code == 400
    assert "archived" in rejected.get_json()["allowed"]

    accepted = client.patch("/api/network-migration/nmig-nonexistent",
                            json={"status": "archived"})
    assert accepted.status_code == 200


def test_closed_session_is_not_flagged(monkeypatch, stale_session_row):
    """An archived session must produce no stale-session finding."""
    inserts: list = []
    closed = dict(stale_session_row, status="archived")
    _patch_conns(monkeypatch, [closed], inserts)

    result = reflex.run({}, None)

    assert result["details"]["breakdown"]["stale_sessions"] == 0
