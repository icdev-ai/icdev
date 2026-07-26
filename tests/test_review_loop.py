# CUI // SP-CTI
"""Tests for tools/quality/review_loop.py — the local review-until-green loop.

Gates are exercised through injected fakes (no real ruff/SIPA/DB), so the loop's
scoring, progress-detection, and iteration-cap logic are tested deterministically.
"""
from __future__ import annotations

import importlib

import pytest

rl = importlib.import_module("tools.quality.review_loop")


# ── helpers ────────────────────────────────────────────────────────────────


def _finding(gate="ruff", file="a.py", code="F401", line=1, fixable=False):
    return rl.Finding(gate=gate, file=file, message="x", code=code, line=line, fixable=fixable)


def _gate(name, passed, blocking=True, findings=None, skipped=False):
    return rl.GateResult(
        name=name, blocking=blocking, passed=passed,
        findings=findings or [], skipped=skipped,
    )


def _loop(monkeypatch, gate_sequences, *, max_iterations=3):
    """Build a ReviewLoop whose _run_gates yields successive gate lists.

    gate_sequences: list of "gates for iteration i". changed_py_files and audit
    are stubbed so no git/DB is touched.
    """
    cfg = rl._default_config()
    cfg["max_iterations"] = max_iterations
    cfg["audit"] = False
    loop = rl.ReviewLoop(config=cfg, autofix=False)

    monkeypatch.setattr(rl, "changed_py_files", lambda base, root, staged=False: ["a.py"])
    monkeypatch.setattr(rl, "resolve_base", lambda base, root: base)

    calls = {"i": 0}

    def fake_run_gates(py_files, base):
        idx = min(calls["i"], len(gate_sequences) - 1)
        calls["i"] += 1
        return gate_sequences[idx]

    monkeypatch.setattr(loop, "_run_gates", fake_run_gates)
    return loop


# ── scoring ─────────────────────────────────────────────────────────────────


def test_score_all_passing_is_green():
    gates = [_gate("ruff", True), _gate("sipa", True)]
    green, score = rl.ReviewLoop._score(gates)
    assert green is True
    assert score == "2/2"


def test_score_ignores_nonblocking_gates():
    gates = [_gate("ruff", True), _gate("info", False, blocking=False)]
    green, score = rl.ReviewLoop._score(gates)
    assert green is True
    assert score == "1/1"


def test_score_one_failing_not_green():
    gates = [_gate("ruff", False, findings=[_finding()]), _gate("sipa", True)]
    green, score = rl.ReviewLoop._score(gates)
    assert green is False
    assert score == "1/2"


# ── loop control flow ────────────────────────────────────────────────────────


def test_loop_stops_on_green(monkeypatch):
    loop = _loop(monkeypatch, [[_gate("ruff", True)]])
    report = loop.run(base=None)
    assert report.green is True
    assert len(report.iterations) == 1
    assert report.fix_brief == []
    assert "green" in report.reason.lower()


def test_loop_converges_after_fix(monkeypatch):
    # iter 1 fails on a finding, iter 2 is clean (autofix-like progress).
    seqs = [
        [_gate("ruff", False, findings=[_finding(code="F401")])],
        [_gate("ruff", True)],
    ]
    loop = _loop(monkeypatch, seqs)
    report = loop.run(base=None)
    assert report.green is True
    assert len(report.iterations) == 2


def test_loop_stops_when_no_progress(monkeypatch):
    # Same finding twice → stuck; must stop before the cap.
    same = [_gate("ruff", False, findings=[_finding(code="E999")])]
    loop = _loop(monkeypatch, [same, same, same], max_iterations=5)
    report = loop.run(base=None)
    assert report.green is False
    assert len(report.iterations) == 2  # detected no-progress on the 2nd
    assert "no progress" in report.reason.lower()
    assert len(report.fix_brief) == 1


def test_loop_hits_iteration_cap(monkeypatch):
    # Distinct findings each iteration → never green, never "stuck" → cap.
    seqs = [
        [_gate("ruff", False, findings=[_finding(code="F401", line=1)])],
        [_gate("ruff", False, findings=[_finding(code="F811", line=2)])],
        [_gate("ruff", False, findings=[_finding(code="E711", line=3)])],
    ]
    loop = _loop(monkeypatch, seqs, max_iterations=3)
    report = loop.run(base=None)
    assert report.green is False
    assert len(report.iterations) == 3
    assert "cap reached" in report.reason.lower()


def test_fix_brief_carries_open_findings(monkeypatch):
    seqs = [[
        _gate("ruff", False, findings=[_finding(code="F401")]),
        _gate("sipa", True),
    ]]
    loop = _loop(monkeypatch, seqs, max_iterations=1)
    report = loop.run(base=None)
    assert report.green is False
    assert [f.code for f in report.fix_brief] == ["F401"]


