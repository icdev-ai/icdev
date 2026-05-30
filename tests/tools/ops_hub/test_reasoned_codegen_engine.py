# CUI // SP-CTI
"""Unit tests for the /ops/llm reasoned-codegen engine functions
(tools/ops_hub/llmops_engine.py)."""

from unittest.mock import patch

from tools.ops_hub import llmops_engine as eng


# ── get_reasoned_codegen_config ────────────────────────────────────────────
def test_config_reads_per_function_from_yaml():
    cfg = eng.get_reasoned_codegen_config()
    assert "error" not in cfg
    assert cfg["section_enabled"] in (True, False)
    fns = {f["function"] for f in cfg["functions"]}
    # The three wired/known functions ship in args/llm_config.yaml.
    assert {"code_translation", "code_generation", "child_app"} <= fns
    for f in cfg["functions"]:
        assert f["mode"] in ("off", "cot", "cod")
        assert isinstance(f["enabled"], bool)
        assert isinstance(f["critique"], bool)


def test_config_translation_default_on_cot():
    cfg = eng.get_reasoned_codegen_config()
    tr = next(f for f in cfg["functions"] if f["function"] == "code_translation")
    assert tr["enabled"] is True
    assert tr["mode"] == "cot"


# ── get_recent_chain_runs ──────────────────────────────────────────────────
def test_recent_runs_returns_list():
    runs = eng.get_recent_chain_runs(limit=5)
    assert isinstance(runs, list)
    # Either empty, real rows, or a single graceful error dict — never raises.
    for r in runs:
        assert isinstance(r, dict)


def test_recent_runs_function_filter_passthrough():
    # With a function filter that has no rows, returns an empty list (no crash).
    runs = eng.get_recent_chain_runs(limit=5, function="__nonexistent_fn__")
    assert isinstance(runs, list)
    assert all(isinstance(r, dict) for r in runs)


# ── run_reasoned_codegen_advisor ───────────────────────────────────────────
def test_advisor_heuristic_complex_recommends():
    out = eng.run_reasoned_codegen_advisor(
        "code_generation",
        "implement oauth2 auth with encrypted tokens and NIST audit across api and schema",
        file_count=5, use_llm=False,
    )
    assert out["recommended"] is True
    assert out["mode"] in ("cot", "cod")
    assert out["source"] == "heuristic"


def test_advisor_heuristic_trivial_off():
    out = eng.run_reasoned_codegen_advisor("code_generation", "fix a typo", use_llm=False)
    assert out["recommended"] is False
    assert out["mode"] == "off"


def test_advisor_error_is_graceful():
    with patch("tools.llm.reasoned_codegen_advisor.recommend", side_effect=RuntimeError("boom")):
        out = eng.run_reasoned_codegen_advisor("code_generation", "x")
    assert out["recommended"] is False
    assert out["mode"] == "off"
    assert "error" in out
