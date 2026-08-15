#!/usr/bin/env python3
"""The landed check must reach the three places that act on it. CUI // SP-CTI

A primitive nobody consumes is this platform's signature defect, and a check
that computes the right answer into a log nobody reads is the same defect with
better manners. trust-disc-05 wires task -> main at the three moments where the
board would otherwise act on task -> PR alone:

  SEED     tools/kanban/task_factory.py::create_tasks -- the id is still a
           keystroke rather than a row, and one git call answers for the batch.
  DISPATCH tools/genesis/reflexes/kanban.py::_write_prompt_file -- the banner
           goes ABOVE the description, because the session reading that prompt
           is the one that would re-implement the merged work.
  PR OPEN  tools/genesis/reflexes/kanban.py::_push_branch_and_open_pr -- the
           evidence goes in the PR body, where the human deciding whether to
           merge #1651 would have seen that its -38/+26 was a revert.

Deterministic: the git/PR answer is injected in every case.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402
from tools.kanban import landed_check as lc  # noqa: E402
from tools.kanban import task_factory  # noqa: E402


_LANDED = {
    "task_id": "ctx-perf-02",
    "ref": "origin/main",
    "checked": True,
    "reason": "",
    "landed": True,
    "referenced": True,
    "confidence": lc.CONFIDENCE_SUBJECT,
    "commits": [{"sha": "d5a9ef9f6", "evidence": lc.CONFIDENCE_SUBJECT,
                 "subject": "perf(cortex): stop repaying config resolution (#ctx-perf-02) (#1641)"}],
    "prs": None,
    "blocking": False,
    "mode": "warn",
}

_CLEAN = dict(_LANDED, task_id="trust-disc-05", landed=False, referenced=False,
              confidence=None, commits=[])


@pytest.fixture(autouse=True)
def _clear_cycle_cache():
    k.clear_landed_cache()
    yield
    k.clear_landed_cache()


# --------------------------------------------------------------------------- #
# the per-cycle memo
# --------------------------------------------------------------------------- #

def test_the_answer_is_memoised_within_a_cycle_and_dropped_between(monkeypatch):
    calls = []

    def _fake(task_id, **kw):
        calls.append(task_id)
        return dict(_LANDED, task_id=task_id)

    monkeypatch.setattr(lc, "preflight", _fake)
    k._landed_preflight("ctx-perf-02")
    k._landed_preflight("ctx-perf-02")
    assert calls == ["ctx-perf-02"], "two subprocess calls per dispatch is one too many"

    k.clear_landed_cache()
    k._landed_preflight("ctx-perf-02")
    assert len(calls) == 2, "a memo that outlives its cycle reports a stale merge state"


def test_preflight_failure_fails_open(monkeypatch):
    def _boom(task_id, **kw):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(lc, "preflight", _boom)
    rep = k._landed_preflight("ctx-perf-02")
    assert rep["checked"] is False
    assert rep["landed"] is False
    assert rep["blocking"] is False, "an unreachable git must never wedge dispatch"


# --------------------------------------------------------------------------- #
# DISPATCH: the prompt the building session actually reads
# --------------------------------------------------------------------------- #

def _prompt_for(monkeypatch, tmp_path, report):
    monkeypatch.setattr(k, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(k, "_ensure_prompt_dir", lambda: None)
    monkeypatch.setattr(k, "_get_resume_context", lambda tid: "")
    monkeypatch.setattr(k, "_landed_preflight", lambda tid, **kw: report)
    path = k._write_prompt_file({
        "id": report["task_id"], "title": "Some task",
        "description": "Build the thing.", "task_type": "build",
    })
    return pathlib.Path(path).read_text(encoding="utf-8")


def test_the_dispatch_prompt_carries_the_evidence(monkeypatch, tmp_path):
    text = _prompt_for(monkeypatch, tmp_path, _LANDED)
    assert "ALREADY ON origin/main" in text
    assert "d5a9ef9f6" in text, "name the commit, not just the verdict"
    assert "do NOT re-apply" in text
    assert text.index("ALREADY ON") < text.index("## Description"), \
        "the warning must sit ABOVE the instruction to build"


def test_a_clean_task_gets_no_banner(monkeypatch, tmp_path):
    text = _prompt_for(monkeypatch, tmp_path, _CLEAN)
    assert "ALREADY ON" not in text
    assert "[!WARNING]" not in text
    assert text.startswith("# Kanban Task:")


def test_a_banner_failure_never_blocks_the_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(k, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(k, "_ensure_prompt_dir", lambda: None)
    monkeypatch.setattr(k, "_get_resume_context", lambda tid: "")

    def _boom(tid, **kw):
        raise RuntimeError("no git here")

    monkeypatch.setattr(k, "_landed_preflight", _boom)
    path = k._write_prompt_file({"id": "t-1", "title": "T", "description": "D"})
    assert "# Kanban Task: T" in pathlib.Path(path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# SEED: the cheapest place to find out
# --------------------------------------------------------------------------- #

class _ReachedTheDatabase(Exception):
    """Raised by the stub below so a test can prove where seeding got to."""


@pytest.fixture()
def stop_at_db(monkeypatch):
    """Halt create_tasks at its first DB call, without writing a board row.

    Patching this correctly is its own trap, and getting it wrong is not a
    failing test -- it is a test that quietly seeds the LIVE board. Two layers:

      * ``create_tasks`` imports ``get_connection`` INSIDE the function, so the
        patch must land on the storage module, not on ``task_factory``.
      * ``import tools.db.storage as s`` does NOT bind the same object that
        ``from tools.db.storage import get_connection`` reads from: the ``tools``
        shim resolves the former to ``icdev.tools.db.storage`` while
        ``sys.modules['tools.db.storage']`` stays a distinct module object.
        Verified here 2026-08-15 -- patching only the first let a real INSERT
        through against PostgreSQL. Patch every alias that exists.
    """
    def _stop(*a, **kw):
        raise _ReachedTheDatabase()

    import tools.db.storage as storage_alias

    for name in ("tools.db.storage", "icdev.tools.db.storage"):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "get_connection", _stop, raising=False)
    monkeypatch.setattr(storage_alias, "get_connection", _stop, raising=False)
    monkeypatch.setattr(task_factory, "_sentinel_shaped_work", lambda specs: [])


@pytest.fixture()
def seed_warnings(monkeypatch):
    """Capture task_factory's warnings.

    Not ``caplog``: these go through ``tools.logging.icdev_logger``, whose
    handlers do not propagate to the root logger, so caplog sees an empty record
    list and the assertion passes or fails for the wrong reason.
    """
    captured: list[str] = []

    class _Recorder:
        def warning(self, msg, *args):
            captured.append(msg % args if args else msg)

        def __getattr__(self, _name):
            return lambda *a, **kw: None

    monkeypatch.setattr(task_factory, "logger", _Recorder())
    return captured


def test_seeding_a_landed_id_is_reported(monkeypatch, seed_warnings, stop_at_db):
    """warn mode still seeds the row, but it must SAY what it noticed."""
    monkeypatch.setattr(lc, "check_landed_bulk",
                        lambda ids, **kw: {i: dict(_LANDED, task_id=i) for i in ids})
    monkeypatch.setattr(lc, "mode", lambda: "warn")

    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": "ctx-perf-02", "title": "x",
                                    "task_type": "build"}])
    assert any("ALREADY appear in a commit on the default branch" in m
               for m in seed_warnings), "warn mode reported nothing"
    assert any("d5a9ef9f6" in m for m in seed_warnings), "name the commit"


def test_enforce_mode_refuses_to_seed_before_any_insert(monkeypatch, stop_at_db):
    monkeypatch.setattr(lc, "check_landed_bulk",
                        lambda ids, **kw: {i: dict(_LANDED, task_id=i) for i in ids})
    monkeypatch.setattr(lc, "mode", lambda: "enforce")

    # A ValueError, NOT _ReachedTheDatabase: the refusal has to come first, or a
    # batch half-lands and the board is left inconsistent.
    with pytest.raises(ValueError, match="refusing to seed"):
        task_factory.create_tasks([{"id": "ctx-perf-02", "title": "x",
                                    "task_type": "build"}])


def test_a_clean_batch_is_not_reported(monkeypatch, seed_warnings, stop_at_db):
    monkeypatch.setattr(lc, "check_landed_bulk",
                        lambda ids, **kw: {i: dict(_CLEAN, task_id=i) for i in ids})
    monkeypatch.setattr(lc, "mode", lambda: "enforce")
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": "trust-disc-05", "title": "x"}])
    assert not any("ALREADY appear" in m for m in seed_warnings)


def test_a_git_failure_at_seed_time_does_not_break_seeding(monkeypatch, stop_at_db):
    def _boom(ids, **kw):
        raise RuntimeError("git missing")

    monkeypatch.setattr(lc, "check_landed_bulk", _boom)
    monkeypatch.setattr(lc, "mode", lambda: "enforce")
    # Must get PAST the landed check to the DB, not be refused by it.
    with pytest.raises(_ReachedTheDatabase):
        task_factory.create_tasks([{"id": "t-1", "title": "x"}])
