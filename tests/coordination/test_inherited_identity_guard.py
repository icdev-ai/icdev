# CUI // SP-CTI
"""A descendant never writes the service's registry row (claim-verif-33c9f4cd11).

THE DEFECT, observed live 2026-09-03. `claim_service_identity` puts the
scheduler's `kanban-scheduler-<pid>` id into `os.environ`, and every process the
scheduler spawns inherits it: the kanban worker session, and every command that
worker runs. Any of them calling `session_registry` wrote THE SCHEDULER'S ROW --
`register()` replaced its pid, `heartbeat()` refreshed it on the child's behalf,
the Stop hook's `end_session()` could mark it ended. The claim
`scheduler_heartbeat_is_fresh` then read "scheduler pid 22508 is alive; the
registry's fresh kanban row carries pid 31872" and filed a card for a scheduler
that was looping normally.

Ownership is PROCESS-LOCAL: a name this process claimed. An id that embeds a pid
that is not ours, for a name we never claimed, is inherited, and the registry
writes it under `child_session_id`. A re-executed service claims its name again
in main() before touching the registry, so its inherited id stays its own -- the
self-update property the pid-based id exists for is untouched.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.coordination import service_identity as si  # noqa: E402
from tools.coordination import session_registry as reg  # noqa: E402

ME = os.getpid()
OTHER = ME + 100_003  # a pid that is not this process


@pytest.fixture(autouse=True)
def _own_nothing(monkeypatch):
    """Ownership is process-local state; each test starts owning no name."""
    monkeypatch.setattr(si, "_OWNED", set())
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    try:
        import tools.airgap.hook_compat as hc
        monkeypatch.setattr(hc, "_session_id", None, raising=False)
    except Exception:  # noqa: BLE001
        pass
    yield


# --------------------------------------------------------------------------- #
# 1. Reading an id
# --------------------------------------------------------------------------- #
def test_embedded_pid_reads_the_trailing_number():
    assert si.embedded_pid("kanban-scheduler-22508") == 22508
    assert si.embedded_pid("pr-watcher-7") == 7


def test_an_id_with_no_pid_embeds_none():
    assert si.embedded_pid("kanban-scheduler") is None
    assert si.embedded_pid("set-by-the-launcher") is None
    assert si.embedded_pid("") is None
    assert si.embedded_pid("-42") is None


def test_our_own_service_id_is_not_inherited():
    assert not si.is_inherited_identity(si.service_session_id("kanban-scheduler"))


def test_another_process_id_for_a_name_we_never_claimed_is_inherited():
    """The worker session's exact situation."""
    assert si.is_inherited_identity(f"kanban-scheduler-{OTHER}")


def test_a_re_executed_service_keeps_its_inherited_id():
    """code_reload.respawn on Windows spawns a NEW pid and the environment
    carries the old id across; main() claims the name again, so it is owned."""
    si.claim_service_identity("kanban-scheduler", "kanban")
    assert not si.is_inherited_identity(f"kanban-scheduler-{OTHER}")


def test_claiming_one_name_does_not_own_another():
    """`daemon.py --reflex` run INSIDE a worker claims `genesis-daemon` while
    holding the scheduler's id; it must not thereby own the scheduler's row."""
    si.claim_service_identity("genesis-daemon", "genesis")
    assert si.is_inherited_identity(f"kanban-scheduler-{OTHER}")


def test_an_orchestrator_id_without_a_pid_is_never_inherited():
    assert not si.is_inherited_identity("set-by-the-launcher")
    assert not si.is_inherited_identity("already-claimed-42", pid=42)


# --------------------------------------------------------------------------- #
# 2. The child id
# --------------------------------------------------------------------------- #
def test_the_child_id_keeps_the_parent_readable_and_names_the_child():
    cid = si.child_session_id(f"kanban-scheduler-{OTHER}", pid=555)
    assert cid == f"kanban-scheduler-{OTHER}/child-555"
    assert si.is_child_identity(cid)
    assert si.embedded_pid(cid) == 555


def test_a_child_id_is_its_own_for_the_child():
    """The child must not re-derive itself on every call."""
    cid = si.child_session_id(f"kanban-scheduler-{OTHER}")
    assert not si.is_inherited_identity(cid)


