"""cef-di-02 — tech_writing_assist retrieval: the collection scope and the seam.

Two things are pinned here, and they fail in different ways:

* the COLLECTION SCOPE. ``research_and_draft`` accepted ``collection_id`` and
  passed it to nothing, so a section draft could ground itself in — and cite —
  chunks from any collection in the tenant. The tests below prove a draft
  cannot pull another collection's chunks on EITHER retrieval chain, that the
  scope is passed down natively as well as enforced here, and that an
  unverifiable scope admits nothing rather than everything.
* the ROLLBACK CONTRACT. With ``cortex.enabled`` false the governed seam is
  never consulted and the legacy chain runs unchanged, which is what makes
  "flip the flag" a real alternative to reverting a merge.

No test here touches a real database, a real retriever or a real LLM: the
scope's only substrate read (``_collection_doc_ids``) and the Cortex seam are
both patched, so a failure is about this module and never about the corpus.
"""
import importlib

import pytest

twa = importlib.import_module("tools.document_intelligence.tech_writing_assist")


# ── Doubles ──────────────────────────────────────────────────────────────────

class _Chunk:
    """A rag SearchResult-shaped hit. `source_id` is the doc id a real one carries."""

    def __init__(self, chunk_id, source_id, content, score=0.9):
        self.chunk_id = chunk_id
        self.source_id = source_id
        self.content = content
        self.score = score
        self.source_table = "dic_documents"


class _Citation:
    """A cortex Citation-shaped evidence pointer."""

    def __init__(self, source_id, snippet, source_type="rag_chunk", title=""):
        self.source_id = source_id
        self.snippet = snippet
        self.source_type = source_type
        self.source_table = "dic_documents"
        self.title = title or source_id


class _Resolution:
    def __init__(self, citations, verdict="unknown", conflicts=None):
        self.citations = list(citations)
        self.verdict = verdict
        self.verdict_source = "pack_evaluate" if verdict != "unknown" else "none"
        self.gaps = []
        self.conflicts = list(conflicts or [])
        self.backends_consulted = ["rag", "dic"]
        self.backend_errors = []


#: Collection A owns doc-a1/doc-a2; collection B owns doc-b1. The corpora are
#: deliberately overlapping in TEXT so a draft that pulls the wrong one still
#: looks plausible — which is what made the defect invisible.
_MEMBERSHIP = {"coll-a": {"doc-a1", "doc-a2"}, "coll-b": {"doc-b1"}}

_ALL_CHUNKS = [
    _Chunk("chunk-a1", "doc-a1", "Collection A says BGP peers with the edge."),
    _Chunk("chunk-b1", "doc-b1", "Collection B says BGP peers with the core."),
]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No config file, no DB, no web, no LLM — every seam explicit."""
    monkeypatch.setattr(twa, "_tw_config_cache", {}, raising=False)
    monkeypatch.setattr(twa, "fetch_content", None)
    monkeypatch.setattr(twa, "is_airgap", lambda **kw: True)
    monkeypatch.setattr(twa, "LLMRouter", None)
    monkeypatch.setattr(twa, "LLMRequest", None)
    monkeypatch.setattr(twa, "kg_retrieve", None)
    monkeypatch.setattr(
        twa, "_collection_doc_ids", lambda cid: _MEMBERSHIP.get(cid), raising=True
    )
    yield
    twa._tw_config_cache = None


@pytest.fixture()
def legacy_rag(monkeypatch):
    """A retriever that IGNORES project_id, and records what it was passed.

    Ignoring it is the point: the surface-level predicate has to hold even when
    the backend does not honour the scope, which is the case the Cortex fan-out
    (no collection filter at all) actually is.
    """
    seen: dict = {}

    class FakeRetriever:
        def __init__(self, tenant_id="default"):
            seen["tenant_id"] = tenant_id

        def search(self, query, **kwargs):
            seen.update(kwargs)
            return list(_ALL_CHUNKS)

    monkeypatch.setattr(twa, "RAGRetriever", FakeRetriever)
    return seen


def _enable_cortex(monkeypatch, enabled=True):
    monkeypatch.setattr(twa, "_tw_config_cache", {"cortex": {"enabled": enabled}})


def _stub_resolve(monkeypatch, resolution, seen=None):
    """Patch tools.cortex.api.resolve, which _cortex_retrieve late-imports."""
    import tools.cortex.api as cortex_api

    def _fake(entity, question="", ctx=None, top_k=5):
        if seen is not None:
            seen.update({"entity": entity, "question": question, "ctx": ctx, "top_k": top_k})
        return resolution

    monkeypatch.setattr(cortex_api, "resolve", _fake)


# ── The defect: collection_id was accepted and never passed ──────────────────

def test_legacy_chain_cannot_pull_another_collections_chunks(legacy_rag):
    """A draft scoped to coll-a must not see, register or cite coll-b's chunk."""
    res = twa.research_and_draft(
        query="BGP", section_heading="Overview", collection_id="coll-a",
    )
    assert res.retrieval_path == "legacy"
    doc_ids = {c["doc_id"] for c in res.rag_chunks}
    assert doc_ids == {"doc-a1"}
    assert all("Collection B" not in s.get("label", "") for s in res.sources)
    assert [s["ref"] for s in res.sources] == ["chunk-a1"]
    assert res.scope["enforced"] is True
    assert res.scope["dropped"] == 1
    assert res.scope["dropped_by_type"] == {"rag_chunk": 1}


