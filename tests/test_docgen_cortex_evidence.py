# CUI // SP-CTI
"""cef-di-05 — DIC document generation through the governed cortex.resolve() seam.

One test per acceptance criterion, plus the failure modes that decide whether
this migration can take document generation offline:

1. ``generate_document`` / ``regenerate_section`` — what the ``POST /api/generate``
   and ``POST /api/generate/section`` routes call — source evidence via
   ``cortex.resolve()`` BEHIND A TOGGLE, and with the toggle off Cortex is not
   consulted at all.
2. Generated sections carry VALIDATED citations: the ids the model was shown are
   the ids it is checked against, on either path, and the check is recorded
   rather than merely available.
3. The Chain-of-Debate paths (``_cot_generate`` / ``_cod_compress`` via
   ``ChainOrchestrator``) still function, on both evidence paths.
4. A generated draft does not reintroduce an entity the currency backend reports
   deprecated — the case the verifier structurally cannot catch, because a
   stale runbook SUPPORTS the claim that names it.
5. Every degradation (re-entrancy, spent budget, absent Cortex, a governance
   refusal) lands on the legacy path rather than on an exception.

PATCHING NOTE — this is the trap this canvas sets repeatedly.
``tools.X is icdev.tools.X`` is False: they are separate module objects with
separate thread-local run state. ``doc_generator._evidence_module()`` resolves
``icdev`` FIRST, so a test that patches ``tools.document_intelligence.docgen_evidence``
patches a module the code under test never touches, and every assertion here
would pass against nothing. :func:`_seam` returns what the code actually uses,
and :func:`_patch_cortex` patches EVERY importable alias of ``cortex.api``.
"""
from __future__ import annotations

import importlib
import json

import pytest

from tools.document_intelligence import doc_generator

_ON = {
    "cortex": {"enabled": True, "top_k": 10, "max_resolves_per_run": 50,
               "fallback_on_empty": True},
    "currency_guard": {"enabled": True, "on_deprecated": "annotate",
                       "verdicts": ["deprecated", "superseded"],
                       "max_screen_chars": 6000},
}
_ON_ABSTAIN = {
    "cortex": dict(_ON["cortex"]),
    "currency_guard": dict(_ON["currency_guard"], on_deprecated="abstain"),
}
_ON_NO_FALLBACK = {
    "cortex": dict(_ON["cortex"], fallback_on_empty=False),
    "currency_guard": dict(_ON["currency_guard"]),
}
_ON_NO_GUARD = {
    "cortex": dict(_ON["cortex"]),
    "currency_guard": dict(_ON["currency_guard"], enabled=False),
}
_OFF = {"cortex": {"enabled": False}}


# ── Stubs mirroring only the fields the seam reads ───────────────────────────

class _Citation:
    """The subset of ``cortex.schemas.Citation`` the seam reads."""

    def __init__(self, source_id="", source_type="rag_chunk", source_table="rag_chunks",
                 title="", snippet="", provenance_id="", url="", classification="CUI"):
        self.source_id = source_id
        self.source_type = source_type
        self.source_table = source_table
        self.title = title
        self.snippet = snippet
        self.provenance_id = provenance_id
        self.url = url
        self.classification = classification


class _Assessment:
    """The subset of ``cortex.schemas.EntityAssessment`` the seam reads."""

    def __init__(self, entity="", verdict="current", pack_verdict="", pack_id="",
                 superseded_by="", rationale="", evidence=None, entity_type="",
                 replacement_source="", severity=""):
        self.entity = entity
        self.verdict = verdict
        self.pack_verdict = pack_verdict or verdict
        self.pack_id = pack_id
        self.superseded_by = superseded_by
        self.rationale = rationale
        self.evidence = list(evidence or [])
        self.entity_type = entity_type
        self.replacement_source = replacement_source
        self.severity = severity


class _Resolution:
    """The subset of ``CortexResolution`` the seam reads."""

    def __init__(self, citations=None, assessments=None, backends=None,
                 errors=None, verdict="unknown"):
        self.citations = list(citations or [])
        self.assessments = list(assessments or [])
        self.backends_consulted = list(backends or ["currency", "rag", "dic"])
        self.backend_errors = list(errors or [])
        self.verdict = verdict


class _LegacyCitation:
    def __init__(self, chunk_id):
        self.chunk_id = chunk_id

    def to_dict(self):
        return {"chunk_id": self.chunk_id, "doc_id": "legacy-doc",
                "doc_title": "Legacy Runbook", "page": 3}


