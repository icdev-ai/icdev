# CUI // SP-CTI
"""Unit tests for SAG skills lifecycle (sag-skl-01).

DB-independent: persistence is faked with an in-memory sqlite connection (with the
%s→? translation the real storage layer performs); the .agents/skills tree is
redirected to a tmp path by monkeypatching ``_repo_root``. NOVA's generator is
stubbed so no LLM runs.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import tools.agent_runtime.skills_lifecycle as sl


class _Conn:
    def __init__(self):
        self._c = sqlite3.connect(":memory:")
        # NOVA proposal queue (subset of agent_improvement_artifacts)
        self._c.execute(
            "CREATE TABLE agent_improvement_artifacts ("
            "artifact_id TEXT PRIMARY KEY, task_type TEXT, skill_used TEXT, "
            "improvement_text TEXT, evidence_traces TEXT, status TEXT, created_at TEXT)"
        )

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()


@pytest.fixture()
def db():
    return _Conn()


@pytest.fixture()
def skills_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "_repo_root", lambda: tmp_path)
    return tmp_path / ".agents" / "skills"


def _queue(db, artifact_id, name, spec, status="pending"):
    db.execute(
        "INSERT INTO agent_improvement_artifacts "
        "(artifact_id, task_type, skill_used, improvement_text, evidence_traces, status, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (artifact_id, "skill_generation", name, spec, "{}", status,
         datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


# ---------------------------------------------------------------------------
# naming + novelty
# ---------------------------------------------------------------------------
def test_auto_skill_name_normalises():
    assert sl.auto_skill_name("Deploy Foo") == "icdev-auto-deploy-foo"
    assert sl.auto_skill_name("icdev-deploy") == "icdev-auto-deploy"
    assert sl.auto_skill_name("icdev-auto-x") == "icdev-auto-x"


def test_is_novel():
    assert sl.is_novel("brand new task", existing=set()) is True
    assert sl.is_novel("dup", existing={"icdev-auto-dup"}) is False
    assert sl.is_novel("dup", existing={"icdev-dup"}) is False
    assert sl.is_novel("", existing=set()) is False


# ---------------------------------------------------------------------------
# propose (stub NOVA) + provenance stamp
# ---------------------------------------------------------------------------
def test_propose_stamps_provenance(db, monkeypatch):
    def _fake_gen(pattern, category="general", dry_run=False):
        _queue(db, "art-1", sl.auto_skill_name(pattern), "# spec body")
        return {"skill_id": "art-1", "skill_name": sl.auto_skill_name(pattern), "queued": True}

    import importlib

    nova = importlib.import_module("tools.nova.skill_generator")
    monkeypatch.setattr(nova, "generate_skill_spec", _fake_gen)
    monkeypatch.setattr(sl, "_existing_skill_names", lambda: set())
    res = sl.propose_skill("automate widget", session_id="ctx-9", model="code_generation", conn=db)
    assert res["proposed"] is True and res["novel"] is True
    prop = sl._get_proposal("art-1", conn=db)
    assert prop["provenance"]["session_id"] == "ctx-9"
    assert prop["provenance"]["model"] == "code_generation"


def test_propose_skips_non_novel(db, monkeypatch):
    monkeypatch.setattr(sl, "_existing_skill_names", lambda: {"icdev-auto-known"})
    res = sl.propose_skill("known", conn=db)
    assert res["proposed"] is False and res["novel"] is False


# ---------------------------------------------------------------------------
# HITL: list / edit / reject / approve
# ---------------------------------------------------------------------------
def test_list_and_reject(db):
    _queue(db, "art-2", "icdev-auto-foo", "# foo")
    props = sl.list_proposals("pending", conn=db)
    assert len(props) == 1 and props[0]["artifact_id"] == "art-2"
    assert sl.reject_proposal("art-2", conn=db) is True
    assert sl.list_proposals("pending", conn=db) == []
    assert len(sl.list_proposals("rejected", conn=db)) == 1


def test_edit_proposal(db):
    _queue(db, "art-3", "icdev-auto-bar", "# old")
    assert sl.edit_proposal("art-3", "# new spec", conn=db) is True
    assert sl._get_proposal("art-3", conn=db)["spec"] == "# new spec"


def test_approve_writes_skill_md_with_provenance(db, skills_root, monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_SKILL_PROPOSALS", "0")
    _queue(db, "art-4", "icdev-auto-baz", "# baz body\n## When to use\nx")
    sl._stamp_provenance("art-4", session_id="ctx-x", model="m1", conn=db)
    res = sl.approve_proposal("art-4", approver="alice", conn=db)
    assert res["approved"] is True
    skill_md = skills_root / "icdev-auto-baz" / "SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text(encoding="utf-8")
    assert "trust: unverified-llm-generated" in text
    assert "approved-by: alice" in text
    assert "source-session: ctx-x" in text
    # registered as active + status flipped
    row = sl._get_registry_row("icdev-auto-baz", conn=db)
    assert row["status"] == "active"
    assert sl._get_proposal("art-4", conn=db)["status"] == "approved"


def test_approve_missing_proposal(db, skills_root):
    res = sl.approve_proposal("nope", conn=db)
    assert res["approved"] is False


# ---------------------------------------------------------------------------
# curator: use tracking, pin, archive-never-delete
# ---------------------------------------------------------------------------
def _promote(db, skills_root, name, last_activity):
    d = skills_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: " + name + "\n---\n", encoding="utf-8")
    sl._register_promoted(name, artifact_id="a", skill_dir=str(d), session_id="",
                          model="", approved_by="t", conn=db)
    db.execute(
        "UPDATE sag_skill_registry SET last_activity_at = %s WHERE name = %s",
        (last_activity, name),
    )
    db.commit()


def test_record_use_bumps(db, skills_root):
    _promote(db, skills_root, "icdev-auto-u", datetime.now(timezone.utc).isoformat())
    sl.record_use("icdev-auto-u", conn=db)
    sl.record_use("icdev-auto-u", conn=db)
    assert sl._get_registry_row("icdev-auto-u", conn=db)["use_count"] == 2


def test_curate_archives_idle_but_not_pinned(db, skills_root):
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    _promote(db, skills_root, "icdev-auto-idle", old)
    _promote(db, skills_root, "icdev-auto-pinned", old)
    sl.set_pinned("icdev-auto-pinned", True, conn=db)

    # dry-run reports but does not move
    dry = sl.curate(archive_after_days=30, dry_run=True, conn=db)
    assert "icdev-auto-idle" in dry["archived"]
    assert "icdev-auto-pinned" in dry["retained_pinned"]
    assert (skills_root / "icdev-auto-idle").exists()  # untouched

    # apply archives idle, retains pinned, never deletes
    applied = sl.curate(archive_after_days=30, dry_run=False, conn=db)
    assert applied["archived"] == ["icdev-auto-idle"]
    assert not (skills_root / "icdev-auto-idle").exists()
    assert (skills_root / "_archive" / "icdev-auto-idle" / "SKILL.md").exists()
    assert (skills_root / "icdev-auto-pinned").exists()
    assert sl._get_registry_row("icdev-auto-idle", conn=db)["status"] == "archived"


def test_curate_retains_recent(db, skills_root):
    _promote(db, skills_root, "icdev-auto-fresh", datetime.now(timezone.utc).isoformat())
    report = sl.curate(archive_after_days=30, dry_run=False, conn=db)
    assert report["archived"] == []
    assert (skills_root / "icdev-auto-fresh").exists()


# ---------------------------------------------------------------------------
# post-session hook gating
# ---------------------------------------------------------------------------
def test_hook_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_SKILL_PROPOSALS", raising=False)

    class _R:
        class session:
            turn_count = 5
            title = "Deploy the widget"
            context_id = "ctx"
        llm_function = "code_generation"

    assert sl.maybe_propose_from_session(_R())["proposed"] is False


def test_hook_requires_descriptive_title(monkeypatch):
    monkeypatch.setattr(sl, "propose_skill", lambda *a, **k: {"proposed": True})

    class _R:
        class session:
            turn_count = 5
            title = "Untitled session"
            context_id = "ctx"
        llm_function = "x"

    assert sl.maybe_propose_from_session(_R(), force=True)["proposed"] is False
