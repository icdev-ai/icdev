# CUI // SP-CTI
"""The kg_grounding gate in the Cortex governance chain (trust-kg-03).

`kg_grounding` joins GATE_ORDER alongside the two existing grounding gates, but
it is the first OPT-IN gate the chain has had: it is in the vocabulary and NOT in
`default`, so a profile must name it. These tests pin the three things that
distinction has to buy:

  * every caller that existed before this card is byte-for-byte unchanged — the
    gate records `skip` and its seams are never touched;
  * a profile that DECLARES it and then cannot measure (no graph connection, or
    a graph with no nodes) records **fail**, never `warn` and never `pass`. That
    is the cxo-trust-01 lesson: `citation_type='cortex'` raised for the
    provenance gate's whole lifetime with 0 of 285 rows written and read as
    ordinary flakiness, because a misconfiguration and a transient degradation
    were recorded the same way;
  * `fail` here still does NOT block. `governance.fail_closed` ships false and is
    the single platform-wide switch; a caller that sets `ctx.fail_closed` gets
    the refusal, and nobody else does.

Every gate seam is monkeypatched at `governance._gate_*`, so no database,
knowledge graph, gateway or anonymizer is touched — same discipline as
`test_governance_pipeline.py`.
"""
from __future__ import annotations

import pytest

from tools.cortex import governance
from tools.cortex.governance import (
    DEFAULT_GATES,
    DEFAULT_PROFILE,
    GATE_CITATION_GROUNDING,
    GATE_CONTENT_GROUNDING,
    GATE_KG_GROUNDING,
    GATE_OPERATION,
    GATE_ORDER,
    GATE_OUTPUT_REDACTION,
    GATE_PROVENANCE,
    MANDATORY_GATES,
    OPT_IN_GATES,
    SKIPPABLE_GATES,
    GovernanceBlockedError,
    GovernancePipeline,
    load_governance_profiles,
    resolve_profile,
)
from tools.cortex.schemas import CortexContext, CortexResult
from tools.quality.kg_grounding import (
    ISSUE_CONTRADICTED,
    ISSUE_UNKNOWN_ENTITY,
    KG_SUPPORTED,
    SCHEMA_DECLARED,
    SCHEMA_OBSERVED,
    STATUS_OK,
    STATUS_UNMEASURABLE,
)

TEXT = "Storage imports Router [source: 1]."
SOURCES = [{"source_id": "1", "content": TEXT}]

CLEAN_REPORT = {
    "status": STATUS_OK,
    "schema_source": SCHEMA_OBSERVED,
    "claims": [{"item_number": 1, "claim": TEXT, "verdict": KG_SUPPORTED,
                "unknown_entities": [], "triples": []}],
    "counts": {KG_SUPPORTED: 1},
    "unknown_entities": [],
}


