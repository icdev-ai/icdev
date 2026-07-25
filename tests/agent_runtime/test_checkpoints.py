# CUI // SP-CTI
"""Unit tests for SAG filesystem checkpoints + rollback (sag-safe-02).

DB-independent and git-independent for the untracked-file paths: the tests point
the checkpoint module at an isolated temp "repo" so no real repo state is touched.
Covers create, restore of modified/created files, rollback-of-rollback, prune,
and the tool-call → snapshot mapping.
"""
from __future__ import annotations

import importlib

import pytest

import tools.agent_runtime.checkpoints as cp_mod
from tools.agent_runtime.checkpoints import (
    affected_paths_for_tool,
    create_checkpoint,
    describe_changes,
    list_checkpoints,
    prune,
    rollback,
)

_ALWAYS_YES = lambda _changes: True  # noqa: E731


@pytest.fixture()
def fake_repo(tmp_path, monkeypatch):
    """Point the checkpoint module at an isolated temp repo (no git tracking)."""
    monkeypatch.setattr(cp_mod, "_REPO_ROOT", tmp_path)
    # Force all files to be treated as untracked so we exercise copy-based capture
    # without needing a real git repo in the temp dir.
    monkeypatch.setattr(cp_mod, "_is_tracked", lambda rel: False)
    monkeypatch.setattr(cp_mod, "_stash_create", lambda: None)
    return tmp_path


# ---------------------------------------------------------------------------
# affected_paths_for_tool
# ---------------------------------------------------------------------------
def test_affected_paths_write_file():
    assert affected_paths_for_tool("write_file", {"path": "a/b.txt"}) == ["a/b.txt"]


def test_affected_paths_run_command_empty():
    assert affected_paths_for_tool("run_command", {"command": "python tools/x.py"}) == []


# ---------------------------------------------------------------------------
# create + restore of a modified file
# ---------------------------------------------------------------------------
def test_rollback_restores_modified_file(fake_repo):
    f = fake_repo / "note.txt"
    f.write_text("original", encoding="utf-8")

    cp = create_checkpoint(["note.txt"], label="test")
    assert cp.dir.exists()

    # mutate after snapshot
    f.write_text("changed", encoding="utf-8")
    assert f.read_text() == "changed"

    result = rollback(cp.id, confirm=_ALWAYS_YES, snapshot_current=False)
    assert result["ok"] is True
    assert f.read_text(encoding="utf-8") == "original"


# ---------------------------------------------------------------------------
# create-then-delete: rollback removes a newly created file
# ---------------------------------------------------------------------------
def test_rollback_deletes_newly_created_file(fake_repo):
    # snapshot BEFORE the file exists
    cp = create_checkpoint(["new.txt"], label="pre-create")
    (fake_repo / "new.txt").write_text("hello", encoding="utf-8")
    assert (fake_repo / "new.txt").exists()

    result = rollback(cp.id, confirm=_ALWAYS_YES, snapshot_current=False)
    assert result["ok"] is True
    assert not (fake_repo / "new.txt").exists()


# ---------------------------------------------------------------------------
# rollback-of-rollback
# ---------------------------------------------------------------------------
def test_rollback_of_rollback(fake_repo):
    f = fake_repo / "doc.txt"
    f.write_text("v1", encoding="utf-8")

    cp = create_checkpoint(["doc.txt"], label="v1")
    f.write_text("v2", encoding="utf-8")

    # rollback to v1 (snapshots current v2 first -> undo checkpoint)
    r1 = rollback(cp.id, confirm=_ALWAYS_YES, snapshot_current=True)
    assert r1["ok"] is True
    assert f.read_text() == "v1"
    undo_id = r1["undo_checkpoint"]
    assert undo_id

    # rollback the rollback -> back to v2
    r2 = rollback(undo_id, confirm=_ALWAYS_YES, snapshot_current=False)
    assert r2["ok"] is True
    assert f.read_text(encoding="utf-8") == "v2"


# ---------------------------------------------------------------------------
# confirmation gate
# ---------------------------------------------------------------------------
def test_rollback_declined_makes_no_change(fake_repo):
    f = fake_repo / "keep.txt"
    f.write_text("orig", encoding="utf-8")
    cp = create_checkpoint(["keep.txt"], label="x")
    f.write_text("edited", encoding="utf-8")

    result = rollback(cp.id, confirm=lambda _c: False, snapshot_current=False)
    assert result["ok"] is False
    assert "declined" in result["reason"]
    assert f.read_text() == "edited"  # unchanged


