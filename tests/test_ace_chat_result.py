# CUI // SP-CTI
"""A finished team must report back to the conversation that started it.

goals/ace_coworker.md claimed the "final summary is injected as an assistant
message on the originating session". No such code existed: a team launched from
chat did its work and the conversation never heard back.

Delivery is in-process rather than via the webhook. A webhook is an outbound
POST, so using it internally means the dashboard calling itself over loopback —
it needs a reachable base URL and breaks in air-gapped and odd-port deployments.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def ace_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(tmp_path / "ace_result.db"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    return tmp_path


@pytest.fixture
def cr():
    from icdev.tools.ace import chat_result

    return chat_result


def _make_instance(cr, instance_id, trigger_source, context_id="ctx-1", state="complete"):
    conn = cr._ace_conn()
    try:
        conn.execute(
            "INSERT INTO ace_instances (id, name, state, config_json, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                instance_id, "t", state,
                json.dumps({
                    "problem_text": "Build a compliance dashboard",
                    "trigger_source": trigger_source,
                    "trigger_ref": context_id,
                }),
                "2026-07-31T00:00:00", "2026-07-31T00:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _add_artifact(cr, instance_id, title, body):
    conn = cr._ace_conn()
    try:
        conn.execute(
            "INSERT INTO ace_artifacts (id, instance_id, coworker_id, artifact_type, title, "
            "content_md, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (f"art-{title}", instance_id, "cw", "report", title, body, "2026-07-31T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def posted(cr, monkeypatch):
    """Capture what would be posted to chat."""
    sent: list[dict] = []
    monkeypatch.setattr(
        cr, "_post_to_chat",
        lambda ctx, content, ctype, meta: (
            sent.append({"ctx": ctx, "content": content, "type": ctype, "meta": meta}) or True
        ),
    )
    monkeypatch.setattr(cr, "_synthesize", lambda p, o: "The team found three gaps.")
    return sent


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_non_chat_runs_are_ignored(ace_db, cr, posted):
    """A team launched from the API or a reflex has no conversation to answer."""
    _make_instance(cr, "ace-api", "api")
    _add_artifact(cr, "ace-api", "Report", "findings")

    assert cr.deliver("ace-api") is False
    assert posted == []


def test_unknown_instance_is_ignored(ace_db, cr, posted):
    assert cr.deliver("ace-nope") is False
    assert posted == []


@pytest.mark.parametrize("trigger", ["chat", "chat_suggestion"])
def test_both_chat_triggers_deliver(ace_db, cr, posted, trigger):
    _make_instance(cr, f"ace-{trigger}", trigger)
    _add_artifact(cr, f"ace-{trigger}", "Report", "findings here")

    assert cr.deliver(f"ace-{trigger}") is True
    assert len(posted) == 1


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_result_carries_the_deep_link(ace_db, cr, posted):
    _make_instance(cr, "ace-1", "chat_suggestion", context_id="ctx-42")
    _add_artifact(cr, "ace-1", "Findings", "Three controls unmapped.")

    cr.deliver("ace-1")

    msg = posted[0]
    assert msg["ctx"] == "ctx-42"
    assert "/coworker/ace-1" in msg["content"]
    assert msg["type"] == "markdown"
    assert msg["meta"]["ace_instance_id"] == "ace-1"


def test_completion_without_artifacts_says_so(ace_db, cr, posted):
    """A 'complete' run with no output is the exact ACE failure mode.

    Reporting it as an error rather than posting an empty summary is what makes
    that visible instead of silently plausible.
    """
    _make_instance(cr, "ace-empty", "chat")

    assert cr.deliver("ace-empty") is True
    assert posted[0]["type"] == "error"
    assert "no output" in posted[0]["content"]
    assert "/coworker/ace-empty" in posted[0]["content"]


def test_failed_run_still_reports(ace_db, cr, posted):
    """Silence after 'spinning up a team' is the worst outcome."""
    _make_instance(cr, "ace-bad", "chat", state="failed")

    assert cr.deliver("ace-bad", state="failed") is True
    assert posted[0]["type"] == "error"
    assert "stopped without finishing" in posted[0]["content"]


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def test_synthesis_degrades_to_raw_output(ace_db, cr, monkeypatch):
    """No provider must mean a degraded answer, not silence."""
    def _boom(*a, **k):
        raise RuntimeError("no provider configured")

    monkeypatch.setattr("tools.llm.router.LLMRouter", _boom, raising=False)
    monkeypatch.setattr("icdev.tools.llm.router.LLMRouter", _boom, raising=False)

    out = cr._synthesize("problem", "### Findings\nThree controls unmapped.")
    assert "Three controls unmapped." in out


def test_synthesis_uses_a_logical_function_not_a_model_id(cr):
    """Model choice belongs to args/llm_config.yaml, never to code."""
    import inspect

    assert cr.SYNTHESIS_FUNCTION == "ace_result_synthesis"
    source = inspect.getsource(cr)
    for model_id in ("claude-sonnet", "gpt-4o", "gpt-4", "qwen3", "kimi", "gemini"):
        assert model_id not in source, f"hardcoded model id {model_id!r}"


def test_synthesis_function_is_routable():
    """The logical name must resolve, or delivery silently degrades forever."""
    import yaml
    from pathlib import Path

    cfg_path = Path(__file__).resolve().parents[1] / "args" / "llm_config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "ace_result_synthesis" in cfg["routing"]
    assert cfg["routing"]["ace_result_synthesis"]["chain"]


def test_artifact_collection_is_bounded(ace_db, cr):
    """A chatty team must not blow the synthesis prompt."""
    _make_instance(cr, "ace-big", "chat")
    for i in range(40):
        _add_artifact(cr, "ace-big", f"a{i}", "x" * 500)

    assert len(cr._collect_output("ace-big")) <= cr._MAX_ARTIFACT_CHARS + 500


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_controller_delivers_through_the_in_process_path(monkeypatch):
    """Internal delivery must call chat_result.deliver, not an outbound POST."""
    from icdev.tools.ace import chat_result, controller

    calls: list[tuple] = []
    monkeypatch.setattr(
        chat_result, "deliver",
        lambda instance_id, state="complete": calls.append((instance_id, state)) or True,
    )

    controller.ACEController._deliver_chat_result("ace-xyz", "complete")

    assert calls == [("ace-xyz", "complete")]


def test_delivery_failure_never_breaks_a_finished_run(monkeypatch):
    """A run that already completed must not be affected by a delivery error."""
    from icdev.tools.ace import chat_result, controller

    def _boom(*a, **k):
        raise RuntimeError("chat manager gone")

    monkeypatch.setattr(chat_result, "deliver", _boom)

    # Must swallow, not propagate.
    controller.ACEController._deliver_chat_result("ace-xyz", "complete")
