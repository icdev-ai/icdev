# CUI // SP-CTI
"""Unit tests for SAG per-user profile memory (sag-mem-01).

DB-independent: persistence is faked with an in-memory sqlite connection injected
via shim-aware monkeypatch of ``tools.db.storage.get_connection`` (with the %s→?
placeholder translation the real storage layer performs). No shared-DB tables are
required.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

import tools.agent_runtime.profile_memory as pm


class _Conn:
    """Thin sqlite wrapper translating the %s placeholders the module emits."""

    def __init__(self):
        self._c = sqlite3.connect(":memory:")

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._c.commit()


@pytest.fixture()
def fake_db(monkeypatch):
    conn = _Conn()
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    return conn


# ---------------------------------------------------------------------------
# facts round-trip
# ---------------------------------------------------------------------------
def test_remember_and_list_facts(fake_db):
    n = pm.remember_facts(
        [{"text": "I prefer concise answers", "confidence": 0.9, "source": "manual"}],
        user_id="u1",
    )
    assert n == 1
    facts = pm.list_facts(user_id="u1")
    assert len(facts) == 1
    assert facts[0]["text"] == "I prefer concise answers"


def test_remember_dedups_by_normalised_text(fake_db):
    pm.remember_facts([{"text": "I use Postgres", "confidence": 0.6}], user_id="u2")
    # same fact, different casing/space + higher confidence -> updates, not dup
    pm.remember_facts([{"text": "i  use   postgres", "confidence": 0.95}], user_id="u2")
    facts = pm.list_facts(user_id="u2")
    assert len(facts) == 1
    assert facts[0]["confidence"] == 0.95


def test_low_confidence_facts_dropped(fake_db):
    n = pm.remember_facts([{"text": "maybe true", "confidence": 0.2}], user_id="u3")
    assert n == 0
    assert pm.list_facts(user_id="u3") == []


def test_forget_by_index(fake_db):
    pm.remember_facts(
        [{"text": "fact A", "confidence": 0.9}, {"text": "fact B", "confidence": 0.8}],
        user_id="u4",
    )
    removed = pm.forget_fact("1", user_id="u4")  # highest-confidence first => fact A
    assert removed == "fact A"
    remaining = [f["text"] for f in pm.list_facts(user_id="u4")]
    assert remaining == ["fact B"]


def test_forget_by_text(fake_db):
    pm.remember_facts([{"text": "delete me please", "confidence": 0.9}], user_id="u5")
    removed = pm.forget_fact("delete me", user_id="u5")
    assert removed == "delete me please"
    assert pm.list_facts(user_id="u5") == []


def test_forget_nonexistent_returns_none(fake_db):
    assert pm.forget_fact("nope", user_id="u6") is None


def test_set_preference_round_trip(fake_db):
    assert pm.set_preference("tone", "concise", user_id="u7") is True
    prof = pm.load_profile(user_id="u7")
    assert prof["preferences"]["tone"] == "concise"


# ---------------------------------------------------------------------------
# graceful degradation (no DB)
# ---------------------------------------------------------------------------
def test_load_profile_degrades_without_db(monkeypatch):
    storage = importlib.import_module("tools.db.storage")

    def _boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr(storage, "get_connection", _boom)
    assert pm.load_profile(user_id="x") == {"preferences": {}, "facts": []}
    assert pm.list_facts(user_id="x") == []


# ---------------------------------------------------------------------------
# session-start injection
# ---------------------------------------------------------------------------
def test_build_profile_context_includes_facts_and_prefs(fake_db):
    pm.set_preference("tone", "concise", user_id="u8")
    pm.remember_facts([{"text": "Works on ICDEV", "confidence": 0.9}], user_id="u8")
    ctx = pm.build_profile_context(user_id="u8")
    assert "tone=concise" in ctx
    assert "Works on ICDEV" in ctx
    assert ctx.startswith("## Operator profile")


def test_build_profile_context_empty_when_nothing(fake_db):
    assert pm.build_profile_context(user_id="nobody") == ""


def test_build_profile_context_memory_search_failure_is_safe(fake_db, monkeypatch):
    pm.remember_facts([{"text": "a fact", "confidence": 0.9}], user_id="u9")
    # even if hybrid_search blows up, facts still render
    import tools.memory.hybrid_search as hs

    monkeypatch.setattr(hs, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ctx = pm.build_profile_context(user_id="u9", query="anything")
    assert "a fact" in ctx


# ---------------------------------------------------------------------------
# auto-capture consolidation
# ---------------------------------------------------------------------------
def test_extract_facts_from_text():
    facts = pm.extract_facts_from_text("I prefer dark mode. The weather is nice.")
    texts = [f["text"] for f in facts]
    assert any("prefer dark mode" in t for t in texts)
    # non-marker sentence excluded
    assert not any("weather" in t for t in texts)


def test_extract_confidence_higher_for_imperatives():
    facts = pm.extract_facts_from_text("Please always run tests before committing.")
    assert facts and facts[0]["confidence"] >= 0.85


def test_consolidate_session_facts_persists(fake_db):
    transcript = [
        {"role": "user", "content": "I always use two spaces. Remember that I work on ICDEV."},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "What's the weather?"},  # no durable fact
    ]
    result = pm.consolidate_session_facts(transcript, user_id="u10")
    assert result["extracted"] >= 2
    assert result["remembered"] >= 2
    facts = [f["text"] for f in pm.list_facts(user_id="u10")]
    assert any("two spaces" in t for t in facts)


def test_consolidate_empty_transcript(fake_db):
    assert pm.consolidate_session_facts([], user_id="u11") == {"extracted": 0, "remembered": 0}
