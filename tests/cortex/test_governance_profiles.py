# CUI // SP-CTI
"""Per-node governance profiles (hgx-gov-01).

`GATE_ORDER` used to be the only chain: a call doing internal diligence paid the
same seven gates as one emitting a customer-facing artifact. A *profile* names a
subset of it as data in `args/cortex_config.yaml`, and a caller may name one.

Three guarantees, mirroring the card's acceptance criteria:

  * a caller naming a minimal profile skips ONLY the gates that profile leaves
    out — every other gate runs exactly as it did;
  * no profile can drop `output_redaction` or `provenance`; attempting it is a
    config error raised when the profile loads, not a per-call downgrade;
  * a caller naming NO profile runs the full chain, byte-for-byte as before.

Gate seams are monkeypatched at `governance._gate_*` (same discipline as
`test_governance_pipeline.py`) so no gateway, anonymizer or provenance DB is
touched.
"""
from __future__ import annotations

import pytest

from tools.cortex import governance
from tools.cortex.governance import (
    DEFAULT_GATES,
    DEFAULT_PROFILE,
    GATE_CITATION_GROUNDING,
    GATE_CONTENT_GROUNDING,
    GATE_INPUT_REDACTION,
    GATE_KG_GROUNDING,
    GATE_OPERATION,
    GATE_ORDER,
    GATE_OUTPUT_REDACTION,
    GATE_PRE_CHECK,
    GATE_PROVENANCE,
    MANDATORY_GATES,
    OUTCOME_PASS,
    OUTCOME_SKIP,
    SKIPPABLE_GATES,
    GovernancePipeline,
    GovernanceProfileError,
    load_governance_profiles,
    resolve_profile,
)
from tools.cortex.schemas import CortexContext, CortexResult

CONFIG_HEADER = "# CUI // SP-CTI\n"


@pytest.fixture
def calls(monkeypatch):
    """Benign fakes for every gate seam; records which ones actually ran."""
    record: dict = {"check_text": [], "redact_in": [], "redact_out": [],
                    "citations": [], "provenance": [], "audit": []}

    def fake_check_text(text):
        record["check_text"].append(text)
        return {"allowed": True, "warnings": [], "blocked_reason": None}

    def fake_redact_input(text, classification):
        record["redact_in"].append((text, classification))
        return text, 0

    def fake_redact_output(text):
        record["redact_out"].append(text)
        return text, []

    def fake_validate_citations(text, allowed):
        record["citations"].append((text, list(allowed)))
        return {"hallucinated_citations": [], "cited_count": 1}

    def fake_provenance(output_text, ctx, operation, record_id):
        record["provenance"].append(output_text)
        return "scr-profile"

    monkeypatch.setattr(governance, "_gate_check_text", fake_check_text)
    monkeypatch.setattr(governance, "_gate_redact_input", fake_redact_input)
    monkeypatch.setattr(governance, "_gate_redact_output", fake_redact_output)
    monkeypatch.setattr(governance, "_gate_validate_citations", fake_validate_citations)
    monkeypatch.setattr(governance, "_gate_find_placeholders", lambda text: [])
    monkeypatch.setattr(governance, "_gate_register_provenance", fake_provenance)
    monkeypatch.setattr(governance, "_gate_record_audit", record["audit"].append)
    return record


def _write_config(tmp_path, body: str):
    """Write a cortex_config.yaml with `body` under `governance:` and return it."""
    path = tmp_path / "cortex_config.yaml"
    path.write_text(
        CONFIG_HEADER + "governance:\n  profiles:\n" + body,
        encoding="utf-8",
        newline="",
    )
    return path


# ══════════════════════════════════════════════════════════════
# The vocabulary
# ══════════════════════════════════════════════════════════════

def test_mandatory_and_skippable_gates_partition_the_chain():
    # Every gate is exactly one of the two — a gate in neither list could never
    # be reasoned about by a profile, and one in both is a contradiction.
    assert set(MANDATORY_GATES) | set(SKIPPABLE_GATES) == set(GATE_ORDER)
    assert not set(MANDATORY_GATES) & set(SKIPPABLE_GATES)


def test_egress_and_audit_are_the_non_negotiable_pair():
    assert GATE_OUTPUT_REDACTION in MANDATORY_GATES
    assert GATE_PROVENANCE in MANDATORY_GATES


# ══════════════════════════════════════════════════════════════
# Loading and resolving
# ══════════════════════════════════════════════════════════════

