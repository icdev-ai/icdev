# CUI // SP-CTI
"""aicur-gd-02 — forge_ascent harness-comparison inject.

The inject asks a team to solve one small task with two coding harnesses (or
one harness with and without an instruction file / plan mode) and to submit
diffs, test results and a comparison. These tests pin that it is wired into the
scenario, that its rubric scores all three things the card asked for, and that
the hidden acceptance file the inject hands out really discriminates: it
passes the reference solution and fails the harness mistakes the rubric names.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys

import pytest

from tools.ttx.scenario_loader import load_scenario

INJECT_ID = "inject-06-harness-bakeoff"

REFERENCE = '''
from statistics import median


def normalize_bounded(rows, bounds):
    out = [dict(r) for r in rows]
    for col, (lo, hi) in bounds.items():
        fill = median(r[col] for r in rows if r[col] is not None)
        for r in out:
            v = fill if r[col] is None else r[col]
            r[col] = (min(max(v, lo), hi) - lo) / (hi - lo)
    return out
'''

# The mistakes the rubric's correctness dimension tells the judge to look for.
SCALE_BEFORE_CLIP = REFERENCE.replace(
    "r[col] = (min(max(v, lo), hi) - lo) / (hi - lo)",
    "r[col] = min(max((v - lo) / (hi - lo), lo), hi)",
)

MUTATES_INPUT = REFERENCE.replace("out = [dict(r) for r in rows]", "out = rows")


def _inject():
    sc = load_scenario("forge_ascent")
    return sc, next(i for i in sc["injects"] if i["id"] == INJECT_ID)


def _acceptance_source(body_md: str) -> str:
    blocks = re.findall(r"```python\n(.*?)```", body_md, re.S)
    matches = [b for b in blocks if "test_normalize_acceptance.py" in b]
    assert len(matches) == 1, "inject must hand out exactly one acceptance file"
    return matches[0]


def _run_acceptance(tmp_path, impl: str) -> subprocess.CompletedProcess:
    _, inj = _inject()
    pkg = tmp_path / "sentinel"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "normalize.py").write_text(impl, encoding="utf-8")
    (tmp_path / "test_normalize_acceptance.py").write_text(
        _acceptance_source(inj["body_md"]), encoding="utf-8"
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "test_normalize_acceptance.py"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )


def test_inject_is_wired_into_forge_ascent():
    sc, inj = _inject()
    assert inj["depends_on"] == "inject-02-code-sprint"
    assert 0 < inj["at_minute"] < sc["duration_minutes"]
    sequences = [i["sequence"] for i in sc["injects"]]
    assert len(sequences) == len(set(sequences)), "inject sequence numbers collide"
    # body_path resolved inline by the loader
    assert "body_md" in inj and "body_path" not in inj
    assert inj["scoring"]["receipt_gate"] is True


def test_rubric_scores_correctness_evidence_and_comparison():
    _, inj = _inject()
    rubric = inj["scoring"]["rubric"]
    assert isinstance(rubric, dict), "rubric path did not resolve"
    dims = {d["id"]: d for d in rubric["dimensions"]}
    assert set(dims) == {"correctness", "test_evidence", "comparison_quality"}
    assert sum(d["weight"] for d in dims.values()) == pytest.approx(1.0)
    for d in dims.values():
        assert d["prompt"].strip()


def test_inject_asks_for_diffs_tests_and_a_dated_comparison():
    _, inj = _inject()
    body = inj["body_md"]
    for needle in ("git diff", "pytest", "Comparison table", "instruction file",
                   "plan mode", "as of October 2026"):
        assert needle in body, f"inject body missing {needle!r}"


def test_acceptance_file_passes_the_reference_solution(tmp_path):
    proc = _run_acceptance(tmp_path, REFERENCE)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4 passed" in proc.stdout


@pytest.mark.parametrize("impl", [SCALE_BEFORE_CLIP, MUTATES_INPUT],
                         ids=["scale_before_clip", "mutates_input"])
def test_acceptance_file_catches_the_named_harness_mistakes(tmp_path, impl):
    proc = _run_acceptance(tmp_path, impl)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "failed" in proc.stdout


def test_rubric_is_scoreable_by_ai_scorer(monkeypatch):
    _, inj = _inject()
    rubric = inj["scoring"]["rubric"]
    content = json.dumps({
        "scores": {d["id"]: 8 for d in rubric["dimensions"]},
        "rationale": "ok", "confidence": 0.8,
    })
    router_mod = importlib.import_module("tools.llm.router")

    class _Router:
        def __init__(self, *a, **k):
            pass

        def has_any_llm(self):
            return True

        def invoke(self, function, req):
            return type("Resp", (), {"content": content})()

    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    monkeypatch.setattr(router_mod, "LLMRequest", lambda **k: None)
    from tools.ttx import ai_scorer

    out = ai_scorer.judge_response(inj["body_md"], "team response", rubric)
    assert not out.get("unscored")
    assert 0 < out["total"] <= 100
