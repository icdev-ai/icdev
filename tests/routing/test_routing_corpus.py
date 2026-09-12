# CUI // SP-CTI
"""Routing regression corpus -- one parametrized test per router (xrv-route-02).

The cases are DATA (``tests/routing/corpus.yaml``) and the way a router is
invoked is declared ONCE, in ``tools/routing/corpus_survey.py::ROUTERS``, which
this module IMPORTS rather than re-spells. Two spellings of "how do you ask the
cortex router about a query" is exactly how a suite and its survey come to
disagree about a router neither of them changed.

A case carrying ``known_disagreement`` is a DECLARED standing defect: it is
marked ``xfail(strict=True)``, so it stays red-but-expected while the defect
stands AND turns the suite red the moment it starts passing. A defect silently
fixed leaves a stale declaration behind, and a stale declaration is how the next
reader comes to believe the router is worse than it is.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from tools.routing.corpus_survey import (
    CATEGORY_SEP,
    FALLBACK_ALL,
    ROUTERS,
    ask_router,
    judge,
    load_corpus,
    replay,
    validate_corpus,
)

#: Minimum the card requires, and a floor rather than a target. It may only go
#: UP: a corpus that can be shrunk to get a commit through is not a gate.
MIN_CORPUS_CASES = 150

#: No router may be left ``never_asked``. Four routers and ~14 assertions
#: between them was the state this corpus was written to end.
MIN_CASES_PER_ROUTER = 10

CORPUS: List[Dict[str, Any]] = load_corpus()


def _params(router: str) -> List[Any]:
    """pytest params for one router, xfail-marking the declared defects."""
    out: List[Any] = []
    for case in CORPUS:
        if case.get("router") != router:
            continue
        known = str(case.get("known_disagreement") or "").strip()
        marks = [pytest.mark.xfail(strict=True, reason=known)] if known else []
        out.append(pytest.param(case, id=str(case.get("id")), marks=marks))
    return out


def _assert_routes_as_declared(case: Dict[str, Any]) -> None:
    spec = ROUTERS[case["router"]]
    answer = ask_router(spec, case)
    verdict = judge(case, answer)
    assert verdict["verdict"] != "unmeasurable", (
        f"{case['id']}: {spec.name} could not be asked at all -- "
        f"{verdict.get('reason')}. An unaskable router is not a passing one."
    )
    assert verdict["actual"] == case["expected"], (
        f"{case['id']}: {spec.name} routed {case['query']!r} to "
        f"{verdict['actual']!r}, corpus declares {case['expected']!r}.\n"
        f"  note: {case.get('note')}\n"
        f"  If the ROUTER is now right, update the case and say why in `note`. "
        f"If it is not, this is the regression the corpus exists to catch."
    )
    expected_mode = str(case.get("expected_agent_mode") or "").strip()
    if expected_mode:
        assert verdict.get("actual_agent_mode") == expected_mode, (
            f"{case['id']}: intent matched but agent_mode is "
            f"{verdict.get('actual_agent_mode')!r}, declared {expected_mode!r}. "
            f"cortex.agent's own 'auto' can never select graph, so this hint is "
            f"the only thing that routes a described DAG to a graph run."
        )


# ---------------------------------------------------------------------------
# One parametrized test per router
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _params("chat_canvas"))
def test_chat_canvas_routes_as_declared(case):
    """WHICH CANVAS -- intake or one of the nine design canvases."""
    _assert_routes_as_declared(case)


@pytest.mark.parametrize("case", _params("cortex_facade"))
def test_cortex_facade_routes_as_declared(case):
    """WHICH CORTEX FACADE -- search | ask | complete | agent."""
    _assert_routes_as_declared(case)


@pytest.mark.parametrize("case", _params("rag_shape"))
def test_rag_shape_routes_as_declared(case):
    """WHAT SHAPE of answer -- fact_single | summary | reasoning | unanswerable."""
    _assert_routes_as_declared(case)


@pytest.mark.parametrize("case", _params("skill_category"))
def test_skill_category_routes_as_declared(case):
    """WHICH SKILL CATEGORIES get injected -- the whole matched set."""
    _assert_routes_as_declared(case)


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------


class TestCorpusDeclaration:
    def test_declaration_is_valid(self):
        problems = validate_corpus(CORPUS)
        assert problems == [], "corpus declaration problems: " + "; ".join(problems)

    def test_corpus_meets_the_floor(self):
        assert len(CORPUS) >= MIN_CORPUS_CASES, (
            f"{len(CORPUS)} cases, floor is {MIN_CORPUS_CASES}")

    def test_every_router_is_asked(self):
        """A router with no case is `never_asked`, which is not a clean bill."""
        for name in ROUTERS:
            count = sum(1 for c in CORPUS if c.get("router") == name)
            assert count >= MIN_CASES_PER_ROUTER, (
                f"{name} has {count} case(s); a router nobody asks cannot "
                f"regress visibly")

    def test_every_router_has_a_default_case(self):
        """The DEFAULT verdict is the one a corpus of happy paths never covers.

        Every one of the four answers *something* for a message no rule matches,
        and that answer is a real label -- so a caller cannot tell "matched" from
        "defaulted" without reading the confidence. At least one case per router
        pins what the default IS.
        """
        for name, spec in ROUTERS.items():
            defaults = [c for c in CORPUS
                        if c.get("router") == name
                        and c.get("expected") == spec.default
                        and "default" in str(c.get("note", "")).lower()]
            assert defaults, f"{name} has no case pinning its default " \
                             f"({spec.default!r})"

    def test_known_disagreements_state_a_mechanism(self):
        """A `known_disagreement` names HOW the router is wrong, not that it is.

        "routes badly" is a complaint; "_REASONING_PATTERNS has no `s?`, so the
        plural misses" is a repair. The field is not a mute button, so a short
        one is refused here rather than discovered to be useless later.
        """
        for case in CORPUS:
            known = str(case.get("known_disagreement") or "").strip()
            if not known:
                continue
            assert len(known) >= 80, (
                f"{case['id']}: known_disagreement must name the mechanism "
                f"(got {len(known)} chars)")

    def test_every_case_has_a_readable_note(self):
        for case in CORPUS:
            assert str(case.get("note") or "").strip(), \
                f"{case['id']}: a case whose intent nobody can read cannot be " \
                f"re-judged when a rule changes"


class TestSurveyReport:
    def test_rate_is_none_over_an_empty_corpus(self):
        """Never 0.0 and never 100.0 when nothing was measured."""
        report = replay([])
        assert report["totals"]["agreement_pct"] is None
        for name, sec in report["routers"].items():
            assert sec["agreement_pct"] is None, name
            assert sec["state"] == "never_asked", name

    def test_live_report_measures_every_router(self):
        report = replay(CORPUS)
        for name, sec in report["routers"].items():
            assert sec["state"] != "never_asked", name
            assert sec["measured"] > 0, name
            assert sec["agreement_pct"] is not None, name

    def test_a_router_that_cannot_be_asked_is_unmeasurable_not_wrong(self):
        """`unmeasurable` leaves BOTH sides of the ratio, never just one."""
        answer = {"error": "ImportError: no such module"}
        verdict = judge({"expected": "ask"}, answer)
        assert verdict["verdict"] == "unmeasurable"
        assert verdict["actual"] is None

    def test_known_disagreements_do_not_read_as_clean(self):
        """A router carrying declared defects is `known_only`, not `clean`."""
        report = replay(CORPUS)
        for name, sec in report["routers"].items():
            if sec["known_disagreements"]:
                assert sec["state"] == "known_only", name

    def test_a_resolved_known_case_is_reported(self):
        """A known-wrong case that starts agreeing is news, not a silent pass."""
        verdict = judge(
            {"expected": "ask", "known_disagreement": "returns agent because x"},
            {"actual": "ask"})
        assert verdict["verdict"] == "resolved"


# ---------------------------------------------------------------------------
# The two routers that can reach a provider
# ---------------------------------------------------------------------------
# The corpus pins each router's DETERMINISTIC lane, because that is the only
# part of a provider-reaching classifier a fixture can pin -- the LLM answer
# moves with whether a provider is up. These tests pin the other half: the
# DISPATCH. With no provider answering, the public entry point must return what
# the deterministic lane returns; and the new keyword must not have moved the
# DEFAULT, which is still to consult the provider.


class TestChatClassifierDispatch:
    def test_public_entry_agrees_with_the_deterministic_lane(self, monkeypatch):
        from tools.chat_router import intent_classifier as ic

        monkeypatch.setattr(
            ic, "_llm_classify",
            lambda text: ic._intake_default("LLM unavailable, defaulting to intake"))
        for case in CORPUS:
            if case.get("router") != "chat_canvas":
                continue
            query = case["query"]
            assert ic.classify(query)["mode"] == \
                ic.classify(query, allow_llm_fallback=False)["mode"], query

    def test_the_default_still_consults_the_provider(self, monkeypatch):
        """allow_llm_fallback defaults True -- no existing caller moved."""
        from tools.chat_router import intent_classifier as ic

        calls: List[str] = []

        def _spy(text: str):
            calls.append(text)
            return ic._intake_default("spy")

        monkeypatch.setattr(ic, "_llm_classify", _spy)
        ic.classify("can you reschedule the meeting to Thursday")
        assert calls, "the low-confidence fallback must still run by default"

    def test_the_deterministic_lane_never_consults_the_provider(self, monkeypatch):
        from tools.chat_router import intent_classifier as ic

        def _boom(text: str):
            raise AssertionError("allow_llm_fallback=False reached the provider")

        monkeypatch.setattr(ic, "_llm_classify", _boom)
        assert ic.classify("can you reschedule the meeting to Thursday",
                           allow_llm_fallback=False)["mode"] == "intake"


class TestQueryClassifierDispatch:
    def test_public_entry_agrees_with_the_deterministic_lane(self, monkeypatch):
        from tools.rag import query_classifier as qc

        monkeypatch.setattr(qc, "_llm_classify", lambda q, c="": None)
        for case in CORPUS:
            if case.get("router") != "rag_shape":
                continue
            query, context = case["query"], case.get("context") or ""
            assert qc.classify_query(query, context)["label"] == \
                qc.classify_query(query, context, allow_llm=False)["label"], query

    def test_the_default_still_tries_the_llm_first(self, monkeypatch):
        """This classifier is LLM-FIRST, and allow_llm defaults True."""
        from tools.rag import query_classifier as qc

        calls: List[str] = []

        def _spy(query: str, context: str = ""):
            calls.append(query)
            return None

        monkeypatch.setattr(qc, "_llm_classify", _spy)
        qc.classify_query("What is AC-2?")
        assert calls, "the LLM lane must still be tried by default"

    def test_the_deterministic_lane_never_consults_the_provider(self, monkeypatch):
        from tools.rag import query_classifier as qc

        def _boom(query: str, context: str = ""):
            raise AssertionError("allow_llm=False reached the provider")

        monkeypatch.setattr(qc, "_llm_classify", _boom)
        assert qc.classify_query("What is AC-2?", allow_llm=False)["label"] == \
            "fact_single"


class TestCortexRouterDispatch:
    def test_base_signal_is_asked_deterministically(self, monkeypatch):
        """route(allow_llm_fallback=False) must not reach the provider either.

        The cortex router makes no provider call of its own; the one reachable
        step is the base signal it consumes.
        """
        from tools.chat_router import intent_classifier as ic
        from tools.cortex.intent_router import route

        def _boom(text: str):
            raise AssertionError("route() reached the provider")

        monkeypatch.setattr(ic, "_llm_classify", _boom)
        assert route("the thing is broken",
                     allow_llm_fallback=False)["intent"] == "ask"


# ---------------------------------------------------------------------------
# Invariants the corpus's `expected` column cannot express
# ---------------------------------------------------------------------------


class TestCortexInvariants:
    def test_every_agent_case_requires_confirm(self):
        """An agent launch is never started from an unconfirmed chat message."""
        from tools.cortex.intent_router import route

        seen = 0
        for case in CORPUS:
            if case.get("router") != "cortex_facade" or \
                    case.get("expected") != "agent":
                continue
            if case.get("known_disagreement"):
                continue
            decision = route(case["query"], allow_llm_fallback=False)
            assert decision["requires_confirm"] is True, case["id"]
            seen += 1
        assert seen >= 5, "too few agent cases to make the claim"

    def test_no_non_agent_case_requires_confirm(self):
        from tools.cortex.intent_router import route

        for case in CORPUS:
            if case.get("router") != "cortex_facade" or \
                    case.get("expected") == "agent":
                continue
            if case.get("known_disagreement"):
                continue
            decision = route(case["query"], allow_llm_fallback=False)
            assert decision["requires_confirm"] is False, case["id"]

    def test_graph_mode_needs_two_families(self):
        """_GRAPH_MIN_FAMILIES is 2, and the corpus holds both sides of it."""
        from tools.cortex.intent_router import graph_signal

        graph_cases = [c for c in CORPUS
                       if c.get("expected_agent_mode") == "graph"]
        assert graph_cases, "no graph-shaped case declared"
        for case in graph_cases:
            signal = graph_signal(case["query"])
            assert len(signal["families"]) >= 2, case["id"]
            assert signal["is_graph"] is True, case["id"]
        # And the negatives: a single family is not a DAG.
        single = [c for c in CORPUS
                  if c.get("router") == "cortex_facade"
                  and c.get("expected_agent_mode") == "auto"]
        assert single, "no non-graph agent case declared"


class TestSkillSelectorInvariants:
    def test_fallback_all_injects_every_category(self):
        """The default is the WIDEST answer, which is why it is pinned apart."""
        from tools.agent.skill_selector import load_config, select_skills

        categories = set((load_config() or {}).get("categories") or {})
        assert categories, "no categories configured"
        fallbacks = [c for c in CORPUS
                     if c.get("router") == "skill_category"
                     and c.get("expected") == FALLBACK_ALL
                     and not c.get("known_disagreement")]
        assert fallbacks, "no fallback_all case declared"
        for case in fallbacks:
            result = select_skills(query=case["query"])
            assert result["status"] == "fallback_all", case["id"]
            matched = {c["name"] for c in result["matched_categories"]}
            assert matched == categories, case["id"]

    def test_a_matched_case_is_narrower_than_the_fallback(self):
        from tools.agent.skill_selector import load_config, select_skills

        categories = set((load_config() or {}).get("categories") or {})
        for case in CORPUS:
            if case.get("router") != "skill_category" or \
                    case.get("expected") == FALLBACK_ALL or \
                    case.get("known_disagreement"):
                continue
            result = select_skills(query=case["query"])
            matched = {c["name"] for c in result["matched_categories"]}
            assert matched == set(case["expected"].split(CATEGORY_SEP)), case["id"]
            assert matched < categories, \
                f"{case['id']}: a matched selection must be NARROWER than the " \
                f"fallback, or the selection bought nothing"