class _LegacyResult:
    """The subset of ``DICSearchResult`` doc_generator reads."""

    def __init__(self, chunk_id, content):
        self.chunk_id = chunk_id
        self.content = content
        self.doc_title = "Legacy Runbook"
        self.doc_id = "legacy-doc"
        self.page = 3
        self.citation = _LegacyCitation(chunk_id)


class _Claim:
    def __init__(self, method="cited", supported=True):
        self.method = method
        self.supported = supported


class _VerifyResult:
    def __init__(self, text, abstained=False, verified=True, supported=True):
        self.verified_text = text
        self.abstained = abstained
        self.verified = verified
        self.claims = [_Claim(supported=supported)]


# ── Harness ──────────────────────────────────────────────────────────────────

def _seam():
    """The docgen_evidence module the code under test ACTUALLY uses."""
    module = doc_generator._evidence_module()
    assert module is not None, "the governed evidence seam must be importable"
    return module


@pytest.fixture(autouse=True)
def _clean_run():
    """Fresh per-run state on BOTH copies, before and after each test.

    Both, deliberately: a previous test may have populated the copy this test
    does not use, and a leaked memo cache is exactly the kind of cross-test
    coupling that makes one file green alone and red in-suite.
    """
    for name in ("tools.document_intelligence.docgen_evidence",
                 "icdev.tools.document_intelligence.docgen_evidence"):
        try:
            importlib.import_module(name).reset_run_state()
        except Exception:  # pragma: no cover - one tree may be absent
            pass
    yield