def test_legacy_chain_passes_the_scope_down_natively(legacy_rag):
    """Enforcing here is not a licence to stop scoping the query itself."""
    twa.research_and_draft(query="BGP", section_heading="X", collection_id="coll-a")
    assert legacy_rag["project_id"] == "coll-a"


def test_no_collection_id_requests_no_scope_and_changes_nothing(legacy_rag):
    """The unscoped call must be byte-for-byte what it was before cef-di-02."""
    res = twa.research_and_draft(query="BGP", section_heading="Overview")
    assert {c["doc_id"] for c in res.rag_chunks} == {"doc-a1", "doc-b1"}
    assert "project_id" not in legacy_rag
    assert res.scope["requested"] is False
    assert res.scope["dropped"] == 0


def test_kg_lane_is_scoped_by_graph_project(monkeypatch):
    """graph_rag is scoped by kg_graphs.project_id — pass it or it sees every graph."""
    seen: dict = {}

    def _kg(query, **kwargs):
        seen.update(kwargs)
        return {"nodes": []}

    monkeypatch.setattr(twa, "RAGRetriever", None)
    monkeypatch.setattr(twa, "kg_retrieve", _kg)
    twa.research_and_draft(query="BGP", section_heading="X", collection_id="coll-a")
    assert seen["project_id"] == "coll-a"


def test_unverifiable_scope_admits_nothing(monkeypatch, legacy_rag):
    """Membership unreadable -> fail closed, and say so. Never fail open."""
    monkeypatch.setattr(twa, "_collection_doc_ids", lambda cid: None)
    res = twa.research_and_draft(
        query="BGP", section_heading="Overview", collection_id="coll-a",
    )
    assert res.rag_chunks == []
    assert res.sources == []
    assert res.scope["enforced"] is False
    assert any("could not be verified" in w for w in res.warnings)


def test_empty_collection_admits_nothing_but_is_not_an_error(monkeypatch, legacy_rag):
    """`set()` and `None` are different answers and must not be merged."""
    monkeypatch.setattr(twa, "_collection_doc_ids", lambda cid: set())
    res = twa.research_and_draft(
        query="BGP", section_heading="Overview", collection_id="coll-empty",
    )
    assert res.rag_chunks == []
    assert res.scope["enforced"] is True
    assert res.scope["in_collection"] == 0
    assert not any("could not be verified" in w for w in res.warnings)


# ── The seam: retrieval through cortex.resolve() ─────────────────────────────

def test_cortex_toggle_off_never_consults_the_seam(monkeypatch, legacy_rag):
    """The rollback contract: off means the seam is not called at all."""
    import tools.cortex.api as cortex_api

    def _boom(*a, **kw):
        raise AssertionError("cortex.resolve must not be consulted when disabled")

    monkeypatch.setattr(cortex_api, "resolve", _boom)
    res = twa.research_and_draft(query="BGP", section_heading="X", collection_id="coll-a")
    assert res.retrieval_path == "legacy"
    assert res.resolution == {}


def test_cortex_path_retrieves_and_registers_citable_sources(monkeypatch):
    """Toggle on -> evidence comes from resolve(), in the same source register."""
    _enable_cortex(monkeypatch)
    seen: dict = {}
    _stub_resolve(monkeypatch, _Resolution([
        _Citation("doc-a1", "Collection A says BGP peers with the edge."),
    ]), seen)
    res = twa.research_and_draft(
        query="BGP", section_heading="Overview", collection_id="coll-a",
    )
    assert res.retrieval_path == "cortex"
    assert [s["id"] for s in res.sources] == ["1"]
    assert res.sources[0]["ref"] == "doc-a1"
    assert seen["entity"] == "BGP" and seen["question"] == "Overview"
    assert seen["ctx"].domain == "document_intelligence"


def test_cortex_path_cannot_pull_another_collections_chunks(monkeypatch):
    """The Cortex fan-out has no collection filter; this surface supplies it."""
    _enable_cortex(monkeypatch)
    _stub_resolve(monkeypatch, _Resolution([
        _Citation("doc-a1", "Collection A says BGP peers with the edge."),
        _Citation("doc-b1", "Collection B says BGP peers with the core."),
    ]))
    res = twa.research_and_draft(
        query="BGP", section_heading="Overview", collection_id="coll-a",
    )
    assert {c["doc_id"] for c in res.rag_chunks} == {"doc-a1"}
    assert [s["ref"] for s in res.sources] == ["doc-a1"]
    assert res.scope["dropped"] == 1


