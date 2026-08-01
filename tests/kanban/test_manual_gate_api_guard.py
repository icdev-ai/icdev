#!/usr/bin/env python3
# CUI // SP-CTI
"""A manual-mode gate must be immovable from the board — through EVERY door.

Regression for 2026-07-12: the gate guard was added to ``/move`` only. A
``PATCH /api/kanban/tasks/<id>`` with ``{"status": "done"}`` sailed straight past
it (PATCH accepts ``status`` in its ``allowed`` list), completed ``prem-gate-00``,
and the scheduler promoted all 28 gated tasks — 3 of them reached dispatch, on
work targeting private external repos ICDev cannot build in.

The lesson the test encodes: **a gate has more than one door.** Guarding the
obvious one is not the same as guarding the invariant. Every endpoint that can
write ``kanban_tasks.status`` is exercised here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban.gates import is_manual_gate  # noqa: E402


GATE_ID = "test-gate-00"
GATE_TITLE = "MANUAL-MODE GATE - do not complete; do not dispatch"
DEP_ID = "test-gate-dependent-01"


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------


def test_predicate_matches_on_id_suffix():
    assert is_manual_gate("prem-gate-00", None) is True
    assert is_manual_gate("anything-gate-00", "") is True


def test_predicate_matches_on_title_marker():
    """Either signal alone is enough, so renaming one does not silently un-gate."""
    assert is_manual_gate("some-other-id", GATE_TITLE) is True


def test_predicate_rejects_normal_tasks():
    assert is_manual_gate("prem-bid-01", "Bid pricing engine") is False
    assert is_manual_gate(None, None) is False
    # Must not match a task that merely mentions a gate in prose.
    assert is_manual_gate("prem-bid-02", "Add a gate to the pricing flow") is False


def test_all_call_sites_share_one_predicate():
    """The predicate used to be copy-pasted in 3 modules. It must not drift again."""
    from tools.genesis.kanban_scheduler import _is_manual_gate as sched
    from tools.genesis.reflexes.kanban import _is_manual_gate as reflex
    from tools.kanban.promote_backlog_to_scheduled import _is_manual_gate as promote

    assert sched is reflex is promote is is_manual_gate


# ---------------------------------------------------------------------------
# Every API door
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Flask test client wired to a throwaway SQLite DB holding a gate + dependent."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection as _real_get_connection

    db_path = tmp_path / "gate.db"
    seed = _real_get_connection(db_path=str(db_path))
    seed.executescript(MINIMAL_ICDEV_SCHEMA)
    for tid, title, status, dep in (
        (GATE_ID, GATE_TITLE, "in_progress", None),
        (DEP_ID, "Gated work", "backlog", GATE_ID),
    ):
        seed.execute(
            "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
            "VALUES (%s, %s, %s, %s)",
            (tid, title, status, dep),
        )
    seed.commit()

    import tools.dashboard.api.kanban as kanban_api_mod

    monkeypatch.setattr(
        kanban_api_mod,
        "get_connection",
        lambda *_a, **_kw: _real_get_connection(db_path=str(db_path)),
    )

    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(kanban_api_mod.kanban_api)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _gate_status(client) -> str:
    """Read the gate straight back out of the DB the API just wrote to."""
    import tools.dashboard.api.kanban as kanban_api_mod

    conn = kanban_api_mod.get_connection()
    row = conn.execute(
        "SELECT status FROM kanban_tasks WHERE id = %s", (GATE_ID,)
    ).fetchone()
    return dict(row)["status"]


def test_move_endpoint_refuses_to_complete_a_gate(client):
    r = client.post(f"/api/kanban/tasks/{GATE_ID}/move", json={"status": "done"})
    assert r.status_code == 409, r.get_data(as_text=True)
    assert _gate_status(client) == "in_progress"


def test_move_endpoint_refuses_ANY_status_change_to_a_gate(client):
    """Not just `done`.

    A move to `done` is already refused by the unrelated verification gate (guard-7
    wants a passing kanban_verifications row), so asserting only on `done` would pass
    even with no gate guard at all — green for the wrong reason. `scheduled` has no
    such coincidental protection, so this is the assertion that actually pins the
    gate guard.
    """
    r = client.post(f"/api/kanban/tasks/{GATE_ID}/move", json={"status": "scheduled"})
    assert r.status_code == 409, r.get_data(as_text=True)
    assert "gate" in r.get_json()["error"].lower()
    assert _gate_status(client) == "in_progress"


def test_patch_endpoint_refuses_to_complete_a_gate(client):
    """THE regression. This door was open and the gate went through it."""
    r = client.patch(f"/api/kanban/tasks/{GATE_ID}", json={"status": "done"})
    assert r.status_code == 409, r.get_data(as_text=True)
    assert _gate_status(client) == "in_progress"


def test_bulk_move_refuses_a_gate_but_still_moves_the_rest(client):
    r = client.post(
        "/api/kanban/tasks/bulk-move",
        json={"task_ids": [GATE_ID, DEP_ID], "status": "done"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert [f["id"] for f in body["failed"]] == [GATE_ID]
    assert body["moved"] == 1          # the non-gate task still moved
    assert _gate_status(client) == "in_progress"


def test_delete_endpoint_refuses_a_gate(client):
    """Deleting the sentinel STRANDS its dependents rather than releasing them."""
    r = client.delete(f"/api/kanban/tasks/{GATE_ID}")
    assert r.status_code == 409, r.get_data(as_text=True)
    assert _gate_status(client) == "in_progress"


def test_patch_still_allows_non_status_edits_on_a_gate(client):
    """The guard fires on status changes only — it must not freeze the whole row."""
    r = client.patch(f"/api/kanban/tasks/{GATE_ID}", json={"priority": "high"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _gate_status(client) == "in_progress"


def test_normal_task_is_unaffected(client):
    """The guard must not become a board-wide freeze."""
    r = client.patch(f"/api/kanban/tasks/{DEP_ID}", json={"status": "scheduled"})
    assert r.status_code == 200, r.get_data(as_text=True)
