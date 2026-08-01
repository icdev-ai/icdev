"""ChatManager.shutdown must actually stop agent loop threads.

create_context spawns a daemon thread per context. close_context sets that
context's stop event but never joins the thread, and nothing addressed all
contexts at once -- so a process that created contexts accumulated loops that
outlived whatever created them.

In the test suite those loops poll on a 0.1s sleep and keep writing (task rows,
message rows, budget usage) while LATER tests run, so their work is attributed
to whichever test is executing and any transaction they hold blocks DDL on
other connections. These tests pin the stop-and-join behaviour the autouse
teardown in conftest depends on.
"""

import pytest

from tools.dashboard.chat_manager import chat_manager


@pytest.fixture
def context_id():
    """Create a chat context, guaranteeing it is torn down."""
    created = chat_manager.create_context(
        user_id="shutdown-test-user",
        title="shutdown probe",
    )
    cid = created["context_id"]
    yield cid
    chat_manager.close_context(cid)
    chat_manager.shutdown(timeout=2.0)


def _thread_for(cid):
    ctx = chat_manager._contexts.get(cid)
    return getattr(ctx, "_thread", None) if ctx else None


def test_create_context_starts_a_live_agent_loop(context_id):
    """Baseline: the thread this is all about really is running."""
    thread = _thread_for(context_id)
    assert thread is not None, "create_context did not attach a thread"
    assert thread.is_alive(), "agent loop thread is not running"


def test_shutdown_stops_the_agent_loop(context_id):
    thread = _thread_for(context_id)
    assert thread.is_alive()

    still_alive = chat_manager.shutdown(timeout=5.0)

    assert still_alive == 0, f"{still_alive} agent loop(s) survived shutdown"
    assert not thread.is_alive(), "agent loop thread did not stop"


def test_shutdown_joins_rather_than_only_signalling(context_id):
    """The thread is gone when shutdown RETURNS, not merely asked to stop.

    close_context sets the stop event and returns immediately, so the loop can
    still be mid-write afterwards. Joining is what makes the teardown a real
    boundary between tests rather than a request that may not have landed yet.
    """
    thread = _thread_for(context_id)
    chat_manager.shutdown(timeout=5.0)
    # No sleep, no retry — if shutdown only signalled, this races and fails.
    assert not thread.is_alive()


def test_shutdown_is_idempotent_and_safe_with_no_contexts():
    """Called twice, or with nothing running, it reports zero and does not raise."""
    chat_manager.shutdown(timeout=2.0)
    assert chat_manager.shutdown(timeout=2.0) == 0


def test_shutdown_does_not_leak_threads_across_contexts():
    """Several contexts all stop, and none are left behind.

    Asserts on the agent loop threads specifically rather than on
    ``threading.active_count()``. create_context and close_context also spawn
    short-lived helper threads (hook dispatch, requirement intake) that this
    method neither owns nor should wait for, so a global thread count is both
    flaky and not the claim being made.
    """
    ids = [
        chat_manager.create_context(user_id=f"multi-{i}", title=f"probe {i}")["context_id"]
        for i in range(3)
    ]
    threads = [_thread_for(cid) for cid in ids]
    assert all(t is not None and t.is_alive() for t in threads)

    assert chat_manager.shutdown(timeout=5.0) == 0

    alive = [t.name for t in threads if t.is_alive()]
    assert not alive, f"agent loop thread(s) survived shutdown: {alive}"

    for cid in ids:
        chat_manager.close_context(cid)

    # Nothing anywhere in the manager is still running an agent loop.
    surviving = [
        cid for cid in ids
        if (t := _thread_for(cid)) is not None and t.is_alive()
    ]
    assert not surviving, f"contexts still running after shutdown: {surviving}"