def _patch_cortex(monkeypatch, fn):
    """Patch ``resolve`` on every importable alias of the Cortex facade.

    Returns the call log. Patching one alias is not enough: the seam's late
    ``from tools.cortex.api import resolve`` resolves a different module object
    depending on which tree the seam itself came from, and a half-applied patch
    reads as "Cortex was never called" — which is the assertion several of these
    tests make, so it would pass for the wrong reason.
    """
    calls: list = []

    def _resolve(entity, question="", ctx=None, top_k=5):
        calls.append({"entity": entity, "question": question, "top_k": top_k, "ctx": ctx})
        return fn(entity, question, ctx, top_k)

    patched = 0
    for name in ("tools.cortex.api", "icdev.tools.cortex.api"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover - one tree may be absent
            continue
        monkeypatch.setattr(module, "resolve", _resolve, raising=False)
        patched += 1
    assert patched, "no Cortex facade to patch — the harness is not exercising anything"
    return calls


def _patch_config(monkeypatch, config):
    monkeypatch.setattr(_seam(), "load_config", lambda path=None: config)


def _patch_legacy(monkeypatch, results):
    """Patch ``DICSearchEngine`` and log whether the legacy chain was used."""
    calls: list = []

    class _Engine:
        def __init__(self, tenant_id="default", **kwargs):
            self.tenant_id = tenant_id

        def search(self, query, collection_id=None, top_k=10):
            calls.append({"query": query, "collection_id": collection_id, "top_k": top_k})
            return list(results)

    for name in ("tools.document_intelligence.search_engine",
                 "icdev.tools.document_intelligence.search_engine"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover
            continue
        monkeypatch.setattr(module, "DICSearchEngine", _Engine, raising=False)
    return calls


def _patch_drafting(monkeypatch, section_text, *, outline_headings=("Transport Security",)):
    """Stub the LLM and the verifier. Returns the prompts the drafter built."""
    prompts: list = []
    outline = json.dumps({
        "title": "Network SOP",
        "sections": [{"heading": h, "summary": f"{h} summary"} for h in outline_headings],
    })

    def _llm(prompt, function="document_qna", max_tokens=2048):
        prompts.append(prompt)
        return outline if "outline" in prompt.lower() else section_text

    monkeypatch.setattr(doc_generator, "_llm_generate", _llm)
    for name in ("tools.document_intelligence.verifier",
                 "icdev.tools.document_intelligence.verifier"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover
            continue
        monkeypatch.setattr(
            module, "verify", lambda text, evidence: _VerifyResult(text), raising=False
        )
    return prompts


_GOVERNED = [
    _Citation(source_id="gov-1", source_type="rag_chunk", source_table="rag_chunks",
              title="Transport Security Standard", snippet="TLS 1.3 is the mandated "
              "transport for all enclave traffic.", provenance_id="prov-1"),
    _Citation(source_id="gov-2", source_type="dic_document", source_table="dic_documents",
              title="Enclave Runbook", snippet="Cipher suites are reviewed quarterly.",
              provenance_id="prov-2"),
]


def _generate(**kwargs):
    return doc_generator.generate_document(
        kwargs.pop("query", "network transport SOP"),
        kwargs.pop("collection_id", "default"),
        tenant_id="t1",
        classification="CUI",
        **kwargs,
    )


# ── 1. Behind a toggle ───────────────────────────────────────────────────────

def test_toggle_off_never_consults_cortex(monkeypatch):
    """The rollback contract: flip the flag, do not revert the merge.

    Asserts Cortex is NOT CALLED, rather than that its result was ignored. A
    seam that resolves and then discards the answer still costs a five-backend
    fan-out on every draft, and would still fail closed on a governance refusal
    — so "we ignored it" is not the same rollback at all.
    """
    _patch_config(monkeypatch, _OFF)
    calls = _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=_GOVERNED))
    legacy = _patch_legacy(monkeypatch, [_LegacyResult("chunk-9", "Legacy evidence body.")])
    _patch_drafting(monkeypatch, "Prose citing [source: chunk chunk-9].")

    result = _generate()

    assert calls == [], "toggle off must not consult Cortex at all"
    assert legacy, "toggle off must run the original DICSearchEngine retrieval"
    assert result.sections
    assert result.sections[0].citation_report["evidence_path"] == "legacy"


def test_toggle_on_sources_evidence_from_cortex(monkeypatch):
    """Criterion 1: evidence comes from ``cortex.resolve()``, not a second RAG call."""
    _patch_config(monkeypatch, _ON)
    calls = _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=_GOVERNED))
    legacy = _patch_legacy(monkeypatch, [_LegacyResult("chunk-9", "Legacy evidence body.")])
    prompts = _patch_drafting(monkeypatch, "Prose citing [source: chunk gov-1].")

    result = _generate()

    assert calls, "toggle on must consult Cortex"
    assert calls[0]["entity"] == "network transport SOP"
    assert not legacy, "the governed answer must not also pay for the legacy retrieval"

    section = result.sections[0]
    assert section.citation_report["evidence_path"] == "cortex"
    # The governed snippets are what the drafter was actually shown.
    assert any("TLS 1.3 is the mandated" in p for p in prompts)
    # And the extra provenance the legacy citation pack could not carry rode
    # along into what gets persisted, without any caller branching for it.
    cited = {c["chunk_id"]: c for c in section.citations if c.get("chunk_id")}
    assert cited["gov-1"]["source_table"] == "rag_chunks"
    assert cited["gov-1"]["provenance_id"] == "prov-1"
    assert cited["gov-1"]["evidence_path"] == "cortex"


def test_regenerate_section_sources_evidence_from_cortex(monkeypatch):
    """The OTHER route — ``POST /api/generate/section`` — migrates too.

    Driven through ``_governed_retrieval`` rather than the full entry point,
    because ``regenerate_section``'s other half is ``dic_sections`` /
    ``dic_versions`` row reads that this migration deliberately does not touch:
    they are exact primary-key lookups, not evidence retrieval, and a ranked
    seam cannot return "the section before this one".
    """
    _patch_config(monkeypatch, _ON)
    calls = _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=_GOVERNED))

    results, path, detail, deprecated = doc_generator._governed_retrieval(
        "Transport Security",
        collection_id="net-sop",
        tenant_id="t1",
        classification="CUI",
        top_k=8,
        legacy=lambda: pytest.fail("legacy retrieval must not run when the seam answers"),
    )

    assert calls[0]["entity"] == "Transport Security"
    assert calls[0]["top_k"] == 8, "the legacy top_k=8 must carry over — like-for-like"
    assert path == "cortex"
    assert [r.chunk_id for r in results] == ["gov-1", "gov-2"]
    assert detail["backends"] == ["currency", "rag", "dic"]


# ── 2. Validated citations ───────────────────────────────────────────────────