def test_a_grandchild_gets_its_own_child_id():
    cid = si.child_session_id(f"kanban-scheduler-{OTHER}", pid=555)
    assert si.is_inherited_identity(cid)
    assert si.child_session_id(cid, pid=777).endswith("/child-555/child-777")


def test_a_child_row_is_not_one_of_the_services_processes():
    """The claim reads scheduler rows through this predicate; a child carrying
    the scheduler's id as a PREFIX must not count as a scheduler."""
    assert si.is_service_session(f"kanban-scheduler-{OTHER}", "kanban-scheduler")
    assert not si.is_service_session(
        f"kanban-scheduler-{OTHER}/child-555", "kanban-scheduler")


# --------------------------------------------------------------------------- #
# 3. The registry, driven as the descendant
# --------------------------------------------------------------------------- #
def _fresh() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def parent_row(tmp_path, monkeypatch):
    """A scratch registry holding the SCHEDULER'S row, and us as its child."""
    from tools.db.storage import get_connection

    db = tmp_path / "registry.db"
    monkeypatch.setattr(reg, "_conn", lambda: get_connection(db_path=str(db)))
    parent = f"kanban-scheduler-{OTHER}"
    monkeypatch.setenv("ICDEV_SESSION_ID", parent)
    monkeypatch.setenv("ICDEV_AGENT", "kanban")
    # `_ensure_table` remembers that it ran, per process; this is a NEW file.
    monkeypatch.setattr(reg, "_table_ready", False)
    conn = reg._conn()
    conn.execute(reg._DDL)
    conn.commit()
    conn.execute(
        "INSERT INTO agent_sessions (session_id, agent_type, pid, host, cwd, "
        "started_at, last_heartbeat, current_intent, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active')",
        (parent, "kanban", OTHER, "h", "c", _fresh(), _fresh(), "cycle 9"),
    )
    conn.commit()
    conn.close()
    return parent


def _row(sid: str):
    conn = reg._conn()
    try:
        r = conn.execute(
            "SELECT * FROM agent_sessions WHERE session_id = %s", (sid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def test_register_from_a_child_leaves_the_parents_pid_alone(parent_row):
    assert reg.register(intent="worker").get("ok")
    parent = _row(parent_row)
    assert parent["pid"] == OTHER, "the child re-registered the SCHEDULER'S row"
    child = _row(si.child_session_id(parent_row))
    assert child is not None and child["pid"] == ME
    assert child["current_intent"] == "worker"


def test_heartbeat_from_a_child_does_not_refresh_the_parent(parent_row):
    old = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    conn = reg._conn()
    conn.execute("UPDATE agent_sessions SET last_heartbeat = %s WHERE session_id = %s",
                 (old, parent_row))
    conn.commit()
    conn.close()
    assert reg.heartbeat(intent="still here") is True
    assert _row(parent_row)["last_heartbeat"] == old, (
        "a child's heartbeat vouched for a parent it knows nothing about")
    assert _row(si.child_session_id(parent_row))["current_intent"] == "still here"


def test_end_session_from_a_child_does_not_end_the_parent(parent_row):
    reg.register(intent="worker")
    assert reg.end_session() is True
    assert _row(parent_row)["status"] == "active"
    assert _row(si.child_session_id(parent_row))["status"] == "ended"


def test_the_parent_is_one_of_the_childs_others(parent_row):
    """From inside the worker the scheduler IS another session -- the old code
    hid it, because `others()` excluded the inherited id as 'me'."""
    reg.register(intent="worker")
    ids = {s["session_id"] for s in reg.others()}
    assert parent_row in ids
    assert si.child_session_id(parent_row) not in ids


def test_the_service_itself_still_writes_its_own_row(parent_row):
    """The guard is for DESCENDANTS only. The process that claimed the name --
    including one re-executed under a new pid -- keeps writing the row."""
    si.claim_service_identity("kanban-scheduler", "kanban")
    assert reg.register(intent="cycle 10").get("ok")
    parent = _row(parent_row)
    assert parent["pid"] == ME
    assert parent["current_intent"] == "cycle 10"
    assert _row(si.child_session_id(parent_row)) is None
