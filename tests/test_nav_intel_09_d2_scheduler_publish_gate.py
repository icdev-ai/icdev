# CUI // SP-CTI
"""Scheduler auto-publish RED-verdict gate (nav-intel-09-d2).

The Pulse pipeline's automated publish stage (``_publish_stage`` in
``tools/pulse/engine/scheduler.py``) must consult the shared judge-verdict gate
(``tools/pulse/publish_gate.py``) before executing. Fail-closed semantics:

  * RED verdict                → publish stage skipped, reason logged;
  * no verdict (judge errored
    or never ran)              → publish stage skipped, reason logged;
  * green/yellow/purple/blue   → publish stage proceeds (export executes).

``_build_result`` surfaces the block as ``publish_blocked`` +
``publish_block_reason`` on the pipeline result.

export_both and the module logger are patched on the imported scheduler module
object itself (shim-aware: attribute patching on the module resolved via
importlib hits the same object the function's globals reference).
"""
from __future__ import annotations

import importlib

import pytest

scheduler = importlib.import_module("tools.pulse.engine.scheduler")


class _RecordingLogger:
    """Minimal logger stub capturing (level, rendered message) tuples."""

    def __init__(self):
        self.records = []

    def _record(self, level, msg, *args):
        self.records.append((level, msg % args if args else msg))

    def info(self, msg, *args, **kwargs):
        self._record("info", msg, *args)

    def warning(self, msg, *args, **kwargs):
        self._record("warning", msg, *args)

    def error(self, msg, *args, **kwargs):
        self._record("error", msg, *args)

    def debug(self, msg, *args, **kwargs):
        self._record("debug", msg, *args)

    def messages(self, level=None):
        return [m for lvl, m in self.records if level is None or lvl == level]


@pytest.fixture
def harness(monkeypatch):
    """Patch export_both + logger on the scheduler module; return (logger, calls)."""
    calls = []

    def _fake_export_both(post_id):
        calls.append(post_id)
        return {"mdx": f"/exports/{post_id}.mdx", "html": f"/exports/{post_id}.html", "post_id": post_id}

    log = _RecordingLogger()
    monkeypatch.setattr(scheduler, "export_both", _fake_export_both)
    monkeypatch.setattr(scheduler, "logger", log)
    return log, calls


# ---------------------------------------------------------------------------
# _publish_stage: skip on RED / absent verdict
# ---------------------------------------------------------------------------


def test_publish_stage_skips_on_red_verdict(harness):
    log, calls = harness
    post = {"id": "post-red", "judge_color": "red"}

    result = scheduler._publish_stage("run-t1", "post-red", post)

    assert result["skipped"] is True
    assert result["blocked"] is True
    assert result["verdict"] == "red"
    assert "RED" in result["reason"]
    assert calls == [], "export_both must NOT run on a RED verdict"
    warnings = log.messages("warning")
    assert len(warnings) == 1
    assert "Auto-publish SKIPPED" in warnings[0]
    assert "post-red" in warnings[0]
    assert "RED" in warnings[0]


@pytest.mark.parametrize("judge_color", [None, "", "   "])
def test_publish_stage_skips_when_judge_never_ran_or_errored(harness, judge_color):
    """A judge that errored or never ran leaves judge_color empty — fail closed."""
    log, calls = harness
    post = {"id": "post-nojudge", "judge_color": judge_color}

    result = scheduler._publish_stage("run-t2", "post-nojudge", post)

    assert result["skipped"] is True
    assert result["blocked"] is True
    assert result["verdict"] is None
    assert "judge" in result["reason"].lower()
    assert calls == []
    warnings = log.messages("warning")
    assert len(warnings) == 1
    assert "Auto-publish SKIPPED" in warnings[0]
    assert "absent" in warnings[0]


def test_publish_stage_skips_when_judge_key_missing_entirely(harness):
    log, calls = harness

    result = scheduler._publish_stage("run-t3", "post-x", {"id": "post-x"})

    assert result["blocked"] is True
    assert calls == []


# ---------------------------------------------------------------------------
# _publish_stage: proceed on cleared verdicts
# ---------------------------------------------------------------------------


def test_publish_stage_proceeds_on_green(harness):
    log, calls = harness
    post = {"id": "post-green", "judge_color": "green"}

    result = scheduler._publish_stage("run-t4", "post-green", post)

    assert calls == ["post-green"], "export_both must run on a GREEN verdict"
    assert result["post_id"] == "post-green"
    assert "blocked" not in result
    assert not log.messages("warning")
    assert any("Publish gate cleared" in m for m in log.messages("info"))


@pytest.mark.parametrize("judge_color", ["yellow", "purple", "blue", "GREEN"])
def test_publish_stage_proceeds_on_other_cleared_verdicts(harness, judge_color):
    log, calls = harness
    post = {"id": "post-ok", "judge_color": judge_color}

    result = scheduler._publish_stage("run-t5", "post-ok", post)

    assert calls == ["post-ok"]
    assert result.get("skipped") is not True


# ---------------------------------------------------------------------------
# _build_result surfaces the block
# ---------------------------------------------------------------------------


def _result_ctx(exports):
    return {
        "run_id": "run-t6",
        "post_data": {"id": "post-1", "title": "T", "slug": "t", "judge_color": "red"},
        "quality": {"passed": True, "overall_score": 90},
        "rewrite_data": None,
        "template_type": "challenge_solution",
        "exports": exports,
    }


def test_build_result_flags_blocked_publish():
    blocked_exports = {
        "skipped": True,
        "blocked": True,
        "post_id": "post-1",
        "verdict": "red",
        "reason": "LLM judge verdict is RED",
    }
    result = scheduler._build_result(_result_ctx(blocked_exports))
    assert result["publish_blocked"] is True
    assert "RED" in result["publish_block_reason"]
    assert result["exports"] is blocked_exports


def test_build_result_clean_on_normal_export():
    result = scheduler._build_result(_result_ctx({"mdx": "/e/t.mdx", "html": "/e/t.html", "post_id": "post-1"}))
    assert "publish_blocked" not in result
    assert "publish_block_reason" not in result