# ── config ───────────────────────────────────────────────────────────────────


def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = rl.load_config(tmp_path / "nope.yaml")
    assert cfg["max_iterations"] == 3
    assert cfg["gates"]["ruff"]["blocking"] is True


def test_load_config_merges_partial_override(tmp_path):
    p = tmp_path / "rl.yaml"
    p.write_text("max_iterations: 7\ngates:\n  sipa:\n    enabled: false\n", encoding="utf-8")
    cfg = rl.load_config(p)
    assert cfg["max_iterations"] == 7
    assert cfg["gates"]["sipa"]["enabled"] is False
    # untouched gate keeps its default
    assert cfg["gates"]["ruff"]["blocking"] is True


def test_load_config_bad_yaml_degrades(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("max_iterations: : :\n  - broken", encoding="utf-8")
    cfg = rl.load_config(p)
    assert cfg["max_iterations"] == 3  # fell back to defaults


# ── gates with injected runners ──────────────────────────────────────────────


def test_gate_ruff_no_files_skips():
    res = rl.gate_ruff([], {"blocking": True}, autofix=False, repo_root=rl.ROOT)
    assert res.passed is True
    assert res.skipped is True


def test_gate_ruff_parses_findings(monkeypatch):
    import json as _json

    class _Proc:
        stdout = _json.dumps([
            {"filename": "a.py", "code": "F401", "message": "unused",
             "location": {"row": 3}, "fix": {"applicability": "safe"}},
        ])

    monkeypatch.setattr(rl, "_run_ruff", lambda files, flags, root: _Proc())
    res = rl.gate_ruff(["a.py"], {"blocking": True}, autofix=False, repo_root=rl.ROOT)
    assert res.passed is False
    assert len(res.findings) == 1
    assert res.findings[0].code == "F401"
    assert res.findings[0].fixable is True


def test_gate_sipa_quarantine_blocks(monkeypatch):
    import sys as _sys
    import types as _types

    fake = _types.ModuleType("tools.integrity.pr_gates")
    fake.assess_changed_files = lambda **kw: {
        "verdict": "quarantine", "risk_score": 91.0,
        "findings": [{"file": "x.py", "message": "malware-ish", "rule": "R1"}],
    }
    monkeypatch.setitem(_sys.modules, "tools.integrity.pr_gates", fake)

    res = rl.gate_sipa(["x.py"], {"blocking": True, "block_on": ["quarantine"]},
                       autofix=False, repo_root=rl.ROOT, base="origin/main")
    assert res.passed is False
    assert len(res.findings) == 1


def test_gate_sipa_allow_passes(monkeypatch):
    import sys as _sys
    import types as _types

    fake = _types.ModuleType("tools.integrity.pr_gates")
    fake.assess_changed_files = lambda **kw: {
        "verdict": "review", "risk_score": 30.0, "findings": [],
    }
    monkeypatch.setitem(_sys.modules, "tools.integrity.pr_gates", fake)

    res = rl.gate_sipa(["x.py"], {"blocking": True, "block_on": ["quarantine"]},
                       autofix=False, repo_root=rl.ROOT, base="origin/main")
    assert res.passed is True  # 'review' is not in block_on


# ── loose JSON recovery (coherence interleaves logs with JSON) ───────────────


def test_loads_loose_clean_json():
    assert rl._loads_loose('{"overall_pass": true}') == {"overall_pass": True}


def test_loads_loose_recovers_from_log_noise():
    noisy = 'INFO  some log line\nWARNING another\n{\n  "overall_pass": false\n}\n'
    assert rl._loads_loose(noisy) == {"overall_pass": False}


def test_loads_loose_returns_none_on_garbage():
    assert rl._loads_loose("not json at all") is None
    assert rl._loads_loose("") is None


def test_gate_coherence_parses_failed_checks(monkeypatch, tmp_path):
    # Point the gate at a fake coherence_checker that emits noisy JSON.
    import subprocess as _sp

    class _Proc:
        returncode = 1
        stdout = (
            "INFO running checks\n"
            '{"overall_pass": false, "checks": ['
            '{"name": "schema_code", "passed": false, "issues": ["drift in X"]},'
            '{"name": "imports", "passed": true}]}'
        )

    # Make the script path "exist" and the subprocess return our fake proc.
    monkeypatch.setattr(rl.pathlib.Path, "exists", lambda self: True)
    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Proc())
    res = rl.gate_coherence(["a.py"], {"blocking": True}, autofix=False, repo_root=tmp_path)
    assert res.passed is False
    assert any(f.code == "schema_code" for f in res.findings)


