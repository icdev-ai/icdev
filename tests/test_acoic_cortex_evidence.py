# CUI // SP-CTI
"""cef-di-03 — acoic SSP evidence through the governed cortex.resolve() seam.

One test per acceptance criterion, plus the failure modes that decide whether
this migration can take DocDrift offline:

1. acoic retrieval flows through ``cortex.resolve()`` BEHIND A TOGGLE, and with
   the toggle off Cortex is not consulted at all.
2. The deterministic RICOAS/NIST crosswalk and the cited-template fallback both
   still work with NO LLM available — asserted by arming the router to raise.
3. Every degradation (re-entrancy, spent budget, absent Cortex, a governance
   refusal) lands on the legacy path rather than on an exception.
4. The provenance the legacy path could not give — a ``[SOURCE-N]`` that
   resolves to a real source id — is persisted on the fragment.
"""
from __future__ import annotations

import json

import pytest

from tools.document_intelligence import acoic

# The CANONICAL module, not the `tools.document_intelligence.ssp_evidence`
# re-export. Patching the re-export would set an attribute on a namespace the
# seam's own functions never read — they resolve `load_config` out of the
# canonical module's globals — so the patch would silently do nothing.
from icdev.tools.document_intelligence import ssp_evidence as seam

_ON = {"cortex": {"enabled": True, "top_k": 5, "max_resolves_per_run": 5,
                  "fallback_on_empty": True}}
_ON_NO_FALLBACK = {"cortex": {"enabled": True, "top_k": 5,
                              "max_resolves_per_run": 5,
                              "fallback_on_empty": False}}
_OFF = {"cortex": {"enabled": False}}


class _Citation:
    """The subset of ``cortex.schemas.Citation`` the seam reads."""

    def __init__(self, source_id="", source_type="", source_table="", title="",
                 snippet="", provenance_id=""):
        self.source_id = source_id
        self.source_type = source_type
        self.source_table = source_table
        self.title = title
        self.snippet = snippet
        self.provenance_id = provenance_id


class _Resolution:
    """The subset of ``CortexResolution`` the seam reads."""

    def __init__(self, citations=None, backends=None, errors=None, verdict="unknown"):
        self.citations = list(citations or [])
        self.backends_consulted = list(backends or ["currency", "rag", "dic"])
        self.backend_errors = list(errors or [])
        self.verdict = verdict


def _rag_citation(n: int) -> _Citation:
    return _Citation(
        source_id=f"chunk-{n}",
        source_type="rag_chunk",
        source_table="rag_chunks",
        title=f"AC-2 Account Management ({n})",
        snippet=f"Accounts are provisioned through the central IdP, evidence {n}.",
        provenance_id=f"prov-{n}",
    )


class _ResolveRecorder(list):
    """The calls the seam made, plus the answer the next one gets.

    A list subclass so a test can assert on it directly (``len``, ``== []``)
    while still carrying the mutable answer slot a test needs to swap.
    """

    def __init__(self):
        super().__init__()
        self.state = {
            "resolution": _Resolution(citations=[_rag_citation(1), _rag_citation(2)])
        }


@pytest.fixture(autouse=True)
def _fresh_run():
    """Each test starts with an empty memo cache and a full budget."""
    seam.reset_run_state()
    yield
    seam.reset_run_state()


@pytest.fixture
def legacy_calls(monkeypatch):
    """Record every legacy ``_retrieve_evidence`` call and answer deterministically."""
    calls: list = []

    def _fake(query, tenant_id, k=5):
        calls.append({"query": query, "tenant_id": tenant_id, "k": k})
        return [f"LEGACY chunk about {query} [{i}]" for i in range(1, 3)]

    monkeypatch.setattr(acoic, "_retrieve_evidence", _fake)
    return calls


