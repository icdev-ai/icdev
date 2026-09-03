# CUI // SP-CTI
"""rmf-wp-01 — WHITEPAPER document type, and ``template_id`` made load-bearing.

``doc_generator.generate_document`` accepted ``template_id`` and never
referenced it again — one grep hit, the signature. Every document got a
freeform LLM outline sliced at ``[:6]`` while twelve declared skeletons in
``constants.TEMPLATE_SECTIONS`` went unused, and every section was drafted
against ONE document-wide retrieval.

Three things are pinned here, each against the merge base:

1. ``generate_document(template_id="WHITEPAPER")`` produces the DECLARED
   skeleton — every heading, in order, nothing invented — not the freeform
   outline. WHITEPAPER is resolved through ``outline_contract.get_contract``
   with no edit to that module.
2. The ``[:6]`` cap is gone: a freeform outline of nine sections drafts nine.
3. Per-section retrieval: the evidence each section is drafted against is
   RETRIEVED FOR THAT SECTION, so two sections see different evidence and carry
   different citations.

PATCHING NOTE (the trap this canvas sets repeatedly): ``tools.X`` and
``icdev.tools.X`` may be distinct module objects, so every stub is installed on
every importable alias, mirroring ``tests/test_docgen_cortex_evidence.py``.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

from tools.document_intelligence import doc_generator

REPO_ROOT = Path(__file__).resolve().parents[1]

_OFF = {"cortex": {"enabled": False}}


# ── Stubs mirroring only the fields doc_generator reads ─────────────────────

class _LegacyCitation:
    def __init__(self, chunk_id):
        self.chunk_id = chunk_id

    def to_dict(self):
        return {"chunk_id": self.chunk_id, "doc_id": "legacy-doc",
                "doc_title": "Legacy Corpus", "page": 1}


class _LegacyResult:
    def __init__(self, chunk_id, content):
        self.chunk_id = chunk_id
        self.content = content
        self.doc_title = "Legacy Corpus"
        self.doc_id = "legacy-doc"
        self.page = 1
        self.citation = _LegacyCitation(chunk_id)


class _Claim:
    def __init__(self, method="cited", supported=True):
        self.method = method
        self.supported = supported


class _VerifyResult:
    def __init__(self, text):
        self.verified_text = text
        self.abstained = False
        self.verified = True
        self.claims = [_Claim()]


# ── Harness ──────────────────────────────────────────────────────────────────

def _seam():
    module = doc_generator._evidence_module()
    assert module is not None, "the governed evidence seam must be importable"
    return module


@pytest.fixture(autouse=True)
def _clean_run(monkeypatch):
    """Toggle the governed seam OFF and reset both copies' run state.

    These tests are about the OUTLINE and about WHICH retrieval each section
    gets, not about the Cortex seam (cef-di-05 owns that). With the toggle off
    the legacy ``DICSearchEngine`` is the only retriever, and its call log is
    the evidence.
    """
    for name in ("tools.document_intelligence.docgen_evidence",
                 "icdev.tools.document_intelligence.docgen_evidence"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover - one tree may be absent
            continue
        module.reset_run_state()
        monkeypatch.setattr(module, "load_config", lambda path=None: _OFF)
    # Never touch a real database from here: persistence is fail-open and its
    # outcome is not what these tests measure.
    monkeypatch.setenv("ICDEV_DIC_SECTION_RETRIEVAL", "1")
    yield


def _patch_legacy(monkeypatch, results_for):
    """Patch ``DICSearchEngine``; ``results_for(query)`` decides what comes back.

    Returns the call log so a test can assert WHAT was searched for, per
    section, rather than only what came back.
    """
    calls: list = []

    class _Engine:
        def __init__(self, tenant_id="default", **kwargs):
            self.tenant_id = tenant_id

        def search(self, query, collection_id=None, top_k=10):
            calls.append({"query": query, "collection_id": collection_id, "top_k": top_k})
            return list(results_for(query))

    for name in ("tools.document_intelligence.search_engine",
                 "icdev.tools.document_intelligence.search_engine"):
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover
            continue
        monkeypatch.setattr(module, "DICSearchEngine", _Engine, raising=False)
    return calls


def _patch_drafting(monkeypatch, *, outline_headings, section_text=None):
    """Stub the LLM and the verifier.

    The outline stub answers EVERY outline-shaped prompt with the given
    headings — so if the drafter still asks for a freeform outline when a
    template was named, those headings are what it would draft, and the test
    that expects the declared skeleton fails. Returns the prompts the drafter
    built, in order.
    """
    prompts: list = []
    outline = json.dumps({
        "title": "Stubbed Title",
        "sections": [{"heading": h, "summary": f"{h} summary"} for h in outline_headings],
    })

    def _llm(prompt, function="document_qna", max_tokens=2048):
        prompts.append(prompt)
        if "outline" in prompt.lower():
            return outline
        if section_text is not None:
            return section_text
        # Echo the first chunk id the section was shown, so the prose cites
        # what this section's evidence actually contained.
        m = re.search(r"\[chunk ([^\]\s·]+)", prompt)
        cid = m.group(1) if m else "none"
        return f"Prose for this section citing [source: chunk {cid}]."

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


def _generate(**kwargs):
    return doc_generator.generate_document(
        kwargs.pop("query", "zero trust whitepaper"),
        kwargs.pop("collection_id", "default"),
        tenant_id="t1",
        classification="CUI",
        **kwargs,
    )


_FREEFORM = tuple(f"Freeform Section {i}" for i in range(1, 10))  # nine, > the old cap


# ── 1. WHITEPAPER is DECLARED, and resolves with no edit to outline_contract ──

def test_whitepaper_declared_everywhere_the_other_templates_are():
    from tools.document_intelligence import blueprint, constants

    assert "WHITEPAPER" in constants.TEMPLATE_TYPES
    assert constants.TEMPLATE_SECTIONS.get("WHITEPAPER"), "no section skeleton declared"
    assert len(constants.TEMPLATE_SECTIONS["WHITEPAPER"]) > 6, (
        "a whitepaper skeleton that fits under the old [:6] cap could not prove the cap is gone"
    )
    mode = constants.TEMPLATE_TYPE_TO_WRITEGUARD_MODE.get("WHITEPAPER")
    assert mode in constants.WRITEGUARD_MODES
    tw = [t for t in blueprint._TEMPLATES if t["id"] == "WHITEPAPER"]
    assert len(tw) == 1 and tw[0]["category"] == "techwriter"


def test_whitepaper_resolves_through_the_contract_registry_untouched():
    from tools.quality import outline_contract

    contract = outline_contract.get_contract("WHITEPAPER")
    assert contract is not None
    from tools.document_intelligence.constants import TEMPLATE_SECTIONS
    assert list(contract.required) == TEMPLATE_SECTIONS["WHITEPAPER"]
    assert "WHITEPAPER" in outline_contract.list_contracts()
    # The registry resolves through the declaration; it must not have grown a
    # copy of it.
    src = Path(outline_contract.__file__).read_text(encoding="utf-8")
    assert "WHITEPAPER" not in src


# ── 2. template_id is LOAD-BEARING ──────────────────────────────────────────

def test_template_id_produces_the_declared_skeleton_not_a_freeform_outline(monkeypatch):
    """The acceptance criterion, verbatim.

    The outline stub offers nine freeform headings. With a template named, NONE
    of them may appear: the headings are the declared skeleton, every one of
    them, in declared order. At the merge base this drafts the first six
    freeform headings.
    """
    from tools.document_intelligence.constants import TEMPLATE_SECTIONS

    _patch_legacy(monkeypatch, lambda q: [_LegacyResult("c-1", f"Evidence for {q}.")])
    _patch_drafting(monkeypatch, outline_headings=_FREEFORM)

    result = _generate(template_id="WHITEPAPER")

    headings = [s.heading for s in result.sections]
    assert headings == TEMPLATE_SECTIONS["WHITEPAPER"]
    assert not any(h.startswith("Freeform") for h in headings)
    assert result.template_id == "WHITEPAPER"
    assert result.outline_source == "contract:WHITEPAPER"
    payload = result.to_dict()
    assert payload["outline_source"] == "contract:WHITEPAPER"
    assert payload["template_id"] == "WHITEPAPER"


def test_declared_skeleton_passes_its_own_outline_contract(monkeypatch):
    """What was drafted is exactly what ``check_outline`` would demand of it."""
    from tools.quality.outline_contract import check_outline, get_contract

    _patch_legacy(monkeypatch, lambda q: [_LegacyResult("c-1", f"Evidence for {q}.")])
    _patch_drafting(monkeypatch, outline_headings=_FREEFORM)

    result = _generate(template_id="SOP")

    report = check_outline(
        [{"heading": s.heading} for s in result.sections], get_contract("SOP"),
    )
    assert report["measurable"] is True
    assert report["findings"] == []


def test_unresolvable_template_id_falls_back_to_freeform_and_says_so(monkeypatch):
    """An id no declaration knows is NOT silently a whitepaper, nor an error.

    The freeform path is what the caller got before; what is new is that the
    result RECORDS that the template it asked for resolved to nothing.
    """
    _patch_legacy(monkeypatch, lambda q: [_LegacyResult("c-1", f"Evidence for {q}.")])
    _patch_drafting(monkeypatch, outline_headings=("Alpha", "Beta"))

    result = _generate(template_id="NO_SUCH_TEMPLATE")

    assert [s.heading for s in result.sections] == ["Alpha", "Beta"]
    assert result.outline_source == "freeform:unresolved:NO_SUCH_TEMPLATE"


def test_no_template_id_is_still_freeform(monkeypatch):
    _patch_legacy(monkeypatch, lambda q: [_LegacyResult("c-1", f"Evidence for {q}.")])
    _patch_drafting(monkeypatch, outline_headings=("Alpha", "Beta"))

    result = _generate()

    assert [s.heading for s in result.sections] == ["Alpha", "Beta"]
    assert result.outline_source == "freeform"


# ── 3. The [:6] cap is gone ──────────────────────────────────────────────────

def test_freeform_outline_is_no_longer_sliced_at_six(monkeypatch):
    _patch_legacy(monkeypatch, lambda q: [_LegacyResult("c-1", f"Evidence for {q}.")])
    _patch_drafting(monkeypatch, outline_headings=_FREEFORM)

    result = _generate()

    assert [s.heading for s in result.sections] == list(_FREEFORM)
    assert len(result.sections) == 9


def test_the_slice_literal_is_gone_from_the_source():
    src = Path(doc_generator.__file__).read_text(encoding="utf-8")
    assert "sections_meta[:6]" not in src


# ── 4. Per-section retrieval ────────────────────────────────────────────────

def test_each_section_is_drafted_against_its_own_retrieval(monkeypatch):
    """Two sections, two retrievals, two different evidence sets.

    The stub retriever answers with a chunk NAMED AFTER THE QUERY, so the only
    way two sections can cite different chunks is if two different queries were
    run. At the merge base every section is drafted against the one
    document-wide retrieval and both sections cite ``doc-wide``.
    """
    def _results_for(query):
        if query == "zero trust whitepaper":
            return [_LegacyResult("doc-wide", "Document-wide evidence.")]
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        return [_LegacyResult(f"sec-{slug}", f"Targeted evidence for {query}.")]

    calls = _patch_legacy(monkeypatch, _results_for)
    prompts = _patch_drafting(monkeypatch, outline_headings=("Alpha", "Beta"))

    result = _generate()

    assert len(result.sections) == 2
    alpha, beta = result.sections

    # A retrieval was run for each section, phrased for that section, and it
    # is not the document-wide query re-issued under a new name.
    queries = [c["query"] for c in calls]
    assert "zero trust whitepaper" in queries
    assert any("Alpha" in q for q in queries), queries
    assert any("Beta" in q for q in queries), queries

    # The evidence each section was SHOWN differs.
    alpha_prompt = next(p for p in prompts if "Section heading: Alpha" in p)
    beta_prompt = next(p for p in prompts if "Section heading: Beta" in p)
    assert "Targeted evidence for" in alpha_prompt
    assert alpha_prompt != beta_prompt
    assert re.search(r"chunk sec-[a-z0-9-]*alpha", alpha_prompt)
    assert re.search(r"chunk sec-[a-z0-9-]*beta", beta_prompt)
    assert not re.search(r"chunk sec-[a-z0-9-]*beta", alpha_prompt)

    # And the citations the sections CARRY differ too — the persisted record,
    # not only the prompt.
    alpha_ids = {c.get("chunk_id") for c in alpha.citations}
    beta_ids = {c.get("chunk_id") for c in beta.citations}
    assert alpha_ids != beta_ids
    assert any(str(i).startswith("sec-") for i in alpha_ids)
    # What the section cited validated against what THAT section was shown.
    assert alpha.citation_report["valid"] is True
    assert beta.citation_report["valid"] is True


def test_section_with_no_targeted_hits_keeps_the_document_wide_evidence(monkeypatch):
    """Targeted retrieval that finds nothing must not leave a section blind."""
    def _results_for(query):
        if query == "zero trust whitepaper":
            return [_LegacyResult("doc-wide", "Document-wide evidence.")]
        return []

    _patch_legacy(monkeypatch, _results_for)
    prompts = _patch_drafting(monkeypatch, outline_headings=("Alpha",))

    result = _generate()

    section_prompt = next(p for p in prompts if "Section heading: Alpha" in p)
    assert "chunk doc-wide" in section_prompt
    assert {c.get("chunk_id") for c in result.sections[0].citations} == {"doc-wide"}
    assert result.sections[0].citation_report["valid"] is True


def test_section_retrieval_can_be_stood_down_by_env(monkeypatch):
    """``ICDEV_DIC_SECTION_RETRIEVAL=0`` restores one retrieval per document."""
    monkeypatch.setenv("ICDEV_DIC_SECTION_RETRIEVAL", "0")
    calls = _patch_legacy(monkeypatch, lambda q: [_LegacyResult("doc-wide", "Evidence.")])
    _patch_drafting(monkeypatch, outline_headings=("Alpha", "Beta"))

    _generate()

    assert [c["query"] for c in calls] == ["zero trust whitepaper"]


# ── 5. The surfaces that enumerate templates follow the declaration ─────────

def test_techwriter_page_section_counts_are_derived_not_hand_typed():
    """The page carried its own ``{'STANDARD_GUIDE': 9, ...}`` map beside the
    declared skeletons — a copy that would have shown WHITEPAPER as 7 sections
    forever. It now renders ``section_counts`` the route derives from
    ``TEMPLATE_SECTIONS``."""
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
           / "techwriter.html").read_text(encoding="utf-8")
    assert "'STANDARD_GUIDE': 9" not in tpl
    assert "section_counts" in tpl
    assert "WHITEPAPER" in tpl  # the kind colour / example title branches


def test_docgen_doctype_alias_reaches_whitepaper():
    from tools.document_intelligence.constants import DOCGEN_DOCTYPE_TO_TEMPLATE
    assert DOCGEN_DOCTYPE_TO_TEMPLATE.get("whitepaper") == "WHITEPAPER"


def test_migration_derives_the_check_from_the_constant():
    """The CHECK on ``dic_documents.template_type`` is REBUILT from
    ``TEMPLATE_TYPES``, never respelled: a migration carrying the literal
    'WHITEPAPER' would be the next stale copy."""
    mig_root = REPO_ROOT / "tools" / "db" / "migrations"
    dirs = sorted(p for p in mig_root.iterdir()
                  if p.is_dir() and "template_type_whitepaper" in p.name)
    assert len(dirs) == 1, dirs
    up = dirs[0] / "up.py"
    assert up.exists(), "the constraint body must be generated, so this is a Python migration"
    src = up.read_text(encoding="utf-8")
    assert "TEMPLATE_TYPES" in src
    assert "'WHITEPAPER'" not in src and '"WHITEPAPER"' not in src
    assert not (dirs[0] / "up.sql").exists()
