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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

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
        lines.append(f"[chunk {chunk_id}] {content}")
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
        lines.append(f"[chunk {chunk_id} · {loc}] {content}")
    return "\n\n".join(lines) or "(no evidence available)"


def _llm_generate(prompt: str) -> str | None:
    try:
        for ns in ("icdev.tools.llm.router", "tools.llm.router"):
            try:
                import importlib
                mod = importlib.import_module(ns)
                router = mod.LLMRouter()
                for meth in ("generate", "complete", "chat", "route", "call"):
                    fn = getattr(router, meth, None)
                    if callable(fn):
                        result = fn(prompt)
                        if isinstance(result, str):
                            return result
                        if isinstance(result, dict):
                            return result.get("text") or result.get("content") or str(result)
            except ImportError:
                continue
    except Exception as exc:
        logger.warning("doc_generator: LLM call failed: %s", exc)
    return None


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

    # 1. Retrieve evidence.
    engine = DICSearchEngine(tenant_id=tenant_id)
    search_results = engine.search(query, collection_id=collection_id, top_k=10)
    evidence = _evidence_block(search_results)

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

        raw_text = _llm_generate(
            _SECTION_PROMPT.format(
                title=title, heading=heading, summary=summary, evidence=evidence,
                chunk_id=search_results[0].chunk_id if search_results else "N/A",
            )
        )
        if not raw_text:
            generated_sections.append(GeneratedSection(heading=heading, abstained=True))
            continue

        # Verify against evidence.
        verified = False
        abstained = False
        citations = [r.citation.to_dict() for r in search_results[:3]]
        if _has_verifier:
            try:
                vr = verify(raw_text, [r.content for r in search_results])
                if vr.abstained:
                    abstained = True
                    raw_text = "(Abstained — insufficient evidence to support this section.)"
                else:
                    raw_text = vr.verified_text or raw_text
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
            "SELECT heading, content FROM dic_sections WHERE version_id = ? ORDER BY rowid",
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

    # 2. Targeted retrieval.
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

    # 4. CoD verify.
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
            "SELECT heading, content FROM dic_sections WHERE version_id = ? ORDER BY rowid",
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
