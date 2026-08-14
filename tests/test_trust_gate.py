#!/usr/bin/env python3
"""TRUST v2 two-stage gate — the invariants, not the plumbing. CUI // SP-CTI

Every test here corresponds to a failure this platform has already shipped.
The plumbing (does citation_gate parse a tag?) is covered by
tests/test_citation_grounding.py and tests/test_claim_grounding.py; what is
tested here is the POLICY that composes them, because that is where the
interesting mistakes live:

  * a guard that could not run reporting "clean"
  * an LLM pass clearing a deterministic refusal
  * an unreachable verifier silently waving an ATO-bearing artifact through
  * an override nobody can account for afterwards
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from tools.quality.citation_grounding import PUBLISH_GATES
from tools.quality.trust_gate import (
    ALL_GUARDS,
    SEV_BLOCK,
    SEV_WARN,
    STAGE2_DISABLED,
    STAGE2_SKIPPED_BLOCKED,
    STAGE2_UNAVAILABLE,
    STATUS_CLEAN,
    STATUS_FINDINGS,
    STATUS_UNAVAILABLE,
    STATUS_UNMEASURABLE,
    Finding,
    GuardResult,
    TrustGate,
    TrustGateConfigError,
    evaluate,
    list_profiles,
    load_profile,
)

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "args" / "trust_gate.yaml"

# A claim that cites a real source which does not contain the specific it asserts.
_INVENTED = "The system achieved an average patch latency of 47 days [source: kb1]."
_SOURCES = {
    "kb1": "The system enforces multi-factor authentication for all privileged "
           "accounts and logs each session to the central collector."
}
_GROUNDED = "The system enforces multi-factor authentication for all privileged accounts [source: kb1]."


# --------------------------------------------------------------------------- #
# Config integrity
# --------------------------------------------------------------------------- #

def test_all_four_profiles_ship():
    assert set(list_profiles()) == {
        "drafting", "compliance_evidence", "agent_output", "chat_rag"
    }


def test_every_declared_guard_is_a_recordable_publish_gate():
    """A guard name that is not in PUBLISH_GATES cannot be persisted.

    idr_publish_audit.gate's CHECK is rendered from PUBLISH_GATES, so declaring
    a guard here that is absent there means the override INSERT dies at exactly
    the moment a reviewer overrides — the one event that must never go
    unrecorded (NIST AU). This is the same failure migration 300 was written for.
    """
    raw = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))["trust_gate"]
    declared = {g for p in raw["profiles"].values() for g in (p.get("guards") or {})}
    assert declared, "no guards declared"
    assert declared <= set(PUBLISH_GATES)
    assert declared <= set(ALL_GUARDS)


def test_unknown_profile_raises_rather_than_degrading():
    """A typo'd profile name must not quietly become 'nothing blocks'."""
    with pytest.raises(TrustGateConfigError):
        load_profile("no_such_profile")


def test_only_compliance_evidence_refuses_on_an_unmeasurable_guard():
    """The posture split is deliberate and worth pinning."""
    assert load_profile("compliance_evidence").unmeasurable == SEV_BLOCK
    for name in ("drafting", "agent_output", "chat_rag"):
        assert load_profile(name).unmeasurable == SEV_WARN


# --------------------------------------------------------------------------- #
# Invariant 1 — stage 1 never needs an LLM
# --------------------------------------------------------------------------- #

def test_stage1_gates_with_no_router_at_all():
    """Air-gapped IL6 must still refuse an ungrounded claim."""
    verdict = evaluate(_INVENTED, sources=_SOURCES, router=None, profile="drafting")
    assert verdict.blocked is True
    assert verdict.gate == "claim_guard"


def test_a_grounded_cited_claim_passes_stage1():
    verdict = evaluate(_GROUNDED, sources=_SOURCES, profile="drafting")
    assert verdict.blocked is False
    assert verdict.gate is None
    assert verdict.stage1["claim_guard"].status == STATUS_CLEAN


