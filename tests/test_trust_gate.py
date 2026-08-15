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
    STAGE1_GUARDS,
    STAGE2_DISABLED,
    STAGE2_SKIPPED_BLOCKED,
    STAGE2_UNAVAILABLE,
    STATUS_CLEAN,
    STATUS_FINDINGS,
    STATUS_NOT_APPLICABLE,
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


class _MeasurableGraph:
    """A populated-but-irrelevant graph, so kg_guard is CLEAN rather than
    unmeasurable.

    The stage-2 policy tests below are about what happens when the LLM verifier
    is unreachable. Under `compliance_evidence` an unmeasurable guard blocks
    stage 1 (invariant 2), which short-circuits stage 2 and would make those
    tests assert the wrong thing. Supplying a graph that resolves none of the
    claim's entities keeps kg_guard clean and leaves stage 2 the subject.
    """

    _NODES = [{"id": "n1", "label": "Zzz Unrelated Node", "entity_type": "thing",
               "properties": "{}"}]

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        if "COUNT(*) AS n FROM kg_nodes" in q:
            rows = [{"n": 1}]
        elif "FROM kg_ontology" in q:
            rows = []
        elif "FROM kg_edges e" in q:
            rows = [{"st": "thing", "p": "relates_to", "ot": "thing"}]
        elif "FROM kg_nodes" in q:
            rows = list(self._NODES)
        else:
            rows = []

        class _Cur:
            def fetchall(self_inner):
                return rows
        return _Cur()

    def close(self):
        pass


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
        conn=_MeasurableGraph(),
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
        conn=_MeasurableGraph(),
    )
    assert strict.stage2_status == STAGE2_UNAVAILABLE
    assert strict.blocked is True
    assert strict.gate in {"cove_guard", "constitution_guard"}

    lenient = TrustGate("drafting").evaluate(
        _GROUNDED, sources=_SOURCES, router=None, run_stage2=True,
        conn=_MeasurableGraph(),
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


# --------------------------------------------------------------------------- #
# kg_guard wiring (trust-kg-02)
# --------------------------------------------------------------------------- #

def test_kg_guard_without_a_connection_is_unmeasurable_not_clean():
    """Invariant 2 again, on the newest guard.

    The whole check is "does the graph attest this?" — with no graph there is
    nothing to attest anything, and reporting clean would be a guard that never
    looked claiming it found nothing wrong.
    """
    verdict = TrustGate("drafting").evaluate(_GROUNDED, sources=_SOURCES)
    assert verdict.stage1["kg_guard"].status == STATUS_UNMEASURABLE


def test_an_unmeasurable_kg_guard_refuses_compliance_evidence_but_not_drafting():
    """Same missing evidence, two stated postures — the profile decides."""
    assert TrustGate("drafting").evaluate(
        _GROUNDED, sources=_SOURCES, run_stage2=False,
    ).blocked is False

    strict = TrustGate("compliance_evidence").evaluate(
        _GROUNDED, sources=_SOURCES, run_stage2=False,
    )
    assert strict.blocked is True
    assert strict.gate == "kg_guard"


def test_kg_guard_reports_which_schema_produced_its_verdict():
    """A caller must never have to guess whether a verdict could have blocked.

    Only a DECLARED schema (kg_ontology, which ships empty) licenses a
    contradiction; under the observed schema this guard can inform but never
    refuse.
    """
    verdict = TrustGate("drafting").evaluate(
        _GROUNDED, sources=_SOURCES, conn=_MeasurableGraph(),
    )
    result = verdict.stage1["kg_guard"]
    assert result.status == STATUS_CLEAN
    assert result.detail["schema_source"] == "observed"


def test_kg_guard_is_declared_by_every_profile_and_is_recordable():
    for name in list_profiles():
        assert "kg_guard" in load_profile(name).guards
    assert "kg_guard" in PUBLISH_GATES


# --------------------------------------------------------------------------- #
# structure_guard wiring (trust-struct-03)
# --------------------------------------------------------------------------- #

_CONTRACT = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "score": {"type": "number"}},
    "required": ["label"],
}


def test_no_contract_is_not_applicable_rather_than_unmeasurable():
    """The distinction this guard lives or dies on.

    A contract is a DECLARATION that the artifact has a shape. Absent one there
    is no obligation to measure, so there is nothing unmeasured — unlike
    claim_guard, where a claim always owes its source. Collapsing the two would
    make every prose artifact carry a permanent "could not verify".
    """
    result = TrustGate("agent_output").evaluate("plain prose").stage1["structure_guard"]
    assert result.status == STATUS_NOT_APPLICABLE
    assert result.detail["reason"]


def test_a_missing_contract_does_not_refuse_a_compliance_export():
    """The false-positive trap, pinned.

    compliance_evidence refuses on an unmeasurable guard (invariant 2). An SSP
    narrative declares no output contract and never will. Had "no contract"
    been scored unmeasurable, this profile would refuse every compliance
    artifact it exists to gate — a check with no applicability gate is 100%
    false positives, which is no more useful than one that never fires.
    """
    verdict = TrustGate("compliance_evidence").evaluate(
        _GROUNDED, sources=_SOURCES, conn=_MeasurableGraph(), run_stage2=False,
    )
    assert verdict.stage1["structure_guard"].status == STATUS_NOT_APPLICABLE
    assert verdict.gate != "structure_guard"


def test_a_conforming_payload_is_clean():
    verdict = TrustGate("agent_output").evaluate(
        '{"label": "ok", "score": 1}', contract=_CONTRACT,
    )
    assert verdict.stage1["structure_guard"].status == STATUS_CLEAN
    assert verdict.blocked is False


