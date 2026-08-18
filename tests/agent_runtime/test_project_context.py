# CUI // SP-CTI
"""Unit tests for SAG project-context loading (hgx-sess-01).

Covers document discovery, budgeting against
``context_budget.floor_window_for_function`` (never a constant), degradation on
a small-window chain, and injection into the runtime's system prompt at session
start. DB-independent: chat persistence is faked and the (DB-backed)
project-state section is switched off via ``ICDEV_SAG_PROJECT_STATE=0``.
"""
from __future__ import annotations

from typing import Any

import pytest

import tools.agent_runtime.profile_memory as pm_mod
import tools.agent_runtime.project_context as pc
import tools.agent_runtime.runtime as rt_mod
import tools.agent_runtime.sessions as sess_mod
from tools.agent_runtime.runtime import AgentRuntime
from tools.llm import context_budget as cb

# Sized like the real files: CLAUDE.md is ~16k tokens by the platform
# estimator, so it fits a 200k window intact and must be trimmed for a 32k one.
_CLAUDE = "# CLAUDE.md\n" + "\n".join(f"- rule number {i}" for i in range(3000))
_AGENTS = "# AGENTS.md\n" + "\n".join(f"- agent note {i}" for i in range(400))
_MEMORY = "# MEMORY\n" + "\n".join(f"- memory {i}" for i in range(200))


