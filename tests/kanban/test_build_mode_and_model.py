# CUI // SP-CTI
"""Manual Build, and picking the model the runner builds with.

Two switches, two failure modes worth engineering against — and they are the same
failure: **a control that looks like it worked and did nothing.**

  * **Manual Build** must stop DISPATCH without stopping the board. If it froze the whole
    cycle (like Pause Scheduler does) you would hand-build against a board that stopped
    tracking, and an interrupted build would leave no record of where it got to. And the
    stale-reaper must not reset a human's in-flight task to backlog after 30 minutes —
    from the outside, a CLI session two hours into a task looks exactly like a scheduler
    subprocess that died an hour ago.

  * **The model selector** must actually select the model. Choosing Kimi while the runner
    keeps dispatching the Claude CLI (which cannot serve it) is the dropdown doing
    nothing, silently — the exact shape of bug this codebase keeps producing.
"""
from __future__ import annotations

import importlib

import pytest

kanban = importlib.import_module("tools.genesis.reflexes.kanban")
build_mode = importlib.import_module("tools.kanban.build_mode")
model_override = importlib.import_module("tools.kanban.model_override")


@pytest.fixture(autouse=True)
def isolated_flags(tmp_path, monkeypatch):
    """Never touch the real data/ flags."""
    monkeypatch.setattr(build_mode, "_FLAG", tmp_path / "manual_build.json")
    monkeypatch.setattr(model_override, "_FLAG", tmp_path / "model.json")


# ---------------------------------------------------------------------------
# Manual Build: the board keeps working
# ---------------------------------------------------------------------------
def test_default_is_automatic_build():
    assert build_mode.is_manual() is False
    assert build_mode.status()["mode"] == "automatic"


def test_enable_then_disable_round_trips():
    build_mode.enable(actor="dashboard")
    assert build_mode.is_manual() is True
    assert build_mode.status()["mode"] == "manual"

    build_mode.disable()
    assert build_mode.is_manual() is False, "unchecking restores the default"


def test_manual_build_does_NOT_dispatch_and_leaves_the_task_SCHEDULED(monkeypatch):
    """The whole point. The task stays visible in Scheduled for a CLI session to take."""
    build_mode.enable()

    spawned, moved = [], []
    monkeypatch.setattr(kanban, "_dispatch_via_claude_cli",
                        lambda *a, **kw: spawned.append(a))
    monkeypatch.setattr(kanban, "_move_task", lambda tid, st, **kw: moved.append((tid, st)))
    monkeypatch.setattr(kanban, "_create_worktree", lambda tid: "/tmp/wt")

    kanban._dispatch_to_claude({"id": "dm-portal-01", "title": "x"}, "/nonexistent.md")

    assert spawned == [], "no executor may be spawned"
    assert moved == [], "and the task must NOT be moved to in_progress"


def test_automatic_build_still_dispatches(monkeypatch, tmp_path):
    """No regression: unchecked is the default and it behaves exactly as before."""
    prompt = tmp_path / "p.md"
    prompt.write_text("do it", encoding="utf-8")

    spawned = []
    monkeypatch.setattr(kanban, "_dispatch_via_claude_cli",
                        lambda *a, **kw: spawned.append(a))
    monkeypatch.setattr(kanban, "_claude_code_available", lambda: True)
    monkeypatch.setattr(kanban, "_create_worktree", lambda tid: str(tmp_path))
    monkeypatch.setattr(kanban, "_pre_dispatch_check", lambda t: (False, ""))
    monkeypatch.setattr(kanban, "_had_recent_success", lambda tid, **kw: False)
    monkeypatch.setattr(kanban, "_has_open_pr", lambda tid: False)
    monkeypatch.setattr(kanban, "_set_executor_type", lambda *a, **kw: None)

    kanban._dispatch_to_claude({"id": "dm-portal-01", "title": "x"}, str(prompt))

    assert len(spawned) == 1, "automatic build must still dispatch"


def test_an_unreadable_flag_falls_back_to_AUTOMATIC(monkeypatch):
    """The failure that is hard to notice is the one where nothing happens. A corrupt
    flag must never silently stop every build on the board."""
    monkeypatch.setattr(build_mode, "_read", lambda: (_ for _ in ()).throw(OSError("boom")))
    assert build_mode.is_manual() is False


def test_the_reflex_helper_never_raises(monkeypatch):
    monkeypatch.setattr(kanban, "_manual_build", kanban._manual_build)  # real one
    assert kanban._manual_build() in (True, False)


