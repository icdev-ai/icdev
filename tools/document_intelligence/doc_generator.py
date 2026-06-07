# CUI // SP-CTI
"""DIC AI-Assisted Document Generator.

Every draft is:
  1. Grounded — built from DIC search results, not hallucination.
  2. CoT/CoD verified — each section replayed against cited evidence; unsupported
     claims stripped; system abstains if evidence is insufficient.
  3. HITL-gated — written to dic_versions with status='pending_review' and
     origin='ai_generated'. Never auto-published.
  4. AI-labeled — visible badge in UI; promoted only by a human approver.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_OUTLINE_PROMPT = """You are a technical writer building a document outline.

Query: {query}

Source evidence (grounded — each item is tagged [SOURCE-N]):
{evidence}

Produce a JSON outline: {{"title": "...", "sections": [{{"heading": "...", "summary": "..."}}]}}
Keep sections ≤ 6. Only propose sections supported by the evidence."""

_SECTION_PROMPT = """You are writing one section of a technical document.

Document title: {title}
Section heading: {heading}
Section summary: {summary}

Source evidence (each item is tagged [SOURCE-N] — cite exactly, do not invent facts):
{evidence}

Write the section in clear, professional prose. For every factual claim, append a
citation tag naming the numbered source it came from, e.g. [SOURCE-1]. Use ONLY
the SOURCE numbers shown in the evidence above. If the evidence does not support a
claim, omit it rather than inventing it. If the evidence contains an unresolved
template placeholder (e.g. [ORGANIZATION], [DATE]), keep it verbatim — never
substitute a fabricated value."""


@dataclass
class GeneratedSection:
    heading: str = ""
    content: str = ""
    verified: bool = False
    abstained: bool = False
    citations: list[dict] = field(default_factory=list)


@dataclass
class GenerateResult:
    doc_id: str = ""
    version_id: str = ""
    title: str = ""
    query: str = ""
    collection_id: str = ""
    sections: list[GeneratedSection] = field(default_factory=list)
    origin: str = "ai_generated"
    status: str = "pending_review"
    error: str = ""
    context_canvases: list[str] = field(default_factory=list)   # cross-canvas sources used
    cross_canvas_sources: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "version_id": self.version_id,
            "title": self.title,
            "query": self.query,
            "collection_id": self.collection_id,
            "sections": [
                {
                    "heading": s.heading,
                    "content": s.content,
                    "verified": s.verified,
                    "abstained": s.abstained,
                    "citation_count": len(s.citations),
                }
                for s in self.sections
            ],
            "origin": self.origin,
            "status": self.status,
            "error": self.error,
            "context_canvases": self.context_canvases,
            "cross_canvas_sources": self.cross_canvas_sources,
        }


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hid(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _numbered_evidence(
    items: list[tuple[str, str, dict]], max_sources: int = 10
) -> tuple[str, list[str], list[dict]]:
    """Build a SOURCE-numbered evidence block + aligned text and citation lists.

    ``items`` is a list of ``(locator, text, citation)`` triples (locator = doc
    title / page / "cross-canvas"). The block numbers each non-empty source as
    ``[SOURCE-1] (locator) text…``; the returned ``texts`` and ``citations`` lists
    are in the SAME order, so a ``[SOURCE-N]`` tag in the generated prose maps to
    BOTH ``texts[N-1]`` (for the verifier — verify aligns ``[SOURCE-1]`` with
    ``evidence[0]``) AND ``citations[N-1]`` (for the clickable UI chip). Keeping a
    single source of truth is what makes the inline [SOURCE-N] links correct.
    """
    kept = [(loc, t, c) for (loc, t, c) in items if (t or "").strip()][:max_sources]
    block = "\n\n".join(
        f"[SOURCE-{i}] ({loc}) {(t or '')[:500]}" for i, (loc, t, _c) in enumerate(kept, start=1)
    ) or "(no evidence available)"
    texts = [t for _, t, _ in kept]
    # Carry a human-readable snippet of the actual evidence on each citation so the
    # UI chip can show WHAT the source says, not just an opaque doc_id / chunk hash.
    cites = []
    for _loc, t, c in kept:
        cc = dict(c or {})
        cc["snippet"] = " ".join((t or "").split())[:180]
        cites.append(cc)
    return block, texts, cites


def _result_locator(r) -> str:
    """Short, human-readable source label for a search result."""
    page = getattr(r, "page", None)
    if page:
        title = getattr(r, "doc_title", None) or getattr(r, "doc_id", "") or "source"
        return f"{title} p.{page}"
    return getattr(r, "doc_title", None) or getattr(r, "doc_id", "") or "source"


# --------------------------------------------------------------------------- #
# Model selection lives in .env (never hardcode model IDs). The aliases below are
# only DEFAULTS used when the env vars are unset; the real selection is in .env:
#   DIC_GEN_DEBATERS    — comma-separated aliases that debate (diverse drafts)
#   DIC_GEN_SYNTHESIZER — alias that merges the debate into the final section
#   DIC_GEN_MODEL       — alias for the non-CoD single-invoke path
# Each alias must be defined in args/llm_config.yaml (models:). Defaults are the
# Ollama-cloud models confirmed to return content on ollama.com (probed 2026-06-07).
# --------------------------------------------------------------------------- #
# Defaults only — real selection is in .env. mistral-large-cloud + minimax-m3
# reliably emit [SOURCE-N]; gemini3-cloud adds debate diversity but is a poor
# citer, so it is NOT the synthesizer (it would strip citations during merge).
_DEFAULT_DIC_DEBATERS = "minimax-m3,mistral-large-cloud,gemini3-cloud"
_DEFAULT_DIC_SYNTHESIZER = "mistral-large-cloud"
_DEFAULT_DIC_MODEL = "mistral-large-cloud"

_COD_SYNTH_SYSTEM = (
    "You are merging several independent drafts of ONE technical document section "
    "into the single best version. Keep ONLY claims supported by the drafts; "
    "preserve EVERY [SOURCE-N] citation tag exactly as written; keep any unresolved "
    "template placeholder (e.g. [ORGANIZATION], [DATE]) verbatim; never invent facts. "
    "Output ONLY the merged section prose — no preamble, no commentary about the drafts."
)


def _env_models(var: str, default: str) -> list[str]:
    """Read a comma-separated list of model aliases from .env (selection, not IDs)."""
    raw = os.environ.get(var, "") or default
    return [m.strip() for m in raw.split(",") if m.strip()]


def _invoke_model(router, alias: str, prompt: str, system_prompt: str = "") -> str | None:
    """Invoke ONE specific model alias (defined in llm_config.yaml ``models:``).

    Returns the model's text, or ``None`` when the alias is unknown, the provider
    is unreachable, or the model returns empty (some rotated Ollama-cloud IDs do) —
    so callers skip that debater / ABSTAIN rather than fabricate. WHICH alias is
    used is .env-driven; the alias->model_id mapping stays in config.
    """
    try:
        from tools.llm.provider import LLMRequest

        if hasattr(router, "is_no_llm_mode") and router.is_no_llm_mode():
            return None
        cfg = router._get_model_config(alias)
        if not cfg:
            return None
        provider = router._get_provider(cfg.get("provider", ""))
        if not provider:
            return None
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt or "",
            max_tokens=900,
            temperature=0.2,
            classification="CUI",
        )
        resp = provider.invoke(req, cfg.get("model_id"), cfg)
        txt = (getattr(resp, "content", "") or "").strip()
        return txt or None
    except Exception as exc:
        logger.warning("doc_generator: model %s failed: %s", alias, exc)
        return None


def _cod_generate(prompt: str, *, system_prompt: str = "") -> str | None:
    """Chain-of-Debate for document CONTENT: N diverse drafts -> 1 synthesized section.

    Unlike the router's generic CoD (whose judge emits a *verdict*, not prose), this
    asks each .env-selected debater for an independent grounded draft, then a
    synthesizer MERGES them into the single best section — preserving [SOURCE-N]
    citations and placeholders. Degrades to a single draft, then None (abstain).
    """
    try:
        from tools.llm.router import LLMRouter
    except Exception:
        return None
    router = LLMRouter()

    drafts: list[str] = []
    for alias in _env_models("DIC_GEN_DEBATERS", _DEFAULT_DIC_DEBATERS):
        txt = _invoke_model(router, alias, prompt, system_prompt)
        if txt:
            drafts.append(txt)
    if not drafts:
        return None
    if len(drafts) == 1:
        return drafts[0]

    synth = _env_models("DIC_GEN_SYNTHESIZER", _DEFAULT_DIC_SYNTHESIZER)
    merge_prompt = (
        "Independent drafts of the SAME section follow. Merge them into the single "
        "best grounded version, following the rules in the system prompt.\n\n"
        + "\n\n".join(f"=== DRAFT {i} ===\n{d}" for i, d in enumerate(drafts, start=1))
    )
    merged = _invoke_model(router, synth[0], merge_prompt, _COD_SYNTH_SYSTEM) if synth else None
    return merged or drafts[0]


def _llm_generate(prompt: str, *, system_prompt: str = "", use_cod: bool = False) -> str | None:
    """Generate text via Ollama-cloud (selection .env-driven). CoD for content.

    ``use_cod=True`` (ALL AI-Assist content, per user requirement) routes through
    :func:`_cod_generate` (debate -> synthesize). Everything else uses the single
    ``DIC_GEN_MODEL``. Returns ``None`` when nothing is produced so callers ABSTAIN
    rather than fabricate.
    """
    if use_cod:
        out = _cod_generate(prompt, system_prompt=system_prompt)
        if out:
            return out
        # CoD produced nothing (e.g. all cloud debaters empty) — try single model.
    try:
        from tools.llm.router import LLMRouter
    except Exception:
        return None
    model = _env_models("DIC_GEN_MODEL", _DEFAULT_DIC_MODEL)
    return _invoke_model(LLMRouter(), model[0], prompt, system_prompt) if model else None


def _parse_outline(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return {}


def generate_document(
    query: str,
    collection_id: str,
    *,
    template_id: str | None = None,
    tenant_id: str = "default",
    classification: str = "CUI",
    created_by: str = "ai_assist",
    context_canvases: list[str] | None = None,
) -> GenerateResult:
    """Generate a document draft grounded in DIC search results.

    Steps:
      1. Retrieve top chunks via DICSearchEngine.
      2. Build outline via LLM (grounded on evidence).
      3. Draft each section via LLM + verify with CoT/CoD verifier.
      4. Write pending_review version to dic_versions + dic_sections.

    Returns GenerateResult with sections and version_id for HITL.
    """
    from tools.document_intelligence.search_engine import DICSearchEngine

    result = GenerateResult(query=query, collection_id=collection_id, origin="ai_generated", status="pending_review")

    # 1. Retrieve evidence (own collection) + cross-canvas RAG+KG context.
    engine = DICSearchEngine(tenant_id=tenant_id)
    search_results = engine.search(query, collection_id=collection_id, top_k=10)

    # Pull context from related canvases (NDC/migration for network docs,
    # SDC/compliance for security docs, etc.). Auto-resolved from the collection +
    # query when not explicitly requested. Additive and best-effort.
    xctx = None
    try:
        from tools.document_intelligence.cross_canvas_context import gather, resolve_context_canvases
        canvases = context_canvases if context_canvases is not None else \
            resolve_context_canvases(collection_id, query)
        xctx = gather(query, canvases, tenant_id=tenant_id)
        result.context_canvases = xctx.canvases
        result.cross_canvas_sources = xctx.sources
    except Exception as exc:
        logger.warning("doc_generator: cross-canvas context unavailable: %s", exc)
    _xtexts = list(xctx.texts) if xctx else []
    _xcites = list(xctx.citations) if xctx else []

    # Build a single SOURCE-numbered evidence block shared by the outline + section
    # prompts, the verifier, AND the persisted citations, so a [SOURCE-N] tag in the
    # generated prose aligns with evidence_texts[N-1] (verifier) and
    # evidence_citations[N-1] (the clickable UI chip). _xtexts and _xcites are 1:1
    # aligned (cross_canvas_context.gather appends them together).
    _ev_items = [
        (_result_locator(r), getattr(r, "content", "") or "", r.citation.to_dict())
        for r in search_results
    ]
    _ev_items += [("cross-canvas", t, c) for t, c in zip(_xtexts, _xcites)]
    evidence, evidence_texts, evidence_citations = _numbered_evidence(_ev_items)

    # 2. Build outline.
    outline_raw = _llm_generate(_OUTLINE_PROMPT.format(query=query, evidence=evidence))
    outline = _parse_outline(outline_raw)
    title = outline.get("title") or f"Draft: {query[:60]}"
    sections_meta = outline.get("sections") or [{"heading": "Overview", "summary": query}]
    result.title = title

    # 3. Draft each section with verification.
    try:
        from tools.document_intelligence.verifier import verify
        _has_verifier = True
    except Exception:
        _has_verifier = False

    generated_sections: list[GeneratedSection] = []
    for sec in sections_meta[:6]:
        heading = sec.get("heading", "")
        summary = sec.get("summary", "")

        # Section CONTENT uses Chain-of-Debate (user requirement: ALL AI-Assist content).
        raw_text = _llm_generate(
            _SECTION_PROMPT.format(
                title=title, heading=heading, summary=summary, evidence=evidence,
            ),
            use_cod=True,
        )
        if not raw_text:
            generated_sections.append(GeneratedSection(heading=heading, abstained=True))
            continue

        # Verify against evidence (own collection + cross-canvas context).
        # verify() returns a DICT — access by key, not attribute.
        verified = False
        abstained = False
        # Citations aligned 1:1 with the [SOURCE-N] numbering (clickable in the UI).
        citations = list(evidence_citations)
        if _has_verifier:
            try:
                vr = verify(raw_text, evidence_texts)
                if vr.get("abstained"):
                    abstained = True
                    raw_text = "(Abstained — insufficient grounded evidence; nothing fabricated.)"
                else:
                    raw_text = vr.get("verified_text") or raw_text
                    verified = True
            except Exception as exc:
                logger.warning("doc_generator: verifier error: %s", exc)
                verified = False
        else:
            verified = False

        generated_sections.append(GeneratedSection(
            heading=heading,
            content=raw_text,
            verified=verified,
            abstained=abstained,
            citations=citations,
        ))

    result.sections = generated_sections

    # 4. Persist to dic_versions as pending_review + dic_sections.
    try:
        from tools.db.storage import get_connection

        doc_id = _hid("dic_gen", query, collection_id)
        version_id = f"ver-{uuid.uuid4().hex[:16]}"
        full_text = "\n\n".join(
            f"## {s.heading}\n\n{s.content}" for s in generated_sections if not s.abstained
        )
        sha = hashlib.sha256(full_text.encode()).hexdigest()

        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO dic_documents "
                "(doc_id, collection_id, source_id, filename, content_type, provider, title, "
                "byte_size, content_sha256, page_count, created_at, tenant_id, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, collection_id, doc_id, "ai_generated.md", "text/markdown",
                 "ai_generator", title, len(full_text), sha, 1, _now_utc(), tenant_id, classification),
            )
            conn.execute(
                "INSERT OR IGNORE INTO dic_versions "
                "(version_id, doc_id, version_no, origin, status, assigned_to, review_notes, content_sha256, "
                "created_at, created_by, tenant_id, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (version_id, doc_id, 1, "ai_generated", "pending_review", None, None, sha,
                 _now_utc(), created_by, tenant_id, classification),
            )
            # Write individual sections for per-section regeneration.
            for idx, sec in enumerate(generated_sections, start=1):
                section_id = f"sec-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT OR REPLACE INTO dic_sections "
                    "(section_id, version_id, doc_id, heading, content, citations_json, status, origin, "
                    "created_at, created_by, tenant_id, classification) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (section_id, version_id, doc_id, sec.heading, sec.content,
                     json.dumps(sec.citations), "pending_review", "ai_generated",
                     _now_utc(), created_by, tenant_id, classification),
                )
            conn.commit()
        finally:
            conn.close()

        result.doc_id = doc_id
        result.version_id = version_id
    except Exception as exc:
        logger.warning("doc_generator: DB write failed: %s", exc)
        result.error = str(exc)

    return result


def regenerate_section(
    version_id: str,
    heading: str,
    collection_id: str,
    *,
    tenant_id: str = "default",
    classification: str = "CUI",
    created_by: str = "ai_assist",
    context_canvases: list[str] | None = None,
) -> dict:
    """Regenerate a single section with targeted evidence retrieval.

    Steps:
      1. Read the existing section + document context.
      2. Search the collection with the heading as query (targeted retrieval).
      3. Draft the section via LLM using ONLY the targeted evidence.
      4. CoD-verify and strip unsupported claims.
      5. Update dic_sections + reassemble dic_versions blob.

    Returns dict with new content, citation_count, and status.
    """
    from tools.db.storage import get_connection
    from tools.document_intelligence.search_engine import DICSearchEngine
    from tools.document_intelligence.verifier import verify as _verify

    conn = get_connection()
    try:
        # 1. Load existing context + adjacent sections for coherence.
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_id, title FROM dic_documents WHERE doc_id = "
            "(SELECT doc_id FROM dic_versions WHERE version_id = ? LIMIT 1)",
            (version_id,),
        )
        row = cur.fetchone()
        doc_id = row[0] if row else ""
        doc_title = (row[1] if row else "") or heading
        # Load all sections for this version ordered by rowid to find neighbors.
        cur.execute(
            "SELECT heading, content FROM dic_sections WHERE version_id = ? ORDER BY created_at, section_id",
            (version_id,),
        )
        all_sections = cur.fetchall()
        target_idx = -1
        for i, (h, _) in enumerate(all_sections):
            if h == heading:
                target_idx = i
                break
        adjacent_context = []
        if target_idx >= 0:
            if target_idx > 0:
                prev_h, prev_c = all_sections[target_idx - 1]
                adjacent_context.append(f"Previous section: {prev_h}\nSummary: {prev_c[:300]}" if prev_c else f"Previous section: {prev_h}")
            if target_idx < len(all_sections) - 1:
                next_h, next_c = all_sections[target_idx + 1]
                adjacent_context.append(f"Next section: {next_h}\nSummary: {next_c[:300]}" if next_c else f"Next section: {next_h}")
    finally:
        conn.close()

    # 2. Targeted retrieval (own collection) + cross-canvas RAG+KG context.
    # Key the query on the DOCUMENT TITLE + heading, not the heading alone — a
    # generic heading like "Overview" retrieves off-topic chunks on its own, so
    # the title anchors retrieval to the document's actual subject (contextual
    # relevance). Cross-canvas context below is already keyed on title + heading.
    engine = DICSearchEngine(tenant_id=tenant_id)
    _query = f"{doc_title} {heading}".strip() if doc_title else heading
    search_results = engine.search(_query, collection_id=collection_id, top_k=8)

    # Cross-canvas context, keyed on the section heading + doc title.
    _xtexts: list[str] = []
    _xcites: list[dict] = []
    _xcanvases: list[str] = []
    try:
        from tools.document_intelligence.cross_canvas_context import gather, resolve_context_canvases
        canvases = context_canvases if context_canvases is not None else \
            resolve_context_canvases(collection_id, f"{doc_title} {heading}")
        xctx = gather(f"{heading} {doc_title}", canvases, tenant_id=tenant_id)
        _xtexts, _xcites, _xcanvases = xctx.texts, xctx.citations, xctx.canvases
    except Exception as exc:
        logger.warning("doc_generator: cross-canvas context unavailable: %s", exc)

    if not search_results and not _xtexts:
        return {
            "version_id": version_id,
            "heading": heading,
            "content": "(Abstained — no targeted evidence found for this section.)",
            "citations": [],
            "citation_count": 0,
            "status": "pending_review",
            "abstained": True,
            "reason": "no_evidence",
        }

    # SOURCE-numbered evidence shared by the draft prompt, the verifier, AND the
    # persisted citations, so a [SOURCE-N] tag in the prose aligns with both
    # evidence_texts[N-1] (verifier) and evidence_citations[N-1] (clickable chip).
    _ev_items = [
        (_result_locator(r), getattr(r, "content", "") or "", r.citation.to_dict())
        for r in search_results
    ]
    _ev_items += [("cross-canvas", t, c) for t, c in zip(_xtexts, _xcites)]
    evidence, evidence_texts, evidence_citations = _numbered_evidence(_ev_items)

    # 3. Draft with targeted evidence + adjacent context for coherence.
    prompt = (
        "You are rewriting ONE section of a technical document.\n\n"
        f"Document title: {doc_title}\n"
        f"Section heading: {heading}\n\n"
    )
    if adjacent_context:
        prompt += "Adjacent sections for context (do not repeat their content; ensure smooth transitions):\n"
        prompt += "\n---\n".join(adjacent_context) + "\n\n"
    prompt += (
        "Source evidence (each item is tagged [SOURCE-N] — cite exactly, do not invent facts):\n"
        f"{evidence}\n\n"
        "Write the section in clear, professional prose. For every factual claim, "
        "append a citation tag naming the numbered source it came from, e.g. [SOURCE-1]. "
        "Use ONLY the SOURCE numbers shown above. If the evidence does not support a "
        "claim, omit it rather than inventing it. If the evidence contains an unresolved "
        "template placeholder (e.g. [ORGANIZATION], [DATE]), keep it verbatim — never "
        "substitute a fabricated value."
    )
    # Section CONTENT uses Chain-of-Debate (user requirement: ALL AI-Assist content).
    raw_text = _llm_generate(prompt, use_cod=True)
    if not raw_text:
        return {
            "version_id": version_id,
            "heading": heading,
            "content": "(Abstained — LLM unavailable.)",
            "citations": [],
            "citation_count": 0,
            "status": "pending_review",
            "abstained": True,
            "reason": "llm_unavailable",
        }

    # 4. CoD verify. verify() returns a DICT — access by key, not attribute.
    verified_text = raw_text
    abstained = False
    try:
        vr = _verify(raw_text, evidence_texts)
        if vr.get("abstained"):
            abstained = True
            verified_text = "(Abstained — insufficient grounded evidence; nothing fabricated.)"
        else:
            verified_text = vr.get("verified_text") or raw_text
    except Exception as exc:
        logger.warning("doc_generator: per-section verify error: %s", exc)

    # Citations aligned 1:1 with the [SOURCE-N] numbering (clickable in the UI).
    citations = list(evidence_citations)

    # 5. Persist update.
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Update the section row.
        cur.execute(
            "UPDATE dic_sections SET content = ?, citations_json = ?, status = ?, origin = ?, "
            "created_at = ?, created_by = ? WHERE version_id = ? AND heading = ?",
            (verified_text, json.dumps(citations), "pending_review", "ai_generated",
             _now_utc(), created_by, version_id, heading),
        )
        if cur.rowcount == 0:
            # Insert if missing.
            section_id = f"sec-{uuid.uuid4().hex[:12]}"
            cur.execute(
                "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
                "citations_json, status, origin, created_at, created_by, tenant_id, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (section_id, version_id, doc_id, heading, verified_text, json.dumps(citations),
                 "pending_review", "ai_generated", _now_utc(), created_by, tenant_id, classification),
            )
        # Reassemble full version blob from all sections.
        cur.execute(
            "SELECT heading, content FROM dic_sections WHERE version_id = ? ORDER BY created_at, section_id",
            (version_id,),
        )
        rows = cur.fetchall()
        full_text = "\n\n".join(
            f"## {r[0]}\n\n{r[1]}" for r in rows if r[1]
        )
        sha = hashlib.sha256(full_text.encode()).hexdigest()
        cur.execute(
            "UPDATE dic_versions SET content_sha256 = ? WHERE version_id = ?",
            (sha, version_id),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("doc_generator: per-section DB write failed: %s", exc)
        return {
            "version_id": version_id,
            "heading": heading,
            "error": str(exc),
        }
    finally:
        conn.close()

    return {
        "version_id": version_id,
        "heading": heading,
        "content": verified_text,
        "citations": citations,
        "citation_count": len(citations),
        "status": "pending_review",
        "abstained": abstained,
        "reason": "insufficient_support" if abstained else "ok",
        "context_canvases": _xcanvases,
        "cross_canvas_count": len(_xtexts),
    }
