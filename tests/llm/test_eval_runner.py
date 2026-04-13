# CUI // SP-CTI
"""OPT-64: tests for tools/llm/eval_runner.py — declarative LLM eval harness.

All tests are offline. The runner is fed a fake router whose
_get_model_config / _get_provider return canned responses so no real
LLM is invoked.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.llm import eval_runner  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Assertion primitives
# ────────────────────────────────────────────────────────────────────────────


def test_assert_contains_pass_and_fail():
    r = eval_runner.run_assertions(
        "hello world", [{"type": "contains", "value": "world"}]
    )
    assert r[0].passed

    r = eval_runner.run_assertions(
        "hello world", [{"type": "contains", "value": "absent"}]
    )
    assert not r[0].passed
    assert "absent" in r[0].detail


def test_assert_not_contains():
    r = eval_runner.run_assertions(
        "clean", [{"type": "not_contains", "value": "TODO"}]
    )
    assert r[0].passed

    r = eval_runner.run_assertions(
        "TODO: fix", [{"type": "not_contains", "value": "TODO"}]
    )
    assert not r[0].passed


def test_assert_regex():
    r = eval_runner.run_assertions(
        "def foo(): pass", [{"type": "regex", "pattern": r"def\s+\w+"}]
    )
    assert r[0].passed

    r = eval_runner.run_assertions(
        "no function here", [{"type": "regex", "pattern": r"def\s+\w+"}]
    )
    assert not r[0].passed

    r = eval_runner.run_assertions(
        "anything", [{"type": "regex", "pattern": r"([a-"}]
    )
    assert not r[0].passed
    assert "bad pattern" in r[0].detail


def test_assert_max_and_min_length():
    r = eval_runner.run_assertions(
        "x" * 50, [{"type": "max_length", "value": 100}]
    )
    assert r[0].passed

    r = eval_runner.run_assertions(
        "x" * 200, [{"type": "max_length", "value": 100}]
    )
    assert not r[0].passed

    r = eval_runner.run_assertions(
        "x" * 50, [{"type": "min_length", "value": 100}]
    )
    assert not r[0].passed

    r = eval_runner.run_assertions(
        "x" * 150, [{"type": "min_length", "value": 100}]
    )
    assert r[0].passed


def test_assert_json_schema_roundtrip():
    body = '{"output": "ok", "score": 1}'
    r = eval_runner.run_assertions(
        body,
        [{"type": "json_schema",
          "schema": {"type": "object", "required": ["output"]}}],
    )
    assert r[0].passed

    bad = "not json"
    r = eval_runner.run_assertions(
        bad,
        [{"type": "json_schema",
          "schema": {"type": "object", "required": ["output"]}}],
    )
    assert not r[0].passed

    missing = '{"score": 1}'
    r = eval_runner.run_assertions(
        missing,
        [{"type": "json_schema",
          "schema": {"type": "object", "required": ["output"]}}],
    )
    assert not r[0].passed


def test_unknown_assertion_type_marked_failed():
    r = eval_runner.run_assertions(
        "x", [{"type": "mystery_type"}]
    )
    assert not r[0].passed
    assert "unsupported" in r[0].detail


# ────────────────────────────────────────────────────────────────────────────
# Runner (with fake router)
# ────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content, model_id="fake-id"):
        self.content = content
        self.provider = "fake"
        self.model_id = model_id
        self.input_tokens = 10
        self.output_tokens = 20
        self.duration_ms = 5


class _FakeProvider:
    def __init__(self, content_map):
        self._content_map = content_map

    def invoke(self, request, model_id, model_cfg):
        key = model_cfg.get("_name")
        return _FakeResponse(self._content_map.get(key, "default output"),
                             model_id=model_id)


class _FakeRouter:
    def __init__(self, content_map):
        self._models = {
            "fake-good": {"_name": "fake-good",
                          "provider": "fake",
                          "model_id": "fake-good-id"},
            "fake-bad": {"_name": "fake-bad",
                         "provider": "fake",
                         "model_id": "fake-bad-id"},
        }
        self._provider = _FakeProvider(content_map)

    def _get_model_config(self, name):
        return self._models.get(name, {})

    def _get_provider(self, name):
        return self._provider if name == "fake" else None


def test_run_eval_two_models_side_by_side(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_runner, "DEFAULT_REPORT_DIR", tmp_path)
    cfg = {
        "name": "unit-eval",
        "description": "offline unit eval",
        "models": ["fake-good", "fake-bad"],
        "default_max_tokens": 100,
        "prompts": [
            {
                "id": "sum",
                "messages": [{"role": "user",
                              "content": "write add(a,b)"}],
                "assertions": [
                    {"type": "contains", "value": "def add"},
                ],
            }
        ],
    }
    router = _FakeRouter({
        "fake-good": "def add(a, b):\n    return a + b\n",
        "fake-bad": "sorry I can't help",
    })
    report = eval_runner.run_eval(cfg, router=router)
    assert len(report.runs) == 2

    good = next(r for r in report.runs if r.model == "fake-good")
    bad = next(r for r in report.runs if r.model == "fake-bad")
    assert good.passed
    assert not bad.passed

    summary = report.summary
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["total_runs"] == 2


def test_run_eval_missing_model_yields_error_run(monkeypatch):
    cfg = {
        "name": "missing",
        "models": ["does-not-exist"],
        "prompts": [{"id": "p", "messages": [
            {"role": "user", "content": "hi"}]}],
    }
    router = _FakeRouter({})
    report = eval_runner.run_eval(cfg, router=router)
    assert len(report.runs) == 1
    assert report.runs[0].ok is False
    assert "not in llm_config" in report.runs[0].error


def test_run_eval_raises_on_missing_models_key():
    with pytest.raises(ValueError):
        eval_runner.run_eval({"prompts": [{"id": "a"}]}, router=_FakeRouter({}))


def test_run_eval_raises_on_missing_prompts_key():
    with pytest.raises(ValueError):
        eval_runner.run_eval(
            {"models": ["fake-good"]}, router=_FakeRouter({}),
        )


# ────────────────────────────────────────────────────────────────────────────
# Renderers
# ────────────────────────────────────────────────────────────────────────────


def _build_report():
    cfg = {
        "name": "render-test",
        "description": "sanity check for render_*",
        "models": ["fake-good"],
        "prompts": [{
            "id": "hello",
            "messages": [{"role": "user", "content": "hi"}],
            "assertions": [{"type": "contains", "value": "world"}],
        }],
    }
    router = _FakeRouter({"fake-good": "hello world"})
    return eval_runner.run_eval(cfg, router=router)


def test_render_json_roundtrip():
    report = _build_report()
    out = eval_runner.render_json(report)
    parsed = json.loads(out)
    assert parsed["name"] == "render-test"
    assert parsed["total_prompts"] == 1
    assert parsed["runs"][0]["passed"] is True
    assert parsed["summary"]["passed"] == 1


def test_render_markdown_contains_table():
    report = _build_report()
    md = eval_runner.render_markdown(report)
    assert "# LLM Eval: render-test" in md
    assert "| Prompt | Model |" in md
    assert "hello" in md
    assert "PASS" in md


def test_render_html_contains_table():
    report = _build_report()
    html = eval_runner.render_html(report)
    assert "<title>LLM Eval" in html
    assert "PASS" in html
    assert "<table>" in html


def test_write_report_emits_three_files(tmp_path):
    report = _build_report()
    paths = eval_runner.write_report(report, tmp_path)
    assert paths["markdown"].exists()
    assert paths["json"].exists()
    assert paths["html"].exists()
    assert paths["markdown"].read_text(encoding="utf-8").startswith("# LLM Eval")


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def test_cli_gate_exits_nonzero_on_failure(tmp_path, monkeypatch):
    eval_path = tmp_path / "bad.yaml"
    eval_path.write_text(
        'name: cli-bad\n'
        'models: [fake-good]\n'
        'prompts:\n'
        '  - id: p\n'
        '    messages: [{role: user, content: "hi"}]\n'
        '    assertions:\n'
        '      - {type: contains, value: "IMPOSSIBLE_STRING"}\n',
        encoding="utf-8",
    )

    _real_run_eval = eval_runner.run_eval

    def fake_run_eval(cfg, router=None, model_override=None):
        return _real_run_eval(
            cfg,
            router=_FakeRouter({"fake-good": "totally unrelated"}),
            model_override=model_override,
        )

    monkeypatch.setattr(eval_runner, "run_eval", fake_run_eval)

    rc = eval_runner.main([
        "--eval", str(eval_path),
        "--output-dir", str(tmp_path / "reports"),
        "--gate",
    ])
    assert rc == 1


def test_cli_gate_exits_zero_when_all_pass(tmp_path, monkeypatch, capsys):
    eval_path = tmp_path / "good.yaml"
    eval_path.write_text(
        'name: cli-good\n'
        'models: [fake-good]\n'
        'prompts:\n'
        '  - id: p\n'
        '    messages: [{role: user, content: "hi"}]\n'
        '    assertions:\n'
        '      - {type: contains, value: "hello"}\n',
        encoding="utf-8",
    )

    _real_run_eval = eval_runner.run_eval

    def fake_run_eval(cfg, router=None, model_override=None):
        return _real_run_eval(
            cfg,
            router=_FakeRouter({"fake-good": "hello world"}),
            model_override=model_override,
        )

    monkeypatch.setattr(eval_runner, "run_eval", fake_run_eval)

    rc = eval_runner.main([
        "--eval", str(eval_path),
        "--output-dir", str(tmp_path / "reports"),
        "--gate",
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"passed": true' in out.lower() or '"passed": 1' in out.lower()


def test_cli_missing_file_returns_2(tmp_path, capsys):
    rc = eval_runner.main(["--eval", str(tmp_path / "nope.yaml")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err.lower()
