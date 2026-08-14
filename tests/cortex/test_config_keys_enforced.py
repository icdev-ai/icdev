# CUI // SP-CTI
"""Behaviour tests for three Cortex config keys that used to be dead (ctx-enf-03).

``search.strategy_weights``, ``analyst.nlq_fallback_enabled`` and
``governance.skip_grounding_for_plain_complete`` were referenced only by
``CORTEX_CONFIG_DEFAULTS``; nothing read them at runtime. The pre-existing
tests asserted the keys LOAD (test_airgap_assertion.py, test_search_router.py),
which is exactly the assertion that let them stay dead — a value can round-trip
through the loader forever without ever reaching a branch.

So every test here writes TWO configs that differ only in the key under test
and asserts the OBSERVABLE OUTCOME differs: a different fused ordering, a
raise-vs-return, a ``skip``-vs-``fail`` gate outcome. A test that merely reads
the loaded value back is not a regression test for this class of defect and is
deliberately absent.
"""
from __future__ import annotations

import itertools

import pytest
import yaml

from tools.cortex import analyst, config, governance, search_service
from tools.cortex.analyst import CortexAnalystError, ask
from tools.cortex.governance import (
    GATE_CITATION_GROUNDING,
    GATE_CONTENT_GROUNDING,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_SKIP,
    OUTCOME_WARN,
    GovernanceBlockedError,
    GovernancePipeline,
)
from tools.cortex.schemas import (
    Citation,
    CortexContext,
    CortexResult,
    CortexSearchResult,
    GovernanceReport,
)

# RRF constant the shipped config uses; the fused contribution of a rank-1 hit
# under weight w is w / (RRF_K + 1).
RRF_K = 60


# ---------------------------------------------------------------------------
# Shared fixture: point $ICDEV_CORTEX_CONFIG at a config we control
# ---------------------------------------------------------------------------
@pytest.fixture()
def write_config(tmp_path, monkeypatch):
    """Write a cortex_config.yaml and make it THE config for this process.

    Each call gets a fresh filename so ``load_cortex_config``'s mtime cache
    (keyed on the path string) can never serve a previous payload — on a
    coarse-grained filesystem clock, rewriting one path within the same test
    is exactly how a "config changed" assertion silently tests nothing.
    """
    counter = itertools.count()

    def _write(payload: dict) -> str:
        path = tmp_path / f"cortex_config_{next(counter)}.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        monkeypatch.setenv(config.CORTEX_CONFIG_ENV_VAR, str(path))
        config._config_cache.pop(str(path), None)
        return str(path)

    return _write


# ===========================================================================
# 1. search.strategy_weights — per-backend weight in the RRF fusion
# ===========================================================================
def _hit(name: str, backend: str, score: float) -> CortexSearchResult:
    return CortexSearchResult(
        content=name,
        score=score,
        backend=backend,
        citation=Citation(source_id=name),
    )


@pytest.fixture()
def two_backends(monkeypatch):
    """A rag hit and a kb hit, each rank 1 within its own backend.

    ``rag`` returns the HIGHER raw score. That matters: RRF ties break by raw
    score, so an implementation that ignores the weights ranks A first under
    every possible weighting. Any test below that sees B first is seeing the
    weight term and nothing else.
    """
    monkeypatch.setattr(
        search_service,
        "BACKEND_ADAPTERS",
        {
            "rag": lambda q, top_k=5, ctx=None: [_hit("A", "rag", 0.9)],
            "kb": lambda q, top_k=5, ctx=None: [_hit("B", "kb", 0.5)],
        },
    )


def _order(results) -> list:
    return [r.content for r in results]


def _rrf_of(results) -> dict:
    return {r.content: r.raw_scores["rrf"] for r in results}


def test_strategy_weights_reorder_the_fused_result_set(write_config, two_backends):
    """The whole point of the key: two weightings, two different top hits."""
    write_config({"search": {"strategy_weights": {"rag": 1.0, "kb": 0.6}}})
    rag_favoured = search_service.search_all("q", backends=["rag", "kb"])

    write_config({"search": {"strategy_weights": {"rag": 0.1, "kb": 1.0}}})
    kb_favoured = search_service.search_all("q", backends=["rag", "kb"])

    assert _order(rag_favoured) == ["A", "B"]
    assert _order(kb_favoured) == ["B", "A"]
    # Same two hits, same ranks, same raw scores — only the config moved.
    assert {r.content for r in rag_favoured} == {r.content for r in kb_favoured}


