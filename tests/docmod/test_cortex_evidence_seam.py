# CUI // SP-CTI
"""cef-di-01 — DocMod evidence through the governed cortex.resolve() seam.

The four things this file is here to hold down, one per acceptance criterion:

1. scanner.py and the packs obtain evidence through the seam, BEHIND A TOGGLE.
2. Pack verdicts stay DETERMINISTIC — evaluate() is not LLM-driven, and no
   claim an LLM could have authored can reach a verdict.
3. Toggle-on and toggle-off produce the SAME finding for the same evidence, so
   a rescan is comparable to the pre-migration run.
4. Toggle off restores the legacy path EXACTLY — cortex is not called at all.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from tools.doc_modernization import evidence as seam
from tools.doc_modernization.base_pack import CandidateEntity, ChunkRef

_REF = ChunkRef(doc_id="doc-di", version_id="doc-di_v1", section="Hardware")

# A model no catalog and no EOL feed knows, so NetworkHardwarePack.evaluate()
# always reaches step 3 — the one lookup this card migrated.
_UNKNOWN_MODEL = "Zeta ZX-9911 Aggregator"

_ON = {"cortex": {"enabled": True, "top_k": 3, "max_resolves_per_run": 5}}
_OFF = {"cortex": {"enabled": False}}

#: The store's answer for _UNKNOWN_MODEL, in entity_currency.resolve()'s shape.
#: A verdict word with NO date — the case the pack honours explicitly.
_STORE_HIT = {
    "verdict": "end_of_life",
    "eol_date": None,
    "eos_date": None,
    "superseded_by": "",
    "source": "vendor_catalog",
    "as_of": "2025-03-01",
    "confidence": 0.9,
    "authoritative": True,
    "conflict": False,
}


class _Citation:
    def __init__(self, source_id="", source_type="", source_table="", title="",
                 snippet="", provenance_id=""):
        self.source_id = source_id
        self.source_type = source_type
        self.source_table = source_table
        self.title = title
        self.snippet = snippet
        self.provenance_id = provenance_id


class _Resolution:
    """The subset of CortexResolution the seam reads."""

    def __init__(self, claims=(), citations=()):
        self.citations = list(citations)
        self.backends_consulted = ["currency", "rag", "dic", "graph", "kb"]
        self.backend_errors = []
        self.metadata = {"entity_resolution": {"claims": list(claims)}}


def _structured_claim(**over):
    """A `currency`-backend structured claim carrying the same fields the store
    published — this is what cef-bck-01 puts on CortexSearchResult.metadata and
    cef-rsv-02 turns into an EntityClaim."""
    claim = {
        "entity_label": _UNKNOWN_MODEL,
        "entity_type": "hardware_model",
        "status": "deprecated",
        "raw_status": "end_of_life",
        "superseded_by": "",
        "eol_date": "",
        "eos_date": "",
        "backend": "currency",
        "source": "vendor_catalog",
        "authoritative": True,
        "confidence": 0.9,
        "as_of": "2025-03-01",
        "extraction": "structured",
    }
    claim.update(over)
    return claim


@pytest.fixture()
def stub_cortex(monkeypatch):
    """Install a fake `cortex.api.resolve` and record every call it received."""
    calls = []

    def _install(resolution):
        import tools.cortex.api as api

        def _fake(entity, question="", ctx=None, top_k=5):
            calls.append({"entity": entity, "question": question, "top_k": top_k})
            if isinstance(resolution, Exception):
                raise resolution
            return resolution

        monkeypatch.setattr(api, "resolve", _fake, raising=False)
        return calls

    _install.calls = calls
    return _install


@pytest.fixture(autouse=True)
def _fresh_run():
    seam.reset_run_state()
    yield
    seam.reset_run_state()


# ---------------------------------------------------------------------------
# AC1 — the seam exists, and it is behind a toggle
# ---------------------------------------------------------------------------
def test_toggle_defaults_off_in_the_shipped_config():
    """The migration ships OFF. Nobody gets a five-backend fan-out by upgrading."""
    from tools.doc_modernization.pack_loader import load_config

    assert seam.cortex_enabled(load_config()) is False
    assert seam.cortex_enabled({}) is False


def test_seam_is_not_consulted_when_the_toggle_is_off(stub_cortex):
    calls = stub_cortex(_Resolution(claims=[_structured_claim()]))
    assert seam.resolve_evidence(_UNKNOWN_MODEL, config=_OFF) is None
    assert calls == [], "cortex.resolve was called with the toggle off"


def test_seam_returns_governed_evidence_when_the_toggle_is_on(stub_cortex):
    calls = stub_cortex(_Resolution(
        claims=[_structured_claim()],
        citations=[_Citation(source_id="ec-1", source_type="currency_assertion",
                             source_table="entity_currency", title=_UNKNOWN_MODEL,
                             snippet="end of life per vendor_catalog")],
    ))
    bundle = seam.resolve_evidence(
        _UNKNOWN_MODEL, entity_type="hardware_model", config=_ON,
    )
    assert bundle is not None and not bundle.is_empty
    assert bundle.currency and bundle.citations[0]["source"].startswith("cortex:")
    assert calls[0]["entity"] == _UNKNOWN_MODEL
    assert calls[0]["top_k"] == 3, "top_k must come from the docmod toggle block"


def test_one_resolution_per_entity_per_run(stub_cortex):
    calls = stub_cortex(_Resolution(claims=[_structured_claim()]))
    for _ in range(4):
        seam.resolve_evidence(_UNKNOWN_MODEL, entity_type="hardware_model", config=_ON)
    assert len(calls) == 1, "the per-run memo cache did not hold"
    assert seam.run_stats()["resolutions"] == 1


def test_outbound_budget_is_reported_not_silent(stub_cortex):
    """A bounded sweep must not read like a complete one."""
    stub_cortex(_Resolution(claims=[_structured_claim()]))
    config = {"cortex": {"enabled": True, "max_resolves_per_run": 1}}
    assert seam.resolve_evidence("entity one", config=config) is not None
    assert seam.resolve_evidence("entity two", config=config) is None
    assert seam.run_stats() == {"resolutions": 1, "capped": 1, "cached_entities": 1}


def test_a_refused_resolution_degrades_and_says_so(stub_cortex):
    from tools.cortex.resolver import CortexResolutionBlocked

    stub_cortex(CortexResolutionBlocked("nope", entity=_UNKNOWN_MODEL))
    bundle = seam.resolve_evidence(_UNKNOWN_MODEL, config=_ON)
    assert bundle is not None
    assert bundle.blocked == "hallucinated_citation"
    assert bundle.is_empty, "a refusal must carry no evidence"
    assert seam.currency_assertion(bundle) is None


def test_re_entrant_ask_returns_none_instead_of_recursing(stub_cortex):
    """cortex.resolve runs the packs. A pack asking the seam from inside one
    would recurse without bound; the thread-local guard is what stops it."""
    seen = []

    def _reentrant_resolution(entity, question="", ctx=None, top_k=5):
        # This is what resolver.assess -> pack.evaluate -> the seam looks like.
        seen.append(seam.resolve_evidence("inner entity", config=_ON))
        return _Resolution(claims=[_structured_claim()])

    import tools.cortex.api as api

    original = getattr(api, "resolve", None)
    api.resolve = _reentrant_resolution
    try:
        assert seam.resolve_evidence(_UNKNOWN_MODEL, config=_ON) is not None
    finally:
        if original is not None:
            api.resolve = original
    assert seen == [None], "the re-entrancy guard did not fire"


# ---------------------------------------------------------------------------
# AC2 — the verdict stays deterministic, and evaluate() is not LLM-driven
# ---------------------------------------------------------------------------
def test_only_structured_claims_can_become_a_verdict():
    """A claim read off a document's PROSE, or a pack's own verdict coming back
    around, must never be handed to evaluate(). This is the whole TRUST rule."""
    prose = _structured_claim(extraction="text_pattern", backend="rag")
    pack_echo = _structured_claim(extraction="pack", backend="")

    resolution = _Resolution(claims=[prose, pack_echo])
    assert seam._currency_lane(resolution) == []

    with_structured = _Resolution(claims=[prose, _structured_claim(), pack_echo])
    lane = seam._currency_lane(with_structured)
    assert [c["extraction"] for c in lane] == ["structured"]


def test_advisory_and_prose_claims_never_reach_currency_assertion(stub_cortex):
    stub_cortex(_Resolution(claims=[
        _structured_claim(extraction="text_pattern", backend="rag",
                          raw_status="end_of_life"),
    ]))
    bundle = seam.resolve_evidence(_UNKNOWN_MODEL, config=_ON)
    assert bundle is not None
    assert seam.currency_assertion(bundle) is None, (
        "a prose-derived claim was allowed to become a pack's evidence"
    )


def test_pack_evaluate_bodies_name_no_llm():
    """Structural half: no pack's evaluate()/recommend() mentions an LLM.

    Cheap, durable, and it fails the moment somebody reaches for a model call
    inside the one method base_pack requires to be deterministic.
    """
    banned = {"llmrouter", "llmrequest", "llm_router", "completion", "chat_completion",
              "openai", "anthropic", "ollama", "cortex_ask", "cortex_complete"}
    pack_dir = pathlib.Path(__file__).resolve().parents[2] / "tools" / "doc_modernization"
    offenders = []
    for path in sorted((pack_dir / "packs").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in (
                "evaluate", "recommend", "_currency_hit", "_kg_corroboration"
            ):
                continue
            words = {
                n.id.casefold() for n in ast.walk(node) if isinstance(n, ast.Name)
            } | {
                n.attr.casefold() for n in ast.walk(node) if isinstance(n, ast.Attribute)
            }
            hit = words & banned
            if hit:
                offenders.append(f"{path.name}::{node.name} -> {sorted(hit)}")
    assert offenders == [], offenders


def test_network_pack_verdict_is_identical_on_both_paths(monkeypatch, stub_cortex):
    """AC3 in miniature: same evidence, same verdict, whichever seam supplied it.

    The LLM router is armed to RAISE for the whole test, so a model call
    anywhere on either path fails the assertion rather than passing quietly.
    """
    import tools.currency.entity_currency as store
    import tools.llm.router as router
    from tools.doc_modernization.packs.network_hardware import NetworkHardwarePack

    def _no_llm(*a, **k):
        raise AssertionError("an LLM was constructed on the deterministic path")

    monkeypatch.setattr(router.LLMRouter, "__init__", _no_llm, raising=False)

    entity = CandidateEntity(
        label=_UNKNOWN_MODEL, entity_type="hardware_model",
        pack_id="network_hardware", chunk_ref=_REF, raw_match=_UNKNOWN_MODEL,
    )
    pack = NetworkHardwarePack(config={"pack_id": "network_hardware"})

    # Legacy path — the store answers directly, cortex is never consulted.
    monkeypatch.setattr(store, "resolve", lambda *a, **k: dict(_STORE_HIT))
    monkeypatch.setattr(seam, "cortex_enabled", lambda config=None: False)
    legacy = pack.evaluate(entity, conn=None)

    # Migrated path — the SAME facts arrive as a governed structured claim.
    stub_cortex(_Resolution(claims=[_structured_claim()]))
    monkeypatch.setattr(seam, "cortex_enabled", lambda config=None: True)
    monkeypatch.setattr(seam, "cortex_config", lambda config=None: _ON["cortex"])
    monkeypatch.setattr(
        store, "resolve",
        lambda *a, **k: pytest.fail("the legacy store was consulted with the seam on"),
    )
    seam.reset_run_state()
    migrated = pack.evaluate(entity, conn=None)

    assert legacy.currency_verdict == migrated.currency_verdict == "eol"
    assert legacy.finding_type == migrated.finding_type == "eol_hardware"
    assert legacy.severity == migrated.severity == "high"
    assert legacy.confidence == migrated.confidence == pytest.approx(0.9)
    assert legacy.rationale == migrated.rationale
    # Only the PROVENANCE of the evidence differs — which is the point.
    assert [e.get("via") for e in legacy.evidence] == ["entity_currency"]
    assert [e.get("via") for e in migrated.evidence] == ["cortex.resolve"]


# ---------------------------------------------------------------------------
# AC4 — toggle off restores the legacy path exactly
# ---------------------------------------------------------------------------
def test_network_pack_uses_the_store_directly_when_the_toggle_is_off(
    monkeypatch, stub_cortex
):
    import tools.currency.entity_currency as store
    from tools.doc_modernization.packs.network_hardware import NetworkHardwarePack

    calls = stub_cortex(_Resolution(claims=[_structured_claim()]))
    monkeypatch.setattr(seam, "cortex_enabled", lambda config=None: False)
    asked = []
    monkeypatch.setattr(
        store, "resolve",
        lambda label, **k: asked.append(label) or dict(_STORE_HIT),
    )
    pack = NetworkHardwarePack(config={"pack_id": "network_hardware"})
    hit, via = pack._currency_hit(_UNKNOWN_MODEL, conn=None)

    assert via == "entity_currency" and hit["verdict"] == "end_of_life"
    assert asked == [_UNKNOWN_MODEL]
    assert calls == [], "cortex.resolve ran with the toggle off"


def test_policy_pack_runs_the_kg_select_when_the_toggle_is_off(monkeypatch, stub_cortex):
    from tools.doc_modernization.packs.policy_refs import PolicyRefsPack

    calls = stub_cortex(_Resolution(claims=[]))
    monkeypatch.setattr(seam, "cortex_enabled", lambda config=None: False)

    executed = []

    class _Conn:
        def execute(self, sql, params=()):
            executed.append(sql)
            return self

        def fetchone(self):
            return {"id": "kg-77"}

    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    entity = CandidateEntity(label="SP 800-53", entity_type="standard",
                             pack_id="policy_refs", chunk_ref=_REF)
    out = pack._kg_corroboration(entity, _Conn())

    assert out == [{"source": "kg:kg-77",
                    "detail": "KG standard node for SP 800-53", "date": ""}]
    assert executed and "kg_nodes" in executed[0]
    assert calls == [], "cortex.resolve ran with the toggle off"


def test_policy_pack_takes_kg_corroboration_from_the_seam_when_on(
    monkeypatch, stub_cortex
):
    from tools.doc_modernization.packs.policy_refs import PolicyRefsPack

    stub_cortex(_Resolution(citations=[
        _Citation(source_id="kg-42", source_type="kg_node", source_table="kg_nodes",
                  title="SP 800-53", snippet="standard node"),
    ]))
    monkeypatch.setattr(seam, "cortex_enabled", lambda config=None: True)
    monkeypatch.setattr(seam, "cortex_config", lambda config=None: _ON["cortex"])

    class _Conn:
        def execute(self, *a, **k):
            pytest.fail("the hand-written kg_nodes SELECT ran with the seam on")

    pack = PolicyRefsPack(config={"pack_id": "policy_refs"})
    entity = CandidateEntity(label="SP 800-53", entity_type="standard",
                             pack_id="policy_refs", chunk_ref=_REF)
    out = pack._kg_corroboration(entity, _Conn())

    assert out == [{"source": "cortex:kg_node:kg-42",
                    "detail": "SP 800-53", "date": ""}]


def test_scanner_enrichment_is_off_with_the_toggle_and_adds_nothing(monkeypatch):
    from tools.doc_modernization import scanner

    assert scanner._enrich_findings_enabled(_OFF) is False
    assert scanner._enrich_findings_enabled(_ON) is True
    # enrich_findings can be taken down on its own without disabling the
    # pack-level lookups — different blast radius, different switch.
    assert scanner._enrich_findings_enabled(
        {"cortex": {"enabled": True, "enrich_findings": False}}
    ) is False


def test_scanner_enrichment_adds_citations_and_moves_no_verdict(
    monkeypatch, stub_cortex
):
    from tools.doc_modernization import scanner
    from tools.doc_modernization.base_pack import Verdict

    stub_cortex(_Resolution(citations=[
        _Citation(source_id="rag-9", source_type="rag_chunk", source_table="rag_chunks",
                  title="Network Standard", snippet="Zeta ZX-9911 is retired"),
    ]))
    monkeypatch.setattr(seam, "cortex_enabled", lambda config=None: True)
    monkeypatch.setattr(seam, "cortex_config", lambda config=None: _ON["cortex"])

    entity = CandidateEntity(label=_UNKNOWN_MODEL, entity_type="hardware_model",
                             pack_id="network_hardware", chunk_ref=_REF)
    verdict = Verdict(
        currency_verdict="eol", finding_type="eol_hardware", severity="high",
        rationale="retired", confidence=1.0,
        evidence=[{"source": "catalog:1", "detail": "curated", "date": ""}],
    )
    before = scanner.dedupe_key("doc-di", "network_hardware", entity.label,
                                verdict.finding_type)

    via = scanner._enrich_evidence(verdict, entity, tenant_id=None, classification="CUI")

    assert via == "cortex.resolve"
    assert verdict.currency_verdict == "eol" and verdict.severity == "high"
    assert verdict.confidence == 1.0
    assert scanner.dedupe_key("doc-di", "network_hardware", entity.label,
                              verdict.finding_type) == before
    sources = [e["source"] for e in verdict.evidence]
    assert sources == ["catalog:1", "cortex:rag_chunk:rag-9"]
    assert verdict.evidence[-1]["via"] == "cortex.resolve"


def test_scanner_enrichment_never_raises_when_cortex_explodes(monkeypatch):
    from tools.doc_modernization import scanner
    from tools.doc_modernization.base_pack import Verdict

    def _boom(*a, **k):
        raise RuntimeError("cortex is down")

    monkeypatch.setattr(seam, "resolve_evidence", _boom)
    verdict = Verdict(currency_verdict="eol", finding_type="eol_hardware",
                      evidence=[{"source": "catalog:1", "detail": "", "date": ""}])
    entity = CandidateEntity(label=_UNKNOWN_MODEL, entity_type="hardware_model",
                             pack_id="network_hardware", chunk_ref=_REF)

    assert scanner._enrich_evidence(verdict, entity, None, "CUI") == ""
    assert verdict.evidence == [{"source": "catalog:1", "detail": "", "date": ""}]