def test_cortex_path_over_fetches_for_the_post_filter_but_never_widens_scope(monkeypatch):
    """scope_overfetch is a recall knob: more hits asked for, same scope applied."""
    _enable_cortex(monkeypatch)
    monkeypatch.setattr(twa, "_tw_config_cache", {
        "cortex": {"enabled": True, "top_k": 5, "scope_overfetch": 4, "max_top_k": 40},
    })
    seen: dict = {}
    _stub_resolve(monkeypatch, _Resolution([_Citation("doc-b1", "outside")]), seen)
    res = twa.research_and_draft(
        query="BGP", section_heading="X", collection_id="coll-a",
    )
    assert seen["top_k"] == 20
    assert res.rag_chunks == []


def test_cortex_refusal_reports_and_does_not_fall_back_to_the_legacy_chain(
    monkeypatch, legacy_rag,
):
    """A governance chain you can route around by failing is decoration."""
    _enable_cortex(monkeypatch)
    import tools.cortex.api as cortex_api

    class _Blocked(RuntimeError):
        reason = "hallucinated_citation"

    def _refuse(*a, **kw):
        raise _Blocked("refused")

    monkeypatch.setattr(cortex_api, "resolve", _refuse)
    res = twa.research_and_draft(query="BGP", section_heading="X", collection_id="coll-a")
    assert res.retrieval_path == "cortex"
    assert res.rag_chunks == [] and res.sources == []
    assert res.resolution["blocked"] == "hallucinated_citation"
    assert any("refused or failed" in w for w in res.warnings)


def test_cortex_verdict_and_conflicts_are_surfaced_not_injected(monkeypatch):
    """The deterministic verdict is a finding about the subject, not prompt text."""
    _enable_cortex(monkeypatch)
    _stub_resolve(monkeypatch, _Resolution(
        [_Citation("doc-a1", "TLS 1.1 is configured on the edge.")],
        verdict="deprecated",
        conflicts=[{"kind": "status", "entity_label": "TLS 1.1", "sides": []}],
    ))
    res = twa.research_and_draft(
        query="TLS 1.1", section_heading="Crypto", collection_id="coll-a",
    )
    assert res.resolution["verdict"] == "deprecated"
    assert any("is deprecated" in w for w in res.warnings)
    assert any("disagree about this subject" in w for w in res.warnings)


# ── The invariants the migration must not break ──────────────────────────────

def test_airgap_still_fails_safe_on_both_chains(monkeypatch, legacy_rag):
    """A failing air-gap probe means air-gapped, and no web source is fetched."""
    fetched: list = []

    def _explode(**kwargs):
        raise RuntimeError("detector down")

    monkeypatch.setattr(twa, "is_airgap", _explode)
    monkeypatch.setattr(twa, "fetch_content", lambda url: fetched.append(url) or "x")
    for enabled in (False, True):
        _enable_cortex(monkeypatch, enabled)
        if enabled:
            _stub_resolve(monkeypatch, _Resolution([]))
        res = twa.research_and_draft(
            query="BGP", section_heading="X", collection_id="coll-a",
            web_urls=["https://example.invalid/a"],
        )
        assert res.is_airgap is True
        assert res.web_sources == []
    assert fetched == []


def test_post_checks_still_run_on_the_cortex_path(monkeypatch):
    """Placeholders, standards references and the citation report all still fire."""
    _enable_cortex(monkeypatch)
    _stub_resolve(monkeypatch, _Resolution([_Citation("doc-a1", "Edge routing.")]))
    draft = (
        "Configure the [PLACEHOLDER] per NIST SP 800-9999 [source: 7].\n"
        "## References\nNIST SP 800-9999\n"
    )

    import tools.cortex.api as cortex_api
    from tools.cortex.schemas import CortexResult

    monkeypatch.setattr(twa, "LLMRouter", object)
    monkeypatch.setattr(twa, "LLMRequest", object)
    monkeypatch.setattr(
        cortex_api, "complete",
        lambda *a, **kw: CortexResult(text=draft),
    )
    res = twa.research_and_draft(
        query="BGP", section_heading="Overview", collection_id="coll-a",
    )
    assert res.draft_content == draft.strip()
    assert any("Unresolved placeholders" in w for w in res.warnings)
    assert any("NIST" in w and "whitelist" in w for w in res.warnings)
    assert res.citation_report.get("hallucinated_citations") == ["7"]
    assert any("never retrieved" in w for w in res.warnings)


def test_backend_outage_is_not_reported_as_an_empty_collection(monkeypatch):
    """A dead rung and a corpus that matched nothing are different answers."""
    _enable_cortex(monkeypatch)
    resolution = _Resolution([])
    resolution.backend_errors = [{"backend": "rag", "stage": "timeout", "message": "10s"}]
    _stub_resolve(monkeypatch, resolution)
    res = twa.research_and_draft(
        query="BGP", section_heading="X", collection_id="coll-a",
    )
    assert res.sources == []
    assert any("outage, not an empty collection" in w for w in res.warnings)