def test_strategy_weights_change_the_fused_score_not_just_the_order(
    write_config, two_backends
):
    """fused = weight / (rrf_k + rank), the formula the YAML always documented."""
    write_config({"search": {"strategy_weights": {"rag": 1.0, "kb": 0.6}}})
    heavy = _rrf_of(search_service.search_all("q", backends=["rag", "kb"]))

    write_config({"search": {"strategy_weights": {"rag": 0.25, "kb": 0.6}}})
    light = _rrf_of(search_service.search_all("q", backends=["rag", "kb"]))

    assert heavy["A"] == pytest.approx(1.0 / (RRF_K + 1))
    assert light["A"] == pytest.approx(0.25 / (RRF_K + 1))
    assert light["A"] < heavy["A"]
    # kb was untouched between the two configs, so its contribution must not move.
    assert light["B"] == heavy["B"] == pytest.approx(0.6 / (RRF_K + 1))


def test_unlisted_backend_weighs_one_not_zero(write_config, monkeypatch):
    """A backend with no entry in the mapping is neutral, not silenced.

    Guards the upgrade path: adding the weight term must not make a backend
    nobody thought to list disappear from the ranking. Uses a backend name
    absent from CORTEX_CONFIG_DEFAULTS, since the loader deep-merges over the
    defaults and so cannot express "listed nowhere" for a shipped backend.
    """
    monkeypatch.setattr(
        search_service,
        "BACKEND_ADAPTERS",
        {
            "rag": lambda q, top_k=5, ctx=None: [_hit("A", "rag", 0.9)],
            "zeta": lambda q, top_k=5, ctx=None: [_hit("Z", "zeta", 0.5)],
        },
    )

    write_config({"search": {"strategy_weights": {"rag": 1.0}}})
    unlisted = _rrf_of(search_service.search_all("q", backends=["rag", "zeta"]))

    write_config({"search": {"strategy_weights": {"rag": 1.0, "zeta": 0.2}}})
    listed = _rrf_of(search_service.search_all("q", backends=["rag", "zeta"]))

    assert unlisted["Z"] == pytest.approx(1.0 / (RRF_K + 1))
    assert listed["Z"] == pytest.approx(0.2 / (RRF_K + 1))
    assert unlisted["A"] == listed["A"] == pytest.approx(1.0 / (RRF_K + 1))


def test_unusable_strategy_weight_demotes_that_backend_rather_than_inverting(
    write_config, two_backends
):
    """A negative or non-numeric weight clamps to 0.0 — sinks, never flips."""
    write_config({"search": {"strategy_weights": {"rag": 1.0, "kb": 1.0}}})
    sane = _rrf_of(search_service.search_all("q", backends=["rag", "kb"]))

    write_config({"search": {"strategy_weights": {"rag": -5.0, "kb": "banana"}}})
    broken = search_service.search_all("q", backends=["rag", "kb"])

    assert sane["A"] > 0 and sane["B"] > 0
    assert _rrf_of(broken) == {"A": 0.0, "B": 0.0}
    # Both zeroed, so ordering falls back to raw score — deterministic, and the
    # higher-confidence hit still leads. A negative weight never ranks below-zero.
    assert _order(broken) == ["A", "B"]


# ===========================================================================
# 2. analyst.nlq_fallback_enabled — policy control on the IQE -> NL->SQL degrade
# ===========================================================================
@pytest.fixture()
def analyst_paths(monkeypatch):
    """Fail the IQE path on a fallback-eligible gate; record NLQ invocations."""
    calls: list = []

    def failing_iqe(question, mode, ctx, gov, started, **kwargs):
        exc_gov = GovernanceReport()
        exc_gov.gates_run.append(analyst._GATE_RESOLUTION)
        exc_gov.outcomes[analyst._GATE_RESOLUTION] = "fail"
        raise CortexAnalystError(
            "no collection matched", question=question, governance=exc_gov
        )

    def fake_nlq(question, ctx, gov, started, mode, summarize=False):
        calls.append({"question": question, "mode": mode})
        return "NLQ-RESULT"

    monkeypatch.setattr(analyst, "_screen_question", lambda *a, **k: None)
    monkeypatch.setattr(analyst, "_ask_iqe", failing_iqe)
    monkeypatch.setattr(analyst, "_ask_nlq", fake_nlq)
    return calls


