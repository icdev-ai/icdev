# CUI // SP-CTI
"""Pressure-test the price-to-win POSTURE through idea_lab's Council. Abstracted.

prem-bid-06.

The Council is genuinely good at one thing here: arguing about a STANCE. Is a
best-value tradeoff on a recompete where we are the incumbent an argument for pricing at
our cost, or for pricing at our value? Five advisors disagreeing about that is worth
reading.

It is worth nothing on our rate tables, and our rate tables must never reach it.

## The question is TEMPLATED, not composed

Every field of a posture is an enum drawn from a closed vocabulary in this module.
The question is rendered from those enum values and nothing else. There is no free-text
field, no `notes`, no `context` — because any of those would be a hole through which a
requirement, an LCAT, or a number could be pasted, and one day would be.

The consequence, asserted before we send: **the question contains no digits.** Not a
rate, not a total, not a CLIN number, not a solicitation number. That is a crude rule and
it is exactly why it holds — there is no clever way to violate it accidentally.

idea_lab is cloud-only. specialist_consult sanitizes on the way out, but redaction is
defense-in-depth, not a licence to pass raw content: this module's job is that there is
nothing to redact in the first place.

## Advisory. Always.

Returns None if the gate is off, if the posture is incomplete, or if idea_lab is
unreachable. No pricing decision anywhere depends on this call, and none should.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

_GATE = "ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED"

# The closed vocabulary. A posture is these words, and only these words.
CONTRACT_TYPES = ("firm-fixed-price", "time-and-materials", "cost-plus-fixed-fee",
                  "cost-plus-incentive-fee", "indefinite-delivery")
POSITIONS = ("incumbent", "incumbent-teammate", "challenger", "new-entrant")
EVALUATION_SCHEMES = ("lowest-price-technically-acceptable", "best-value-tradeoff",
                      "highest-technically-rated-fair-price")
# Buckets, never counts. "Several competitors" says what matters about the field and
# carries no digit; "5 competitors" invites "5 competitors bidding against our $4.2M".
COMPETITION = ("sole-source", "limited-competition", "full-and-open")
PRESSURE = ("price-sensitive", "capability-sensitive", "schedule-sensitive")

_FIELDS = {
    "contract_type": CONTRACT_TYPES,
    "position": POSITIONS,
    "evaluation_scheme": EVALUATION_SCHEMES,
    "competition": COMPETITION,
    "pressure": PRESSURE,
}

_DIGIT = re.compile(r"\d")


def enabled() -> bool:
    return os.environ.get(_GATE, "").strip().lower() in ("1", "true", "yes")


def validate_posture(posture: Dict[str, Any]) -> Optional[str]:
    """Every field present, and every value from its closed vocabulary. Else a reason."""
    if not isinstance(posture, dict):
        return "posture must be an object"
    for field, allowed in _FIELDS.items():
        value = posture.get(field)
        if value is None or str(value).strip() == "":
            return f"'{field}' is required"
        if value not in allowed:
            return (f"'{field}' must be one of {list(allowed)} — a posture is drawn from a "
                    f"closed vocabulary, because a free-text field is a hole through which "
                    f"a rate or a requirement would eventually be pasted")
    return None


def posture_question(posture: Dict[str, Any]) -> str:
    """Render the abstracted question. Enum values only — no caller text reaches this."""
    problem = validate_posture(posture)
    if problem:
        raise ValueError(problem)

    question = (
        f"We are bidding a {posture['contract_type']} contract as the "
        f"{posture['position']}, under {posture['competition']}, evaluated on a "
        f"{posture['evaluation_scheme']} basis, against a customer we read as "
        f"{posture['pressure']}.\n\n"
        "Pressure-test our price-to-win posture. Where is the strongest argument that we "
        "are reading this wrong? What does this posture systematically cause bidders to "
        "get wrong, and what would you look at to find out whether we are making that "
        "mistake?\n\n"
        "Answer about the POSTURE. You have no access to our rates, our labour mix, our "
        "cost structure, or the solicitation, and you should not ask for them."
    )

    # Belt and braces. The template has no digits and the vocabulary has no digits, so
    # this can only fire if someone later adds a field that carries caller data — which
    # is exactly when we want it to fire, and before anything is sent.
    if _DIGIT.search(question):
        raise ValueError(
            "the posture question contains a digit. A price-to-win consult is an "
            "abstracted question about a STANCE — a number in it is a rate, a total, or a "
            "solicitation identifier, and none of those may cross the boundary."
        )
    return question


def consult_posture(posture: Dict[str, Any], *,
                    consult=None) -> Optional[Dict[str, Any]]:
    """Pressure-test the posture through idea_lab's Council. Advisory; never blocking.

    Returns None when the gate is off, the posture is incomplete, or idea_lab is
    unreachable. Nothing downstream may depend on the answer.
    """
    if not enabled():
        return None

    if validate_posture(posture) is not None:
        return None

    question = posture_question(posture)

    if consult is None:
        from tools.govcon.specialist_consult import request_council_consult

        consult = request_council_consult

    # No context_summary. There is no summary of this bid that belongs on the wire — the
    # posture IS the whole of what we are willing to say.
    result = consult("price-to-win posture", question)
    if not result:
        return None

    return {
        "verdict": result.get("verdict"),
        "stop_reason": result.get("stop_reason"),
        "source": result.get("source"),
        "posture": dict(posture),
        # What we sent, verbatim, on the record. A consult you cannot audit is one you
        # cannot defend when someone asks what left the building.
        "question_sent": question,
        "advisory": True,
        "note": ("Advisory only. The Council saw a posture, not a price: no rate, no LCAT, "
                 "no requirement text, and no number of any kind crossed the boundary."),
    }