def test_gate_coherence_parses_real_shape(monkeypatch, tmp_path):
    # The real coherence_checker shape: check_id/check_name/status/message,
    # and 'warn' must NOT be treated as a failure.
    import subprocess as _sp

    class _Proc:
        returncode = 1
        stdout = (
            "2026-06-08 INFO coherence run\n"
            '{"overall_pass": false, "checks": ['
            '{"check_id": "schema_code", "check_name": "Schema/Code", '
            '"status": "fail", "message": "table drift"},'
            '{"check_id": "x", "check_name": "X", "status": "warn", "message": "minor"},'
            '{"check_id": "y", "check_name": "Y", "status": "pass", "message": "ok"}]}'
        )

    monkeypatch.setattr(rl.pathlib.Path, "exists", lambda self: True)
    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Proc())
    res = rl.gate_coherence(["a.py"], {"blocking": True}, autofix=False, repo_root=tmp_path)
    assert res.passed is False
    codes = [f.code for f in res.findings]
    assert codes == ["schema_code"]  # only the 'fail' check; warn/pass excluded
    assert res.findings[0].message == "table drift"


# ── rendering ────────────────────────────────────────────────────────────────


def test_render_summary_green(monkeypatch):
    loop = _loop(monkeypatch, [[_gate("ruff", True)]])
    report = loop.run(base=None)
    out = rl.render_summary(report)
    assert "GREEN" in out
    assert "iter" in out


def test_render_summary_lists_fix_brief(monkeypatch):
    seqs = [[_gate("ruff", False, findings=[_finding(code="F401", file="a.py", line=2)])]]
    loop = _loop(monkeypatch, seqs, max_iterations=1)
    report = loop.run(base=None)
    out = rl.render_summary(report)
    assert "fix_brief" in out
    assert "F401" in out
    assert "a.py:2" in out


# ── preflight entrypoint + scope/staged plumbing (workflow integrations) ─────


def test_preflight_only_gates_disables_others(monkeypatch):
    captured = {}

    class _FakeLoop:
        def __init__(self, *, config, repo_root, autofix, staged):
            captured["config"] = config
            captured["staged"] = staged

        def run(self, base=None):
            captured["base"] = base
            return rl.LoopReport(
                started_at="", finished_at="", green=True,
                iterations=[], changed_files=[], fix_brief=[], reason="ok",
            )

    monkeypatch.setattr(rl, "ReviewLoop", _FakeLoop)
    rl.preflight(base="origin/main", only_gates=["ruff"], staged=True, coherence_scope="changed")

    gates = captured["config"]["gates"]
    assert gates["ruff"]["enabled"] is True
    assert gates["coherence"]["enabled"] is False
    assert gates["sipa"]["enabled"] is False
    assert gates["coherence"]["scope"] == "changed"
    assert captured["staged"] is True
    assert captured["base"] == "origin/main"


def test_gate_coherence_scope_changed_no_files_skips(tmp_path):
    # scope=changed with no changed files is a clean no-op (the reflex case).
    (tmp_path / "tools" / "workflow").mkdir(parents=True)
    (tmp_path / "tools" / "workflow" / "coherence_checker.py").write_text("# stub", encoding="utf-8")
    res = rl.gate_coherence([], {"blocking": True, "scope": "changed"},
                            autofix=False, repo_root=tmp_path)
    assert res.passed is True
    assert res.skipped is True


def test_gate_coherence_scope_changed_uses_changed_files_flag(monkeypatch, tmp_path):
    import subprocess as _sp
    seen = {}

    class _Proc:
        returncode = 0
        stdout = '{"overall_pass": true, "checks": []}'

    def _fake_run(cmd, *a, **k):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(rl.pathlib.Path, "exists", lambda self: True)
    monkeypatch.setattr(_sp, "run", _fake_run)
    res = rl.gate_coherence(["a.py", "b.py"], {"blocking": True, "scope": "changed"},
                            autofix=False, repo_root=tmp_path)
    assert res.passed is True
    assert "--changed-files" in seen["cmd"]
    assert "a.py,b.py" in seen["cmd"]
    assert "--all" not in seen["cmd"]


def test_changed_py_files_staged_uses_cached(monkeypatch, tmp_path):
    calls = []

    def _fake_git(args, root):
        calls.append(args)
        if "--cached" in args:
            return "tools/x.py\nREADME.md\n"
        return ""

    monkeypatch.setattr(rl, "_git", _fake_git)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text("x = 1\n", encoding="utf-8")
    out = rl.changed_py_files(None, tmp_path, staged=True)
    assert out == ["tools/x.py"]  # only the existing .py, from --cached
    assert any("--cached" in c for c in calls)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