def test_nlq_fallback_enabled_gates_whether_the_degrade_happens(
    write_config, analyst_paths
):
    """True: ask() degrades into NL->SQL. False: it refuses and re-raises."""
    write_config({"analyst": {"nlq_fallback_enabled": True}})
    assert ask("how many satellites?", ctx=CortexContext()) == "NLQ-RESULT"
    assert len(analyst_paths) == 1

    write_config({"analyst": {"nlq_fallback_enabled": False}})
    with pytest.raises(CortexAnalystError):
        ask("how many satellites?", ctx=CortexContext())
    # The refusal is the whole point: no LLM ever generated SQL on this call.
    assert len(analyst_paths) == 1


def test_disabled_nlq_fallback_records_an_auditable_policy_gate(
    write_config, analyst_paths
):
    """The refusal must be visible on the report, not an invisible non-event."""
    write_config({"analyst": {"nlq_fallback_enabled": True}})
    ask("how many satellites?", ctx=CortexContext())

    write_config({"analyst": {"nlq_fallback_enabled": False}})
    with pytest.raises(CortexAnalystError) as excinfo:
        ask("how many satellites?", ctx=CortexContext())

    outcomes = excinfo.value.governance.outcomes
    assert outcomes[analyst._GATE_NLQ_FALLBACK] == "skip"
    # The IQE failure that triggered it stays on the report as history.
    assert outcomes[analyst._GATE_RESOLUTION] == "fail"


def test_disabled_fallback_still_honours_an_explicit_nlq_mode(
    write_config, analyst_paths
):
    """Scope pin: the key governs the DEGRADE, not a caller naming mode="nlq".

    Under one config value, two call shapes diverge — auto is refused, explicit
    nlq runs — which is exactly the boundary args/cortex_config.yaml documents.
    """
    write_config({"analyst": {"nlq_fallback_enabled": False}})

    with pytest.raises(CortexAnalystError):
        ask("how many satellites?", mode="auto", ctx=CortexContext())
    assert analyst_paths == []

    assert ask("how many satellites?", mode="nlq", ctx=CortexContext()) == "NLQ-RESULT"
    assert [c["mode"] for c in analyst_paths] == ["nlq"]


# ===========================================================================
# 3. governance.skip_grounding_for_plain_complete — grounding on plain complete()
# ===========================================================================
@pytest.fixture()
def stub_gates(monkeypatch):
    """Neutralize every gate except the two grounding gates under test."""
    monkeypatch.setattr(
        governance,
        "_gate_check_text",
        lambda text: {
            "allowed": True, "warnings": [], "blocked_reason": None,
            "injection_score": 0.0, "pii_labels": [], "request_id": "gw_test",
        },
    )
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(
        governance, "_gate_register_provenance", lambda *a, **k: "scr-test"
    )
    monkeypatch.setattr(governance, "_gate_record_audit", lambda payload: None)


def _plain_complete(text: str):
    """Run `text` through the pipeline as a non-retrieval completion."""
    return GovernancePipeline(operation="cortex.complete").wrap(
        lambda p: text, CortexContext(), prompt="draft it", retrieval=False
    )


def test_skip_flag_decides_whether_a_plain_completion_is_citation_checked(
    write_config, stub_gates
):
    """A plain completion injects no sources, so [source: 1] is fabricated.

    True: the gate records `skip` and never looks. False: it looks, and finds it.
    """
    tagged = "Controls are required [source: 1]."

    write_config({"governance": {"skip_grounding_for_plain_complete": True}})
    _, skipped = _plain_complete(tagged)

    write_config({"governance": {"skip_grounding_for_plain_complete": False}})
    _, enforced = _plain_complete(tagged)

    assert skipped.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_SKIP
    assert enforced.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_FAIL
    # A skipped gate is not part of the chain that ran; an enforced one is.
    assert GATE_CITATION_GROUNDING not in skipped.gates_run
    assert GATE_CITATION_GROUNDING in enforced.gates_run