def test_placeholder_is_reported_before_citation():
    """Ordering is the pre-existing docgen contract."""
    verdict = evaluate("Body [source: kb1] with a [PLACEHOLDER] left in.", profile="drafting")
    assert verdict.gate == "placeholder_guard"


# --------------------------------------------------------------------------- #
# Invariant 2 — unmeasured is not clean
# --------------------------------------------------------------------------- #

def test_claim_guard_without_sources_is_unmeasurable_not_clean():
    verdict = evaluate(_GROUNDED, profile="drafting")
    assert verdict.stage1["claim_guard"].status == STATUS_UNMEASURABLE
    assert verdict.stage1["claim_guard"].status != STATUS_CLEAN


def test_unmeasurable_warns_for_drafting_but_refuses_for_compliance_evidence():
    """The same missing evidence, two defensible postures — both stated.

    A drafting surface has a human in the loop by construction; an SSP control
    narrative does not, and an unverifiable control claim is an audit failure.
    """
    assert evaluate(_GROUNDED, profile="drafting").blocked is False

    verdict = evaluate(_GROUNDED, profile="compliance_evidence", run_stage2=False)
    assert verdict.blocked is True
    assert verdict.gate == "claim_guard"
    assert verdict.stage1["claim_guard"].status == STATUS_UNMEASURABLE


def test_a_raising_guard_is_unavailable_not_a_pass(monkeypatch):
    """A guard that blows up must never be mistaken for one that passed."""
    import tools.quality.trust_gate as tg

    def _boom(*_a, **_kw):
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(tg, "_run_citation_guard", _boom)
    verdict = TrustGate("compliance_evidence").evaluate(_GROUNDED, sources=_SOURCES, run_stage2=False)
    assert verdict.stage1["citation_guard"].status == STATUS_UNAVAILABLE
    assert verdict.blocked is True


# --------------------------------------------------------------------------- #
# Invariant 3 — stage 2 may never unblock stage 1
# --------------------------------------------------------------------------- #

def test_a_clean_stage2_cannot_clear_a_stage1_refusal(monkeypatch):
    """The whole point of having two stages in this order.

    An LLM that can talk its way past the deterministic gate is not a gate.
    """
    import tools.quality.trust_gate as tg

    monkeypatch.setattr(
        tg, "_run_cove_guard",
        lambda *_a, **_kw: GuardResult("cove_guard", STATUS_CLEAN, SEV_BLOCK),
    )
    monkeypatch.setattr(
        tg, "_run_constitution_guard",
        lambda *_a, **_kw: GuardResult("constitution_guard", STATUS_CLEAN, SEV_BLOCK),
    )
    verdict = TrustGate("compliance_evidence").evaluate(
        _INVENTED, sources=_SOURCES, router=object(), run_stage2=True,
    )
    assert verdict.blocked is True
    assert verdict.gate == "claim_guard", "a stage-1 guard must win the gate slot"


def test_stage2_is_not_run_for_an_already_blocked_artifact(monkeypatch):
    """Spending LLM calls on a verdict that cannot change the outcome is waste."""
    import tools.quality.trust_gate as tg

    called: list[str] = []
    monkeypatch.setattr(
        tg, "_run_cove_guard",
        lambda *_a, **_kw: called.append("cove") or GuardResult("cove_guard", STATUS_CLEAN, SEV_BLOCK),
    )
    verdict = TrustGate("compliance_evidence").evaluate(
        _INVENTED, sources=_SOURCES, router=object(), run_stage2=True,
    )
    assert verdict.stage2_status == STAGE2_SKIPPED_BLOCKED
    assert called == []


