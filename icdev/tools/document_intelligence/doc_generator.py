# CUI // SP-CTI
"""DIC AI-Assisted Document Generator.

Every draft is:
  1. Grounded — built from full DIC KB search + KG entity expansion + session uploads.
  2. CoT-reasoned — each section uses Chain-of-Thought (reasoner→critic→synthesizer)
     when evidence is substantial (>500 chars).
  3. CoD-compressed — sections >800 words get a Chain-of-Density pass to tighten prose.
  4. Confidence-gated — verifier attribution score gates inclusion (≥0.7 = include;
     0.4–0.69 = include + HITL flag; <0.4 = abstain).
  5. HITL-gated — written to dic_versions with status='pending_review' and
     origin='ai_generated'. Never auto-published.
  6. AI-labeled — visible badge in UI; promoted only by a human approver.

Evidence priority (highest → lowest):
  OPERATOR > EMAIL > SESSION-DOC > DIC-KB > KG-ENTITY
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Evidence tier labels — must match context_builder.py constants
_TIER_OPERATOR = "OPERATOR"
_TIER_EMAIL = "EMAIL"
_TIER_DIC_KB = "DIC-KB"
_TIER_KG = "KG-ENTITY"

_OUTLINE_PROMPT = """You are a technical writer building a document outline.

Query: {query}

Source evidence (grounded):
{evidence}

Produce a JSON outline: {{"title": "...", "sections": [{{"heading": "...", "summary": "..."}}]}}
Keep sections ≤ 6. Only propose sections supported by the evidence."""

_SECTION_PROMPT = """You are writing one section of a technical document.

Document title: {title}
Section heading: {heading}
Section summary: {summary}

Source evidence (cite exactly — do not invent facts):
{evidence}

