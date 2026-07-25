# CUI // SP-CTI
"""Tests for the opt-in solution-generator divergence step (dvg-wire-02).

Verifies the alternatives-before-blueprint step is OFF by default, degrades
cleanly (never raises) when the chain is disabled / pool empty / critic fails,
and — when it runs — picks the highest-scoring non-trap approach, retains the
rejected alternatives, and visibly marks trap-flagged approaches. No live LLM/DB:
the orchestrator and critic are injected/monkeypatched.
"""
from __future__ import annotations

import importlib

sg = importlib.import_module("tools.innovation.solution_generator")
# Shim-aware: patch on the concrete critic module object, not a tools.* string.
critic_mod = importlib.import_module("tools.quality.divergence_critic")


class _FakeChainResult:
    def __init__(self, content="", trace_id="trace-xyz", stop_reason="completed"):
        self.content = content
        self.trace_id = trace_id
        self.stop_reason = stop_reason


class _FakeOrchestrator:
    def __init__(self, *, content="", raise_runtime=False, trace_id="trace-xyz"):
        self._content, self._raise, self._trace = content, raise_runtime, trace_id

    def invoke_divergence(self, function, request):
        if self._raise:
            raise RuntimeError("Divergence is disabled in config")
        return _FakeChainResult(content=self._content, trace_id=self._trace)


class _FakeScore:
    def __init__(self):
        self.ordered = [object()]
        self.stop_reason = "completed"


def _fake_deepened(clusters):
    return {"trace_id": "trace-xyz", "function": "f", "k": 3, "cluster_count": len(clusters), "clusters": clusters}


SIGNAL = {"id": "sig-1", "title": "Faster CVE triage", "description": "Analysts overwhelmed", "category": "security"}
DIV_CFG = {"enabled": True, "function": "innovation_ideation"}


# ------------------------------------------------------------------- degrade
def test_disabled_chain_degrades_cleanly():
    out = sg._run_divergence_step(SIGNAL, DIV_CFG, orchestrator=_FakeOrchestrator(raise_runtime=True))
    assert out["ran"] is False and out["reason"].startswith("divergence_unavailable")
    assert out["chosen"] is None and out["rejected"] == []


def test_empty_pool_degrades_cleanly():
    out = sg._run_divergence_step(SIGNAL, DIV_CFG, orchestrator=_FakeOrchestrator(content=""))
    assert out["ran"] is False and out["reason"].startswith("empty_pool")


def test_critic_failure_degrades_cleanly(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("critic exploded")

    monkeypatch.setattr(critic_mod, "score_idea_pool", _boom)
    out = sg._run_divergence_step(SIGNAL, DIV_CFG, orchestrator=_FakeOrchestrator(content="## Frame: x\n1. idea"))
    assert out["ran"] is False and out["reason"].startswith("critic_failed")


# ------------------------------------------------------------------- pick
def test_picks_highest_non_trap_approach(monkeypatch):
    clusters = [
        {"label": "seductive shortcut", "best_composite": 0.90, "has_trap": True, "members": [{"idea": "magic"}]},
        {"label": "solid pipeline", "best_composite": 0.80, "has_trap": False, "members": [{"idea": "queue"}],
         "sketch": "Build a triage queue", "risks": ["ops load"]},
        {"label": "manual tags", "best_composite": 0.50, "has_trap": False, "members": [{"idea": "tags"}]},
    ]
    monkeypatch.setattr(critic_mod, "score_idea_pool", lambda *a, **k: _FakeScore())
    monkeypatch.setattr(critic_mod, "cluster_and_deepen", lambda *a, **k: _fake_deepened(clusters))
    out = sg._run_divergence_step(SIGNAL, DIV_CFG, orchestrator=_FakeOrchestrator(content="pool", trace_id="t7"))
    assert out["ran"] is True
    # Highest composite is a trap -> chosen must be the highest NON-trap cluster.
    assert out["chosen"]["label"] == "solid pipeline"
    assert len(out["rejected"]) == 2
    md = out["section_markdown"]
    assert "CHOSEN: solid pipeline" in md
    assert "[TRAP-FLAGGED]" in md            # the seductive trap approach is marked
    assert "t7" in md                        # trace recoverable from the spec


def test_all_traps_surfaces_for_human(monkeypatch):
    clusters = [
        {"label": "trap a", "best_composite": 0.7, "has_trap": True, "members": [{"idea": "a"}]},
        {"label": "trap b", "best_composite": 0.6, "has_trap": True, "members": [{"idea": "b"}]},
    ]
    monkeypatch.setattr(critic_mod, "score_idea_pool", lambda *a, **k: _FakeScore())
    monkeypatch.setattr(critic_mod, "cluster_and_deepen", lambda *a, **k: _fake_deepened(clusters))
    out = sg._run_divergence_step(SIGNAL, DIV_CFG, orchestrator=_FakeOrchestrator(content="pool"))
    assert out["ran"] is True and out["chosen"] is None
    assert len(out["rejected"]) == 2
    assert "surfaced for human choice" in out["section_markdown"]


# ------------------------------------------------------------------- pure
def test_build_signal_problem_includes_fields():
    prob = sg._build_signal_problem(SIGNAL)
    assert "Faster CVE triage" in prob and "security" in prob and "APPROACHES" in prob


def test_render_section_no_traps_no_flag():
    chosen = {"label": "clean", "best_composite": 0.5, "has_trap": False, "members": []}
    md = sg._render_divergence_section("t1", chosen, [])
    assert "[TRAP-FLAGGED]" not in md and "CHOSEN: clean" in md