@pytest.fixture
def resolve_calls(monkeypatch):
    """Install a fake ``cortex.resolve`` and record what the seam asked it."""
    recorder = _ResolveRecorder()

    def _fake_resolve(entity, question="", ctx=None, top_k=5):
        recorder.append({"entity": entity, "question": question, "top_k": top_k,
                         "tenant_id": getattr(ctx, "tenant_id", None),
                         "classification": getattr(ctx, "classification", None)})
        answer = recorder.state["resolution"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    import tools.cortex.api as cortex_api

    monkeypatch.setattr(cortex_api, "resolve", _fake_resolve)
    return recorder


# ---------------------------------------------------------------------------
# AC1 — the toggle
# ---------------------------------------------------------------------------
def test_toggle_off_never_consults_cortex(monkeypatch, legacy_calls):
    """OFF is the pre-migration behaviour EXACTLY, not an approximation of it.

    The rollback this epic mandates is a flag flip, which only holds if the
    seam is not reached at all — so the assertion is that ``cortex.resolve``
    was never called, not merely that the legacy text won.
    """
    monkeypatch.setattr(seam, "load_config", lambda path=None: _OFF)

    def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("cortex.resolve was consulted with the toggle OFF")

    import tools.cortex.api as cortex_api

    monkeypatch.setattr(cortex_api, "resolve", _explode)

    texts, citations, path, detail = acoic._gather_evidence(
        "AC-2", {"fedramp_high": ["AC-2"]}, None, "CUI"
    )

    assert path == seam.PATH_LEGACY
    assert citations == []
    assert texts and all(t.startswith("LEGACY chunk") for t in texts)
    assert len(legacy_calls) == 1
    # The legacy query shape is unchanged, character for character.
    assert legacy_calls[0]["query"] == "AC-2 fedramp_high implementation"


def test_toggle_on_routes_retrieval_through_cortex_resolve(
    monkeypatch, legacy_calls, resolve_calls
):
    """ON: the evidence comes from ``cortex.resolve`` and the legacy call is skipped."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)

    texts, citations, path, detail = acoic._gather_evidence(
        "AC-2", {"fedramp_high": ["AC-2"], "cmmc_level_2": ["AC.L2-3.1.1"]}, "t1", "CUI"
    )

    assert path == seam.PATH_CORTEX
    assert legacy_calls == [], "the legacy retriever must not run when the seam answered"
    assert len(resolve_calls) == 1
    ask = resolve_calls[0]
    # The ENTITY is the control id — the question only frames retrieval.
    assert ask["entity"] == "AC-2"
    assert "cmmc_level_2, fedramp_high" in ask["question"]
    assert ask["tenant_id"] == "t1"
    assert ask["classification"] == "CUI"
    assert ask["top_k"] == 5
    # Evidence is the resolution's own citations, in order.
    assert texts == [
        "Accounts are provisioned through the central IdP, evidence 1.",
        "Accounts are provisioned through the central IdP, evidence 2.",
    ]
    assert detail["backends"] == ["currency", "rag", "dic"]


def test_texts_and_citations_are_index_aligned(monkeypatch, resolve_calls):
    """``[SOURCE-N]`` must resolve to ``citations[N-1]``.

    This is the property the legacy path could not offer at all: it returned
    bare chunk texts, so a persisted ``[SOURCE-1]`` named nothing retrievable.
    """
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)

    texts, citations, _path, _detail = acoic._gather_evidence("AC-2", {}, None, "CUI")

    assert len(texts) == len(citations) == 2
    for i, (text, citation) in enumerate(zip(texts, citations), start=1):
        assert citation["detail"] == text
        assert citation["source"] == f"cortex:rag_chunk:chunk-{i}"
        assert citation["source_table"] == "rag_chunks"
        assert citation["provenance_id"] == f"prov-{i}"


def test_pack_evidence_citations_never_become_ssp_evidence(monkeypatch, resolve_calls):
    """A pack's own verdict rationale is not implementation evidence for a control.

    Letting a ``pack_evidence`` citation through would make a DERIVED verdict
    the ground truth for an SSP narrative — the same category error
    ``doc_modernization.evidence``'s ``extraction: structured`` filter exists to
    prevent, one canvas over.
    """
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    resolve_calls.state["resolution"] = _Resolution(citations=[
        _Citation(source_id="pack-1", source_type="pack_evidence",
                  source_table="network_hardware", snippet="pack says end_of_life"),
        _rag_citation(7),
    ])

    texts, citations, _path, _detail = acoic._gather_evidence("AC-2", {}, None, "CUI")

    assert len(texts) == 1
    assert citations[0]["source"] == "cortex:rag_chunk:chunk-7"
    assert not any("pack says" in t for t in texts)


# ---------------------------------------------------------------------------
# AC2 — the air-gap-safe paths survive
# ---------------------------------------------------------------------------
def test_deterministic_crosswalk_survives_with_no_llm(monkeypatch):
    """``map_changed_controls`` is a pure lookup and must not need a provider."""
    import tools.llm.router as router_module

    def _no_provider(*a, **k):  # pragma: no cover - invoked only on a defect
        raise RuntimeError("no LLM provider is reachable (air-gap)")

    monkeypatch.setattr(router_module, "LLMRouter", _no_provider)

    mapping = acoic.map_changed_controls(["AC-2", "sc-13"])

    assert set(mapping) == {"AC-2", "SC-13"}
    frameworks = mapping["AC-2"]["frameworks"]
    assert "fedramp_high" in frameworks and "cmmc_level_2" in frameworks


def test_cited_template_fallback_survives_with_no_llm(monkeypatch):
    """No provider -> a deterministic cited draft, every sentence ``[SOURCE-N]``-tagged.

    This is the air-gap path the card says must survive, so it is asserted on
    the DRAFT itself rather than on the absence of an exception.
    """
    import tools.llm.router as router_module

    def _no_provider(*a, **k):
        raise RuntimeError("no LLM provider is reachable (air-gap)")

    monkeypatch.setattr(router_module, "LLMRouter", _no_provider)

    evidence = ["Accounts are reviewed quarterly.", "Disabled after 35 days idle."]
    draft = acoic._draft_fragment_text("AC-2", {"fedramp_high": []}, evidence)

    assert draft
    assert "[SOURCE-1]" in draft and "[SOURCE-2]" in draft
    assert "fedramp_high" in draft
    for line in draft.splitlines()[1:]:
        assert line.rstrip().endswith("]"), line


def test_cited_template_fallback_is_reached_on_the_cortex_path_too(
    monkeypatch, resolve_calls
):
    """The migration must not couple the air-gap draft to the legacy retriever."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    import tools.llm.router as router_module

    monkeypatch.setattr(router_module, "LLMRouter",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("air-gap")))

    texts, _citations, path, _detail = acoic._gather_evidence("AC-2", {}, None, "CUI")
    draft = acoic._draft_fragment_text("AC-2", {}, texts)

    assert path == seam.PATH_CORTEX
    assert "[SOURCE-1]" in draft and "[SOURCE-2]" in draft