class _FakeConn:
    """Stands in for a graph connection; records that the gate closed it."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def calls(monkeypatch):
    """Benign fakes for every seam. `kg` records the gate's own inputs."""
    record: dict = {"kg_conn": [], "kg_ground": [], "kg_findings": [],
                    "redact_out": [], "provenance": [], "audit": []}

    def fake_kg_connection():
        conn = _FakeConn()
        record["kg_conn"].append(conn)
        return conn

    def fake_kg_ground(text, conn, graph_id=None):
        record["kg_ground"].append((text, conn, graph_id))
        return dict(CLEAN_REPORT)

    def fake_kg_findings(report, flag_unknown_entities=False):
        record["kg_findings"].append((report, flag_unknown_entities))
        return []

    def fake_redact_output(text):
        record["redact_out"].append(text)
        return text, []

    monkeypatch.setattr(governance, "_gate_check_text",
                        lambda t: {"allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(governance, "_gate_redact_input", lambda t, c: (t, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", fake_redact_output)
    monkeypatch.setattr(governance, "_gate_validate_citations",
                        lambda t, allowed: {"hallucinated_citations": [], "cited_count": 1})
    monkeypatch.setattr(governance, "_gate_find_placeholders", lambda t: [])
    monkeypatch.setattr(governance, "_gate_kg_connection", fake_kg_connection)
    monkeypatch.setattr(governance, "_gate_kg_ground_claims", fake_kg_ground)
    monkeypatch.setattr(governance, "_gate_kg_findings", fake_kg_findings)
    monkeypatch.setattr(governance, "_gate_register_provenance",
                        lambda text, ctx, op, rid: "scr-kg")
    monkeypatch.setattr(governance, "_gate_record_audit", record["audit"].append)
    return record


def _run(profile: str = "kg_attested", ctx=None, **kwargs):
    pipeline = GovernancePipeline(operation="cortex.test", profile=profile)
    return pipeline.wrap(
        lambda p: CortexResult(text=TEXT),
        ctx if ctx is not None else CortexContext(tenant_id="t1"),
        prompt="what does Storage import?",
        context_sources=SOURCES,
        **kwargs,
    )


# ══════════════════════════════════════════════════════════════
# The vocabulary
# ══════════════════════════════════════════════════════════════

def test_kg_grounding_sits_with_the_other_grounding_gates():
    # Alongside them, and — critically — BEFORE output_redaction: redaction masks
    # PII, which includes the entity labels this gate resolves against, so a gate
    # placed after it would validate claims about [REDACTED].
    order = list(GATE_ORDER)
    assert order.index(GATE_CONTENT_GROUNDING) < order.index(GATE_KG_GROUNDING)
    assert order.index(GATE_KG_GROUNDING) < order.index(GATE_OUTPUT_REDACTION)


def test_kg_grounding_is_opt_in_and_skippable_never_mandatory():
    assert GATE_KG_GROUNDING in OPT_IN_GATES
    assert GATE_KG_GROUNDING in SKIPPABLE_GATES
    assert GATE_KG_GROUNDING not in MANDATORY_GATES
    # The two egress guarantees are what a profile may never drop; a grounding
    # gate is not one of them, no matter how much attestation it adds.
    assert set(MANDATORY_GATES) == {GATE_OPERATION, GATE_OUTPUT_REDACTION,
                                    GATE_PROVENANCE}


def test_default_gates_are_the_chain_minus_the_opt_in_gates():
    assert set(DEFAULT_GATES) == set(GATE_ORDER) - set(OPT_IN_GATES)
    assert GATE_KG_GROUNDING not in DEFAULT_GATES
    # Derived, not hand-written: adding a gate to the vocabulary must not leave
    # this list behind.
    assert list(DEFAULT_GATES) == [g for g in GATE_ORDER if g not in OPT_IN_GATES]


def test_the_shipped_config_declares_a_profile_that_runs_it():
    # A gate no shipped profile names is declared-but-unconsumed — the defect
    # this platform ships most, and the one trust-kg-03 must not add to.
    profiles = load_governance_profiles()
    declaring = [n for n, gates in profiles.items() if GATE_KG_GROUNDING in gates]
    assert declaring, "no shipped profile declares kg_grounding"
    assert "kg_attested" in declaring
    assert resolve_profile("kg_attested") >= frozenset(MANDATORY_GATES)
    assert GATE_KG_GROUNDING not in resolve_profile(DEFAULT_PROFILE)


# ══════════════════════════════════════════════════════════════
# Callers that predate the gate are unchanged
# ══════════════════════════════════════════════════════════════

def test_default_profile_skips_the_gate_without_touching_its_seams(calls):
    result, report = _run(profile="")

    assert report.outcomes[GATE_KG_GROUNDING] == "skip"
    assert GATE_KG_GROUNDING not in report.gates_run
    assert calls["kg_conn"] == []          # no connection opened
    assert calls["kg_ground"] == []        # no graph queried
    assert report.kg_grounding == {}
    # And the answer's grounded verdict is decided by the gates that DID run.
    assert result.grounded is True


def test_a_narrower_profile_still_skips_it(calls):
    _, report = _run(profile="internal_diligence")
    assert report.outcomes[GATE_KG_GROUNDING] == "skip"
    assert calls["kg_conn"] == []


# ══════════════════════════════════════════════════════════════
# A profile that declares it
# ══════════════════════════════════════════════════════════════

def test_declaring_profile_runs_the_gate_and_reports_its_schema(calls):
    result, report = _run()

    assert GATE_KG_GROUNDING in report.gates_run
    assert report.outcomes[GATE_KG_GROUNDING] == "pass"
    assert report.kg_grounding["schema_source"] == SCHEMA_OBSERVED
    assert report.kg_grounding["counts"] == {KG_SUPPORTED: 1}
    assert report.kg_grounding["findings"] == []
    assert result.grounded is True


def test_the_gate_sees_the_unredacted_output_and_closes_its_connection(calls):
    _run()

    text, conn, graph_id = calls["kg_ground"][0]
    assert text == TEXT
    assert graph_id is None                # governance.kg_grounding.graph_id: null
    assert conn.closed is True             # a per-call connection must not leak


def test_findings_policy_is_delegated_not_reimplemented(calls):
    """The gate asks kg_gate which verdicts are worth a finding.

    A second copy of that policy here would drift from trust_gate's kg_guard —
    the two would disagree about the same artifact.
    """
    _run()
    report, flag_unknown = calls["kg_findings"][0]
    assert report["counts"] == {KG_SUPPORTED: 1}
    assert flag_unknown is False           # governance.kg_grounding default


# ══════════════════════════════════════════════════════════════
# Findings: fail vs warn, and the fail-open default
# ══════════════════════════════════════════════════════════════

def _with_findings(monkeypatch, findings):
    monkeypatch.setattr(governance, "_gate_kg_findings",
                        lambda report, flag_unknown_entities=False: findings)


def test_a_contradicted_claim_fails_the_gate_but_does_not_block(calls, monkeypatch):
    """Fail-open, stated on the card: governance.fail_closed ships false.

    kg_contradicted is a provable defect, so the gate records `fail` — but the
    call still returns. Refusing is a platform-wide policy change behind ONE
    switch, not something a new gate grants itself.
    """
    _with_findings(monkeypatch, [{"item_number": 1, "issue": ISSUE_CONTRADICTED,
                                  "detail": [["tool", "imports", "tool"]]}])

    result, report = _run()

    assert report.outcomes[GATE_KG_GROUNDING] == "fail"
    assert report.blocked is False
    assert result.grounded is False
    # The mandatory tail still ran: a grounding verdict never costs the egress
    # guarantee or the audit row.
    assert report.outcomes[GATE_OUTPUT_REDACTION] == "pass"
    assert report.outcomes[GATE_PROVENANCE] == "pass"


def test_the_same_finding_blocks_when_the_caller_opts_into_fail_closed(calls, monkeypatch):
    _with_findings(monkeypatch, [{"item_number": 1, "issue": ISSUE_CONTRADICTED,
                                  "detail": [["tool", "imports", "tool"]]}])

    with pytest.raises(GovernanceBlockedError) as excinfo:
        _run(ctx=CortexContext(tenant_id="t1", fail_closed=True))

    assert excinfo.value.gate == GATE_KG_GROUNDING
    assert excinfo.value.report.blocked is True


def test_an_unknown_entity_only_warns(calls, monkeypatch):
    """A name the graph has not indexed is coverage, not a defect.

    The graph covers a fraction of the world. Recording `fail` for every proper
    noun it has not seen is how a gate earns itself a blanket override.
    """
    _with_findings(monkeypatch, [{"item_number": 1, "issue": ISSUE_UNKNOWN_ENTITY,
                                  "detail": ["Zorblax Prime"]}])

    result, report = _run()

    assert report.outcomes[GATE_KG_GROUNDING] == "warn"
    assert report.blocked is False
    assert result.grounded is False


# ══════════════════════════════════════════════════════════════
# Declared-but-unmeasurable is the loudest outcome this gate has
# ══════════════════════════════════════════════════════════════

def test_no_graph_connection_is_recorded_fail_not_warn(calls, monkeypatch):
    monkeypatch.setattr(governance, "_gate_kg_connection", lambda: None)

    result, report = _run()

    # NOT "warn" (which reads as a transient outage) and NOT "pass" (which would
    # certify an artifact nothing inspected). The operator declared the gate; it
    # did not run; the audit says so.
    assert report.outcomes[GATE_KG_GROUNDING] == "fail"
    assert report.kg_grounding["status"] == STATUS_UNMEASURABLE
    assert report.blocked is False
    assert result.grounded is False


def test_an_empty_graph_is_recorded_fail_and_never_fabricates_findings(calls, monkeypatch):
    """kg_ground_claims reports kg_unmeasurable rather than marking every claim
    unsupported, and the gate carries that through instead of inventing a
    verdict. A fresh worktree must not manufacture findings — but it must also
    not report a clean pass for a check that never happened."""
    monkeypatch.setattr(
        governance, "_gate_kg_ground_claims",
        lambda text, conn, graph_id=None: {
            "status": STATUS_UNMEASURABLE,
            "detail": "graph has no nodes; nothing to validate against",
            "counts": {}, "claims": [], "unknown_entities": [],
            "schema_source": "unavailable",
        },
    )

    _, report = _run()

    assert report.outcomes[GATE_KG_GROUNDING] == "fail"
    assert report.kg_grounding["status"] == STATUS_UNMEASURABLE
    assert "no nodes" in report.kg_grounding["reason"]
    assert report.kg_grounding["findings"] == []
    assert report.blocked is False


def test_a_query_error_degrades_to_warn_not_to_the_misconfiguration_fail(calls, monkeypatch):
    """The two must stay distinguishable in the opposite direction too.

    A raised query IS the transient case an empty graph is not, so it takes the
    ordinary fail-open degrade path. Collapsing both onto `fail` would make the
    signal as useless as collapsing both onto `warn` did for provenance.
    """
    def boom(text, conn, graph_id=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(governance, "_gate_kg_ground_claims", boom)

    _, report = _run()

    assert report.outcomes[GATE_KG_GROUNDING] == "warn"
    assert report.blocked is False


def test_a_query_error_blocks_under_fail_closed(calls, monkeypatch):
    def boom(text, conn, graph_id=None):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(governance, "_gate_kg_ground_claims", boom)

    with pytest.raises(GovernanceBlockedError) as excinfo:
        _run(ctx=CortexContext(tenant_id="t1", fail_closed=True))

    assert excinfo.value.gate == GATE_KG_GROUNDING


# ══════════════════════════════════════════════════════════════
# The audit row
# ══════════════════════════════════════════════════════════════

def test_the_audit_payload_carries_why_not_just_the_outcome(calls, monkeypatch):
    _with_findings(monkeypatch, [{"item_number": 1, "issue": ISSUE_CONTRADICTED,
                                  "detail": [["tool", "imports", "tool"]]}])
    _run()

    payload = calls["audit"][-1]
    assert payload["outcomes"][GATE_KG_GROUNDING] == "fail"
    # Without this, a `fail` from an unmeasurable graph and a `fail` from a
    # contradicted claim are the same row — and telling those apart is the only
    # reason this gate records `fail` at all.
    assert payload["kg_grounding"]["schema_source"] == SCHEMA_OBSERVED
    assert payload["kg_grounding"]["findings"][0]["issue"] == ISSUE_CONTRADICTED


def test_a_default_profile_call_writes_an_empty_kg_block(calls):
    _run(profile="")
    assert calls["audit"][-1]["kg_grounding"] == {}


# ══════════════════════════════════════════════════════════════
# Non-retrieval calls
# ══════════════════════════════════════════════════════════════

def test_the_gate_runs_on_a_non_retrieval_call_too(calls, monkeypatch):
    """The graph is its own evidence set.

    citation_grounding and content_grounding have nothing to attest against when
    no sources were injected, which is why they skip. kg_grounding does not
    depend on injected sources at all — a fabricated entity in a free-form
    completion is exactly what it can catch.
    """
    pipeline = GovernancePipeline(operation="cortex.complete", profile="kg_attested")
    _, report = pipeline.wrap(
        lambda p: CortexResult(text=TEXT), CortexContext(tenant_id="t1"),
        prompt="draft something", retrieval=False,
    )

    assert report.outcomes[GATE_CITATION_GROUNDING] == "skip"
    assert report.outcomes[GATE_KG_GROUNDING] == "pass"
    assert calls["kg_ground"], "the gate never queried the graph"


def test_empty_output_skips_rather_than_failing(calls):
    """Nothing to ground is not the same as a gate that could not run."""
    pipeline = GovernancePipeline(operation="cortex.complete", profile="kg_attested")
    _, report = pipeline.wrap(
        lambda p: CortexResult(text=""), CortexContext(tenant_id="t1"),
        prompt="q", retrieval=False,
    )

    assert report.outcomes[GATE_KG_GROUNDING] == "skip"
    assert calls["kg_conn"] == []


def test_a_list_result_skips_rather_than_grounding_its_repr(calls):
    """`search` returns a list, whose `str()` is a repr, not prose.

    Feeding that to the claim decomposer would read dataclass field names as
    sentences and manufacture verdicts out of a serialization artifact — the one
    failure mode kg_grounding is written to avoid. Output redaction below handles
    the list shape properly and must be unaffected.
    """
    pipeline = GovernancePipeline(operation="cortex.search", profile="kg_attested")
    _, report = pipeline.wrap(
        lambda p: [CortexResult(text=TEXT)], CortexContext(tenant_id="t1"),
        prompt="q", context_sources=SOURCES, attach=False,
    )

    assert report.outcomes[GATE_KG_GROUNDING] == "skip"
    assert calls["kg_conn"] == []
    assert report.outcomes[GATE_OUTPUT_REDACTION] == "pass"


def test_a_plain_string_result_is_grounded(calls):
    pipeline = GovernancePipeline(operation="cortex.complete", profile="kg_attested")
    _, report = pipeline.wrap(
        lambda p: TEXT, CortexContext(tenant_id="t1"),
        prompt="q", context_sources=SOURCES,
    )

    assert report.outcomes[GATE_KG_GROUNDING] == "pass"
    assert calls["kg_ground"][0][0] == TEXT


def test_declared_schema_is_reported_when_the_graph_gains_an_ontology(calls, monkeypatch):
    """kg_ontology ships empty, so `observed` is what runs today.

    The moment it gains rows the gate reports `declared` with no code change —
    and only then may a contradiction verdict be reached at all. A consumer that
    reads the verdict without reading schema_source is reading more assurance
    than the gate offered.
    """
    monkeypatch.setattr(
        governance, "_gate_kg_ground_claims",
        lambda text, conn, graph_id=None: {**CLEAN_REPORT,
                                           "schema_source": SCHEMA_DECLARED},
    )

    _, report = _run()

    assert report.kg_grounding["schema_source"] == SCHEMA_DECLARED