def test_default_is_the_default_chain_with_no_config_at_all(tmp_path):
    # DEFAULT_GATES = GATE_ORDER minus OPT_IN_GATES. `default` is built into
    # code precisely so an unreadable config cannot change it, and that has to
    # hold for the opt-in gates too: a missing file must not silently ADD
    # kg_grounding any more than it may silently drop output_redaction.
    missing = tmp_path / "absent.yaml"
    assert load_governance_profiles(missing) == {DEFAULT_PROFILE: frozenset(DEFAULT_GATES)}
    assert resolve_profile("", missing) == frozenset(DEFAULT_GATES)
    assert resolve_profile(DEFAULT_PROFILE, missing) == frozenset(DEFAULT_GATES)


def test_a_declared_profile_resolves_to_its_gate_subset(tmp_path):
    path = _write_config(tmp_path, "    lean:\n      gates: [operation, output_redaction, provenance]\n")

    assert resolve_profile("lean", path) == frozenset(MANDATORY_GATES)


def test_an_unknown_profile_name_is_an_error_not_a_silent_fallback(tmp_path):
    path = _write_config(tmp_path, "    lean:\n      gates: [operation, output_redaction, provenance]\n")

    with pytest.raises(GovernanceProfileError) as exc:
        resolve_profile("laen", path)
    # The message names what IS declared, so a typo is fixable from the error.
    assert "laen" in str(exc.value) and "lean" in str(exc.value)


@pytest.mark.parametrize("dropped", [GATE_OUTPUT_REDACTION, GATE_PROVENANCE])
def test_a_profile_cannot_drop_egress_or_the_audit_row(tmp_path, dropped):
    gates = [g for g in GATE_ORDER if g != dropped]
    path = _write_config(tmp_path, f"    holey:\n      gates: [{', '.join(gates)}]\n")

    with pytest.raises(GovernanceProfileError) as exc:
        load_governance_profiles(path)
    assert dropped in str(exc.value)


def test_a_profile_cannot_drop_the_operation_itself(tmp_path):
    gates = [g for g in GATE_ORDER if g != GATE_OPERATION]
    path = _write_config(tmp_path, f"    noop:\n      gates: [{', '.join(gates)}]\n")

    with pytest.raises(GovernanceProfileError):
        load_governance_profiles(path)


def test_an_unknown_gate_name_is_a_config_error(tmp_path):
    path = _write_config(
        tmp_path,
        "    typo:\n      gates: [operation, output_redacton, provenance]\n",
    )

    with pytest.raises(GovernanceProfileError) as exc:
        load_governance_profiles(path)
    assert "output_redacton" in str(exc.value)


def test_default_cannot_be_redefined(tmp_path):
    # Redefining `default` is the one thing profiles must not do: it would change
    # the behaviour of every caller that names no profile.
    path = _write_config(
        tmp_path,
        "    default:\n      gates: [operation, output_redaction, provenance]\n",
    )

    with pytest.raises(GovernanceProfileError):
        load_governance_profiles(path)


@pytest.mark.parametrize("body", [
    "    empty:\n      gates: []\n",
    "    nogates:\n      description: forgot the list\n",
    "    scalar: minimal\n",
])
def test_a_malformed_profile_is_a_config_error(tmp_path, body):
    with pytest.raises(GovernanceProfileError):
        load_governance_profiles(_write_config(tmp_path, body))


def test_shipped_profiles_all_conform():
    # args/cortex_config.yaml is part of the deliverable — if a shipped profile
    # were malformed, every caller naming it would fail at runtime instead.
    profiles = load_governance_profiles()
    assert DEFAULT_PROFILE in profiles
    for name, gates in profiles.items():
        assert gates <= frozenset(GATE_ORDER), name
        assert frozenset(MANDATORY_GATES) <= gates, name


# ══════════════════════════════════════════════════════════════
# The pipeline honours the profile
# ══════════════════════════════════════════════════════════════

SOURCES = [{"source_id": "1", "content": "Grounded fact [source: 1]."}]


def _run(profile: str = "", **kwargs):
    pipeline = GovernancePipeline(operation="cortex.test", profile=profile)
    return pipeline.wrap(
        lambda p: CortexResult(text="Grounded fact [source: 1]."),
        CortexContext(tenant_id="t1"),
        prompt="what happened?",
        context_sources=SOURCES,
        **kwargs,
    )