# ---------------------------------------------------------------------------
# AC3 — every degradation lands on the legacy path
# ---------------------------------------------------------------------------
def test_governance_refusal_falls_back_and_is_reported(
    monkeypatch, legacy_calls, resolve_calls
):
    """A refusal is a FACT about the control, not an outage and not an empty corpus."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    blocked = RuntimeError("resolution blocked")
    blocked.reason = "hallucinated_citation"
    resolve_calls.state["resolution"] = blocked

    texts, citations, path, detail = acoic._gather_evidence("AC-2", {}, None, "CUI")

    assert path == seam.PATH_CORTEX_EMPTY_FALLBACK
    assert detail["blocked"] == "hallucinated_citation"
    assert citations == []
    assert texts and texts[0].startswith("LEGACY chunk")
    assert len(legacy_calls) == 1


def test_empty_resolution_falls_back_and_keeps_the_backend_errors(
    monkeypatch, legacy_calls, resolve_calls
):
    """A dead backend and an empty corpus are different answers; both are carried.

    Measured on the live canvas: ``cortex.resolve`` abandons its rag/dic/graph
    rungs at their 10s/10s/8s budgets while the direct retriever needs 11.76s,
    so the governed answer can be thin for an infrastructure reason. Reporting
    that as "no evidence" is how an outage reaches a reader as a statement
    about the corpus.
    """
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    resolve_calls.state["resolution"] = _Resolution(
        citations=[],
        errors=[{"backend": "rag", "stage": "timeout", "message": "timed out after 10.0s"}],
    )

    texts, _citations, path, detail = acoic._gather_evidence("AC-2", {}, None, "CUI")

    assert path == seam.PATH_CORTEX_EMPTY_FALLBACK
    assert detail["backend_errors"][0]["stage"] == "timeout"
    assert texts and texts[0].startswith("LEGACY chunk")


def test_fallback_on_empty_false_keeps_the_governed_answer(
    monkeypatch, legacy_calls, resolve_calls
):
    """The fallback is a config decision, not a hard-wired one."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON_NO_FALLBACK)
    resolve_calls.state["resolution"] = _Resolution(citations=[])

    texts, citations, path, _detail = acoic._gather_evidence("AC-2", {}, None, "CUI")

    assert path == seam.PATH_CORTEX
    assert (texts, citations) == ([], [])
    assert legacy_calls == []


def test_reentrant_ask_takes_the_legacy_path(monkeypatch, legacy_calls):
    """resolve -> assess -> pack.evaluate -> here would recurse without bound."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)

    def _reentrant_resolve(entity, question="", ctx=None, top_k=5):
        # Inside the outbound call, exactly where a pack's evaluate() runs.
        inner = seam.resolve_evidence("AC-2", frameworks=[])
        assert inner is None, "a re-entrant ask must not resolve again"
        return _Resolution(citations=[_rag_citation(1)])

    import tools.cortex.api as cortex_api

    monkeypatch.setattr(cortex_api, "resolve", _reentrant_resolve)

    texts, _citations, path, _detail = acoic._gather_evidence("AC-2", {}, None, "CUI")
    assert path == seam.PATH_CORTEX and len(texts) == 1


def test_spent_budget_is_reported_not_silent(monkeypatch, legacy_calls, resolve_calls):
    """A bounded run that reads as a complete one is the defect 'no silent caps' names."""
    monkeypatch.setattr(
        seam, "load_config",
        lambda path=None: {"cortex": {"enabled": True, "max_resolves_per_run": 1}},
    )

    _t1, _c1, path1, _d1 = acoic._gather_evidence("AC-2", {}, None, "CUI")
    _t2, _c2, path2, _d2 = acoic._gather_evidence("AU-3", {}, None, "CUI")

    assert path1 == seam.PATH_CORTEX
    assert path2 == seam.PATH_LEGACY
    stats = acoic._evidence_run_stats()
    assert stats["resolutions"] == 1 and stats["capped"] == 1


def test_absent_cortex_takes_the_legacy_path(monkeypatch, legacy_calls):
    """A DIC install without Cortex degrades; it does not fail to draft."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    import builtins

    real_import = builtins.__import__

    def _no_cortex(name, *args, **kwargs):
        if name.startswith("tools.cortex"):
            raise ImportError("no cortex in this deployment")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_cortex)

    texts, citations, path, _detail = acoic._gather_evidence("AC-2", {}, None, "CUI")

    assert path == seam.PATH_LEGACY
    assert citations == []
    assert texts and texts[0].startswith("LEGACY chunk")