def test_stage2_can_add_a_block_of_its_own(monkeypatch):
    """Findings accumulate — stage 2 is additive, just never subtractive."""
    import tools.quality.trust_gate as tg

    monkeypatch.setattr(
        tg, "_run_cove_guard",
        lambda *_a, **_kw: GuardResult(
            "cove_guard", STATUS_FINDINGS, SEV_BLOCK,
            findings=[Finding("cove_guard", "claim_needs_revision", SEV_BLOCK)],
        ),
    )
    monkeypatch.setattr(
        tg, "_run_constitution_guard",
        lambda *_a, **_kw: GuardResult("constitution_guard", STATUS_CLEAN, SEV_BLOCK),
    )
    verdict = TrustGate("compliance_evidence").evaluate(
        _GROUNDED, sources=_SOURCES, router=object(), run_stage2=True,
    )
    assert verdict.blocked is True
    assert verdict.gate == "cove_guard"


# --------------------------------------------------------------------------- #
# Invariant 5 — an unavailable stage 2 is a stated posture
# --------------------------------------------------------------------------- #

def test_no_router_refuses_compliance_evidence_but_only_warns_for_drafting():
    """The direct fix for cortex_config governance.fail_closed: false.

    That flag shipped false, so the Cortex provenance/grounding gates warned
    instead of blocking — which is how citation_type="cortex" raised ValueError
    for its entire lifetime with 0 of 285 rows recorded and nothing went red.
    """
    strict = TrustGate("compliance_evidence").evaluate(
        _GROUNDED, sources=_SOURCES, router=None, run_stage2=True,
    )
    assert strict.stage2_status == STAGE2_UNAVAILABLE
    assert strict.blocked is True
    assert strict.gate in {"cove_guard", "constitution_guard"}

    lenient = TrustGate("drafting").evaluate(
        _GROUNDED, sources=_SOURCES, router=None, run_stage2=True,
    )
    assert lenient.stage2_status == STAGE2_UNAVAILABLE
    assert lenient.blocked is False
    assert lenient.stage2["cove_guard"].status == STATUS_UNAVAILABLE


def test_stage2_off_is_reported_as_disabled_not_clean():
    verdict = TrustGate("chat_rag").evaluate(_GROUNDED, sources=_SOURCES)
    assert verdict.stage2_status == STAGE2_DISABLED


# --------------------------------------------------------------------------- #
# Invariant 4 — an override states its reason
# --------------------------------------------------------------------------- #

def test_a_reasonless_override_is_dropped_and_the_block_stands():
    verdict = evaluate(
        _INVENTED, sources=_SOURCES, profile="drafting", force={"claim_guard": ""},
    )
    assert verdict.blocked is True
    assert verdict.overrides == {}


def test_an_explained_override_clears_the_block_and_is_recorded():
    verdict = evaluate(
        _INVENTED, sources=_SOURCES, profile="drafting",
        force={"claim_guard": "SME verified against the paper copy"},
    )
    assert verdict.blocked is False
    override = verdict.overrides["claim_guard_override"]
    assert override["reason"] == "SME verified against the paper copy"
    assert override["findings"]


def test_an_override_of_a_clean_guard_is_not_recorded():
    """An override that suppressed nothing is noise in the audit trail."""
    verdict = evaluate(
        _GROUNDED, sources=_SOURCES, profile="drafting",
        force={"citation_guard": "belt and braces"},
    )
    assert verdict.blocked is False
    assert verdict.overrides == {}


# --------------------------------------------------------------------------- #
# Verdict shape
# --------------------------------------------------------------------------- #

def test_the_gate_value_is_always_persistable():
    """Whatever lands in `gate` has to survive the idr_publish_audit CHECK."""
    for profile in list_profiles():
        verdict = TrustGate(profile).evaluate(_INVENTED, sources=_SOURCES, run_stage2=False)
        if verdict.gate is not None:
            assert verdict.gate in PUBLISH_GATES


def test_to_dict_is_json_safe():
    import json

    verdict = evaluate(_INVENTED, sources=_SOURCES, profile="drafting")
    payload = json.dumps(verdict.to_dict())
    assert "claim_guard" in payload
    assert json.loads(payload)["blocked"] is True
