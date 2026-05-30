# CUI // SP-CTI
"""Regression tests for tools/coordination — cross-session coordination.

Backend-agnostic: leases are filelock+file based; the registry self-creates its
table on the conftest-forced SQLite backend. Two logical sessions are simulated
in one process by switching ICDEV_SESSION_ID and resetting the cached id.
"""
import os

import pytest

from tools.coordination import leases, session_registry as reg


def _as_session(sid: str, agent: str = "claude") -> None:
    os.environ["ICDEV_SESSION_ID"] = sid
    os.environ["CLAUDE_SESSION_ID"] = sid
    os.environ["ICDEV_AGENT"] = agent
    try:
        import tools.airgap.hook_compat as hc
        hc._session_id = None  # reset cached resolver
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_leases():
    from tools.coordination.constants import LEASE_DIR
    import shutil
    if LEASE_DIR.exists():
        shutil.rmtree(LEASE_DIR)
    yield
    if LEASE_DIR.exists():
        shutil.rmtree(LEASE_DIR)


def test_hard_lease_refuses_other_session():
    res = "service:test-dashboard"
    _as_session("sess-A")
    a = leases.acquire(res, intent="restart", block=False)
    assert a is not None
    _as_session("sess-B")
    b = leases.acquire(res, intent="restart", block=False)
    assert b is None, "hard lease must refuse a second session"
    assert leases.holder(res)["holder_session"] == "sess-A"
    _as_session("sess-A")
    assert leases.release(res) is True
    _as_session("sess-B")
    assert leases.acquire(res, block=False) is not None, "freed lease is acquirable"
    leases.release(res)


def test_soft_file_lease_warns_but_allows():
    res = "file:tools/example.py"
    _as_session("sess-A")
    leases.acquire(res, intent="editing")
    _as_session("sess-B")
    b = leases.acquire(res, intent="editing")
    assert b is not None, "soft file lease never blocks"
    assert b.prior_holder is not None
    assert b.prior_holder["holder_session"] == "sess-A"


def test_release_only_by_holder():
    res = "service:test-x"
    _as_session("sess-A")
    leases.acquire(res, block=False)
    _as_session("sess-B")
    assert leases.release(res) is False, "non-holder cannot release"
    assert leases.holder(res) is not None
    _as_session("sess-A")
    assert leases.release(res) is True


def test_registry_sees_other_sessions():
    _as_session("reg-A")
    reg.register(intent="task A")
    _as_session("reg-B")
    reg.register(intent="task B")
    sids = {s["session_id"] for s in reg.list_active()}
    assert {"reg-A", "reg-B"} <= sids
    others = {s["session_id"] for s in reg.others()}
    assert "reg-A" in others and "reg-B" not in others
    reg.end_session()  # ends reg-B
    active = {s["session_id"] for s in reg.list_active()}
    assert "reg-B" not in active


def test_lease_handle_context_manager():
    res = "migration:test-schema"
    _as_session("ctx-A")
    with leases.acquire(res, block=False) as h:
        assert h is not None
        assert leases.holder(res)["holder_session"] == "ctx-A"
    assert leases.holder(res) is None, "context manager releases on exit"


def test_gitlock_serializes():
    from tools.coordination.gitlock import repo_commit_lock
    with repo_commit_lock(timeout=5) as held:
        assert held is True