def test_generated_sections_carry_validated_citations(monkeypatch):
    """Criterion 2. The ids the model was SHOWN are the ids it is CHECKED against.

    That equivalence is what lets the migration keep the citation contract
    without touching a prompt: ``chunk_id`` is the Cortex citation's
    ``source_id`` on the governed path, and both the drafting prompt and
    ``validate_citations`` read the same field.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=_GOVERNED))
    _patch_legacy(monkeypatch, [])
    _patch_drafting(
        monkeypatch,
        "Enclave traffic uses TLS 1.3 [source: chunk gov-1]. "
        "Suites are reviewed quarterly [source: chunk gov-2].",
    )

    report = _generate().sections[0].citation_report

    assert report["valid"] is True
    assert report["cited_count"] == 2
    assert report["hallucinated_citations"] == []


def test_a_citation_naming_no_governed_source_is_reported(monkeypatch):
    """The discriminating half — the check above must be able to FAIL.

    A validation that cannot go red records nothing. This is the same draft with
    one invented source id, and it must come back invalid and NAME the id, so a
    reviewer is told which tag to distrust rather than that the section is
    generically suspect.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=_GOVERNED))
    _patch_legacy(monkeypatch, [])
    _patch_drafting(
        monkeypatch,
        "Enclave traffic uses TLS 1.3 [source: chunk gov-1]. "
        "Keys rotate hourly [source: chunk gov-999].",
    )

    report = _generate().sections[0].citation_report

    assert report["valid"] is False
    assert report["hallucinated_citations"] == ["gov-999"]


# ── 3. Chain-of-Debate preserved ─────────────────────────────────────────────

def test_chain_of_debate_paths_still_function(monkeypatch):
    """Criterion 3. ``ChainOrchestrator`` is untouched by the migration.

    Both CoD entry points consume an evidence STRING and do not care which chain
    produced it, which is precisely why they needed no change — asserted here
    rather than assumed, because "we did not touch it" is not evidence that it
    still runs.
    """
    seen: list = []

    class _Result:
        # Over 100 characters: _cod_compress rejects a shorter body and returns
        # the original, which would make the compression assertion vacuous.
        content = (
            "Synthesized prose from the debate chain, dense enough that the "
            "compressor accepts it rather than falling back to the original body."
        )

    class _Orchestrator:
        def invoke_chain_of_thought(self, function, req):
            seen.append(req)
            return _Result()

    for name in ("tools.llm.chain_orchestrator", "icdev.tools.llm.chain_orchestrator"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover
            continue
        monkeypatch.setattr(module, "ChainOrchestrator", _Orchestrator, raising=False)

    drafted = doc_generator._cot_generate("Transport Security", "TLS 1.3 evidence body.")
    # Over _COD_WORD_THRESHOLD, or the compressor returns the text untouched and
    # never reaches the orchestrator at all — which would make this test pass
    # while asserting nothing about the chain.
    long_body = " ".join(["sentence"] * (doc_generator._COD_WORD_THRESHOLD + 50))
    compressed = doc_generator._cod_compress(long_body, "Transport Security")

    assert len(seen) == 2, "both Chain-of-Debate entry points must still be reached"
    assert drafted == _Result.content
    assert compressed == _Result.content


def test_cot_path_consumes_governed_evidence(monkeypatch):
    """CoD on the NEW path: it is handed the governed evidence, unchanged in shape."""
    monkeypatch.setenv("ICDEV_DIC_COT_ENABLED", "true")
    _patch_config(monkeypatch, _ON)
    # The CoT path is only taken above _COT_EVIDENCE_THRESHOLD characters of
    # evidence. A short fixture would skip it and this test would assert
    # nothing about CoD at all.
    # Note `_evidence_block` truncates each result to 300 chars, so the length
    # that matters is per-result AND across results — one long citation is not
    # enough to clear the threshold.
    long_snippet = "TLS 1.3 is the mandated transport for all enclave traffic. " * 12
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=[
        _Citation(source_id=f"gov-{i}", title="Transport Security Standard",
                  snippet=long_snippet, provenance_id=f"prov-{i}")
        for i in range(1, 4)
    ]))
    _patch_legacy(monkeypatch, [])
    _patch_drafting(monkeypatch, "Fallback prose [source: chunk gov-1].")

    seen: list = []
    monkeypatch.setattr(
        doc_generator, "_cot_generate",
        lambda heading, evidence, **kw: seen.append(evidence) or "CoD prose [source: chunk gov-1].",
    )

    result = _generate()

    assert seen, "the CoT path must still be taken when it is enabled"
    assert "TLS 1.3 is the mandated" in seen[0], "CoD must receive the governed evidence"
    assert "CoD prose" in result.sections[0].content