def test_describe_changes_lists_restore(fake_repo):
    f = fake_repo / "z.txt"
    f.write_text("a", encoding="utf-8")
    cp = create_checkpoint(["z.txt"], label="x")
    f.write_text("b", encoding="utf-8")
    changes = describe_changes(cp)
    assert any("restore untracked" in c for c in changes)


# ---------------------------------------------------------------------------
# list newest-first
# ---------------------------------------------------------------------------
def test_list_checkpoints_newest_first(fake_repo):
    (fake_repo / "a.txt").write_text("1", encoding="utf-8")
    c1 = create_checkpoint(["a.txt"], label="first")
    import time as _t

    _t.sleep(0.005)
    c2 = create_checkpoint(["a.txt"], label="second")
    cps = list_checkpoints()
    ids = [c.id for c in cps]
    assert c1.id in ids and c2.id in ids
    # newest first
    assert ids.index(c2.id) <= ids.index(c1.id)


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------
def test_prune_removes_old_checkpoints(fake_repo):
    (fake_repo / "p.txt").write_text("x", encoding="utf-8")
    cp = create_checkpoint(["p.txt"], label="old")
    import os
    import time as _t

    # backdate mtime > 7 days
    old = _t.time() - 8 * 86400
    os.utime(cp.dir, (old, old))
    removed = prune(max_age_days=7)
    assert removed >= 1
    assert not cp.dir.exists()


def test_prune_keeps_recent(fake_repo):
    (fake_repo / "r.txt").write_text("x", encoding="utf-8")
    cp = create_checkpoint(["r.txt"], label="fresh")
    removed = prune(max_age_days=7)
    assert cp.dir.exists()
    assert removed == 0


# ---------------------------------------------------------------------------
# out-of-repo path is skipped
# ---------------------------------------------------------------------------
def test_create_checkpoint_skips_escaping_path(fake_repo):
    cp = create_checkpoint(["../outside.txt"], label="x")
    assert cp.files == []


# ---------------------------------------------------------------------------
# rollback with no checkpoints
# ---------------------------------------------------------------------------
def test_rollback_no_checkpoints(fake_repo):
    result = rollback(confirm=_ALWAYS_YES)
    assert result["ok"] is False
    assert "no checkpoints" in result["reason"]


# ---------------------------------------------------------------------------
# safety-gate approval triggers a snapshot (sag-safe-01 → sag-safe-02 wiring)
# ---------------------------------------------------------------------------
def test_approved_mutation_creates_checkpoint(fake_repo, monkeypatch):
    # allow-all pre-check + no-op audit so no DB is needed
    hc = importlib.import_module("tools.airgap.hook_compat")
    monkeypatch.setattr(hc, "run_pre_tool_check",
                        lambda t, i: {"allowed": True, "reason": "ok"})
    monkeypatch.setattr(hc, "store_event", lambda *a, **k: 1)
    monkeypatch.setattr(hc, "get_session_id", lambda: "s")

    from tools.agent_runtime.safety import build_safety_gate

    gate = build_safety_gate(mode="off", checkpoint=True)
    before = len(list_checkpoints())
    allowed, _ = gate("write_file", {"path": "gated.txt", "content": "x"}, False)
    assert allowed is True
    assert len(list_checkpoints()) == before + 1


def test_gate_checkpoint_disabled_skips_snapshot(fake_repo, monkeypatch):
    hc = importlib.import_module("tools.airgap.hook_compat")
    monkeypatch.setattr(hc, "run_pre_tool_check",
                        lambda t, i: {"allowed": True, "reason": "ok"})
    monkeypatch.setattr(hc, "store_event", lambda *a, **k: 1)
    monkeypatch.setattr(hc, "get_session_id", lambda: "s")

    from tools.agent_runtime.safety import build_safety_gate

    gate = build_safety_gate(mode="off", checkpoint=False)
    before = len(list_checkpoints())
    gate("write_file", {"path": "nogate.txt", "content": "x"}, False)
    assert len(list_checkpoints()) == before