Write the section in clear, professional prose. For every factual claim write a
bracketed citation: [source: chunk {chunk_id}]. If the evidence does not support
a claim, omit it rather than inventing it."""

# Minimum evidence length to justify CoT (cheaper direct call below this)
_COT_EVIDENCE_THRESHOLD = 500
# Minimum section word count to trigger CoD compression
_COD_WORD_THRESHOLD = 800


@dataclass
class GeneratedSection:
    heading: str = ""
    content: str = ""
    verified: bool = False
    abstained: bool = False
    citations: list[dict] = field(default_factory=list)
    confidence: float = 1.0
    low_confidence: bool = False
    hitl_note: str = ""


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
                    "confidence": round(s.confidence, 3),
                    "low_confidence": s.low_confidence,
                    "hitl_note": s.hitl_note,
                }
                for s in self.sections
            ],
            "origin": self.origin,
            "status": self.status,
            "error": self.error,
        }


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hid(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _evidence_block(results: list) -> str:
    lines = []
    for r in results[:8]:
        chunk_id = getattr(r, "chunk_id", "?")
        content = getattr(r, "content", "")[:300]
        lines.append(f"[{_TIER_DIC_KB}][chunk {chunk_id}] {content}")
    return "\n\n".join(lines) or "(no evidence available)"


def _targeted_evidence_block(results: list) -> str:
    """Same as _evidence_block but includes doc title and page for richer citations."""
    lines = []
    for r in results[:8]:
        chunk_id = getattr(r, "chunk_id", "?")
        doc_title = getattr(r, "doc_title", None) or getattr(r, "doc_id", "")
        page = getattr(r, "page", None)
        content = getattr(r, "content", "")[:400]
        loc = f"p.{page}" if page else doc_title
        lines.append(f"[{_TIER_DIC_KB}][chunk {chunk_id} · {loc}] {content}")
    return "\n\n".join(lines) or "(no evidence available)"


def _build_evidence_pool(
    search_results: list,
    kg_chunks: list[dict],
    supplemental_text: str,
    source_text_fallback: str,
) -> str:
    """Assemble evidence in priority order: OPERATOR > EMAIL/SOURCE > DIC-KB > KG-ENTITY."""
    parts: list[str] = []

    # OPERATOR — always first
    if supplemental_text and supplemental_text.strip():
        parts.append(
            f"[{_TIER_OPERATOR} — AUTHORITATIVE — highest priority, overrides conflicts]\n"
            f"{supplemental_text[:4000]}"
        )

    # DIC-KB chunks (includes SESSION-DOC and EMAIL tiers labelled by context_builder)
    if search_results:
        parts.append(_evidence_block(search_results))
    elif source_text_fallback:
        parts.append(
            f"[Extracted from source documents]\n{source_text_fallback}"
        )

    # KG entity expansion chunks
    for chunk in (kg_chunks or [])[:5]:
        text = chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
        if text.strip():
            parts.append(text[:1500])

    return "\n\n".join(parts) or "(no evidence available)"


_LLM_TIMEOUT = 45  # seconds — guards against CLI-bridge poll thread hanging


def _llm_generate(
    prompt: str, *, function: str = "document_qna", max_tokens: int = 2048
) -> str | None:
    """Call the ICDEV LLM router using the canonical ``invoke(fn, LLMRequest)`` API.

    Degrades gracefully to ``None`` when no LLM provider is available (air-gapped
    / headless mode) so the caller can abstain rather than hallucinate.
    Times out after _LLM_TIMEOUT seconds to avoid hanging on CLI-bridge poll loops.
    """
    import concurrent.futures as _cf
    try:
        try:
            from icdev.tools.llm.router import LLMRouter
            from icdev.tools.llm.provider import LLMRequest
        except ImportError:
            from tools.llm.router import LLMRouter  # type: ignore[import]
            from tools.llm.provider import LLMRequest  # type: ignore[import]
        router = LLMRouter()
        if router.is_no_llm_mode():
            logger.info("doc_generator: no-LLM mode; skipping generation")
            return None
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            skip_injection_scan=True,
        )
        _ex = _cf.ThreadPoolExecutor(max_workers=1)
        _fut = _ex.submit(router.invoke, function, req)
        try:
            resp = _fut.result(timeout=_LLM_TIMEOUT)
            if resp and getattr(resp, "content", None):
                return resp.content.strip() or None
        except _cf.TimeoutError:
            pass  # timed out — CLI-bridge not running; degrade gracefully
        finally:
            _ex.shutdown(wait=False)  # don't block on CLI-bridge poll thread
    except Exception as exc:
        logger.warning("doc_generator: LLM call failed: %s", exc)
    return None


def _cot_generate(
    heading: str,
    evidence: str,
    *,
    function: str = "document_qna",
) -> str | None:
    """Generate section text via Chain-of-Thought (reasoner → critic → synthesizer).

    Falls back to _llm_generate() if ChainOrchestrator is unavailable.
    """
    try:
        try:
            from icdev.tools.llm.chain_orchestrator import ChainOrchestrator
            from icdev.tools.llm.provider import LLMRequest
        except ImportError:
            from tools.llm.chain_orchestrator import ChainOrchestrator  # type: ignore[import]
            from tools.llm.provider import LLMRequest  # type: ignore[import]

        orchestrator = ChainOrchestrator()
        req = LLMRequest(
            messages=[{"role": "user", "content": f"Write the '{heading}' section using this evidence:\n\n{evidence}"}],
            max_tokens=2048,
            skip_injection_scan=True,
        )
        result = orchestrator.invoke_chain_of_thought(function, req)
        return result.content.strip() if result and result.content else None
    except Exception as exc:
        logger.debug("doc_generator: CoT failed (%s), falling back to direct LLM", exc)
        return None


def _cod_compress(text: str, heading: str, *, function: str = "document_qna") -> str:
    """Apply Chain-of-Density compression to long sections (>_COD_WORD_THRESHOLD words).

    Returns the compressed text, or the original if CoD is unavailable.
    """
    if len(text.split()) <= _COD_WORD_THRESHOLD:
        return text
    try:
        try:
            from icdev.tools.llm.chain_orchestrator import ChainOrchestrator
            from icdev.tools.llm.provider import LLMRequest
        except ImportError:
            from tools.llm.chain_orchestrator import ChainOrchestrator  # type: ignore[import]
            from tools.llm.provider import LLMRequest  # type: ignore[import]

        orchestrator = ChainOrchestrator()
        compress_prompt = (
            f"Compress the following '{heading}' section to be denser and more concise "
            f"without losing any factual claims or citation references:\n\n{text[:4000]}"
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": compress_prompt}],
            max_tokens=1500,
            skip_injection_scan=True,
        )
        result = orchestrator.invoke_chain_of_debate(function, req)
        compressed = result.content.strip() if result and result.content else None
        if compressed and len(compressed) > 100:
            logger.info(
                "doc_generator: CoD compressed '%s' from %d → %d words",
                heading, len(text.split()), len(compressed.split()),
            )
            return compressed
    except Exception as exc:
        logger.debug("doc_generator: CoD compression failed (%s), using original", exc)
    return text


def _compute_section_confidence(verdict) -> float:
    """Compute a [0, 1] confidence score from a verifier VerifyResult."""
    try:
        claims = getattr(verdict, "claims", [])
        if not claims:
            return 1.0 if not getattr(verdict, "abstained", False) else 0.0
        supported = [c for c in claims if getattr(c, "supported", False)]
        return len(supported) / len(claims)
    except Exception:
        return 1.0


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
    collection_id: str | None,
    *,
    template_id: str | None = None,
    tenant_id: str = "default",
    classification: str = "CUI",
    created_by: str = "ai_assist",
    supplemental_text: str = "",
    kg_chunks: list[dict] | None = None,
) -> "GenerateResult":
    """Generate a document draft grounded in DIC search results.

    Steps:
      1. Retrieve top chunks via DICSearchEngine (full-KB when collection_id=None;
         falls back to session-scoped if zero results).
      2. Merge evidence pool: OPERATOR > SESSION-DOC/EMAIL > DIC-KB > KG-ENTITY.
      3. Build outline via LLM (grounded on evidence).
      4. Draft each section via CoT (when evidence > 500 chars) or direct LLM.
      5. Apply CoD compression for long sections (> 800 words).
      6. Verify each section; gate by confidence threshold (≥0.7 = include,
         0.4–0.69 = flag, <0.4 = abstain).
      7. Write pending_review version to dic_versions + dic_sections.

    Returns GenerateResult with sections and version_id for HITL.
    """
    from tools.document_intelligence.search_engine import DICSearchEngine

    result = GenerateResult(
        query=query,
        collection_id=collection_id or "",
        origin="ai_generated",
        status="pending_review",
    )

    # 1. Retrieve evidence — full KB first, fall back to session-scoped
    engine = DICSearchEngine(tenant_id=tenant_id)
    search_results = engine.search(query, collection_id=None, top_k=10)
    if not search_results and collection_id:
        search_results = engine.search(query, collection_id=collection_id, top_k=10)
        if search_results:
            logger.info(
                "doc_generator: full-KB search returned 0; fell back to collection %s (%d hits)",
                collection_id, len(search_results),
            )

    # When no DIC chunks exist, fall back to source text embedded in query string
    _source_text_fallback = ""
    if not search_results:
        _marker = "Source document content:"
        _idx = query.find(_marker)
        if _idx >= 0:
            _source_text_fallback = query[_idx + len(_marker):].strip()[:8000]

    # 2. Build evidence pool with tier labels
    evidence = _build_evidence_pool(
        search_results=search_results,
        kg_chunks=kg_chunks or [],
        supplemental_text=supplemental_text,
        source_text_fallback=_source_text_fallback,
    )

    # 3. Build outline
    outline_raw = _llm_generate(_OUTLINE_PROMPT.format(query=query, evidence=evidence[:6000]))
    outline = _parse_outline(outline_raw)
    title = outline.get("title") or f"Draft: {query[:60]}"
    sections_meta = outline.get("sections") or [{"heading": "Overview", "summary": query}]
    result.title = title

    # 4-6. Draft, CoT/CoD, verify, confidence-gate each section.
    try:
        from tools.document_intelligence.verifier import verify
        _has_verifier = True
    except Exception:
        _has_verifier = False

    flagged_headings: list[str] = []
    generated_sections: list[GeneratedSection] = []

    for sec in sections_meta[:6]:
        heading = sec.get("heading", "")
        summary = sec.get("summary", "")

        # Build section-specific evidence
        sec_evidence = evidence  # per-section targeted retrieval would improve this further

        # 4. Draft — CoT when evidence is rich, direct otherwise
        raw_text: str | None = None
        if len(sec_evidence) > _COT_EVIDENCE_THRESHOLD:
            raw_text = _cot_generate(heading, sec_evidence)

        if not raw_text:
            raw_text = _llm_generate(
                _SECTION_PROMPT.format(
                    title=title, heading=heading, summary=summary, evidence=sec_evidence,
                    chunk_id=search_results[0].chunk_id if search_results else "N/A",
                )
            )

        if not raw_text:
            # LLM unavailable — synthesize from source text so the document has real content
            if _source_text_fallback:
                raw_text = (
                    f"**{heading}**\n\n"
                    f"{summary}\n\n"
                    f"*Synthesized from source document content:*\n\n"
                    f"{_source_text_fallback[:3000]}"
                )
            else:
                generated_sections.append(
                    GeneratedSection(heading=heading, abstained=True, confidence=0.0)
                )
                continue

        # 5. CoD compression for verbose sections
        raw_text = _cod_compress(raw_text, heading)

        # 6. Verify and confidence-gate
        confidence = 1.0
        verified = False
        abstained = False
        low_confidence = False
        hitl_note = ""
        citations = [r.citation.to_dict() for r in search_results[:3]] if search_results else []

        if _has_verifier and search_results:
            try:
                vr = verify(raw_text, [r.content for r in search_results])
                confidence = _compute_section_confidence(vr)

                if vr.abstained:
                    abstained = True
                    confidence = 0.0
                    raw_text = "(Abstained — insufficient evidence to support this section.)"
                else:
                    raw_text = vr.verified_text or raw_text
                    verified = True
            except Exception as exc:
                logger.warning("doc_generator: verifier error: %s", exc)

        # Apply confidence threshold gate
        if not abstained:
            if confidence >= 0.7:
                pass  # include normally
            elif confidence >= 0.4:
                low_confidence = True
                hitl_note = (
                    f"⚠ Confidence {confidence:.0%} — below 0.7 threshold; "
                    f"verify against source documents before publishing."
                )
                flagged_headings.append(heading)
                raw_text = f"{raw_text}\n\n> {hitl_note}"
            else:
                # Very low confidence — exclude (abstain)
                abstained = True
                confidence = confidence
                logger.info(
                    "doc_generator: section '%s' excluded — confidence %.2f < 0.4",
                    heading, confidence,
                )

        generated_sections.append(GeneratedSection(
            heading=heading,
            content=raw_text,
            verified=verified,
            abstained=abstained,
            citations=citations,
            confidence=confidence,
            low_confidence=low_confidence,
            hitl_note=hitl_note,
        ))

    result.sections = generated_sections

    # 7. Persist to dic_versions as pending_review + dic_sections
    try:
        from tools.db.storage import get_connection

        doc_id = _hid("dic_gen", query, collection_id or "")
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
                (doc_id, collection_id or "", doc_id, "ai_generated.md", "text/markdown",
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
    patch_mode: bool = False,
    change_context: str = "",
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
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_id, title FROM dic_documents WHERE doc_id = "
            "(SELECT doc_id FROM dic_versions WHERE version_id = ? LIMIT 1)",
            (version_id,),
        )
        row = cur.fetchone()
        doc_id = row[0] if row else ""
        doc_title = (row[1] if row else "") or heading
        cur.execute(
            "SELECT heading, content FROM dic_sections WHERE version_id = ? ORDER BY section_id",
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

    engine = DICSearchEngine(tenant_id=tenant_id)
    search_results = engine.search(heading, collection_id=collection_id, top_k=8)
    evidence = _targeted_evidence_block(search_results)

    if not search_results:
        return {
            "version_id": version_id,
            "heading": heading,
            "content": "(Abstained — no targeted evidence found for this section.)",
            "citation_count": 0,
            "status": "pending_review",
            "abstained": True,
        }

    if patch_mode:
        prompt = (
            "You are making a TARGETED PATCH to ONE section of a technical document.\n\n"
            f"Document title: {doc_title}\n"
            f"Section heading: {heading}\n\n"
        )
        if change_context:
            prompt += f"Change context (system event that triggered this update):\n{change_context}\n\n"
        if adjacent_context:
            prompt += "Adjacent sections for context:\n"
            prompt += "\n---\n".join(adjacent_context) + "\n\n"
        prompt += (
            "Source evidence (cite exactly — do not invent facts):\n"
            f"{evidence}\n\n"
            "TASK: Given the current section content and the change context above, produce ONLY the "
            "minimal edit that incorporates the change. Return ONLY the affected paragraph(s). "
            "Use [KEEP] on a line by itself to indicate unchanged text that should be preserved. "
            "For every new factual claim write a bracketed citation: [source: chunk <id>]. "
            "If the evidence does not support the change, write [REVIEW REQUIRED] and explain what "
            "information is needed. Do NOT rewrite the entire section."
        )
    else:
        prompt = (
            "You are rewriting ONE section of a technical document.\n\n"
            f"Document title: {doc_title}\n"
            f"Section heading: {heading}\n\n"
        )
        if change_context:
            prompt += f"Change context:\n{change_context}\n\n"
        if adjacent_context:
            prompt += "Adjacent sections for context (do not repeat their content; ensure smooth transitions):\n"
            prompt += "\n---\n".join(adjacent_context) + "\n\n"
        prompt += (
            "Source evidence (cite exactly — do not invent facts):\n"
            f"{evidence}\n\n"
            "Write the section in clear, professional prose. "
            "For every factual claim write a bracketed citation: [source: chunk <id>]. "
            "If the evidence does not support a claim, omit it rather than inventing it."
        )
    raw_text = _llm_generate(prompt)
    if not raw_text:
        return {
            "version_id": version_id,
            "heading": heading,
            "content": "(Abstained — LLM unavailable.)",
            "citation_count": 0,
            "status": "pending_review",
            "abstained": True,
        }

    verified_text = raw_text
    abstained = False
    try:
        vr = _verify(raw_text, [r.content for r in search_results])
        if vr.abstained:
            abstained = True
            verified_text = "(Abstained — insufficient evidence to support this section.)"
        else:
            verified_text = vr.verified_text or raw_text
    except Exception as exc:
        logger.warning("doc_generator: per-section verify error: %s", exc)

    citations = [r.citation.to_dict() for r in search_results[:5]]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dic_sections SET content = ?, citations_json = ?, status = ?, origin = ?, "
            "created_at = ?, created_by = ? WHERE version_id = ? AND heading = ?",
            (verified_text, json.dumps(citations), "pending_review", "ai_generated",
             _now_utc(), created_by, version_id, heading),
        )
        if cur.rowcount == 0:
            section_id = f"sec-{uuid.uuid4().hex[:12]}"
            cur.execute(
                "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
                "citations_json, status, origin, created_at, created_by, tenant_id, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (section_id, version_id, doc_id, heading, verified_text, json.dumps(citations),
                 "pending_review", "ai_generated", _now_utc(), created_by, tenant_id, classification),
            )
        cur.execute(
            "SELECT heading, content FROM dic_sections WHERE version_id = ? ORDER BY section_id",
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
        "citation_count": len(citations),
        "status": "pending_review",
        "abstained": abstained,
    }