@pytest.fixture()
def fake_root(tmp_path):
    """A throwaway repo root carrying all three instruction files."""
    (tmp_path / "CLAUDE.md").write_text(_CLAUDE, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(_AGENTS, encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text(_MEMORY, encoding="utf-8")
    return tmp_path


def _set_window(monkeypatch, tokens: int) -> None:
    """Pin the routed floor window in both binding sites."""
    monkeypatch.setattr(cb, "floor_window_for_function", lambda _fn: tokens)
    monkeypatch.setattr(pc, "floor_window_for_function", lambda _fn: tokens)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def test_collects_all_three_instruction_files(fake_root):
    sections = pc.collect_sections(fake_root, include_project_state=False)
    assert [s.source for s in sections] == ["CLAUDE.md", "AGENTS.md", "memory/MEMORY.md"]


def test_missing_files_are_skipped(tmp_path):
    (tmp_path / "AGENTS.md").write_text(_AGENTS, encoding="utf-8")
    sections = pc.collect_sections(tmp_path, include_project_state=False)
    assert [s.source for s in sections] == ["AGENTS.md"]


def test_no_documents_yields_empty_block(tmp_path):
    text = pc.build_project_context(
        llm_function="code_generation", root=tmp_path, include_project_state=False
    )
    assert text == ""


def test_crlf_documents_are_normalised(tmp_path):
    (tmp_path / "CLAUDE.md").write_bytes(b"# CLAUDE.md\r\n- rule one\r\n")
    sections = pc.collect_sections(tmp_path, include_project_state=False)
    assert "\r" not in sections[0].text


# ---------------------------------------------------------------------------
# Budgeting — derived from floor_window_for_function, not a constant
# ---------------------------------------------------------------------------
def test_budget_tracks_the_routed_floor_window(monkeypatch):
    _set_window(monkeypatch, 32_768)
    small = pc.context_budget_tokens("code_generation")
    _set_window(monkeypatch, 200_000)
    large = pc.context_budget_tokens("code_generation")
    assert large > small * 5
    # ...and it really is a share of that window, not a hardcoded ceiling.
    assert small == pytest.approx(32_768 * pc.WINDOW_SHARE, rel=0.15)


def test_small_window_degrades_instead_of_consuming_the_window(monkeypatch, fake_root):
    _set_window(monkeypatch, 32_768)
    report = pc.describe(
        llm_function="code_generation", root=fake_root, include_project_state=False
    )
    claude = report["sections"][0]
    assert claude["truncated"] is True
    assert claude["omitted_lines"] > 0
    # The whole block stays inside its share of a 32k window.
    assert report["tokens_used"] <= report["budget"]
    assert report["tokens_used"] < 32_768 * (pc.WINDOW_SHARE + 0.05)
    # Truncation is announced, and names where to read the rest.
    assert "omitted to fit the context budget" in report["text"]
    assert "read `CLAUDE.md`" in report["text"]


def test_large_window_carries_the_documents_intact(monkeypatch, fake_root):
    _set_window(monkeypatch, 200_000)
    report = pc.describe(
        llm_function="code_generation", root=fake_root, include_project_state=False
    )
    assert all(not s["truncated"] for s in report["sections"])
    assert "rule number 399" in report["text"]


def test_tiny_budget_drops_low_priority_sections_and_says_so(fake_root):
    report = pc.describe(
        llm_function="code_generation",
        root=fake_root,
        include_project_state=False,
        budget_tokens=400,
    )
    included = [s["title"] for s in report["sections"] if s["included"]]
    assert included == ["Project instructions (CLAUDE.md)"]
    assert "Omitted for context budget" in report["text"]


def test_zero_budget_yields_no_block(fake_root):
    assert (
        pc.build_project_context(
            llm_function="code_generation",
            root=fake_root,
            include_project_state=False,
            budget_tokens=0,
        )
        == ""
    )


def test_unspent_allowance_rolls_forward(tmp_path):
    """Small leading files buy MEMORY.md room rather than wasting their share.

    With a 1000-token budget, MEMORY.md's own 15% share is 150 tokens — below
    ``MIN_SECTION_TOKENS``, so a share-only allocator would drop it. The two
    tiny files ahead of it leave enough unspent allowance that it survives.
    """
    (tmp_path / "CLAUDE.md").write_text("- one rule\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("- one note\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text(_MEMORY, encoding="utf-8")

    assert int(1_000 * pc.DOCUMENTS[2][2]) < pc.MIN_SECTION_TOKENS
    report = pc.describe(
        llm_function="code_generation",
        root=tmp_path,
        include_project_state=False,
        budget_tokens=1_000,
    )
    memory = report["sections"][2]
    assert memory["included"] is True
    assert memory["tokens"] > pc.MIN_SECTION_TOKENS


# ---------------------------------------------------------------------------
# Runtime injection
# ---------------------------------------------------------------------------
class _FakeChatManager:
    def __init__(self) -> None:
        self._n = 0

    def create_context(self, *, title="", **_kw) -> str:
        self._n += 1
        return f"ctx-{self._n}"

    def add_message(self, context_id, *, role, content, **_kw) -> int:
        return 1

    def get_messages(self, context_id, **_kw) -> list[dict[str, Any]]:
        return []

    def update_title(self, context_id, title) -> None:
        return None


@pytest.fixture()
def runtime(monkeypatch):
    monkeypatch.setattr(sess_mod, "ChatManager", lambda *a, **k: _FakeChatManager())
    monkeypatch.setattr(pm_mod, "build_profile_context", lambda *a, **k: "")
    monkeypatch.setenv("ICDEV_SAG_PROJECT_STATE", "0")
    return AgentRuntime()


def test_fresh_session_system_prompt_carries_project_instructions(runtime):
    prompt = runtime._effective_system_prompt("hello")
    assert "# Project context (loaded at session start)" in prompt
    assert "Project instructions (CLAUDE.md)" in prompt
    # The base prompt is still there, after the injected block.
    assert prompt.endswith(rt_mod._DEFAULT_SYSTEM_PROMPT)


def test_project_context_is_cached_per_session_and_cleared_by_new(runtime, monkeypatch):
    first = runtime._effective_system_prompt("hello")
    calls: list[str] = []
    # **kw absorbs the hcx-evt-03 event ids (session_id / correlation_id) the
    # runtime now passes; a positional-only stub would raise TypeError into the
    # runtime's best-effort except and read as "the block was empty".
    monkeypatch.setattr(
        pc, "build_for_runtime", lambda fn, sp="", **kw: calls.append(fn) or "REBUILT"
    )
    # cached — no rebuild on the next turn of the same session
    assert runtime._effective_system_prompt("again") == first
    assert calls == []
    # /new invalidates it
    runtime.new_session()
    assert "REBUILT" in runtime._effective_system_prompt("hello")
    assert calls == ["code_generation"]


def test_env_kill_switch_suppresses_the_block(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_PROJECT_CONTEXT", "0")
    assert pc.build_for_runtime("code_generation") == ""


def test_build_for_runtime_never_raises(monkeypatch):
    def _boom(**_kw):
        raise RuntimeError("nope")

    # `describe`, not `build_project_context`: since hcx-evt-03 build_for_runtime
    # calls describe directly, because the recorded event carries the budget
    # accounting and only describe reports it.
    monkeypatch.setattr(pc, "describe", _boom)
    assert pc.build_for_runtime("code_generation") == ""
