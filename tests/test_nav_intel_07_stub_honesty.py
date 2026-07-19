# CUI // SP-CTI
"""nav-intel-07 — ontology + autoresearch stub-honesty regression tests.

Two P2 findings from the nav-intel-01 audit (stub computation presented as
real capability) are locked in here per the wave honesty standard:

  1. Ontology "SPARQL" query — ``query_federation`` (exposed via the
     ``ontology_query`` MCP tool) is a heuristic keyword/substring matcher over
     a handful of hard-coded query shapes, NOT a SPARQL evaluator. The docstring,
     tool descriptions, and the payload used to advertise "SPARQL-like" query
     capability. They now say keyword/pattern search and the payload carries
     ``engine="heuristic_keyword_matcher"`` / ``sparql=False``.

  2. Autoresearch stub math — the experiment engine measures pre/post metrics
     against an *identity* baseline (no real code modification is applied), the
     Bayesian selector scores on constant 0.5 placeholder features, and the
     skill fitness evaluator returns a constant 0.5 baseline. None of these
     changed (math is untouched) — instead every payload now carries an explicit
     ``placeholder_metrics``/``heuristic`` flag, and the /autoresearch dashboard
     renders a placeholder badge, so results are never mistaken for real
     experiment evaluation.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =========================================================================
# 1. Ontology — no false SPARQL claim
# =========================================================================


@pytest.mark.parametrize(
    "rel",
    [
        "tools/ontology/federation.py",
        "icdev/tools/ontology/federation.py",
    ],
)
def test_federation_does_not_advertise_sparql_capability(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    # The old copy claimed a "SPARQL-like query" capability. It must not.
    assert "SPARQL-like" not in src, "federation.py still advertises 'SPARQL-like' query capability"
    # And it must positively label itself as a heuristic keyword matcher.
    assert "heuristic_keyword_matcher" in src


def test_query_federation_payload_flags_heuristic_engine():
    """A query payload must self-identify as a keyword matcher, not SPARQL."""
    from tools.ontology.federation import query_federation

    # A query that matches no hard-coded pattern avoids any kg_nodes lookup,
    # so this is DB-light: it returns the labeled payload with empty results.
    result = query_federation(query_text="ping the engine please")
    assert result.get("engine") == "heuristic_keyword_matcher"
    assert result.get("sparql") is False
    assert "not a SPARQL" in result.get("engine_note", "")


@pytest.mark.parametrize(
    "rel",
    [
        "tools/mcp/ontology_server.py",
        "icdev/tools/mcp/ontology_server.py",
    ],
)
def test_ontology_mcp_server_copy_is_honest(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "SPARQL-like" not in src, "ontology_server.py still advertises 'SPARQL-like'"
    assert "not a SPARQL" in src or "not SPARQL" in src


def test_ontology_query_registry_entry_is_honest():
    # ontology_query is registered in the MCP RESOURCE_REGISTRY dict.
    from tools.mcp.tool_registry import RESOURCE_REGISTRY

    entry = RESOURCE_REGISTRY["ontology_query"]
    desc = entry["description"]
    assert "SPARQL-like" not in desc
    assert "NOT a SPARQL evaluator" in desc
    qdesc = entry["input_schema"]["properties"]["query"]["description"]
    assert "not SPARQL" in qdesc


# =========================================================================
# 2. Autoresearch — placeholder/identity metrics carry explicit flags
# =========================================================================


def test_bayesian_selector_score_flags_placeholder_features():
    from tools.autoresearch.bayesian_selector import score_experiment_candidate

    result = score_experiment_candidate(
        {"id": "c1", "hypothesis": "Increase the compliance gate pass rate by X."},
        domain="compliance",
    )
    assert result.get("heuristic") is True
    assert result.get("placeholder_features") is True


def test_bayesian_selector_select_flags_placeholder_features():
    from tools.autoresearch.bayesian_selector import select_next_experiment

    candidates = [
        {"id": "c1", "hypothesis": "Tune retrieval chunk size for RAG relevance."},
        {"id": "c2", "hypothesis": "Add a caching layer to the SAST runner."},
    ]
    result = select_next_experiment(candidates, domain="compliance")
    assert result.get("selected") is not None
    assert result.get("heuristic") is True
    assert result.get("placeholder_features") is True


def test_fitness_evaluator_skill_baseline_flags_placeholder():
    from tools.autoresearch.fitness_evaluator import evaluate_skill

    # No assertions => constant 0.5 placeholder, which must be flagged.
    result = evaluate_skill(skill_name="demo", assertions=None)
    assert result["metric_value"] == 0.5
    assert result.get("placeholder_metrics") is True
    assert result.get("heuristic") is True
    # The note must make the placeholder nature explicit.
    assert "placeholder" in result["details"]["note"].lower()


@pytest.mark.parametrize(
    "func_name",
    ["run_experiment", "evaluate_experiment", "decide", "run_loop", "get_status"],
)
def test_experiment_engine_payloads_carry_placeholder_flags(func_name):
    """The identity-baseline metric payloads must all flag placeholder_metrics.

    These functions require DB state to exercise end-to-end; the honesty
    contract is that whenever they return a metric-bearing payload it carries
    the flag. Assert on the function source (same approach as the slides
    honesty suite for status/template markup).
    """
    import inspect

    from tools.autoresearch import experiment_engine

    src = inspect.getsource(getattr(experiment_engine, func_name))
    assert '"placeholder_metrics": True' in src, f"{func_name} payload missing placeholder_metrics flag"
    assert '"heuristic": True' in src, f"{func_name} payload missing heuristic flag"


def test_experiment_engine_defines_placeholder_note():
    from tools.autoresearch import experiment_engine

    note = experiment_engine._PLACEHOLDER_METRICS_NOTE
    assert "identity baseline" in note
    assert "placeholder" in note.lower()


# =========================================================================
# 3. Autoresearch UI badge (both dashboard API + template twins)
# =========================================================================


@pytest.mark.parametrize(
    "rel",
    [
        "tools/dashboard/templates/autoresearch.html",
        "icdev/tools/dashboard/templates/autoresearch.html",
    ],
)
def test_autoresearch_template_renders_placeholder_badge(rel):
    html = (ROOT / rel).read_text(encoding="utf-8")
    assert "ar-placeholder-badge" in html
    assert "placeholder" in html.lower()
    assert "identity baseline" in html


@pytest.mark.parametrize(
    "rel",
    [
        "tools/dashboard/app.py",
        "icdev/tools/dashboard/app.py",
    ],
)
def test_autoresearch_api_payload_flags_placeholder(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "_AUTORESEARCH_PLACEHOLDER_NOTE" in src
    assert '"placeholder_metrics": True' in src