# ---------------------------------------------------------------------------
# Manual Build: a human's in-flight task is not reaped
# ---------------------------------------------------------------------------
def test_manual_build_is_consulted_by_the_stale_reaper():
    """A CLI session two hours into a task looks, from the outside, exactly like a
    scheduler subprocess that died an hour ago: no live PID, no log output. The reaper
    would reset it to backlog and bump failure_count — destroying the record of where
    the build had got to, which is the one thing Manual Build exists to preserve."""
    import inspect

    src = inspect.getsource(kanban._reap_stale_in_progress)
    assert "_manual_build()" in src
    assert "_task_dispatched_by_scheduler" in src


def test_manual_build_does_not_expire():
    """The pause flag goes stale after 120 min so a crashed pipeline can't wedge the
    scheduler. Manual Build is the opposite kind of thing: flipping back to automatic
    mid-build would spawn an agent into the worktree a human is standing in — the exact
    collision it exists to prevent.

    So an ancient flag is still ON. You turn it off; nothing turns it off for you.
    """
    import json

    build_mode.enable(actor="cli")
    meta = json.loads(build_mode._FLAG.read_text(encoding="utf-8"))
    meta["since"] = "2020-01-01T00:00:00+00:00"      # a year and a half of "staleness"
    build_mode._FLAG.write_text(json.dumps(meta), encoding="utf-8")

    assert build_mode.is_manual() is True
    assert kanban._manual_build() is True


# ---------------------------------------------------------------------------
# The model selector actually selects the model
# ---------------------------------------------------------------------------
def test_the_dropdown_only_offers_models_the_router_knows():
    models = model_override.available()
    assert models, "llm_config.yaml must yield models"
    names = {m["name"] for m in models}
    assert "claude-opus" in names
    # And every entry says whether the Claude CLI can serve it.
    assert all("cli_capable" in m for m in models)


def test_an_unknown_model_is_refused_at_SELECTION_not_at_dispatch():
    with pytest.raises(ValueError, match="unknown model"):
        model_override.set_model("gpt-9-ultra")


def test_selecting_a_non_claude_model_DROPS_claude_cli_from_the_chain():
    """The failure this exists to prevent: you pick Kimi because Claude is exhausted, the
    runner keeps dispatching the Claude CLI, and the dropdown has done nothing."""
    local = next((m for m in model_override.available() if not m["cli_capable"]), None)
    assert local, "llm_config must define at least one non-Claude model"

    model_override.set_model(local["name"])
    chain = kanban._build_effective_executor_chain(
        ["claude_cli", "gitlab", "ollama_local"])

    assert "claude_cli" not in chain
    assert "ollama_local" in chain


def test_selecting_a_claude_model_KEEPS_claude_cli():
    model_override.set_model("claude-opus")
    chain = kanban._build_effective_executor_chain(["claude_cli", "ollama_local"])
    assert chain[0] == "claude_cli"


def test_no_selection_leaves_the_chain_untouched():
    """Default behaviour must be byte-unchanged."""
    original = ["claude_cli", "gitlab", "ollama_local"]
    assert kanban._build_effective_executor_chain(list(original)) == original


def test_a_claude_model_is_passed_to_the_cli_as_dash_dash_model():
    import inspect

    src = inspect.getsource(kanban._dispatch_via_claude_cli)
    assert '"--model"' in src
    assert '_model.get("cli_capable")' in src, \
        "a non-Claude model must never be handed to the Claude CLI"


def test_the_llm_executor_builds_with_the_selected_model():
    import inspect

    src = inspect.getsource(kanban)
    assert "request.model = _mo[\"name\"]" in src, \
        "the LLM executor path must honour the override, not llm_config's routing"


def test_clearing_the_model_restores_config_routing():
    model_override.set_model("claude-opus")
    assert model_override.selected() == "claude-opus"

    model_override.set_model(None)
    assert model_override.selected() is None
    assert kanban._selected_model() is None


def test_a_model_renamed_out_of_config_degrades_to_default_not_to_failure(monkeypatch):
    """A stale override must not be handed to a provider that will reject it on every
    task. It degrades to the default, and says so."""
    model_override.set_model("claude-opus")
    monkeypatch.setattr(model_override, "available", lambda: [])

    assert model_override.spec() is None
    assert model_override.status()["stale"] is True
    assert kanban._selected_model() is None
