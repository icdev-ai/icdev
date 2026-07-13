# CUI // SP-CTI
"""The price-to-win posture consult, and the boundary it cannot cross.

prem-bid-06.

idea_lab is CLOUD-ONLY. Our rate tables, LCAT mix, and the solicitation are CUI. The
Council is genuinely useful for arguing about a STANCE and useless on our rate tables —
so the design is that there is nothing to redact in the first place.

Two things under test:

  1. **The question is templated from a closed vocabulary.** No free-text field exists, so
     there is no hole through which a requirement, an LCAT, or a number can be pasted. The
     asserted consequence: the question contains NO DIGITS.

  2. **specialist_consult now FAILS CLOSED.** It used to return the raw, unsanitized text
     if GovConSanitizer failed to import or raised — an unsanitized CUI egress path that
     opened precisely when redaction was broken.
"""
from __future__ import annotations

import importlib

import pytest

from tools.govcon import ptw_posture

POSTURE = {
    "contract_type": "firm-fixed-price",
    "position": "incumbent",
    "evaluation_scheme": "best-value-tradeoff",
    "competition": "full-and-open",
    "pressure": "price-sensitive",
}


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED", "true")


class FakeConsult:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def __call__(self, topic_domain, question, context_summary=""):
        self.calls.append({"topic_domain": topic_domain, "question": question,
                           "context_summary": context_summary})
        return self.result


VERDICT = {"verdict": "Pricing at cost as the incumbent invites a challenger to buy it.",
           "stop_reason": "consensus", "source": "icdev_council"}


# ---------------------------------------------------------------------------
# Nothing but the posture crosses the boundary
# ---------------------------------------------------------------------------
def test_the_question_contains_no_digits():
    """A number in a price-to-win consult is a rate, a total, or a solicitation number."""
    question = ptw_posture.posture_question(POSTURE)
    assert not any(c.isdigit() for c in question)


def test_every_posture_the_vocabulary_allows_produces_a_digitless_question():
    """Not just this one posture. Every posture that can be built at all."""
    import itertools

    fields = ["contract_type", "position", "evaluation_scheme", "competition", "pressure"]
    vocabularies = [getattr(ptw_posture, name) for name in
                    ("CONTRACT_TYPES", "POSITIONS", "EVALUATION_SCHEMES", "COMPETITION",
                     "PRESSURE")]

    for combo in itertools.product(*vocabularies):
        question = ptw_posture.posture_question(dict(zip(fields, combo)))
        assert not any(c.isdigit() for c in question), combo


def test_a_value_outside_the_vocabulary_is_refused():
    """The closed vocabulary IS the control. Free text would be the hole."""
    bad = {**POSTURE, "position": "incumbent on the $4.2M cyber SOC recompete"}
    assert "closed vocabulary" in ptw_posture.validate_posture(bad)

    with pytest.raises(ValueError, match="closed vocabulary"):
        ptw_posture.posture_question(bad)


def test_there_is_no_free_text_field_to_smuggle_through(gate_on):
    """A caller cannot attach notes, context, or a summary. There is nowhere to put them."""
    consult = FakeConsult(VERDICT)
    smuggled = {**POSTURE, "notes": "Our Cyber Analyst rate is $147.50/hr",
                "context": "Section L.3 requires fully burdened rates"}

    ptw_posture.consult_posture(smuggled, consult=consult)

    sent = consult.calls[0]
    assert "147" not in sent["question"]
    assert "Section L.3" not in sent["question"]
    assert sent["context_summary"] == "", "no context_summary is ever sent"
    # The extra keys are simply not part of the template. They go nowhere.
    assert "notes" not in sent["question"]


def test_an_incomplete_posture_sends_nothing(gate_on):
    consult = FakeConsult(VERDICT)
    assert ptw_posture.consult_posture({"position": "incumbent"}, consult=consult) is None
    assert consult.calls == []


def test_the_question_tells_the_council_it_has_no_access_to_our_numbers():
    question = ptw_posture.posture_question(POSTURE)
    assert "you should not ask for them" in question
    assert "no access to our rates" in question


# ---------------------------------------------------------------------------
# The gate, and the advisory contract
# ---------------------------------------------------------------------------
def test_the_gate_is_off_by_default(monkeypatch):
    monkeypatch.delenv("ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED", raising=False)
    consult = FakeConsult(VERDICT)

    assert ptw_posture.enabled() is False
    assert ptw_posture.consult_posture(POSTURE, consult=consult) is None
    assert consult.calls == [], "nothing leaves the building with the gate off"


def test_an_unreachable_idea_lab_is_none_not_an_exception(gate_on):
    assert ptw_posture.consult_posture(POSTURE, consult=FakeConsult(None)) is None


def test_the_verdict_carries_what_was_sent(gate_on):
    result = ptw_posture.consult_posture(POSTURE, consult=FakeConsult(VERDICT))

    assert result["advisory"] is True
    assert result["verdict"] == VERDICT["verdict"]
    # A consult you cannot audit is one you cannot defend when someone asks what left.
    assert result["question_sent"] == ptw_posture.posture_question(POSTURE)
    assert "no number of any kind crossed the boundary" in result["note"]


# ---------------------------------------------------------------------------
# specialist_consult fails CLOSED
# ---------------------------------------------------------------------------
def test_a_broken_sanitizer_sends_nothing_rather_than_raw_text(monkeypatch):
    """The bug. It used to `return question, context_summary` on ANY exception.

    So if GovConSanitizer failed to import — or raised for any reason — the RAW, verbatim
    RFI/proposal text went to idea_lab, which is cloud-only. An unsanitized CUI egress
    path that opened at precisely the moment redaction was broken.

    It also silently contradicted `redaction.fail_closed: true`. A fail-closed policy with
    a fail-open bypass in the one module that crosses the boundary is not a policy.
    """
    sc = importlib.import_module("tools.govcon.specialist_consult")
    sanitizer_mod = importlib.import_module("tools.redaction.govcon_sanitizer")

    CUI = "Our Cyber Analyst rate is $147.50/hr per Section L.3"

    def _explode(*_a, **_kw):
        raise ImportError("govcon_sanitizer is not importable")

    # Patch the module OBJECT, not a dotted string: tools.* and icdev.tools.* resolve to
    # different module objects through the compat shim, and a string-form patch hits the
    # wrong one.
    monkeypatch.setattr(sanitizer_mod, "GovConSanitizer", _explode)

    # _redact now reports the failure instead of laundering it into "sanitized".
    assert sc._redact(CUI, "") is None

    # And the consult sends NOTHING — it does not reach the network at all.
    monkeypatch.setenv("SPECIALIST_API_KEY", "test-key")
    assert sc.request_council_consult("price-to-win posture", CUI) is None


def test_a_working_sanitizer_still_returns_both_strings():
    sc = importlib.import_module("tools.govcon.specialist_consult")

    out = sc._redact("A posture question with no numbers in it.", "")

    assert out is not None, "the happy path must still sanitize and return"
    question, context = out
    assert isinstance(question, str) and question