def test_a_non_conforming_payload_blocks_agent_output():
    """The one guard that blocks on an otherwise warn-only profile.

    Conformance is not a judgement call: a caller that asked for an object and
    got something else cannot use it, whatever a reviewer thinks.
    """
    verdict = TrustGate("agent_output").evaluate('{"score": 1}', contract=_CONTRACT)
    result = verdict.stage1["structure_guard"]
    assert result.status == STATUS_FINDINGS
    assert verdict.blocked is True
    assert verdict.gate == "structure_guard"
    assert {f.issue for f in result.findings} == {"missing_required"}


def test_prose_where_an_object_was_declared_is_a_finding_not_a_pass():
    """The refusal case — a model that answered in words is not conforming."""
    verdict = TrustGate("agent_output").evaluate(
        "I could not complete that request.", contract=_CONTRACT,
    )
    assert verdict.stage1["structure_guard"].status == STATUS_FINDINGS
    assert verdict.gate == "structure_guard"


def test_structure_is_reported_before_the_prose_guards():
    """Shape precedes content. If the artifact is not the declared object, a
    citation complaint about its braces is noise, and the gate a reviewer sees
    should name the actual defect."""
    assert STAGE1_GUARDS[0] == "structure_guard"
    verdict = TrustGate("agent_output").evaluate("no citations here", contract=_CONTRACT)
    assert verdict.stage1["citation_guard"].findings   # citation_guard also fired
    assert verdict.gate == "structure_guard"           # but structure named the gate


def test_a_malformed_contract_is_unavailable_not_a_pass():
    """A contract outside the supported subset is a defect in the DECLARING
    call site. It must not read as "the artifact passed"."""
    verdict = TrustGate("agent_output").evaluate(
        '{"label": "ok"}', contract={"type": "object", "minLength": 3},
    )
    result = verdict.stage1["structure_guard"]
    assert result.status == STATUS_UNAVAILABLE
    assert "not a supported schema" in result.detail["reason"]


def test_prose_profiles_skip_the_guard_entirely():
    """drafting and chat_rag produce narratives. Declaring a guard they can
    never exercise is the declared-but-unconsumed defect in miniature."""
    for name in ("drafting", "chat_rag"):
        assert "structure_guard" not in load_profile(name).guards
        result = TrustGate(name).evaluate("x", contract=_CONTRACT).stage1["structure_guard"]
        assert result.status == "skipped"


def test_structure_guard_is_declared_where_it_can_fire_and_is_recordable():
    for name in ("compliance_evidence", "agent_output"):
        assert load_profile(name).guards["structure_guard"] == SEV_BLOCK
    assert "structure_guard" in PUBLISH_GATES


def test_an_explained_override_clears_a_structure_block():
    """A structural refusal is still a HITL decision, and its override has to be
    recordable — which is what the PUBLISH_GATES widening buys."""
    verdict = TrustGate("agent_output").evaluate(
        '{"score": 1}', contract=_CONTRACT,
        force={"structure_guard": "hand-checked against the source record"},
    )
    assert verdict.blocked is False
    assert verdict.overrides["structure_guard_override"]["reason"]


# --------------------------------------------------------------------------- #
# structure_guard — the outline half (trust-struct-02's wiring)
# --------------------------------------------------------------------------- #

def _ssp_sections(n=None):
    from tools.quality.outline_contract import get_contract

    required = get_contract("ato_ssp").required
    return [{"heading": h} for h in (required if n is None else required[:n])]


def test_the_outline_half_reports_through_the_same_guard():
    """"Structure" is two checks on this platform — a payload's shape and a
    document's skeleton. Both are deterministic, both emit the same finding
    shape, and both belong to one verdict; splitting them into two gate names
    would mean two migrations and two override vocabularies for one concept."""
    verdict = TrustGate("compliance_evidence").evaluate(
        "prose", sections=_ssp_sections(), artifact_type="ato_ssp", run_stage2=False,
    )
    result = verdict.stage1["structure_guard"]
    assert result.status == STATUS_CLEAN
    assert result.detail["outline"]["missing"] == 0


def test_a_missing_required_section_blocks_a_compliance_export():
    verdict = TrustGate("compliance_evidence").evaluate(
        "prose", sections=_ssp_sections(1), artifact_type="ato_ssp", run_stage2=False,
    )
    assert verdict.gate == "structure_guard"
    assert {f.issue for f in verdict.stage1["structure_guard"].findings} == {"missing_section"}


def test_an_artifact_type_with_no_declared_skeleton_is_unmeasurable():
    """Distinct from not_applicable, and the distinction is the caller's claim.

    Passing ``sections`` asserts this artifact HAS an outline; we simply cannot
    resolve which one. Reporting not_applicable there would discard the
    caller's own declaration, and reporting clean would fabricate a pass.
    """
    result = TrustGate("compliance_evidence").evaluate(
        "prose", sections=_ssp_sections(), artifact_type="no_such_artifact_type",
        run_stage2=False,
    ).stage1["structure_guard"]
    assert result.status == STATUS_UNMEASURABLE
    assert result.detail["reason"]


def test_the_two_halves_compose_into_one_verdict():
    """A caller that declares both gets both sets of findings under one gate."""
    verdict = TrustGate("agent_output").evaluate(
        '{"nope": 1}', contract=_CONTRACT,
        sections=_ssp_sections(1), artifact_type="ato_ssp",
    )
    codes = {f.issue for f in verdict.stage1["structure_guard"].findings}
    assert "missing_required" in codes      # the payload half
    assert "missing_section" in codes       # the outline half
    assert verdict.gate == "structure_guard"