# ── 4. The currency guard — the criterion this card exists for ───────────────

_DEPRECATED = _Assessment(
    entity="TLS 1.1",
    verdict="superseded",
    pack_verdict="deprecated",
    pack_id="crypto_protocols",
    superseded_by="TLS 1.3",
    rationale="Deprecated by RFC 8996",
    evidence=[{"source": "entity_currency:nist", "detail": "deprecated as of 2021-03",
               "date": "2021-03-01"}],
)


def _currency_resolution(entity, question, ctx, top_k):
    """Evidence asks answer with citations; the screening ask answers with a verdict.

    Split on the drafted text so the two asks this migration makes are exercised
    independently — the evidence fan-out and the deterministic screen.
    """
    if "TLS 1.1" in entity:
        return _Resolution(assessments=[_DEPRECATED], verdict="superseded")
    return _Resolution(citations=_GOVERNED)


def test_a_draft_reintroducing_a_deprecated_entity_is_caught(monkeypatch):
    """Criterion 4 — and the case NOTHING else in this pipeline can catch.

    The verifier asks whether a claim is SUPPORTED by the retrieved evidence. A
    draft grounded in a 2019 runbook that recommends TLS 1.1 is fully supported
    by that runbook, so the verifier passes it, the attribution score passes it,
    and the document ships reintroducing a protocol the estate spent two years
    removing. Only a currency verdict — derived by a pack from typed catalog
    fields, never by a model — can tell the difference.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, _currency_resolution)
    _patch_legacy(monkeypatch, [])
    _patch_drafting(monkeypatch, "Configure the enclave to use TLS 1.1 [source: chunk gov-1].")

    section = _generate().sections[0]

    # The draft no longer presents the deprecated entity as current: it carries
    # the verdict and the successor, in the prose.
    assert "TLS 1.1" in section.content and "superseded" in section.content
    assert "TLS 1.3" in section.content

    # It is not `verified`, whatever the verifier said, and it cannot reach a
    # reader without a human: every generated version lands pending_review and
    # this one is flagged on top of that.
    assert section.verified is False
    assert section.low_confidence is True
    assert "Currency" in section.hitl_note

    # And it CITES the verdict — structurally, as a citation record.
    verdicts = [c for c in section.citations if c.get("source_type") == "currency_verdict"]
    assert len(verdicts) == 1
    assert "TLS 1.1 is superseded" in verdicts[0]["detail"]
    assert verdicts[0]["provenance_id"] == "crypto_protocols"

    report = section.citation_report["currency"]
    assert report["screened"] is True
    assert [f["entity"] for f in report["findings"]] == ["TLS 1.1"]


def test_the_currency_advisory_invents_no_citation_tag(monkeypatch):
    """The guard must not manufacture the defect it exists to prevent.

    A pack's evidence ref (``entity_currency:nist``) is a synthetic key, not a
    retrievable chunk id. Tagging it in the prose would put an id in the text
    that ``validate_citations`` cannot match — a hallucinated citation created
    by the trust guard itself. The verdict is cited structurally instead.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, _currency_resolution)
    _patch_legacy(monkeypatch, [])
    _patch_drafting(monkeypatch, "Configure the enclave to use TLS 1.1 [source: chunk gov-1].")

    section = _generate().sections[0]

    assert section.citation_report["hallucinated_citations"] == []
    assert section.citation_report["valid"] is True


def test_abstain_mode_removes_the_draft(monkeypatch):
    """The stricter action, for a caller that wants the prose gone entirely."""
    _patch_config(monkeypatch, _ON_ABSTAIN)
    _patch_cortex(monkeypatch, _currency_resolution)
    _patch_legacy(monkeypatch, [])
    _patch_drafting(monkeypatch, "Configure the enclave to use TLS 1.1 [source: chunk gov-1].")

    section = _generate().sections[0]

    assert section.abstained is True
    assert "Configure the enclave" not in section.content
    assert "TLS 1.1" in section.content, "the reviewer must still be told WHAT was dropped"


