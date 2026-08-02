# CUI // SP-CTI
"""Tests for icdev.tools.ace.evaluator — rule-based session scoring."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tests._sql_compat import translating

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Mirrors migration 220_agent_loop_sessions.sql. The previous hand-rolled
# version drifted from it — it carried a ``done``/``session_json`` pair the real
# table never had and omitted ``messages_json``, which score_session() selects.
# Every SELECT therefore died on "no such column: messages_json".
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_loop_sessions (
    session_id        TEXT PRIMARY KEY,
    instance_id       TEXT NOT NULL DEFAULT '',
    coworker_id       TEXT NOT NULL DEFAULT '',
    llm_function      TEXT NOT NULL DEFAULT '',
    result_subtype    TEXT NOT NULL DEFAULT '',
    turns             INTEGER NOT NULL DEFAULT 0,
    total_input_tokens  INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd    REAL NOT NULL DEFAULT 0.0,
    messages_json     TEXT NOT NULL DEFAULT '[]',
    system_prompt     TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS agent_evals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    done INTEGER NOT NULL DEFAULT 0,
    turns_used INTEGER NOT NULL DEFAULT 0,
    efficiency_score REAL NOT NULL DEFAULT 0.0,
    total_tool_calls INTEGER NOT NULL DEFAULT 0,
    error_tool_calls INTEGER NOT NULL DEFAULT 0,
    tool_error_rate REAL NOT NULL DEFAULT 0.0,
    unique_tools_json TEXT NOT NULL DEFAULT '[]',
    tool_precision REAL NOT NULL DEFAULT 0.0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_coverage REAL NOT NULL DEFAULT 0.0,
    avg_reasoning_chars REAL NOT NULL DEFAULT 0.0,
    has_error_recovery_reasoning INTEGER NOT NULL DEFAULT 0,
    plan_stated INTEGER NOT NULL DEFAULT 0,
    scope_violations INTEGER NOT NULL DEFAULT 0,
    trust_denials INTEGER NOT NULL DEFAULT 0,
    llm_grade_json TEXT,
    reasoning_style TEXT NOT NULL DEFAULT '',
    graded_at TEXT NOT NULL DEFAULT (datetime('now')),
    grading_version TEXT NOT NULL DEFAULT '1.0'
);
"""


def _make_conn():
    # agent_evaluator authors PostgreSQL ``%s`` placeholders and relies on
    # StorageConnection to rewrite them for SQLite. A bare sqlite3 connection
    # removes that layer, so score_from_db()/save_evaluation() raise
    # ``near "%": syntax error`` on every statement.
    #
    # unclosable=True keeps the previous _NoClose behaviour: the module closes
    # the connection it is handed, which would destroy this in-memory DB before
    # the test can read it back.
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.commit()
    return translating(conn, unclosable=True)


def _make_session(session_id="s1", turns=3, done=True, subtype="success",
                  cost=0.01, messages=None):
    msgs = messages or [
        {"role": "user", "content": "Do X"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I'll read the file first."},
            {"type": "tool_use", "id": "tc-1", "name": "read_file", "input": {"path": "x.py"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tc-1",
             "content": [{"type": "text", "text": "def hello(): pass"}]},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "The file defines hello()."},
        ]},
    ]
    return {
        "session_id": session_id, "coworker_id": "cw-1", "turns": turns,
        "total_input_tokens": 100, "total_output_tokens": 50, "total_cost_usd": cost,
        "result_subtype": subtype, "done": 1 if done else 0,
        "session_json": json.dumps(msgs),
    }