def test_skip_flag_decides_whether_a_clean_plain_completion_passes_or_skips(
    write_config, stub_gates
):
    """The difference is not only visible on defective output."""
    clean = "Free-form prose with no citation tags and nothing unresolved."

    write_config({"governance": {"skip_grounding_for_plain_complete": True}})
    _, skipped = _plain_complete(clean)

    write_config({"governance": {"skip_grounding_for_plain_complete": False}})
    _, enforced = _plain_complete(clean)

    assert skipped.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_SKIP
    assert enforced.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS
    assert skipped.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_SKIP
    assert enforced.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_PASS


def test_skip_flag_decides_whether_placeholders_are_scanned(write_config, stub_gates):
    """With no context to ground against, the placeholder scan IS the gate."""
    drafty = "Dear [PLACEHOLDER], your award is [TBD]."

    write_config({"governance": {"skip_grounding_for_plain_complete": True}})
    _, skipped = _plain_complete(drafty)

    write_config({"governance": {"skip_grounding_for_plain_complete": False}})
    _, enforced = _plain_complete(drafty)

    assert skipped.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_SKIP
    assert skipped.content_grounding == {}
    assert enforced.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_WARN
    assert enforced.content_grounding["method"] == "placeholder"


def test_skip_flag_changes_what_fail_closed_blocks_on_a_plain_completion(
    write_config, stub_gates
):
    """Composes with governance.fail_closed: skip=true has nothing to block on."""
    tagged = "Controls are required [source: 1]."
    hard = CortexContext(fail_closed=True)

    def _run():
        return GovernancePipeline(operation="cortex.complete").wrap(
            lambda p: tagged, hard, prompt="draft it", retrieval=False
        )

    write_config({"governance": {"skip_grounding_for_plain_complete": True}})
    _, report = _run()
    assert not report.blocked

    write_config({"governance": {"skip_grounding_for_plain_complete": False}})
    with pytest.raises(GovernanceBlockedError) as excinfo:
        _run()
    assert excinfo.value.gate == GATE_CITATION_GROUNDING


def test_enforced_plain_completion_is_checked_but_never_certified(
    write_config, stub_gates
):
    """`grounded` stays False either way — no evidence set, no certification.

    The key buys a CHECK on plain completions, not a grounding claim; a passing
    citation gate on a call that injected nothing must not read as attested.
    """
    clean = "Free-form prose with no citation tags."

    def _run(text):
        # `grounded` is only ever written onto a CortexResult, so the wrapped
        # call has to return one for this property to be observable at all.
        return GovernancePipeline(operation="cortex.complete").wrap(
            lambda p: CortexResult(text=text, provider="test"),
            CortexContext(),
            prompt="draft it",
            retrieval=False,
        )

    write_config({"governance": {"skip_grounding_for_plain_complete": True}})
    result_skipped, skipped = _run(clean)

    write_config({"governance": {"skip_grounding_for_plain_complete": False}})
    result_enforced, enforced = _run(clean)

    assert skipped.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_SKIP
    assert enforced.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS
    assert result_skipped.grounded is False
    assert result_enforced.grounded is False
    # ...and the defective case is not certified either.
    assert _run("Cited [source: 1].")[0].grounded is False


def test_the_flag_only_reaches_non_retrieval_calls(write_config, stub_gates):
    """Scope pin: under skip=false a retrieval call is unaffected.

    Same config, two call shapes: the plain completion's bogus tag fails while a
    retrieval call citing a real injected source still passes.
    """
    write_config({"governance": {"skip_grounding_for_plain_complete": False}})

    _, plain = _plain_complete("Controls are required [source: 1].")
    assert plain.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_FAIL

    grounded_text = "Account management controls are required [source: 1]."
    sources = [{"source_id": "1", "content": grounded_text.replace(" [source: 1]", "")}]
    result, retrieved = GovernancePipeline(operation="cortex.answer").wrap(
        lambda p: CortexResult(text=grounded_text, provider="test"),
        CortexContext(),
        prompt="q",
        context_sources=sources,
    )
    assert retrieved.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS
    assert result.grounded is True
