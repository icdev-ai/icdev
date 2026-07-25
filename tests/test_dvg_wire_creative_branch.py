# CUI // SP-CTI
"""Tests for the opt-in creative-engine divergence branch (dvg-wire-01).

Verifies the branch is OFF by default, degrades cleanly (never raises) when the
chain is disabled / the pool is empty / the critic fails, and — when it runs —
produces surviving clusters plus a spec section that carries a recoverable
trace_id and visibly marks trap-flagged directions. No live LLM or DB: the
orchestrator and critic are injected/monkeypatched.
"""
from __future__ import annotations

import importlib

branch = importlib.import_module("tools.creative.divergence_branch")
# Patch on the concrete module object (shim-aware): tools.* redirects to
# icdev.tools.*, so string-path monkeypatch can resolve the wrong object.
critic_mod = importlib.import_module("tools.quality.divergence_critic")


class _FakeChainResult:
    def __init__(self, content="", trace_id="trace-xyz", stop_reason="completed"):
        self.content = content
        self.trace_id = trace_id
        self.stop_reason = stop_reason


class _FakeOrchestrator:
    """Stand-in for ChainOrchestrator: returns a canned pool or raises."""

    def __init__(self, *, content="", raise_runtime=False, trace_id="trace-xyz"):
        self._content = content
        self._raise = raise_runtime
        self._trace = trace_id

    def invoke_divergence(self, function, request):
        if self._raise:
            raise RuntimeError("Divergence is disabled in config")
        return _FakeChainResult(content=self._content, trace_id=self._trace)


class _FakeScore:
    def __init__(self):
        self.ordered = [object()]  # non-empty => scoring produced results
        self.stop_reason = "completed"


def _fake_deepened(clusters):
    return {"trace_id": "trace-xyz", "function": "f", "k": 3, "cluster_count": len(clusters), "clusters": clusters}


PAIN = {"id": "pp-1", "title": "Slow onboarding", "description": "New users churn", "category": "ux"}


# --------------------------------------------------------------------------- gates
def test_is_enabled_default_false():
    assert branch.is_enabled({}) is False
    assert branch.is_enabled({"divergence": {}}) is False
    assert branch.is_enabled({"divergence": {"enabled": False}}) is False
    assert branch.is_enabled({"divergence": {"enabled": True}}) is True


def test_branch_function_config_driven():
    assert branch.branch_function({}) == branch.DEFAULT_FUNCTION
    assert branch.branch_function({"divergence": {"function": "custom_fn"}}) == "custom_fn"


# --------------------------------------------------------------------------- degrade
def test_no_pain_point_returns_not_ran():
    res = branch.run_divergence_branch(None)
    assert res["ran"] is False and res["reason"] == "no_pain_point"


def test_disabled_chain_degrades_cleanly():
    res = branch.run_divergence_branch(PAIN, orchestrator=_FakeOrchestrator(raise_runtime=True))
    assert res["ran"] is False
    assert res["reason"].startswith("divergence_unavailable")
    assert res["clusters"] == []


def test_empty_pool_degrades_cleanly():
    res = branch.run_divergence_branch(PAIN, orchestrator=_FakeOrchestrator(content=""))
    assert res["ran"] is False
    assert res["reason"].startswith("empty_pool")


def test_critic_failure_degrades_cleanly(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("critic exploded")

    monkeypatch.setattr(critic_mod, "score_idea_pool", _boom)
    res = branch.run_divergence_branch(
        PAIN, orchestrator=_FakeOrchestrator(content="## Frame: x\n1. idea")
    )
    assert res["ran"] is False
    assert res["reason"].startswith("critic_failed")


# --------------------------------------------------------------------------- success
def test_success_carries_surviving_clusters(monkeypatch):
    clusters = [
        {"label": "self-serve setup", "best_composite": 0.82, "has_trap": False,
         "members": [{"idea": "guided wizard", "is_trap": False}], "sketch": "Build a wizard",
         "risks": ["scope creep"], "next_steps": ["prototype"]},
        {"label": "auto-magic import", "best_composite": 0.60, "has_trap": True,
         "members": [{"idea": "import everything automatically", "is_trap": True}]},
    ]
    monkeypatch.setattr(critic_mod, "score_idea_pool", lambda *a, **k: _FakeScore())
    monkeypatch.setattr(
        critic_mod, "cluster_and_deepen", lambda *a, **k: _fake_deepened(clusters)
    )
    res = branch.run_divergence_branch(
        PAIN, orchestrator=_FakeOrchestrator(content="## Frame: x\n1. idea", trace_id="trace-abc")
    )
    assert res["ran"] is True
    assert res["trace_id"] == "trace-abc"
    assert res["pain_point_id"] == "pp-1"
    assert len(res["clusters"]) == 2
    assert res["trap_count"] == 1
    md = res["section_markdown"]
    assert "Candidate Solution Directions (Divergence)" in md
    assert "trace-abc" in md               # provenance recoverable from the spec
    assert "[TRAP-FLAGGED]" in md          # trap-flagged direction visibly marked
    assert "self-serve setup" in md


# --------------------------------------------------------------------------- pure render
def test_render_section_no_traps_has_no_flag():
    md = branch._render_section("t1", [{"label": "clean", "best_composite": 0.5, "has_trap": False, "members": []}])
    assert "[TRAP-FLAGGED]" not in md
    assert "clean" in md


def test_build_problem_includes_pain_fields():
    prob = branch._build_problem(PAIN)
    assert "Slow onboarding" in prob and "ux" in prob and "SOLUTION DIRECTIONS" in prob


# --------------------------------------------------------------------------- spec wiring
def test_generate_spec_default_signature_unchanged():
    """generate_spec/generate_all_eligible keep working with no divergence arg."""
    sg = importlib.import_module("tools.creative.spec_generator")
    import inspect

    for fn in (sg.generate_spec, sg.generate_all_eligible):
        params = inspect.signature(fn).parameters
        assert "divergence" in params
        assert params["divergence"].default is None  # opt-in: default template path
