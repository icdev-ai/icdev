# CUI // SP-CTI
"""``cortex.resolve`` — the governed evidence-resolution facade (cef-rsv-01).

The assertions here are the card's acceptance criteria, and the one that
carries the most weight is the third: **an LLM cannot author the verdict.**

That is asserted three independent ways, because one way is a coincidence:

1. ``test_verdict_survives_a_router_that_raises`` — every router call raises,
   and the verdict is still the pack's. A path that consulted a model would
   propagate the error or degrade.
2. ``test_no_llm_call_is_made_at_all`` — the REAL search fan-out runs (backend
   adapters stubbed, not the fan-out) with results scoring BELOW the CRAG
   threshold, i.e. the exact condition that triggers the query-rewrite LLM
   call, and the router records zero invocations. This is the discriminating
   test: it fails if ``corrective=False`` is ever dropped from resolver.
3. ``test_an_advisory_opinion_cannot_move_the_verdict`` — an ``sme`` hit whose
   content asserts the opposite verdict is excluded from citations and leaves
   the verdict untouched. ``sme`` is the one backend whose content an LLM
   authors at query time, so it is the only door through which retrieval could
   smuggle a model-authored verdict in.

Gate seams are patched at ``tools.cortex.governance._gate_*`` (the house
pattern from test_governance_pipeline / test_api_governed) so no heavy backend
is touched and the audit payload is observable.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tools.cortex import (
    api,
    governance,
    resolution_provenance,
    resolver,
    search_service,
)
from tools.cortex.resolver import CortexResolutionBlocked
from tools.cortex.schemas import (
    RESOLVE_VERDICTS,
    Citation,
    CortexContext,
    CortexResolution,
    CortexSearchResult,
)
from tools.cortex.search_service import BackendResults


# ---------------------------------------------------------------------------
# Fakes — a DomainPack that answers exactly what a test tells it to
# ---------------------------------------------------------------------------
def _pack_classes():
    from tools.doc_modernization.base_pack import (
        CandidateEntity,
        DomainPack,
        Replacement,
        Verdict,
    )

    return CandidateEntity, DomainPack, Replacement, Verdict


def make_pack(
    pack_id="fake",
    *,
    matches=True,
    currency_verdict="deprecated",
    rationale="Deprecated by RFC 8996.",
    confidence=1.0,
    evidence_source="rule:tls-11",
    replacement=None,
    raise_on="",
):
    """A DomainPack whose extract/evaluate/recommend are fully scripted."""
    CandidateEntity, DomainPack, Replacement, Verdict = _pack_classes()

    class _FakePack(DomainPack):
        def extract(self, text, chunk_ref):
            if raise_on == "extract":
                raise RuntimeError("extract exploded")
            if not matches:
                return []
            return [CandidateEntity(
                label=text.strip(),
                entity_type="protocol",
                pack_id=pack_id,
                chunk_ref=chunk_ref,
            )]

        def evaluate(self, entity, conn):
            if raise_on == "evaluate":
                raise RuntimeError("evaluate exploded")
            return Verdict(
                currency_verdict=currency_verdict,
                finding_type="deprecated_tech" if currency_verdict != "current" else None,
                severity="high",
                rationale=rationale,
                confidence=confidence,
                evidence=([{"source": evidence_source, "detail": rationale, "date": ""}]
                          if evidence_source else []),
            )

        def recommend(self, entity, verdict, conn):
            if replacement is None:
                return None
            return Replacement(
                label=replacement,
                source="rulebook",
                source_ref=evidence_source,
                detail=f"move to {replacement}",
                evidence=[{"source": evidence_source, "detail": "", "date": ""}],
            )

    return _FakePack(config={"pack_id": pack_id, "label": pack_id,
                             "entity_types": ["protocol"]})


def hit(source_id="rag:1", backend="rag", score=0.9, content="TLS 1.1 appears in SOP-12.",
        advisory=False):
    return CortexSearchResult(
        content=content,
        score=score,
        backend=backend,
        strategy="stub",
        citation=Citation(source_id=source_id, source_type=backend,
                          source_table=f"{backend}_chunks", title="stub", snippet=content),
        metadata={"advisory": True} if advisory else {},
    )


class FakeRouter:
    """Records every invoke; raises when told to."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def invoke(self, function, request, **kwargs):
        self.calls.append(function)
        if self.error is not None:
            raise self.error
        raise AssertionError("resolve must not reach the router")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _canvas_grant_provisioned(monkeypatch):
    """The REST cases below use a TENANT-SCOPED human, which needs a canvas grant.

    ``cortex_bp`` is a module-level singleton, and ``guard_component_access``
    attaches itself to it as a ``before_request`` the first time anything in the
    process wires the component registry onto a real app. So whether the guard
    is present when these tests build their throwaway Flask app depends on what
    ELSE has already imported in the same interpreter: run this file alone and
    it is absent, run it after the rest of tests/cortex and it is there, 403ing
    a tenant-scoped principal that holds no grant. That made three cases pass in
    isolation and fail in-suite -- the order-dependence CLAUDE.md requires both
    runs to catch, and the reason this fixture is autouse rather than applied to
    the three tests that happened to expose it.

    Same fixture, same scope and same reasoning as ``test_rest_api.py``: the
    grant LOOKUP only. Whether the guard admits a principal is covered by
    tests/security/test_canvas_guard_service_key.py; these are facade and REST
    contract tests and must not fail on provisioning state. It does not weaken
    ``test_rest_requires_authentication``, which has no principal at all and so
    is refused before any grant check is reached.
    """
    from tools.security import canvas_access

    monkeypatch.setattr(canvas_access, "check_access", lambda *a, **kw: True)