def test_a_current_or_unknown_verdict_never_trips_the_guard(monkeypatch):
    """``unknown`` means no pack RECOGNISED the entity — a gap, not a finding.

    Treating it as one would flag every draft on the board, which is how a
    guard gets switched off within a week.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, lambda entity, q, ctx, k: _Resolution(
        citations=_GOVERNED,
        assessments=[_Assessment(entity="TLS 1.3", verdict="current"),
                     _Assessment(entity="Widget 9000", verdict="unknown")],
    ))
    _patch_legacy(monkeypatch, [])
    _patch_drafting(monkeypatch, "Enclave traffic uses TLS 1.3 [source: chunk gov-1].")

    section = _generate().sections[0]

    assert "⚠ Currency" not in section.content
    assert section.citation_report["currency"]["screened"] is True
    assert section.citation_report["currency"]["findings"] == []
    assert not [c for c in section.citations if c.get("source_type") == "currency_verdict"]


def test_a_screen_that_did_not_run_is_not_a_clean_screen(monkeypatch):
    """``screened: false`` and ``findings: []`` must never read the same.

    "Nothing checked" and "checked, nothing wrong" differ by exactly the
    assurance this card adds, and a reader of a persisted draft has only this
    field to tell them apart.
    """
    _patch_config(monkeypatch, _ON_NO_GUARD)
    _patch_cortex(monkeypatch, _currency_resolution)
    _patch_legacy(monkeypatch, [])
    _patch_drafting(monkeypatch, "Configure the enclave to use TLS 1.1 [source: chunk gov-1].")

    section = _generate().sections[0]

    assert section.citation_report["currency"] == {"screened": False}
    assert "⚠ Currency" not in section.content
    assert _seam().screen_draft("Use TLS 1.1.", tenant_id="t1") is None


def test_the_drafting_prompt_is_told_what_is_deprecated(monkeypatch):
    """The advisory half. Cheap, and it makes the common case come out right first.

    It is explicitly NOT the guarantee — an LLM instruction is a request. The
    deterministic screen above is what holds, and it runs on the OUTPUT.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, lambda entity, q, ctx, k: _Resolution(
        citations=_GOVERNED, assessments=[_DEPRECATED],
    ))
    _patch_legacy(monkeypatch, [])
    prompts = _patch_drafting(monkeypatch, "Enclave traffic uses TLS 1.3 [source: chunk gov-1].")

    _generate()

    section_prompts = [p for p in prompts if "CURRENCY CONSTRAINT" in p]
    assert section_prompts, "the drafter must be told what the catalog calls dead"
    assert "TLS 1.1 is superseded; use TLS 1.3 instead" in section_prompts[0]


# ── 5. Every degradation lands on the legacy path ────────────────────────────

def test_absent_cortex_falls_back_to_legacy(monkeypatch):
    _patch_config(monkeypatch, _ON)

    def _boom(*args, **kwargs):
        raise ImportError("no cortex here")

    monkeypatch.setattr(_seam(), "_resolve", _boom)
    legacy = _patch_legacy(monkeypatch, [_LegacyResult("chunk-9", "Legacy evidence body.")])
    _patch_drafting(monkeypatch, "Prose [source: chunk chunk-9].")

    with pytest.raises(ImportError):
        # Guard the guard: _resolve is the seam's ONE outbound call site, so if
        # this name ever moves, the fallback tests below would patch nothing and
        # pass by exercising the real Cortex.
        _seam()._resolve("x", question="", tenant_id=None, classification=None, top_k=1)

    monkeypatch.setattr(_seam(), "_resolve", lambda *a, **k: (None, ""))
    result = _generate()

    assert legacy, "an absent Cortex must fall through to the legacy retrieval"
    assert result.sections[0].citation_report["evidence_path"] == "legacy"


def test_a_governance_refusal_falls_back_and_is_reported(monkeypatch):
    """A refusal is a FACT about this query, not an empty answer.

    It falls through — a governance block on supplementary evidence must never
    take document generation offline — but the reason is persisted, so a thin
    draft is never laundered into "the corpus had nothing".
    """
    _patch_config(monkeypatch, _ON)

    class _Blocked(RuntimeError):
        reason = "governance_refused"

    def _refuse(entity, question, ctx, top_k):
        raise _Blocked("blocked")

    _patch_cortex(monkeypatch, _refuse)
    legacy = _patch_legacy(monkeypatch, [_LegacyResult("chunk-9", "Legacy evidence body.")])
    _patch_drafting(monkeypatch, "Prose [source: chunk chunk-9].")

    report = _generate().sections[0].citation_report

    assert legacy, "a refusal must not take drafting offline"
    assert report["evidence_path"] == "cortex_empty_fallback"
    assert report["evidence_detail"]["blocked"] == "governance_refused"


