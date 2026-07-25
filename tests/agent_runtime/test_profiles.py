# CUI // SP-CTI
"""Unit tests for SAG profile isolation (sag-prof-01).

DB-independent: the profile state dir is redirected to a tmp path via
``ICDEV_HOME``; the registry is faked with an in-memory sqlite connection injected
via shim-aware monkeypatch of ``tools.db.storage.get_connection`` (with the %s→?
placeholder translation the real storage layer performs).
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

import tools.agent_runtime.profiles as profiles


class _Conn:
    def __init__(self):
        self._c = sqlite3.connect(":memory:")

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_HOME", str(tmp_path / ".icdev"))
    monkeypatch.delenv("ICDEV_SAG_PROFILE", raising=False)
    return tmp_path / ".icdev"


@pytest.fixture()
def fake_db(monkeypatch):
    conn = _Conn()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    return conn


# ---------------------------------------------------------------------------
# name validation + tenant namespacing
# ---------------------------------------------------------------------------
def test_validate_name():
    assert profiles.validate_name("work-1") == "work-1"
    with pytest.raises(ValueError):
        profiles.validate_name("Bad Name")
    with pytest.raises(ValueError):
        profiles.validate_name("default")  # reserved


def test_scoped_tenant_default_is_noop():
    assert profiles.scoped_tenant("acme", "") == "acme"
    assert profiles.scoped_tenant("acme", "default") == "acme"
    assert profiles.scoped_tenant("acme", None) == "acme"


def test_scoped_tenant_named_namespaces():
    assert profiles.scoped_tenant("acme", "work") == "acme::prof:work"
    assert profiles.scoped_tenant("", "work") == "::prof:work"


# ---------------------------------------------------------------------------
# sticky pointer
# ---------------------------------------------------------------------------
def test_active_profile_default_when_unset(home):
    assert profiles.active_profile() == ""


def test_set_and_read_active(home):
    profiles.set_active("work")
    assert profiles.active_profile() == "work"
    # default clears it
    profiles.set_active("default")
    assert profiles.active_profile() == ""


def test_env_override_wins(home, monkeypatch):
    profiles.set_active("work")
    monkeypatch.setenv("ICDEV_SAG_PROFILE", "personal")
    assert profiles.active_profile() == "personal"
    monkeypatch.setenv("ICDEV_SAG_PROFILE", "default")
    assert profiles.active_profile() == ""


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def test_create_profile_scaffolds(home, fake_db):
    info = profiles.create_profile("work", "my work profile", conn=fake_db)
    assert profiles.profile_dir("work").is_dir()
    assert profiles.skills_dir("work").is_dir()
    assert profiles.overlay_env_path("work").exists()
    assert info["description"] == "my work profile"


def test_create_is_idempotent(home, fake_db):
    profiles.create_profile("work", conn=fake_db)
    # writing a custom overlay then re-creating must not clobber it
    profiles.overlay_env_path("work").write_text("FOO=bar\n", encoding="utf-8")
    profiles.create_profile("work", conn=fake_db)
    assert "FOO=bar" in profiles.overlay_env_path("work").read_text(encoding="utf-8")


def test_list_profiles_merges_fs_and_registry(home, fake_db):
    profiles.create_profile("work", "w", conn=fake_db)
    profiles.create_profile("personal", "p", conn=fake_db)
    profiles.set_active("work")
    listed = profiles.list_profiles(conn=fake_db)
    names = {p["name"]: p for p in listed}
    assert set(names) == {"work", "personal"}
    assert names["work"]["active"] is True
    assert names["personal"]["active"] is False


def test_remove_profile_clears_active(home, fake_db):
    profiles.create_profile("work", conn=fake_db)
    profiles.set_active("work")
    assert profiles.remove_profile("work", purge=True, conn=fake_db) is True
    assert profiles.active_profile() == ""
    assert not profiles.profile_dir("work").exists()


# ---------------------------------------------------------------------------
# overlay
# ---------------------------------------------------------------------------
def test_overlay_load_and_apply(home, fake_db, monkeypatch):
    profiles.create_profile("work", conn=fake_db)
    profiles.overlay_env_path("work").write_text(
        "# comment\nICDEV_LLM_PROVIDER=ollama\nEMPTY\n", encoding="utf-8"
    )
    loaded = profiles.load_overlay("work")
    assert loaded == {"ICDEV_LLM_PROVIDER": "ollama"}
    monkeypatch.delenv("ICDEV_LLM_PROVIDER", raising=False)
    applied = profiles.apply_overlay("work")
    assert "ICDEV_LLM_PROVIDER" in applied
    import os

    assert os.environ["ICDEV_LLM_PROVIDER"] == "ollama"


def test_overlay_default_profile_is_empty(home):
    assert profiles.load_overlay("default") == {}
    assert profiles.apply_overlay("") == []


# ---------------------------------------------------------------------------
# runtime integration: startup profile resolution namespaces the tenant
# ---------------------------------------------------------------------------
def test_runtime_picks_up_active_profile(home, monkeypatch):
    import tools.agent_runtime.runtime as rt

    class _DummySession:
        context_id = "ctx-x"

        @classmethod
        def create(cls, **kwargs):
            inst = cls()
            inst._kwargs = kwargs
            return inst

    monkeypatch.setattr(rt, "RuntimeSession", _DummySession)
    profiles.set_active("work")
    runtime = rt.AgentRuntime(tenant_id="acme")
    assert runtime.profile == "work"
    assert runtime.tenant_id == "acme::prof:work"
    # session was created with the namespaced tenant
    assert runtime.session._kwargs["tenant_id"] == "acme::prof:work"


def test_runtime_default_profile_leaves_tenant_unchanged(home, monkeypatch):
    import tools.agent_runtime.runtime as rt

    class _DummySession:
        context_id = "ctx-x"

        @classmethod
        def create(cls, **kwargs):
            return cls()

    monkeypatch.setattr(rt, "RuntimeSession", _DummySession)
    runtime = rt.AgentRuntime(tenant_id="acme")
    assert runtime.profile == ""
    assert runtime.tenant_id == "acme"


# ---------------------------------------------------------------------------
# profile_memory tags the profile column derived from the namespaced tenant
# ---------------------------------------------------------------------------
def test_profile_memory_derives_profile_column(fake_db):
    import tools.agent_runtime.profile_memory as pm

    pm.remember_facts(
        [{"text": "I prefer concise answers", "confidence": 0.9}],
        user_id="u1", tenant_id="acme::prof:work",
    )
    row = fake_db.execute(
        "SELECT profile FROM sag_user_profiles WHERE user_id = 'u1'"
    ).fetchone()
    assert row[0] == "work"
