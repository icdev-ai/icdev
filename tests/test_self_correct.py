# CUI // SP-CTI
"""Tests for tools/quality/self_correct.py (trust-self-01).

The monotone invariant is the whole design, so most of these are written to
FAIL if it is relaxed: accepting a level round, accepting a worse round,
returning a candidate that deleted its way to a clean score, or reporting
``blocked=False`` on any path that did not measure it.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from tools.quality.self_correct import (
    DEFAULT_FUNCTION,
    MAX_DOCUMENT_CHARS,
    REASON_ACCEPTED,
    REASON_NOT_FEWER,
    REASON_RETENTION,
    STATUS_CLEAN,
    STATUS_CORRECTED,
    STATUS_ERROR,
    STATUS_IMPROVED,
    STATUS_UNAVAILABLE,
    STATUS_UNCHANGED,
    STATUS_UNMEASURABLE,
    Verdict,
    build_revision_prompt,
    normalize_verdict,
    self_correct,
    target_findings,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

DRAFT = (
    "The program awarded a contract in 2019 [source: 1]. "
    "The vendor delivered forty-seven units [source: 1]. "
    "Delivery completed on schedule [source: 2]."
)
SOURCES = {
    "1": "The program awarded a contract in 2019 and the vendor delivered seven units.",
    "2": "Delivery completed on schedule.",
}


# ── doubles ──────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, content: str):
        self.content = content


class FakeRouter:
    """Returns a scripted revision per call and records the prompts it saw."""

    def __init__(self, *outputs: str):
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.functions: list[str] = []

    def invoke(self, function, request):
        self.functions.append(function)
        self.prompts.append(request.messages[0]["content"])
        if not self.outputs:
            raise AssertionError("router invoked more times than the test scripted")
        return FakeResponse(self.outputs.pop(0))


def revision(label: str) -> str:
    """A candidate long enough to clear the retention floor.

    The floor is a real check, not a formality — a scripted revision has to be
    a plausible rewrite of DRAFT, not a stub.
    """
    return (
        f"{label}: the program awarded a contract in 2019 [source: 1]. "
        f"The vendor delivered seven units [source: 1]. "
        f"Delivery completed on schedule [source: 2]."
    )


class ExplodingRouter:
    def __init__(self):
        self.calls = 0

    def invoke(self, function, request):
        self.calls += 1
        raise RuntimeError("provider unreachable")


def scripted_validator(*counts: int, blocked: bool | None = None):
    """A validator returning ``counts[i]`` findings on its i-th call."""
    seq = list(counts)

    def _validate(text: str):
        n = seq.pop(0) if seq else 0
        findings = [
            {"item_number": i, "issue": "unsupported_claim", "detail": ["anchor"]}
            for i in range(1, n + 1)
        ]
        return {"findings": findings, "blocked": bool(findings) if blocked is None else blocked}

    return _validate


# ── nothing to do ────────────────────────────────────────────────────────────

def test_clean_draft_never_calls_the_router():
    router = FakeRouter()  # any call raises
    report = self_correct(DRAFT, sources=SOURCES, validators=lambda t: [],
                          router=router, max_rounds=2)
    assert report["status"] == STATUS_CLEAN
    assert report["rounds_used"] == 0
    assert report["revised"] is False
    assert report["needs_hitl"] is False
    assert router.prompts == []


def test_clean_but_blocked_still_needs_hitl():
    """Zero findings with a blocking verdict is a guard that could not measure —
    reporting it as resolved would be exactly the hole trust_gate closed."""
    report = self_correct(DRAFT, validators=lambda t: {"findings": [], "blocked": True},
                          router=FakeRouter(), max_rounds=2)
    assert report["status"] == STATUS_CLEAN
    assert report["blocked"] is True
    assert report["needs_hitl"] is True


# ── the monotone invariant ───────────────────────────────────────────────────

def test_round_accepted_only_when_findings_strictly_decrease():
    router = FakeRouter(revision("ROUND ONE"))
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(3, 1), router=router, max_rounds=1)
    assert report["status"] == STATUS_IMPROVED
    assert report["revised"] is True
    assert report["text"].startswith("ROUND ONE")
    assert report["initial_findings"] == 3
    assert report["final_findings"] == 1
    assert report["rounds"][0]["accepted"] is True
    assert report["rounds"][0]["reason"] == REASON_ACCEPTED


def test_round_that_increases_findings_is_discarded():
    router = FakeRouter(revision("WORSE"))
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(2, 5), router=router, max_rounds=1)
    assert report["status"] == STATUS_UNCHANGED
    assert report["revised"] is False
    assert report["text"] == DRAFT
    assert report["final_findings"] == 2
    assert report["rounds"][0]["accepted"] is False
    assert report["rounds"][0]["reason"] == REASON_NOT_FEWER
    assert report["needs_hitl"] is True


def test_level_round_is_discarded_too():
    """STRICTLY decrease. A round that trades one finding for another has
    bought nothing, and accepting it lets the text churn at a fixed score."""
    router = FakeRouter(revision("SIDEWAYS"))
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(2, 2), router=router, max_rounds=1)
    assert report["status"] == STATUS_UNCHANGED
    assert report["text"] == DRAFT
    assert report["rounds"][0]["reason"] == REASON_NOT_FEWER


def test_best_prior_round_is_kept_when_a_later_round_regresses():
    router = FakeRouter(revision("ROUND ONE"), revision("ROUND TWO"))
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(3, 1, 4), router=router, max_rounds=2)
    assert report["text"].startswith("ROUND ONE")
    assert report["final_findings"] == 1
    assert report["status"] == STATUS_IMPROVED
    assert [r["accepted"] for r in report["rounds"]] == [True, False]


def test_budget_exhausted_with_findings_remaining_needs_hitl():
    router = FakeRouter(revision("FIRST"), revision("SECOND"))
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(5, 3, 2), router=router, max_rounds=2)
    assert report["rounds_used"] == 2
    assert report["final_findings"] == 2
    assert report["status"] == STATUS_IMPROVED
    assert report["needs_hitl"] is True


def test_reaching_zero_findings_stops_early_and_reports_corrected():
    router = FakeRouter(revision("CORRECTED"))
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(2, 0), router=router, max_rounds=3)
    assert report["status"] == STATUS_CORRECTED
    assert report["final_findings"] == 0
    assert report["rounds_used"] == 1
    assert report["needs_hitl"] is False


def test_deleting_the_document_is_not_a_fix():
    """A candidate that deletes its way to a clean score is rejected on the
    retention floor BEFORE it is validated — the empty string is the trivial
    optimum of the finding count, so the count alone cannot catch this."""
    calls = {"n": 0}

    def validator(text):
        calls["n"] += 1
        # The truncated candidate would score clean if it were ever validated.
        return {"findings": [] if len(text) < 40 else [{"item_number": 1, "issue": "x"}],
                "blocked": len(text) >= 40}

    router = FakeRouter("Too short.")
    report = self_correct(DRAFT, sources=SOURCES, validators=validator,
                          router=router, max_rounds=1)
    assert report["rounds"][0]["reason"] == REASON_RETENTION
    assert report["rounds"][0]["findings_after"] is None  # never validated
    assert report["text"] == DRAFT
    assert report["blocked"] is True
    assert calls["n"] == 1  # baseline only


# ── fail closed ──────────────────────────────────────────────────────────────

def test_validator_exception_yields_original_text_and_blocked():
    def boom(text):
        raise RuntimeError("gate exploded")

    router = FakeRouter()
    report = self_correct(DRAFT, sources=SOURCES, validators=boom, router=router, max_rounds=2)
    assert report["status"] == STATUS_ERROR
    assert report["text"] == DRAFT
    assert report["blocked"] is True
    assert report["revised"] is False
    assert report["needs_hitl"] is True
    assert router.prompts == []  # never even attempted a revision


def test_router_exception_preserves_the_measured_verdict():
    router = ExplodingRouter()
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(2), router=router, max_rounds=2)
    assert router.calls == 1  # the loop stops, it does not retry into the budget
    assert report["text"] == DRAFT
    assert report["revised"] is False
    assert report["blocked"] is True
    assert report["needs_hitl"] is True
    assert "RuntimeError" in report["error"]


def test_self_correction_cannot_unblock_a_blocked_draft():
    """A rejected round's verdict must not leak into the result — even when
    that round measured `blocked=False`."""
    seq = [
        {"findings": [{"item_number": 1, "issue": "a"}], "blocked": True},
        # The candidate looks unblocked, but its finding count did not drop.
        {"findings": [{"item_number": 1, "issue": "b"}], "blocked": False},
    ]

    def validator(text):
        return seq.pop(0) if seq else {"findings": [], "blocked": False}

    router = FakeRouter(revision("CLAIMS CLEAN"))
    report = self_correct(DRAFT, sources=SOURCES, validators=validator,
                          router=router, max_rounds=1)
    assert report["blocked"] is True
    assert report["text"] == DRAFT


def test_no_validators_is_unmeasurable_not_clean():
    report = self_correct(DRAFT, sources=SOURCES, validators=None,
                          router=FakeRouter(), max_rounds=2)
    assert report["status"] == STATUS_UNMEASURABLE
    assert report["blocked"] is True
    assert report["needs_hitl"] is True


def test_no_router_returns_the_measured_verdict_unrevised():
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(2), router=None, max_rounds=2)
    assert report["status"] == STATUS_UNAVAILABLE
    assert report["text"] == DRAFT
    assert report["final_findings"] == 2
    assert report["needs_hitl"] is True


def test_oversized_document_is_refused_rather_than_truncated():
    long_draft = "This claim is unsupported [source: 1]. " * (MAX_DOCUMENT_CHARS // 30)
    assert len(long_draft) > MAX_DOCUMENT_CHARS
    router = FakeRouter()
    report = self_correct(long_draft, sources=SOURCES,
                          validators=scripted_validator(2), router=router, max_rounds=1)
    assert report["status"] == STATUS_UNAVAILABLE
    assert report["text"] == long_draft
    assert router.prompts == []


def test_max_rounds_zero_spends_no_budget():
    router = FakeRouter()
    report = self_correct(DRAFT, sources=SOURCES,
                          validators=scripted_validator(2), router=router, max_rounds=0)
    assert report["status"] == STATUS_UNCHANGED
    assert report["rounds_used"] == 0
    assert router.prompts == []


# ── verdict normalisation ────────────────────────────────────────────────────

def test_normalize_verdict_accepts_a_trust_verdict_shape():
    class FakeTrustVerdict:
        findings = [{"issue": "a"}, {"issue": "b"}]
        blocked = True

    verdict = normalize_verdict(FakeTrustVerdict())
    assert verdict.count == 2
    assert verdict.blocked is True


def test_normalize_verdict_accepts_a_bare_findings_list():
    verdict = normalize_verdict([{"item_number": 1, "issue": "a"}])
    assert verdict.count == 1
    assert verdict.blocked is True
    assert normalize_verdict([]) == Verdict([], False)


@pytest.mark.parametrize("bad", [None, "findings", 7])
def test_normalize_verdict_refuses_an_unrecognised_shape(bad):
    """Coercing an unknown return value to an empty list would read as clean."""
    with pytest.raises(TypeError):
        normalize_verdict(bad)


def test_an_unrecognised_validator_result_fails_closed():
    report = self_correct(DRAFT, validators=lambda t: "looks fine to me",
                          router=FakeRouter(), max_rounds=1)
    assert report["status"] == STATUS_ERROR
    assert report["blocked"] is True


# ── targeting ────────────────────────────────────────────────────────────────

def test_target_findings_resolves_item_number_to_the_original_span():
    findings = [{"item_number": 2, "issue": "unsupported_claim", "detail": ["forty-seven"]}]
    targeted = target_findings(DRAFT, findings)
    assert len(targeted) == 1
    entry = targeted[0]
    assert "forty-seven units" in entry["claim"]
    assert DRAFT[entry["start"]:entry["end"]].strip() == entry["claim"]


def test_target_findings_keeps_document_level_findings():
    targeted = target_findings(DRAFT, [{"item_number": "document", "issue": "missing_citation"}])
    assert targeted[0]["start"] is None
    assert targeted[0]["issue"] == "missing_citation"


def test_target_findings_survives_an_out_of_range_index():
    targeted = target_findings(DRAFT, [{"item_number": 99, "issue": "stale"}])
    assert targeted[0]["start"] is None
    assert targeted[0]["issue"] == "stale"


def test_revision_prompt_quotes_the_exact_failing_span_and_its_offsets():
    targeted = target_findings(
        DRAFT, [{"item_number": 2, "issue": "unsupported_claim", "detail": ["forty-seven"]}]
    )
    prompt = build_revision_prompt(DRAFT, targeted, SOURCES)
    start, end = targeted[0]["start"], targeted[0]["end"]
    assert f"chars {start}-{end}" in prompt
    assert "forty-seven units" in prompt
    assert "unsupported_claim" in prompt
    # Evidence is narrowed to the source the failing claim actually cites.
    assert SOURCES["1"] in prompt
    assert SOURCES["2"] not in prompt
    # And the sentence that did NOT fail is not named as a defect.
    assert "1. chars" in prompt
    assert "2. chars" not in prompt


def test_revision_prompt_carries_the_whole_document():
    targeted = target_findings(DRAFT, [{"item_number": 1, "issue": "x"}])
    prompt = build_revision_prompt(DRAFT, targeted, SOURCES)
    assert DRAFT in prompt


def test_the_loop_retargets_against_the_current_best_text():
    """Offsets must index the text being revised, not the original — otherwise
    round 2 quotes spans from a document that no longer exists."""
    revised = revision("ROUND ONE")
    router = FakeRouter(revised, revision("ROUND TWO"))
    self_correct(DRAFT, sources=SOURCES,
                 validators=scripted_validator(3, 2, 1), router=router, max_rounds=2)
    assert DRAFT in router.prompts[0]
    assert revised in router.prompts[1]
    assert DRAFT not in router.prompts[1]


def test_revision_is_issued_under_the_declared_routing_function():
    router = FakeRouter(revision("ANY"))
    self_correct(DRAFT, sources=SOURCES,
                 validators=scripted_validator(2, 1), router=router, max_rounds=1)
    assert router.functions == [DEFAULT_FUNCTION]


# ── routing declaration ──────────────────────────────────────────────────────

@pytest.mark.parametrize("config", ["args/llm_config.yaml", "icdev/args/llm_config.yaml"])
def test_trust_self_correct_is_declared_local_first_in_both_configs(config):
    """An undeclared function falls through to the cloud-first routing.default,
    and the two copies have drifted before — so both are asserted."""
    path = REPO_ROOT / config
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entry = (data.get("routing") or {}).get(DEFAULT_FUNCTION)
    assert entry, f"{config} does not declare routing.{DEFAULT_FUNCTION}"
    chain = entry.get("chain") or []
    assert chain, f"{config}: routing.{DEFAULT_FUNCTION} has an empty chain"
    assert chain[0].endswith("-local"), (
        f"{config}: routing.{DEFAULT_FUNCTION} must be local-first, got {chain[0]}"
    )
    assert any(m.endswith("-local") for m in chain[1:]), (
        f"{config}: routing.{DEFAULT_FUNCTION} needs a local fallback for air-gap"
    )