def test_naming_no_profile_runs_the_default_chain(calls):
    _, report = _run()

    assert report.profile == DEFAULT_PROFILE
    assert report.gates_run == list(DEFAULT_GATES)
    assert set(report.outcomes) == set(GATE_ORDER)
    # The ONLY skip a default call may have is the opt-in gate (trust-kg-03).
    assert {g for g, v in report.outcomes.items() if v == OUTCOME_SKIP} == {
        GATE_KG_GROUNDING
    }


def test_a_minimal_profile_skips_only_the_gates_it_omits(calls):
    _, report = _run(profile="internal_diligence")
    omitted = {GATE_PRE_CHECK, GATE_CITATION_GROUNDING, GATE_CONTENT_GROUNDING,
               GATE_KG_GROUNDING}

    assert report.profile == "internal_diligence"
    for gate in omitted:
        assert report.outcomes[gate] == OUTCOME_SKIP
        assert gate not in report.gates_run
    # Everything else ran, and ran normally.
    for gate in set(GATE_ORDER) - omitted:
        assert report.outcomes[gate] == OUTCOME_PASS, gate
        assert gate in report.gates_run

    # And the skips are real: the omitted gates' seams were never called.
    assert calls["check_text"] == []
    assert calls["citations"] == []
    # While the kept ones were.
    assert calls["redact_in"] and calls["redact_out"] and calls["provenance"]


def test_egress_and_provenance_run_under_the_leanest_shipped_profile(calls):
    result, report = _run(profile="internal_diligence")

    assert report.outcomes[GATE_OUTPUT_REDACTION] == OUTCOME_PASS
    assert report.outcomes[GATE_PROVENANCE] == OUTCOME_PASS
    assert calls["redact_out"] == [result.text]
    assert calls["provenance"] == [result.text]


def test_a_partial_profile_keeps_the_gates_it_names(calls):
    # screened_generation drops only the grounding pair; the input screen stays.
    _, report = _run(profile="screened_generation")

    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_SKIP
    assert report.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_SKIP
    assert report.outcomes[GATE_PRE_CHECK] == OUTCOME_PASS
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_PASS
    assert calls["check_text"] and not calls["citations"]


def test_the_trusted_ingest_profile_keeps_both_grounding_gates(calls):
    _, report = _run(profile="trusted_ingest")

    assert report.outcomes[GATE_PRE_CHECK] == OUTCOME_SKIP
    assert report.outcomes[GATE_INPUT_REDACTION] == OUTCOME_SKIP
    assert report.outcomes[GATE_CITATION_GROUNDING] == OUTCOME_PASS
    assert report.outcomes[GATE_CONTENT_GROUNDING] == OUTCOME_PASS
    assert calls["citations"]


def test_a_skipped_grounding_gate_never_certifies_the_answer(calls):
    result, _ = _run(profile="internal_diligence")

    # The gate that would have attested grounding did not run, so the result is
    # not presented as evidence-backed.
    assert result.grounded is False


def test_the_profile_is_on_the_audit_row(calls):
    _run(profile="internal_diligence")

    assert calls["audit"][-1]["profile"] == "internal_diligence"


def test_no_profile_audits_as_default(calls):
    _run()

    assert calls["audit"][-1]["profile"] == DEFAULT_PROFILE


def test_a_per_call_profile_overrides_the_pipelines(calls):
    pipeline = GovernancePipeline(operation="cortex.test", profile="internal_diligence")

    _, wide = pipeline.wrap(
        lambda p: CortexResult(text="Grounded fact [source: 1]."),
        CortexContext(),
        prompt="q", context_sources=SOURCES, profile="",
    )

    assert wide.profile == DEFAULT_PROFILE
    assert wide.gates_run == list(DEFAULT_GATES)


def test_an_unknown_profile_fails_before_the_operation_runs(calls):
    ran: list = []

    with pytest.raises(GovernanceProfileError):
        GovernancePipeline(operation="cortex.test", profile="not-declared").wrap(
            lambda p: ran.append(p), CortexContext(), prompt="q",
        )

    assert ran == []
    # A refused profile is not a governed call, so it writes no audit row either.
    assert calls["audit"] == []


def test_a_profile_does_not_disturb_the_operation_gate(calls):
    seen: list = []
    pipeline = GovernancePipeline(operation="cortex.test", profile="internal_diligence")

    pipeline.wrap(
        lambda p: seen.append(p) or CortexResult(text="ok"),
        CortexContext(), prompt="the prompt", retrieval=False,
    )

    assert seen == ["the prompt"]