@pytest.fixture
def gates(monkeypatch):
    """Benign governance gate seams; the audit payloads are recorded."""
    record = {"audit": [], "provenance": [], "resolution_provenance": []}
    monkeypatch.setattr(governance, "_gate_check_text",
                        lambda text: {"allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))
    monkeypatch.setattr(
        governance, "_gate_register_provenance",
        lambda out, ctx, op, rid: record["provenance"].append((op, rid)) or "scr-resolve-1",
    )
    monkeypatch.setattr(governance, "_gate_record_audit",
                        lambda payload: record["audit"].append(payload))
    # The resolver's OWN registry write (cef-rsv-03) is a second seam, and it is
    # patched here for the same reason the gate's is: these are facade tests and
    # must not open a connection or land a row. What the write actually persists
    # is asserted against a real database in test_resolve_trust_loop.py, which is
    # where a stub could not observe it.
    monkeypatch.setattr(
        resolution_provenance, "_register_citation",
        lambda **kwargs: record["resolution_provenance"].append(kwargs)
        or "scr-resolution-1",
    )
    return record


@pytest.fixture
def packs(monkeypatch):
    """Install a pack set + a no-op evidence connection."""

    def _install(*pack_objs):
        monkeypatch.setattr(resolver, "_load_packs",
                            lambda: {p.pack_id: p for p in pack_objs})
        return pack_objs

    class _Conn:
        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(resolver, "_evidence_connection", lambda: _Conn())
    return _install


@pytest.fixture
def evidence(monkeypatch):
    """Replace the fan-out with a scripted one; records the kwargs it got."""
    seen = {}

    def _install(results=(), errors=()):
        def fake_search(query, **kwargs):
            seen.update(kwargs)
            seen["query"] = query
            return BackendResults(results, errors=list(errors))

        monkeypatch.setattr(resolver, "_search_impl", fake_search)
        return seen

    return _install


@pytest.fixture
def router(monkeypatch):
    def _install(fake):
        llm_pkg = importlib.import_module("tools.llm")
        monkeypatch.setattr(llm_pkg, "get_router", lambda config_path=None: fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# AC1 — registered in CORTEX_FACADES and wrapped by _governed_facade
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_resolve_is_a_declared_facade(self):
        assert "resolve" in api.CORTEX_FACADES

    def test_resolve_carries_the_governance_stamps(self):
        assert getattr(api.resolve, "__cortex_governed__", False) is True
        assert api.resolve.__cortex_operation__ == "cortex.resolve"

    @pytest.mark.parametrize("namespace", ["tools", "icdev.tools"])
    def test_registration_holds_in_both_namespaces(self, namespace):
        mod = importlib.import_module(f"{namespace}.cortex.api")
        assert "resolve" in mod.CORTEX_FACADES
        assert getattr(mod.resolve, "__cortex_governed__", False) is True

    def test_the_package_exports_it(self):
        import tools.cortex as cortex

        assert "resolve" in cortex.__all__
        # Asserted by PROPERTY, not by object identity. Another test in this
        # suite reloads tools.cortex.api, which replaces api.resolve while the
        # package's `from .api import resolve` binding keeps the pre-reload
        # object — the hazard rest_v1._schema_error_classes documents. An
        # identity assertion here would pass alone and fail in-suite.
        assert getattr(cortex.resolve, "__cortex_governed__", False) is True
        assert cortex.resolve.__cortex_operation__ == "cortex.resolve"

    def test_it_wraps_the_resolver_impl_not_a_second_one(self):
        # __wrapped__ is set by functools.wraps: the governed facade must wrap
        # the module in tools/cortex/resolver.py, not a copy defined in api.py.
        assert api.resolve.__wrapped__ is resolver.resolve


# ---------------------------------------------------------------------------
# AC2 — every resolve writes a cortex_audit row carrying a provenance_id
# ---------------------------------------------------------------------------
class TestAuditAndProvenance:
    def test_a_resolution_audits_with_a_provenance_id(self, gates, packs, evidence):
        packs(make_pack())
        evidence(results=[hit()])

        api.resolve("TLS 1.1", "is this still approved?", ctx=CortexContext(tenant_id="t1"))

        assert len(gates["audit"]) == 1
        payload = gates["audit"][0]
        assert payload["operation"] == "cortex.resolve"
        assert payload["provenance_id"] == "scr-resolve-1"
        # The provenance gate ran once, for this operation. (Its record id is
        # the gate's own, minted independently of the audit row's.)
        assert [op for op, _rid in gates["provenance"]] == ["cortex.resolve"]

    def test_an_unknown_verdict_still_audits(self, gates, packs, evidence):
        """A resolution that answers nothing is still a governed call."""
        packs(make_pack(matches=False))
        evidence(results=[], errors=[])

        result = api.resolve("Nortel Passport 8600", ctx=CortexContext())

        assert result.verdict == "unknown"
        assert len(gates["audit"]) == 1
        assert gates["audit"][0]["provenance_id"] == "scr-resolve-1"

    def test_the_outer_pipeline_report_is_stashed(self, gates, packs, evidence):
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        # attach=False, like ask: the resolution keeps its OWN report and the
        # enforced-pipeline report is surfaced alongside it.
        assert "pipeline_governance" in result.metadata
        assert result.governance.outcomes.get("citation_grounding") == "pass"


# ---------------------------------------------------------------------------
# AC3 — the verdict is provably from pack evaluate(); an LLM cannot author it
# ---------------------------------------------------------------------------
class TestVerdictIsDeterministic:
    def test_verdict_comes_from_pack_evaluate(self, gates, packs, evidence):
        packs(make_pack(currency_verdict="deprecated"))
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert isinstance(result, CortexResolution)
        assert result.verdict == "deprecated"
        assert result.verdict_source == "pack_evaluate"
        assert result.assessments[0].pack_id == "fake"
        assert result.assessments[0].pack_verdict == "deprecated"

    def test_verdict_survives_a_router_that_raises(self, gates, packs, evidence, router):
        """No model is consulted, so a dead router changes nothing."""
        packs(make_pack(currency_verdict="deprecated"))
        evidence(results=[hit()])
        fake = router(FakeRouter(error=RuntimeError("no provider reachable")))

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "deprecated"
        assert result.verdict_source == "pack_evaluate"
        assert fake.calls == []

    def test_no_llm_call_is_made_at_all(self, gates, packs, monkeypatch, router):
        """The REAL fan-out runs, below the CRAG threshold, and no rewrite fires.

        This is the discriminating assertion: a low top score is exactly the
        condition ``_corrective_pass`` rewrites on, and the rewrite is an LLM
        call. It passes only because resolver passes ``corrective=False``.
        """
        packs(make_pack())
        fake = router(FakeRouter())
        low = hit(score=0.01)
        for name in list(search_service.BACKEND_ADAPTERS):
            monkeypatch.setitem(
                search_service.BACKEND_ADAPTERS, name,
                lambda query, top_k=5, ctx=None, _r=low: BackendResults([_r]),
            )

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "deprecated"
        assert fake.calls == [], "resolve reached the LLM router"

    def test_an_advisory_opinion_cannot_move_the_verdict(self, gates, packs, evidence):
        packs(make_pack(currency_verdict="current", rationale="Approved in the catalog.",
                        evidence_source="catalog:tls12"))
        evidence(results=[
            hit(source_id="sme:1", backend="sme", score=0.0, advisory=True,
                content="In my expert opinion this protocol is deprecated."),
            hit(source_id="rag:7"),
        ])

        result = api.resolve("TLS 1.2", ctx=CortexContext())

        assert result.verdict == "current"
        cited = {c.source_id for c in result.citations}
        assert "sme:1" not in cited, "an advisory opinion was cited as evidence"
        assert "rag:7" in cited
        # Carried, visibly, but not as evidence.
        assert result.metadata["advisory"][0]["citation"]["source_id"] == "sme:1"

    def test_verdict_source_has_no_llm_value(self, gates, packs, evidence):
        packs(make_pack(matches=False))
        evidence()
        assert api.resolve("Unknown Thing", ctx=CortexContext()).verdict_source == "none"

        packs(make_pack())
        evidence(results=[hit()])
        assert api.resolve("TLS 1.1", ctx=CortexContext()).verdict_source == "pack_evaluate"


class TestEntityScope:
    """A DOCUMENT-scoped pack must not answer an ENTITY-scoped question."""

    def test_a_candidate_not_derived_from_the_entity_is_out_of_scope(self):
        from types import SimpleNamespace

        # What evidence_currency emits: it ignores `text` entirely and names the
        # citation, not the prose. Against resolve's synthetic ChunkRef that is
        # an assertion about a document id that is not a document.
        anchor = SimpleNamespace(label="(no evidence anchors)", raw_match="")
        assert resolver.in_scope(anchor, "TLS 1.1") is False

        # What every text-matching pack emits.
        match = SimpleNamespace(label="TLS 1.1", raw_match="TLS 1.1")
        assert resolver.in_scope(match, "The SOP still permits TLS 1.1 here.") is True

    def test_the_scope_filter_is_case_insensitive(self):
        from types import SimpleNamespace

        assert resolver.in_scope(
            SimpleNamespace(label="Telnet", raw_match="Telnet"), "telnet"
        ) is True

    def test_an_out_of_scope_candidate_is_reported_not_silently_dropped(
        self, gates, packs, evidence, monkeypatch
    ):
        pack = make_pack(pack_id="doc_scoped")
        # A pack whose candidate label has nothing to do with the entity.
        monkeypatch.setattr(
            type(pack), "extract",
            lambda self, text, chunk_ref: make_pack(
                pack_id="doc_scoped"
            ).extract("(no evidence anchors)", chunk_ref),
        )
        packs(pack)
        evidence(results=[], errors=[])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "unknown"
        assert result.verdict_source == "none"
        assert result.citations == []
        dropped = result.metadata["out_of_scope"]
        assert dropped and dropped[0]["pack_id"] == "doc_scoped"

    def test_the_real_document_scoped_pack_cannot_fabricate_a_citation(
        self, gates, evidence, monkeypatch
    ):
        """End-to-end against the REAL evidence_currency pack.

        It is the case in_scope exists for, and the failure it prevents is not
        cosmetic: its finding cited ``dic_document:cortex.resolve``, which made
        EVERY resolution carry a citation and therefore report grounded=True.
        """
        from tools.doc_modernization.packs.evidence_currency import EvidenceCurrencyPack

        class _Conn:
            def execute(self, *a, **k):
                raise RuntimeError("no db in this test")

            def rollback(self):
                pass

            def close(self):
                pass

        pack = EvidenceCurrencyPack(config={
            "pack_id": "evidence_currency", "label": "Evidence Currency",
            "entity_types": ["evidence_anchor"],
        })
        monkeypatch.setattr(resolver, "_load_packs", lambda: {pack.pack_id: pack})
        monkeypatch.setattr(resolver, "_evidence_connection", lambda: _Conn())
        evidence(results=[], errors=[])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.citations == []
        assert result.grounded is False
        assert result.metadata["out_of_scope"][0]["pack_id"] == "evidence_currency"


class TestVerdictMapping:
    @pytest.mark.parametrize("pack_verdict,successor,expected", [
        ("current", False, "current"),
        ("current", True, "current"),
        ("deprecated", False, "deprecated"),
        ("deprecated", True, "superseded"),
        ("eol", False, "deprecated"),
        ("eol", True, "superseded"),
        ("retired", True, "superseded"),
        # A disagreement about the fielded estate is not a finding that the
        # entity is stale — promoting it would auto-propose a redline.
        ("divergent", True, "unknown"),
        ("unknown", True, "unknown"),
        # Never guessed upward.
        ("a_seventh_verdict_nobody_mapped", True, "unknown"),
    ])
    def test_map_pack_verdict(self, pack_verdict, successor, expected):
        assert resolver.map_pack_verdict(pack_verdict, successor) == expected

    def test_every_docmod_verdict_is_mapped(self):
        from tools.doc_modernization.constants import CURRENCY_VERDICTS

        unmapped = [v for v in CURRENCY_VERDICTS if v not in resolver.PACK_VERDICT_MAP]
        assert not unmapped, f"docmod verdicts with no resolve mapping: {unmapped}"

    def test_every_mapped_value_is_in_the_closed_vocabulary(self):
        assert set(resolver.PACK_VERDICT_MAP.values()) <= set(RESOLVE_VERDICTS)

    def test_a_finding_is_not_masked_by_a_pack_that_saw_nothing_wrong(
        self, gates, packs, evidence
    ):
        packs(
            make_pack(pack_id="broad", currency_verdict="current",
                      evidence_source="catalog:ok"),
            make_pack(pack_id="crypto", currency_verdict="deprecated",
                      evidence_source="rule:tls-11"),
        )
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "deprecated"
        assert len(result.assessments) == 2

    def test_a_repeated_evidence_source_is_cited_once(self, gates, packs, evidence):
        """The verdict and its replacement often cite the SAME rule."""
        packs(make_pack(evidence_source="rule:tls-11", replacement="TLS 1.3"))
        evidence(results=[])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.text.count("[source: rule:tls-11]") == 1
        assert [c.source_id for c in result.citations] == ["rule:tls-11"]

    def test_a_named_successor_wins_over_a_bare_deprecation(self, gates, packs, evidence):
        packs(
            make_pack(pack_id="a", currency_verdict="deprecated",
                      evidence_source="rule:a"),
            make_pack(pack_id="b", currency_verdict="deprecated",
                      evidence_source="rule:b", replacement="TLS 1.3"),
        )
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "superseded"
        assert "TLS 1.3" in result.text


# ---------------------------------------------------------------------------
# AC4 — citations validate through citation_grounding; an invalid one BLOCKS
# ---------------------------------------------------------------------------
class TestCitations:
    def test_pack_and_backend_evidence_both_become_citations(
        self, gates, packs, evidence
    ):
        packs(make_pack(evidence_source="rule:tls-11"))
        evidence(results=[hit(source_id="rag:7")])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        cited = {c.source_id for c in result.citations}
        assert cited == {"rule:tls-11", "rag:7"}
        assert result.metadata["citation_report"]["valid"] is True
        assert result.grounded is True

    def test_an_unresolvable_citation_blocks(self, gates, packs, evidence):
        """A rationale citing a source the resolution does not hold is REFUSED."""
        packs(make_pack(rationale="Superseded [source: rule:deleted-last-year].",
                        evidence_source="rule:tls-11"))
        evidence(results=[hit()])

        with pytest.raises(CortexResolutionBlocked) as excinfo:
            api.resolve("TLS 1.1", ctx=CortexContext())

        assert excinfo.value.entity == "TLS 1.1"
        assert "rule:deleted-last-year" in excinfo.value.report["hallucinated_citations"]

    def test_a_blocked_resolution_still_audits_the_refusal(self, gates, packs, evidence):
        packs(make_pack(rationale="See [source: nowhere]."))
        evidence(results=[hit()])

        with pytest.raises(CortexResolutionBlocked):
            api.resolve("TLS 1.1", ctx=CortexContext())

        assert len(gates["audit"]) == 1
        assert gates["audit"][0]["operation"] == "cortex.resolve"

    def test_an_ungrounded_resolution_is_not_marked_grounded(
        self, gates, packs, evidence
    ):
        packs(make_pack(matches=False))
        evidence(results=[], errors=[])

        result = api.resolve("Nortel Passport 8600", ctx=CortexContext())

        assert result.citations == []
        assert result.grounded is False


# ---------------------------------------------------------------------------
# AC5 — backend_errors: a backend that DIED vs a corpus that matched nothing
# ---------------------------------------------------------------------------
class TestBackendErrorsAndGaps:
    def test_an_empty_corpus_is_not_an_outage(self, gates, packs, evidence):
        packs(make_pack(matches=False))
        evidence(results=[], errors=[])

        result = api.resolve("Nortel Passport 8600", ctx=CortexContext())

        assert result.backend_errors == []
        assert result.gaps[0]["reasons"] == [resolver.GAP_NO_PACK,
                                             resolver.GAP_NO_EVIDENCE]
        assert result.gaps[0]["backends_failed"] == []

    def test_a_dead_backend_is_not_an_empty_corpus(self, gates, packs, evidence):
        packs(make_pack(matches=False))
        evidence(results=[], errors=[
            {"backend": "rag", "stage": "timeout", "message": "timed out after 10.0s"},
        ])

        result = api.resolve("Nortel Passport 8600", ctx=CortexContext())

        assert resolver.GAP_BACKENDS_FAILED in result.gaps[0]["reasons"]
        assert resolver.GAP_NO_EVIDENCE not in result.gaps[0]["reasons"]
        assert result.gaps[0]["backends_failed"] == ["rag"]
        assert result.backend_errors[0]["stage"] == "timeout"

    def test_a_pack_that_explodes_is_reported_not_swallowed(
        self, gates, packs, evidence
    ):
        packs(make_pack(pack_id="broken", raise_on="evaluate"))
        evidence(results=[], errors=[])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.verdict == "unknown"
        assert resolver.GAP_PACKS_FAILED in result.gaps[0]["reasons"]
        assert result.backend_errors[0]["backend"] == "pack:broken"

    def test_a_resolved_verdict_reports_no_gap(self, gates, packs, evidence):
        packs(make_pack())
        evidence(results=[hit()])

        assert api.resolve("TLS 1.1", ctx=CortexContext()).gaps == []

    def test_conflicts_is_declared_and_empty_until_cef_rsv_02(
        self, gates, packs, evidence
    ):
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())

        assert result.conflicts == []
        assert "conflicts" in result.to_dict()


# ---------------------------------------------------------------------------
# The fan-out: the EXISTING one, with an in-boundary rung set
# ---------------------------------------------------------------------------
class TestFanOut:
    def test_it_uses_the_configured_in_boundary_rungs(self, gates, packs, evidence):
        packs(make_pack())
        seen = evidence(results=[hit()])

        api.resolve("TLS 1.1", "still ok?", ctx=CortexContext())

        assert seen["backends"] == ["currency", "rag", "dic", "graph", "kb"]
        assert seen["corrective"] is False
        assert seen["query"] == "TLS 1.1 still ok?"

    def test_the_external_and_sme_rungs_are_not_in_the_default_set(self):
        assert "external" not in resolver.DEFAULT_RESOLVE_BACKENDS
        assert "sme" not in resolver.DEFAULT_RESOLVE_BACKENDS

    def test_an_unknown_configured_backend_is_dropped_not_fatal(self):
        got = resolver.resolve_backends({"resolve": {"backends": ["rag", "nosuchrung"]}})
        assert got == ["rag"]

    def test_a_deployment_may_declare_its_own_rungs(self):
        got = resolver.resolve_backends({"resolve": {"backends": ["currency"]}})
        assert got == ["currency"]

    def test_the_unreadable_config_fallback_does_not_widen_the_rung_set(self):
        """The default that applies when args/cortex_config.yaml cannot be read.

        This is the one that matters: a permissive fallback applies EXACTLY when
        the operator's declaration is unavailable, so ``external`` must be
        absent from ``tools.cortex.config.CORTEX_CONFIG_DEFAULTS`` too, not only
        from the shipped YAML.
        """
        config = importlib.import_module("tools.cortex.config")

        declared = config.CORTEX_CONFIG_DEFAULTS["resolve"]["backends"]
        assert "external" not in declared
        assert "sme" not in declared
        # An empty/absent declaration falls back to the module default, which is
        # the same set — the two cannot drift apart unnoticed.
        assert resolver.resolve_backends({}) == list(resolver.DEFAULT_RESOLVE_BACKENDS)
        assert declared == list(resolver.DEFAULT_RESOLVE_BACKENDS)

    def test_the_shipped_config_declares_the_same_rungs(self):
        """The shipped YAML and the code fallback agree."""
        config = importlib.import_module("tools.cortex.config")

        shipped = (config.load_cortex_config().get("resolve") or {}).get("backends")
        assert shipped == list(resolver.DEFAULT_RESOLVE_BACKENDS)

    def test_search_rejects_an_unknown_explicit_backend(self):
        with pytest.raises(ValueError, match="Unknown Cortex backend"):
            search_service.search("q", backends=["nosuchrung"])

    def test_the_question_never_reaches_a_pack_extractor(self, gates, packs, evidence):
        """A second entity named in the question must not move the verdict."""
        seen_texts = []

        pack = make_pack()
        original_extract = pack.extract

        def spy(text, chunk_ref):
            seen_texts.append(text)
            return original_extract(text, chunk_ref)

        pack.extract = spy
        packs(pack)
        evidence(results=[hit()])

        result = api.resolve("TLS 1.2", "we replaced TLS 1.0 with it, ok?",
                             ctx=CortexContext())

        assert seen_texts == ["TLS 1.2"]
        assert result.entity == "TLS 1.2"


# ---------------------------------------------------------------------------
# AC6 — MCP verb + REST v1 endpoint registered like the other facades
# ---------------------------------------------------------------------------
class TestSurfaces:
    def test_rest_v1_route_is_registered(self):
        from tools.cortex import rest_v1

        names = []

        class _Recorder:
            def add_url_rule(self, rule, endpoint, view, methods=None):
                names.append((rule, endpoint, tuple(methods or ())))

        rest_v1.register_rest_v1(_Recorder())
        assert ("/api/v1/resolve", "api_v1_resolve", ("POST",)) in names

    def _client(self, authed=True):
        from flask import Flask, g

        from tools.cortex.blueprint import cortex_bp

        app = Flask(__name__)
        app.register_blueprint(cortex_bp)

        @app.before_request
        def _simulate_auth():
            if authed:
                g.current_user = {"id": "u1", "role": "admin", "tenant_id": "t1"}
                g.security_context = {"tenant_id": "t1", "user_id": "u1",
                                      "classification": "CUI"}

        return app.test_client()

    def test_rest_round_trip(self, gates, packs, evidence):
        """The endpoint over a real request, not just a registered rule."""
        packs(make_pack(replacement="TLS 1.3"))
        evidence(results=[hit()])

        resp = self._client().post("/cortex/api/v1/resolve",
                                   json={"entity": "TLS 1.1", "question": "ok?"})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["verdict"] == "superseded"
        assert body["verdict_source"] == "pack_evaluate"
        assert body["entity"] == "TLS 1.1"
        assert sorted(c["source_id"] for c in body["citations"]) ==                ["rag:1", "rule:tls-11"]
        assert body["gaps"] == [] and body["conflicts"] == []
        assert body["backend_errors"] == []

    def test_rest_rejects_a_missing_entity_with_400(self, gates):
        resp = self._client().post("/cortex/api/v1/resolve", json={"question": "ok?"})

        assert resp.status_code == 400
        assert "entity" in resp.get_json()["error"]

    def test_rest_returns_403_on_an_unresolvable_citation(
        self, gates, packs, evidence
    ):
        packs(make_pack(rationale="See [source: nowhere]."))
        evidence(results=[hit()])

        resp = self._client().post("/cortex/api/v1/resolve", json={"entity": "TLS 1.1"})

        assert resp.status_code == 403
        body = resp.get_json()
        assert body["blocked"] is True
        assert body["gate"] == "citation_grounding"
        # WHICH of the three refusals, from resolver's closed BLOCK_*
        # vocabulary (cef-rsv-03) — a renderer bug, a detector bug and a pack
        # bug must not all arrive at the caller as one word.
        assert body["reason"] == "hallucinated_citation"
        assert body["entity"] == "TLS 1.1"
        assert "nowhere" in body["citation_report"]["hallucinated_citations"]

    def test_rest_returns_403_on_an_unbacked_replacement(self, gates, packs, evidence):
        """The second refusal over the wire, distinguishable from the first.

        A pack naming a successor it cannot point at is refused rather than
        having its guess rendered as "Recommended replacement:" — the line a
        redline is drafted from (cef-rsv-03).
        """
        packs(make_pack(replacement="TLS 1.3", evidence_source=""))
        evidence(results=[hit()])

        resp = self._client().post("/cortex/api/v1/resolve", json={"entity": "TLS 1.1"})

        assert resp.status_code == 403
        body = resp.get_json()
        assert body["blocked"] is True
        assert body["reason"] == "unattested_replacement"
        assert body["citation_report"]["successor"] == "TLS 1.3"

    def test_rest_requires_authentication(self, gates):
        resp = self._client(authed=False).post("/cortex/api/v1/resolve",
                                               json={"entity": "TLS 1.1"})

        assert resp.status_code == 401

    def test_rest_endpoint_calls_the_governed_facade(self):
        """The endpoint must reach the GOVERNED facade, never the raw impl.

        Checked two ways, neither of which is object identity (see
        test_the_package_exports_it for why identity is order-dependent here):
        the bound callable carries the governance stamps, and the source-level
        import comes from ``.api`` — the same guard
        test_governance_hardening applies to ask/search.
        """
        from tools.cortex import rest_v1

        assert getattr(rest_v1.resolve, "__cortex_governed__", False) is True
        assert rest_v1.resolve.__cortex_operation__ == "cortex.resolve"

        src = Path(rest_v1.__file__).read_text(encoding="utf-8")
        assert "from .resolver import resolve" not in src
        api_line = next(line for line in src.splitlines()
                        if line.strip().startswith("from .api import"))
        assert "resolve" in api_line

    def test_resolve_is_a_declared_rest_operation_and_scope(self):
        service_keys = importlib.import_module("tools.cortex.service_keys")

        assert "resolve" in service_keys.REST_OPERATIONS
        assert "cortex:resolve" in service_keys.CORTEX_SCOPES
        assert "cortex:resolve" in service_keys.ALL_SCOPES

    def test_cortex_resolve_is_an_issuable_scope_and_a_default_grant(self):
        """A key that can search can resolve: same rungs, strictly less reach.

        Asserted through the ISSUANCE path, not by reading the tuple back — the
        scope vocabulary is validated on issue, so an operation added to
        REST_OPERATIONS without reaching ALL_SCOPES would be rejected there and
        nowhere else.
        """
        service_keys = importlib.import_module("tools.cortex.service_keys")

        assert "cortex:resolve" in service_keys.DEFAULT_SCOPES
        unknown = [s for s in ["cortex:resolve"] if s not in service_keys.ALL_SCOPES]
        assert unknown == []

    def test_the_endpoint_scope_name_matches_the_declared_operation(self):
        """``_scope_denied`` derives the scope from the VIEW FUNCTION's name.

        ``api_v1_resolve`` -> ``cortex:resolve``. A view named anything else
        would demand a scope no key can hold, and the mismatch would only show
        up as a 403 for a correctly-scoped caller.
        """
        from tools.cortex import rest_v1

        service_keys = importlib.import_module("tools.cortex.service_keys")
        derived = rest_v1.api_v1_resolve.__name__.replace("api_v1_", "")
        assert f"cortex:{derived}" in service_keys.CORTEX_SCOPES

    def test_the_validator_exposes_no_backend_selection(self):
        """Egress is a deployment decision, never a request parameter."""
        from tools.cortex import validators

        params = validators.validate_resolve({
            "entity": "TLS 1.1", "backends": ["external"], "strategy": "all",
        })
        assert set(params) == {"entity", "question", "top_k"}

    def test_the_validator_requires_an_entity(self):
        from tools.cortex import validators

        with pytest.raises(validators.CortexValidationError):
            validators.validate_resolve({"question": "is it current?"})

    def test_mcp_tool_is_registered_in_both_surfaces(self):
        from tools.mcp.cortex_server import CORTEX_TOOLS
        from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, TOOL_REGISTRY

        tool = next(t for t in CORTEX_TOOLS if t["name"] == "cortex_resolve")
        assert tool["required"] == ["entity"]
        # The two entry points serve one tool set and must not drift.
        assert "cortex_resolve" in TOOL_REGISTRY
        assert TOOL_REGISTRY["cortex_resolve"]["handler"] == "handle_cortex_resolve"
        assert READ_ONLY_DECLARATIONS["cortex_resolve"] is True

    def test_resolve_is_deliberately_not_a_canvas_chat_mode_yet(self):
        """The gap is a DECISION, not an omission — pinned so it stays one.

        A chat turn carries free-form text and resolve needs an ENTITY, exactly
        like classify / extract / govern, which the chat surface advertises and
        then degrades on. The canvas surface is cef-ui-01 / cef-ui-02. If this
        test starts failing because someone added the mode, they must also have
        added the ``_run_facade`` branch that serves it.
        """
        from tools.cortex import constants

        assert "resolve" not in constants.CORTEX_MODE_KEYS
        assert "resolve" in api.CORTEX_FACADES
        assert "resolve" in Path(constants.__file__).read_text(encoding="utf-8"),             "the deliberate gap must stay documented where the modes are declared"

    def test_mcp_handler_requires_an_entity(self):
        from tools.mcp.cortex_server import handle_cortex_resolve

        out = handle_cortex_resolve({})
        assert out["error"] == "entity is required"
        assert out["verdict"] == "unknown"

    def test_mcp_handler_returns_the_resolution(self, gates, packs, evidence):
        from tools.mcp.cortex_server import handle_cortex_resolve

        packs(make_pack())
        evidence(results=[hit()])

        out = handle_cortex_resolve({"entity": "TLS 1.1", "tenant_id": "t1"})

        assert out["verdict"] == "deprecated"
        assert out["verdict_source"] == "pack_evaluate"
        assert out["classification"]

    def test_mcp_handler_reports_a_block_rather_than_raising(
        self, gates, packs, evidence
    ):
        from tools.mcp.cortex_server import handle_cortex_resolve

        packs(make_pack(rationale="See [source: nowhere]."))
        evidence(results=[hit()])

        out = handle_cortex_resolve({"entity": "TLS 1.1"})

        assert out["blocked"] is True
        assert out["blocked_gate"] == "citation_grounding"
        assert out["reason"] == "hallucinated_citation"


# ---------------------------------------------------------------------------
# Shape / contract
# ---------------------------------------------------------------------------
class TestResolutionShape:
    def test_it_is_a_cortex_result_subclass(self):
        from tools.cortex.schemas import CortexResult

        assert issubclass(CortexResolution, CortexResult)

    def test_it_round_trips(self, gates, packs, evidence):
        packs(make_pack())
        evidence(results=[hit()])

        result = api.resolve("TLS 1.1", ctx=CortexContext())
        clone = CortexResolution.from_dict(result.to_dict())

        assert clone.verdict == result.verdict
        assert clone.entity == result.entity
        assert clone.assessments[0].pack_id == result.assessments[0].pack_id
        assert [c.source_id for c in clone.citations] == \
               [c.source_id for c in result.citations]

    def test_errors_aliases_backend_errors_for_the_cache_degrade_check(self):
        r = CortexResolution(backend_errors=[{"backend": "rag"}])
        assert r.errors == r.backend_errors

    def test_an_empty_entity_is_rejected(self, gates):
        with pytest.raises(ValueError, match="non-empty 'entity'"):
            api.resolve("   ", ctx=CortexContext())

    def test_the_verdict_is_always_in_the_closed_vocabulary(
        self, gates, packs, evidence
    ):
        for pack_verdict in ("current", "deprecated", "eol", "retired",
                             "divergent", "unknown"):
            packs(make_pack(currency_verdict=pack_verdict))
            evidence(results=[hit()])
            assert api.resolve("X", ctx=CortexContext()).verdict in RESOLVE_VERDICTS
