"""Tests for tools/quality/content_grounding.py — shared, surface-agnostic
anti-hallucination utilities (extracted from rfi_grounding)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.quality.citation_grounding import CONF_ABSTAIN
from tools.quality.content_grounding import (
    check_numeric_claims,
    find_placeholders,
    ground_content,
    placeholder_findings,
    substitute_facts,
)


class TestSubstituteFacts:
    def test_substitutes_case_insensitively(self):
        out, subs = substitute_facts("[Uei_Number] here", [(r"\[UEI_NUMBER\]", "ABC123")])
        assert out == "ABC123 here"
        assert subs[0]["count"] == 1

    def test_skips_empty_values(self):
        out, subs = substitute_facts("[UEI]", [(r"\[UEI\]", ""), (r"\[UEI\]", None)])
        assert out == "[UEI]"
        assert subs == []

    def test_empty_text(self):
        assert substitute_facts("", [(r"x", "y")]) == ("", [])


class TestPlaceholderFindings:
    def test_reports_by_item_number(self):
        sections = [
            {"item_number": "1.1", "content": "UEI [UEI_NUMBER]"},
            {"item_number": "2.1", "content": "clean"},
            {"item_number": "3.1", "content": "", "ai_draft": "CAGE [CAGE_CODE]"},
        ]
        findings = placeholder_findings(sections)
        assert len(findings) == 2
        assert findings[0] == {"item_number": "1.1", "placeholders": ["[UEI_NUMBER]"]}
        assert findings[1]["item_number"] == "3.1"

    def test_falls_back_to_title_then_id(self):
        findings = placeholder_findings([{"title": "Overview", "content": "[TBD X]"}])
        assert findings[0]["item_number"] == "Overview"

    def test_content_prefers_content_over_draft(self):
        sections = [{"item_number": "1.1", "content": "clean", "ai_draft": "[LEFTOVER]"}]
        assert placeholder_findings(sections) == []

    def test_custom_content_keys(self):
        sections = [{"id": "abc", "draft_content": "[TODO ITEM]"}]
        findings = placeholder_findings(sections, content_keys=("draft_content",))
        assert findings[0]["placeholders"] == ["[TODO ITEM]"]


class TestReExports:
    def test_rfi_grounding_reexports_shared_functions(self):
        from tools.govcon import rfi_grounding
        assert rfi_grounding.find_placeholders is find_placeholders
        assert rfi_grounding.check_numeric_claims is check_numeric_claims


SNIPPETS = [
    "FedRAMP AC-2 requires account management controls for every information system.",
    "Accounts are disabled automatically after ninety days of inactivity.",
]


class TestGroundContent:
    def test_grounded_text_scores_high(self):
        # Output paraphrases the snippets using their own vocabulary.
        out = (
            "Account management controls are required for the system [source: 1]. "
            "Accounts are disabled after ninety days of inactivity [source: 2]."
        )
        r = ground_content(out, SNIPPETS)
        assert r["method"] == "heuristic"
        assert r["score"] >= CONF_ABSTAIN
        assert r["ungrounded_claims"] == []

    def test_fabricated_claims_score_low(self):
        out = (
            "Zebras enjoy bouncing on trampolines during thunderstorms. "
            "The quarterly revenue tripled after the merger with Acme."
        )
        r = ground_content(out, SNIPPETS)
        assert r["method"] == "heuristic"
        assert r["score"] < CONF_ABSTAIN
        # both sentences are unsupported by the context
        assert len(r["ungrounded_claims"]) == 2

    def test_mixed_output_isolates_the_ungrounded_sentence(self):
        out = (
            "Account management controls are required for every system [source: 1]. "
            "Meanwhile the mission to Jupiter launches next Tuesday."
        )
        r = ground_content(out, SNIPPETS)
        assert len(r["ungrounded_claims"]) == 1
        assert "Jupiter" in r["ungrounded_claims"][0]

    def test_empty_context_falls_back_safely(self):
        r = ground_content("Any confident claim at all.", [])
        assert r["method"] == "no_context"
        assert r["score"] == 0.0
        assert r["ungrounded_claims"] == []

    def test_none_context_falls_back_safely(self):
        r = ground_content("Any claim.", None)
        assert r["method"] == "no_context"

    def test_empty_output_is_no_context(self):
        r = ground_content("", SNIPPETS)
        assert r["method"] == "no_context"

    def test_int_context_is_not_treated_as_text(self):
        # RAG passes an int source count in some call sites; it carries no text.
        r = ground_content("claim", 3)
        assert r["method"] == "no_context"

    def test_accepts_dicts_with_content_key(self):
        snippets = [{"content": s} for s in SNIPPETS]
        out = "Account management controls are required [source: 1]."
        r = ground_content(out, snippets)
        assert r["score"] >= CONF_ABSTAIN

    def test_citation_tags_do_not_inflate_score(self):
        # An otherwise-ungrounded sentence must not be rescued by its cite tag.
        out = "Wholly unrelated fabricated assertion [source: 1] [SOURCE-2]."
        r = ground_content(out, SNIPPETS)
        assert r["score"] < CONF_ABSTAIN

    def test_default_floor_is_the_shared_band(self):
        # The grounded sentence clears the default (shared CONF_ABSTAIN) floor
        # but is flagged when the caller demands near-perfect support — proving
        # the default cutoff is the shared band, not a hardcoded local constant.
        out = "Account management controls are required [source: 1]."
        assert ground_content(out, SNIPPETS)["ungrounded_claims"] == []
        assert len(ground_content(out, SNIPPETS, support_floor=0.99)["ungrounded_claims"]) == 1

    def test_llm_method_falls_back_to_heuristic_without_invoke(self):
        # method="llm" but no llm_invoke supplied -> deterministic heuristic.
        r = ground_content("Account management controls required [source: 1].",
                           SNIPPETS, method="llm")
        assert r["method"] == "heuristic"

    def test_llm_method_uses_injected_invoke(self):
        # Deterministic-picker (agx-pick-02): the judge labels each CLAIM with a
        # 3-value enum; Python composes the support ratio. Two claims both
        # grounded -> score 1.0.
        def fake_invoke(prompt):
            assert "CONTEXT" in prompt and "CLAIMS" in prompt
            return '{"labels": ["grounded", "grounded"]}'

        r = ground_content("First claim. Second claim.", SNIPPETS,
                           method="llm", llm_invoke=fake_invoke)
        assert r["method"] == "llm"
        assert r["score"] == 1.0
        assert r["vocabulary_version"]

    def test_llm_method_composes_partial_support(self):
        def fake_invoke(prompt):
            return '{"labels": ["grounded", "ungrounded"]}'

        r = ground_content("First claim. Second claim.", SNIPPETS,
                           method="llm", llm_invoke=fake_invoke)
        assert r["method"] == "llm"
        assert r["score"] == 0.5
        assert len(r["ungrounded_claims"]) == 1

    def test_llm_bad_json_degrades_to_heuristic(self):
        def bad_invoke(prompt):
            return "not json at all"

        r = ground_content("Zebras on trampolines.", SNIPPETS, method="llm",
                           llm_invoke=bad_invoke)
        assert r["method"] == "heuristic"
