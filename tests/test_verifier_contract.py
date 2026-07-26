#!/usr/bin/env python3
"""Contract tests for the DIC verifier — CUI // SP-CTI.

`verify()` used to return `VerifyResult(...).to_dict()` while three of its four
callers used attribute access (`vr.abstained`, `vr.verified`). Every call raised
AttributeError into a bare `except`, so DIC's only anti-hallucination gate was a
no-op on the chat and document-generation paths while the UI displayed a
"CoD-verified" badge.

These tests pin both halves of the contract so the two spellings can never drift
apart again, and assert that an unsupported claim is actually caught.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence.verifier import (
    ClaimVerdict,
    VerifyResult,
    verify,
)
from tools.document_intelligence.doc_generator import _compute_section_confidence


# --------------------------------------------------------------------------- #
# Return-type contract
# --------------------------------------------------------------------------- #


def test_verify_returns_verifyresult_not_dict():
    """The documented return type is the object, not its dict projection."""
    vr = verify("Anything at all.", ["some evidence text"])
    assert isinstance(vr, VerifyResult)
    assert not isinstance(vr, dict)


@pytest.mark.parametrize(
    "draft,evidence",
    [
        ("", []),                                        # no_evidence
        ("...", ["retention is seven years"]),           # no_claims
        ("Retention is seven years [SOURCE-1].", ["retention is seven years"]),
    ],
)
def test_attribute_and_mapping_access_agree(draft, evidence):
    """Both spellings must work and return the same values, on every branch.

    `acoic.py` reads `vr.get("abstained")`; `blueprint.py`/`doc_generator.py`/
    `handoff.py` read `vr.abstained`. Neither may break the other.
    """
    vr = verify(draft, evidence)
    for key in ("verified_text", "abstained", "verified", "reason"):
        assert vr[key] == getattr(vr, key), f"mismatch on {key!r}"
        assert vr.get(key) == getattr(vr, key), f"mismatch on .get({key!r})"
    assert "abstained" in vr
    assert vr.get("does_not_exist", "fallback") == "fallback"
    assert set(vr.keys()) == set(vr.to_dict().keys())


def test_verified_field_exists_for_handoff_caller():
    """`handoff.py:212` reads `vr.verified` — a field that did not exist."""
    vr = verify("Retention is seven years [SOURCE-1].", ["retention is seven years"])
    assert isinstance(vr.verified, bool)
    assert "verified" in vr.to_dict()


def test_abstained_result_is_never_verified():
    vr = verify("Some claim [SOURCE-1].", [])          # no evidence -> abstain
    assert vr.abstained is True
    assert vr.verified is False


# --------------------------------------------------------------------------- #
# The gate actually catching something
# --------------------------------------------------------------------------- #


def test_fabricated_claim_with_valid_citation_is_not_verified():
    """The exact failure mode the gate exists for.

    The citation is structurally perfect — [SOURCE-1] exists and is in range, so
    the structural citation check passes. The claim it is attached to is simply
    not what the source says. Structural validation alone cannot catch this.
    """
    evidence = [
        "The contractor shall retain all records for a period of seven years "
        "following contract closeout, as required by the retention clause."
    ]
    draft = "Personnel must be evacuated within thirty minutes of alarm activation [SOURCE-1]."

    vr = verify(draft, evidence)

    assert vr.verified is False, "a fabricated claim must not read as verified"
    # It is either stripped from the output or the whole draft abstains.
    assert vr.abstained or "thirty minutes" not in vr.verified_text


def test_supported_claim_survives():
    """Control: a claim the source does support must not be thrown away."""
    evidence = [
        "The contractor shall retain all records for a period of seven years "
        "following contract closeout, as required by the retention clause."
    ]
    draft = "The contractor shall retain records for seven years following contract closeout [SOURCE-1]."

    vr = verify(draft, evidence)

    assert vr.abstained is False
    assert "seven years" in vr.verified_text


# --------------------------------------------------------------------------- #
# Confidence scoring — the band gate that never fired
# --------------------------------------------------------------------------- #


def _verdict(*, supported: list[bool], method: str = "lexical", abstained: bool = False):
    return VerifyResult(
        verified_text="x",
        claims=[
            ClaimVerdict(claim=f"c{i}", supported=s, source_n=1, method=method)
            for i, s in enumerate(supported)
        ],
        abstained=abstained,
    )


def test_confidence_reflects_supported_ratio():
    assert _compute_section_confidence(_verdict(supported=[True, True])) == 1.0
    assert _compute_section_confidence(_verdict(supported=[True, False])) == 0.5
    assert _compute_section_confidence(_verdict(supported=[False, False])) == 0.0


def test_confidence_is_zero_when_nothing_was_checked():
    """A dict, a None, or a claimless verdict all meant 1.0 before.

    `getattr(some_dict, "claims", [])` returns [], which the old implementation
    scored as full confidence — so every generated section got 1.0 and the
    CONF_INCLUDE / CONF_ABSTAIN bands were unreachable.
    """
    assert _compute_section_confidence(None) == 0.0
    assert _compute_section_confidence({"claims": [], "abstained": False}) == 0.0
    assert _compute_section_confidence(_verdict(supported=[])) == 0.0


def test_uncited_sentences_do_not_inflate_confidence():
    """Uncited sentences make no attributed assertion, so they are not credit."""
    vr = VerifyResult(
        verified_text="x",
        claims=[
            ClaimVerdict(claim="cited-bad", supported=False, source_n=1, method="lexical"),
            ClaimVerdict(claim="uncited-1", supported=True, source_n=None, method="uncited"),
            ClaimVerdict(claim="uncited-2", supported=True, source_n=None, method="uncited"),
        ],
    )
    # Counting all three would give 2/3; only the cited claim counts -> 0.0.
    assert _compute_section_confidence(vr) == 0.0
    assert vr.verified is False


def test_abstained_verdict_scores_zero():
    assert _compute_section_confidence(_verdict(supported=[True], abstained=True)) == 0.0