def test_same_control_resolves_once_per_run(monkeypatch, resolve_calls):
    """Memoised per RUN, so a queue item naming a control twice costs one fan-out."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)

    acoic._gather_evidence("AC-2", {}, None, "CUI")
    acoic._gather_evidence("AC-2", {}, None, "CUI")
    assert len(resolve_calls) == 1

    seam.reset_run_state()
    acoic._gather_evidence("AC-2", {}, None, "CUI")
    assert len(resolve_calls) == 2


# ---------------------------------------------------------------------------
# AC4 — the provenance is persisted
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, sink):
        self.sink = sink
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def cursor(self):
        return _FakeCursor(self.sink)

    def commit(self):
        pass

    def close(self):
        pass


def _persisted_citation_report(sink) -> dict:
    for sql, params in sink:
        if "INSERT" in sql and "dic_ssp_fragments" in sql:
            return json.loads(params[8])
    raise AssertionError("no dic_ssp_fragments INSERT was issued")


def test_fragment_records_the_governed_sources(monkeypatch, resolve_calls):
    """A ``[SOURCE-N]`` on a persisted fragment now resolves to a real source id."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    sink: list = []
    monkeypatch.setattr(acoic, "get_connection", lambda *a, **k: _FakeConn(sink))
    monkeypatch.setattr(acoic, "_ensure_schema", lambda conn: None)

    acoic.generate_ssp_fragment("AC-2", document_id="doc-1", classification="CUI")

    report = _persisted_citation_report(sink)
    assert report["evidence_path"] == seam.PATH_CORTEX
    assert [s["source"] for s in report["sources"]] == [
        "cortex:rag_chunk:chunk-1", "cortex:rag_chunk:chunk-2",
    ]
    assert report["evidence_detail"]["backends"] == ["currency", "rag", "dic"]


def test_fragment_records_the_legacy_path_honestly(monkeypatch, legacy_calls):
    """The legacy path has no source identity to give, and says so with an empty list.

    An empty ``sources`` is the honest record. Inventing an id for a bare chunk
    text would make the pre-migration fragments indistinguishable from the
    governed ones, which is exactly the confusion this column exists to end.
    """
    monkeypatch.setattr(seam, "load_config", lambda path=None: _OFF)
    sink: list = []
    monkeypatch.setattr(acoic, "get_connection", lambda *a, **k: _FakeConn(sink))
    monkeypatch.setattr(acoic, "_ensure_schema", lambda conn: None)

    acoic.generate_ssp_fragment("AC-2", document_id="doc-1")

    report = _persisted_citation_report(sink)
    assert report["evidence_path"] == seam.PATH_LEGACY
    assert report["sources"] == []


def test_caller_supplied_evidence_still_wins(monkeypatch, resolve_calls):
    """A caller that brought its own evidence asked for THAT, not for a resolution."""
    monkeypatch.setattr(seam, "load_config", lambda path=None: _ON)
    sink: list = []
    monkeypatch.setattr(acoic, "get_connection", lambda *a, **k: _FakeConn(sink))
    monkeypatch.setattr(acoic, "_ensure_schema", lambda conn: None)

    acoic.generate_ssp_fragment(
        "AC-2", document_id="doc-1", evidence_chunks=["a caller-supplied chunk"]
    )

    assert resolve_calls == []
    assert _persisted_citation_report(sink)["evidence_path"] == seam.PATH_CALLER


# ---------------------------------------------------------------------------
# The shipped default
# ---------------------------------------------------------------------------
def test_shipped_config_defaults_the_seam_off():
    """args/dic_acoic_config.yaml ships OFF, so the migration lands inert."""
    config = seam.load_config()
    assert config, "args/dic_acoic_config.yaml must exist and be readable"
    assert seam.cortex_enabled(config) is False
    assert seam.fallback_on_empty(config) is True