def test_a_spent_budget_falls_back_and_is_counted(monkeypatch):
    """A cap that is reached silently makes later sections worse invisibly."""
    _patch_config(monkeypatch, dict(
        _ON, cortex=dict(_ON["cortex"], max_resolves_per_run=1)))
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=_GOVERNED))
    legacy = _patch_legacy(monkeypatch, [_LegacyResult("chunk-9", "Legacy body.")])
    _patch_drafting(monkeypatch, "Prose [source: chunk gov-1].")

    seam = _seam()
    seam.reset_run_state()
    assert seam.resolve_evidence("first query", tenant_id="t1") is not None
    assert seam.resolve_evidence("second query", tenant_id="t1") is None

    stats = seam.run_stats()
    assert stats["resolutions"] == 1
    assert stats["capped"] == 1, "an ask refused by the cap must be COUNTED, never silent"
    assert legacy == [], "the seam itself never runs the legacy retrieval — the caller does"


def test_a_re_entrant_ask_returns_none_instead_of_recursing(monkeypatch):
    """``cortex.resolve`` RUNS the packs, so a pack asking the seam recurses.

    Thread-local rather than global: the search fan-out runs backends in a
    worker pool, and a global flag would suppress an unrelated concurrent
    drafting run's evidence.
    """
    _patch_config(monkeypatch, _ON)
    seen: list = []

    def _reenter(entity, question, ctx, top_k):
        # Exactly what a DomainPack.evaluate() calling back into the seam does.
        seen.append(_seam().resolve_evidence("inner query", tenant_id="t1"))
        seen.append(_seam().screen_draft("Use TLS 1.1.", tenant_id="t1"))
        return _Resolution(citations=_GOVERNED)

    _patch_cortex(monkeypatch, _reenter)

    assert _seam().resolve_evidence("outer query", tenant_id="t1") is not None
    assert seen == [None, None], "a re-entrant ask must take the legacy path, not recurse"


def test_an_empty_governed_answer_can_be_left_unvarnished(monkeypatch):
    """``fallback_on_empty: false`` — to see the governed path as it really is."""
    _patch_config(monkeypatch, _ON_NO_FALLBACK)
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=[]))
    legacy = _patch_legacy(monkeypatch, [_LegacyResult("chunk-9", "Legacy body.")])

    results, path, detail, _ = doc_generator._governed_retrieval(
        "network transport SOP", collection_id="default", tenant_id="t1",
        classification="CUI", top_k=10, legacy=lambda: [_LegacyResult("chunk-9", "x")],
    )

    assert results == []
    assert path == "cortex"
    assert legacy == []


def test_pack_evidence_citations_never_become_drafting_evidence(monkeypatch):
    """A pack's own verdict rationale must not become the source for a document.

    Same rule, same reason, as ``ssp_evidence``: letting a derived verdict come
    back through the fan-out and be cited as corpus evidence would make it the
    ground truth for the thing it was derived from.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=[
        _Citation(source_id="pack-1", source_type="pack_evidence",
                  source_table="packs", snippet="TLS 1.1 is deprecated per the pack."),
        _GOVERNED[0],
    ]))

    results, _, _, _ = doc_generator._governed_retrieval(
        "network transport SOP", collection_id="default", tenant_id="t1",
        classification="CUI", top_k=10, legacy=lambda: [],
    )

    assert [r.chunk_id for r in results] == ["gov-1"]


def test_a_citation_with_no_source_id_is_dropped_not_invented(monkeypatch):
    """An id nothing can be looked up by is worse than one fewer piece of evidence.

    It would pass ``validate_citations`` — it is in the allowed set, because we
    put it there — while resolving to nothing at all.
    """
    _patch_config(monkeypatch, _ON)
    _patch_cortex(monkeypatch, lambda *a: _Resolution(citations=[
        _Citation(source_id="", snippet="Evidence with no traceable source."),
        _Citation(source_id="gov-3", snippet="   "),
        _GOVERNED[0],
    ]))

    results, _, _, _ = doc_generator._governed_retrieval(
        "network transport SOP", collection_id="default", tenant_id="t1",
        classification="CUI", top_k=10, legacy=lambda: [],
    )

    assert [r.chunk_id for r in results] == ["gov-1"]