def _insert_session(conn, sess):
    """Insert *sess* into agent_loop_sessions, naming every column.

    The columns are named rather than relying on ``VALUES (?, ?, …)`` ordinals
    so this stays correct if migration 220 gains a column. ``done`` is not
    persisted — the real table has no such column and score_session() derives
    it from ``result_subtype``.
    """
    conn.execute(
        "INSERT INTO agent_loop_sessions "
        "(session_id, instance_id, coworker_id, llm_function, result_subtype, "
        " turns, total_input_tokens, total_output_tokens, total_cost_usd, messages_json) "
        "VALUES (%s, 'i1', %s, 'code', %s, %s, %s, %s, %s, %s)",
        (sess["session_id"], sess["coworker_id"], sess["result_subtype"],
         sess["turns"], sess["total_input_tokens"], sess["total_output_tokens"],
         sess["total_cost_usd"], sess["session_json"]),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Tests — _extract_reasoning_metrics
# ---------------------------------------------------------------------------

class TestExtractReasoningMetrics:
    def test_reasoning_present_before_tool_call(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        msgs = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "I will read the file."},
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["reasoning_coverage"] == 1.0
        assert r["avg_reasoning_chars"] > 0

    def test_no_reasoning_before_tool_call(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["reasoning_coverage"] == 0.0
        assert r["plan_stated"] is False

    def test_plan_stated_when_long_reasoning(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        long_text = "A" * 150
        msgs = [
            {"role": "assistant", "content": [
                {"type": "text", "text": long_text},
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["plan_stated"] is True

    def test_error_recovery_reasoning_detected(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "is_error": True,
                 "content": [{"type": "text", "text": "File not found"}]},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "The file was not found. Let me try a different path."},
                {"type": "tool_use", "id": "2", "name": "list_files", "input": {}},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["has_error_recovery_reasoning"] is True

    def test_scope_violation_detected(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "is_error": True,
                 "content": [{"type": "text", "text": "ScopeViolationError: path outside repo"}]},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["scope_violations"] == 1

    def test_empty_messages_returns_zero_metrics(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        r = _extract_reasoning_metrics("[]")
        assert r["reasoning_coverage"] == 0.0
        assert r["scope_violations"] == 0

    def test_trust_denial_detected(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        msgs = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "write_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1", "is_error": True,
                 "content": [{"type": "text", "text": "TrustKernelDenied: insufficient trust_tier"}]},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["trust_denials"] == 1

    def test_multiple_tool_calls_coverage_ratio(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        # 2 tool-calling turns: first has reasoning, second does not
        msgs = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check this."},
                {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "1",
                 "content": [{"type": "text", "text": "ok"}]},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "2", "name": "list_files", "input": {}},
            ]},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["reasoning_coverage"] == 0.5

    def test_string_content_not_counted_as_tool_call_turn(self):
        from icdev.tools.ace.evaluator import _extract_reasoning_metrics
        msgs = [
            {"role": "assistant", "content": "Just a text response, no tool call."},
        ]
        r = _extract_reasoning_metrics(json.dumps(msgs))
        assert r["reasoning_coverage"] == 0.0


# ---------------------------------------------------------------------------
# Tests — score_session from AgentLoopResult
# ---------------------------------------------------------------------------

class TestScoreFromResult:
    def _make_result(self, **kwargs):
        """Build a minimal object that looks like AgentLoopResult."""
        class FakeResult:
            pass
        r = FakeResult()
        r.messages = kwargs.get("messages", [])
        r.session_id = kwargs.get("session_id", "test-sess-1")
        r.done = kwargs.get("done", True)
        r.turns = kwargs.get("turns", 2)
        r.result_subtype = kwargs.get("result_subtype", "success")
        r.total_cost_usd = kwargs.get("total_cost_usd", 0.005)
        r.total_input_tokens = kwargs.get("total_input_tokens", 100)
        r.total_output_tokens = kwargs.get("total_output_tokens", 40)
        r.tool_call_log = kwargs.get("tool_call_log", [])
        return r

    def test_success_session(self):
        from icdev.tools.ace.evaluator import _score_from_result
        r = self._make_result(
            messages=[
                {"role": "assistant", "content": [
                    {"type": "text", "text": "I'll start by reading."},
                    {"type": "tool_use", "id": "1", "name": "read_file", "input": {}},
                ]},
            ],
            turns=2, done=True, result_subtype="success", total_cost_usd=0.005,
        )
        eval_r = _score_from_result(r, max_iterations=12, reasoning_style="")
        assert eval_r.done is True
        assert eval_r.outcome == "success"
        assert eval_r.turns_used == 2
        assert eval_r.efficiency_score > 0
        assert eval_r.total_cost_usd == 0.005

    def test_truncated_session_zero_efficiency(self):
        from icdev.tools.ace.evaluator import _score_from_result
        r = self._make_result(messages=[], turns=12, done=False, result_subtype="error_max_turns")
        eval_r = _score_from_result(r, max_iterations=12, reasoning_style="")
        assert eval_r.efficiency_score == 0.0
        assert eval_r.done is False

    def test_zero_max_iterations_no_crash(self):
        from icdev.tools.ace.evaluator import _score_from_result
        r = self._make_result(turns=3)
        eval_r = _score_from_result(r, max_iterations=0, reasoning_style="")
        assert eval_r.efficiency_score == 0.0

    def test_reasoning_style_propagated(self):
        from icdev.tools.ace.evaluator import _score_from_result
        r = self._make_result()
        eval_r = _score_from_result(r, max_iterations=10, reasoning_style="cod")
        assert eval_r.reasoning_style == "cod"


# ---------------------------------------------------------------------------
# Tests — score_session from DB
# ---------------------------------------------------------------------------

class TestScoreFromDB:
    def test_score_existing_session(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        sess = _make_session("sess-db-1", turns=4, done=True, subtype="success")
        _insert_session(conn, sess)
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        eval_r = _m.score_session("sess-db-1")
        assert eval_r.session_id == "sess-db-1"
        assert eval_r.outcome == "success"
        assert eval_r.done is True
        assert eval_r.turns_used == 4

    def test_score_missing_session_returns_not_found(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        eval_r = _m.score_session("nonexistent-session")
        assert eval_r.outcome == "not_found"

    def test_score_db_unavailable_returns_error(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        monkeypatch.setattr(_m, "_get_conn", lambda: None)
        eval_r = _m.score_session("any-session")
        assert "error" in eval_r.outcome

    def test_efficiency_computed_from_turns(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        sess = _make_session("sess-eff-1", turns=6, done=True, subtype="success")
        _insert_session(conn, sess)
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        eval_r = _m.score_session("sess-eff-1", max_iterations=12)
        # 1 - 6/12 = 0.5
        assert abs(eval_r.efficiency_score - 0.5) < 0.001


# ---------------------------------------------------------------------------
# Tests — save_eval and get_eval
# ---------------------------------------------------------------------------

class TestSaveAndGetEval:
    def test_save_and_retrieve(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        from icdev.tools.ace.evaluator import EvalResult, save_eval, get_eval
        er = EvalResult(
            session_id="sev-1", outcome="success", done=True,
            turns_used=3, efficiency_score=0.75, total_tool_calls=5,
        )
        save_eval(er)
        retrieved = get_eval("sev-1")
        assert retrieved is not None
        assert retrieved.session_id == "sev-1"
        assert retrieved.outcome == "success"
        assert retrieved.efficiency_score == 0.75

    def test_overwrite_existing_eval(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        from icdev.tools.ace.evaluator import EvalResult, save_eval, get_eval
        ev1 = EvalResult(session_id="sev-2", outcome="error_max_turns", efficiency_score=0.2)
        save_eval(ev1)
        ev2 = EvalResult(session_id="sev-2", outcome="success", efficiency_score=0.8)
        save_eval(ev2, overwrite=True)

        retrieved = get_eval("sev-2")
        assert retrieved.outcome == "success"
        assert retrieved.efficiency_score == 0.8

    def test_get_missing_returns_none(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)
        assert _m.get_eval("nope") is None

    def test_save_raises_when_db_unavailable(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        monkeypatch.setattr(_m, "_get_conn", lambda: None)
        from icdev.tools.ace.evaluator import EvalResult, save_eval
        with pytest.raises(RuntimeError):
            save_eval(EvalResult(session_id="x"))

    def test_reasoning_fields_persisted(self, monkeypatch):
        import icdev.tools.ace.evaluator as _m
        conn = _make_conn()
        monkeypatch.setattr(_m, "_get_conn", lambda: conn)

        from icdev.tools.ace.evaluator import EvalResult, save_eval, get_eval
        er = EvalResult(
            session_id="sev-3",
            reasoning_coverage=0.8,
            avg_reasoning_chars=120.5,
            has_error_recovery_reasoning=True,
            plan_stated=True,
            scope_violations=2,
            reasoning_style="cod",
        )
        save_eval(er)
        r = get_eval("sev-3")
        assert r.reasoning_coverage == 0.8
        assert r.avg_reasoning_chars == 120.5
        assert r.has_error_recovery_reasoning is True
        assert r.plan_stated is True
        assert r.scope_violations == 2
        assert r.reasoning_style == "cod"


# ---------------------------------------------------------------------------
# Tests — grade_output_quality (Phase 2, mocked)
# ---------------------------------------------------------------------------

class TestGradeOutputQuality:
    def test_missing_prompts_returns_error(self):
        from icdev.tools.ace.evaluator import EvalResult, grade_output_quality
        er = EvalResult(session_id="g1")
        result = grade_output_quality(er, user_prompt="", final_content="")
        assert "error" in result
        assert result["overall"] == 0.0

    def test_missing_final_content_returns_error(self):
        from icdev.tools.ace.evaluator import EvalResult, grade_output_quality
        er = EvalResult(session_id="g2")
        result = grade_output_quality(er, user_prompt="Do X", final_content="")
        assert "error" in result

    def test_llm_failure_returns_error_dict(self, monkeypatch):
        import sys
        import types
        # Patch both canonical and legacy namespaces
        class _FakeRouter:
            def invoke(self, *a, **kw):
                raise RuntimeError("no LLM in test")
        class _CrossGraderViolation(RuntimeError):
            pass
        for mod_name in ("tools.llm.router", "icdev.tools.llm.router"):
            fake_mod = types.ModuleType(mod_name)
            fake_mod.LLMRouter = _FakeRouter
            fake_mod.CrossGraderViolation = _CrossGraderViolation
            monkeypatch.setitem(sys.modules, mod_name, fake_mod)
        for mod_name in ("tools.llm.provider", "icdev.tools.llm.provider"):
            fake_prov = types.ModuleType(mod_name)
            class _LLMRequest:
                def __init__(self, **kw): pass
            fake_prov.LLMRequest = _LLMRequest
            monkeypatch.setitem(sys.modules, mod_name, fake_prov)

        from icdev.tools.ace.evaluator import EvalResult, grade_output_quality
        er = EvalResult(session_id="g3")
        result = grade_output_quality(er, user_prompt="Do X", final_content="Done X.")
        assert "error" in result
        assert result["overall"] == 0.0


# ---------------------------------------------------------------------------
# Tests — eval_runner (Phase 3)
# ---------------------------------------------------------------------------

class TestEvalRunner:
    def test_load_suite_missing_file(self):
        from icdev.tools.ace.eval_runner import load_suite
        result = load_suite("/nonexistent/path/suite.yaml")
        assert result == []

    def test_load_suite_empty_evals(self, tmp_path):
        from icdev.tools.ace.eval_runner import load_suite
        suite = tmp_path / "empty.yaml"
        suite.write_text("evals: []\n", encoding="utf-8")
        assert load_suite(suite) == []

    def test_load_suite_parses_entries(self, tmp_path):
        from icdev.tools.ace.eval_runner import load_suite
        suite = tmp_path / "suite.yaml"
        suite.write_text(
            "evals:\n  - name: test1\n    user_prompt: hello\n    max_turns: 2\n",
            encoding="utf-8",
        )
        evals = load_suite(suite)
        assert len(evals) == 1
        assert evals[0]["name"] == "test1"

    def test_eval_run_result_structure(self):
        from icdev.tools.ace.eval_runner import EvalRunResult
        r = EvalRunResult(run_id="run-1", eval_name="test", passed=True)
        assert r.eval_name == "test"
        assert r.passed is True

    def test_run_eval_missing_prompt_returns_error(self):
        from icdev.tools.ace.eval_runner import run_eval
        result = run_eval({"name": "bad", "user_prompt": ""}, run_id="r1")
        assert result.error != ""
        assert result.passed is False

    def test_run_all_empty_suite(self, tmp_path):
        from icdev.tools.ace.eval_runner import run_all
        suite = tmp_path / "empty.yaml"
        suite.write_text("evals: []\n", encoding="utf-8")
        results = run_all(suite)
        assert results == []

    def test_run_eval_router_failure(self):
        from icdev.tools.ace.eval_runner import run_eval
        result = run_eval(
            {"name": "test", "user_prompt": "Do X"},
            run_id="r1",
            # Pass a broken router object
            router=object(),
        )
        # Should handle gracefully with an error, not raise
        assert isinstance(result.error, str)


# ---------------------------------------------------------------------------
# Tests — judge prompt upgrade (expert persona + confidence calibration)
# ---------------------------------------------------------------------------

class TestGradePromptUpgrade:
    """Verify the upgraded _JUDGE_PROMPT template and grade_output_quality() output."""

    def _mock_router(self, monkeypatch, response_json: str, model_id: str = "claude-sonnet-4-6"):
        """Inject a fake LLMRouter that returns a fixed JSON string."""
        import sys
        import types

        fake_router_mod = types.ModuleType("icdev.tools.llm.router")

        class _FakeResponse:
            content = response_json
            def __init__(self):
                self.model_id = model_id

        class _CrossGraderViolation(RuntimeError):
            pass

        class _FakeRouter:
            def invoke(self, fn, req, **kw):
                exclude = kw.get("exclude_model_ids") or []
                if model_id in exclude:
                    raise _CrossGraderViolation(f"grader {model_id!r} excluded")
                return _FakeResponse()

        fake_router_mod.LLMRouter = _FakeRouter
        fake_router_mod.CrossGraderViolation = _CrossGraderViolation
        monkeypatch.setitem(sys.modules, "icdev.tools.llm.router", fake_router_mod)
        monkeypatch.setitem(sys.modules, "tools.llm.router", fake_router_mod)
        return _CrossGraderViolation

    def _mock_provider(self, monkeypatch):
        import sys
        import types
        fake = types.ModuleType("icdev.tools.llm.provider")
        class _LLMRequest:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
        fake.LLMRequest = _LLMRequest
        monkeypatch.setitem(sys.modules, "icdev.tools.llm.provider", fake)
        monkeypatch.setitem(sys.modules, "tools.llm.provider", fake)

    def test_judge_prompt_contains_expert_persona(self):
        from icdev.tools.ace.evaluator import _JUDGE_PROMPT
        assert "senior AI systems evaluator" in _JUDGE_PROMPT
        assert "10,000+" in _JUDGE_PROMPT

    def test_judge_prompt_uses_categorical_vocabulary(self):
        """Deterministic-picker (agx-pick-02): the judge emits a 3-value enum per
        dimension, not a free-form float. Python composes the numeric grade."""
        from icdev.tools.ace.evaluator import _JUDGE_PROMPT
        for label in ("supported", "partial", "unsupported"):
            assert label in _JUDGE_PROMPT
        # No longer asks the model for numeric scores.
        assert "Do NOT emit numeric scores" in _JUDGE_PROMPT

    def test_judge_prompt_contains_rules_block(self):
        from icdev.tools.ace.evaluator import _JUDGE_PROMPT
        assert "RULES" in _JUDGE_PROMPT
        assert "Identical tool call repeated" in _JUDGE_PROMPT
        assert "faithfulness" in _JUDGE_PROMPT

    def test_judge_prompt_contains_evidence_structure(self):
        from icdev.tools.ace.evaluator import _JUDGE_PROMPT
        assert "evidence" in _JUDGE_PROMPT
        assert "what" in _JUDGE_PROMPT
        assert "so_what" in _JUDGE_PROMPT
        assert "now_what" in _JUDGE_PROMPT

    def test_grade_returns_confidence_fields(self, monkeypatch):
        """grade_output_quality() passes confidence fields through from LLM response."""
        self._mock_provider(monkeypatch)
        good_json = json.dumps({
            "faithfulness": 0.9, "faithfulness_confidence": "HIGH",
            "completeness": 0.85, "completeness_confidence": "MEDIUM",
            "reasoning_quality": 0.8, "reasoning_quality_confidence": "HIGH",
            "cod_quality": 0.5, "cod_quality_confidence": "UNKNOWN",
            "error_adaptation": 0.7, "error_adaptation_confidence": "LOW",
            "overall": 0.82,
            "flags": [],
            "evidence": {
                "what": "Agent completed all 3 tasks in 4 turns.",
                "so_what": "Efficient execution with no wasted turns.",
                "now_what": "NO ACTION REQUIRED",
            },
            "reasoning": "High-quality session with clear reasoning.",
        })
        self._mock_router(monkeypatch, good_json)

        from icdev.tools.ace.evaluator import EvalResult, grade_output_quality
        er = EvalResult(session_id="g-conf-1")
        result = grade_output_quality(er, user_prompt="Do X", final_content="Done X.")

        assert result.get("faithfulness_confidence") == "HIGH"
        assert result.get("completeness_confidence") == "MEDIUM"
        assert "evidence" in result
        assert result["evidence"]["now_what"] == "NO ACTION REQUIRED"
        assert "grader_model_id" in result

    def test_cross_grader_violation_raised_when_same_model(self, monkeypatch):
        """grade_output_quality() raises CrossGraderViolation when grader == session model."""
        self._mock_provider(monkeypatch)
        CrossGraderViolation = self._mock_router(monkeypatch, "{}", model_id="claude-sonnet-4-6")

        from icdev.tools.ace.evaluator import EvalResult, grade_output_quality
        er = EvalResult(session_id="g-cross-1", model_id="claude-sonnet-4-6")
        with pytest.raises(CrossGraderViolation):
            grade_output_quality(er, user_prompt="Do X", final_content="Done X.")
