"""Cross-process LLM concurrency lease (OS file-lock slots).

Same-process test: each ``acquire`` opens a fresh file handle, and both
``fcntl.flock`` (POSIX) and ``msvcrt.locking`` (Windows) conflict across
separate handles — even within one process — so slot exhaustion is observable
here without spawning subprocesses.
"""
import pytest

from tools.llm import cross_process_lease as cpl


@pytest.fixture(autouse=True)
def _lease_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_LLM_LEASE_DIR", str(tmp_path))
    yield


def test_zero_slots_returns_none():
    assert cpl.acquire("x", 0, timeout=0) is None


def test_single_slot_mutual_exclusion():
    a = cpl.acquire("t", 1, timeout=0)
    assert a is not None
    # slot 0 is held — a second acquire cannot get it
    b = cpl.acquire("t", 1, timeout=0.2)
    assert b is None
    a.release()
    # freed — now available again
    c = cpl.acquire("t", 1, timeout=0.5)
    assert c is not None
    c.release()


def test_two_slots_allow_two_then_block():
    a = cpl.acquire("t2", 2, timeout=0)
    b = cpl.acquire("t2", 2, timeout=0)
    assert a is not None and b is not None
    # both slots taken -> third blocks out
    c = cpl.acquire("t2", 2, timeout=0.2)
    assert c is None
    a.release()
    # one freed -> acquire succeeds again
    d = cpl.acquire("t2", 2, timeout=0.4)
    assert d is not None
    b.release()
    d.release()


def test_lease_is_context_manager():
    with cpl.acquire("cm", 1, timeout=0) as lease:
        assert lease is not None
        assert cpl.acquire("cm", 1, timeout=0.1) is None  # held inside the block
    # released on exit
    again = cpl.acquire("cm", 1, timeout=0.3)
    assert again is not None
    again.release()


def test_release_is_idempotent():
    a = cpl.acquire("idem", 1, timeout=0)
    a.release()
    a.release()  # must not raise
