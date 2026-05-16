#!/usr/bin/env python3
# CUI // SP-PROPIN
"""LLM bridge: wraps the GovProposal LLM router for proposal generation.

Routes proposal-specific tasks through the shared ICDEV two-tier model:
  - qwen3 (local Ollama) drafts the section
  - Claude (Bedrock) reviews and refines

All prompts are masked through the exclusion_service before LLM calls.
SHA-256 hashes of prompts/responses are written to ai_telemetry (AU-2).

Functions exposed to the rest of the RFX engine:
  generate_section()   — draft one proposal section with RAG context
  extract_requirements_llm() — LLM-assisted requirement extraction
  summarize_research() — condense web research into a usable brief
  score_section()      — evaluate section quality vs. RFP requirements
"""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# Lazy imports to avoid circular deps
_router = None


def _get_router():
    global _router
    if _router is None:
        try:
            from tools.llm.router import LLMRouter
            _router = LLMRouter()
        except Exception:
            _router = None
    return _router


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _log_telemetry(function: str, prompt: str, response: str,
                   model_id: str, provider: str,
                   input_tokens: int = 0, output_tokens: int = 0,
                   proposal_id: Optional[str] = None) -> None:
    """Write a telemetry record (prompt/response hashed, not stored raw)."""
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO ai_telemetry
                (id, project_id, agent_id, model_id, provider, function,
                 prompt_hash, response_hash, input_tokens, output_tokens,
                 classification, logged_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()),
            proposal_id, "rfx-engine", model_id, provider, function,
            _sha256(prompt), _sha256(response),
            input_tokens, output_tokens,
            "CUI // SP-PROPIN",
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
    finally:
        conn.close()


class LLMUnavailableError(RuntimeError):
    """Raised when all LLM providers fail or the router is unavailable."""


def _invoke(prompt: str, function: str = "proposal_generation",
            max_tokens: int = 2048,
            proposal_id: Optional[str] = None) -> str:
    """Call the LLM router. Returns response text or raises LLMUnavailableError."""
    import logging
    _logger = logging.getLogger(__name__)

    router = _get_router()
    if router is None:
        _logger.error("LLM router not loaded for function=%s", function)
        raise LLMUnavailableError("LLM router could not be initialised.")

    try:
        from tools.llm.provider import LLMRequest
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        response = router.invoke(function, request)
        text = response.content if hasattr(response, "content") else str(response)

        _log_telemetry(
            function=function,
            prompt=prompt,
            response=text,
            model_id=getattr(response, "model_id", "unknown"),
            provider=getattr(response, "provider", "unknown"),
            input_tokens=getattr(response, "input_tokens", 0),
            output_tokens=getattr(response, "output_tokens", 0),
            proposal_id=proposal_id,
        )
        return text

    except LLMUnavailableError:
        raise
    except Exception as e:
        _logger.error("LLM invocation failed for function=%s: %s", function, e, exc_info=True)
        raise LLMUnavailableError(str(e)) from e


def _looks_like_error(text: str) -> bool:
    """Heuristic: detect if LLM response is actually an error message."""
    if not text:
        return True
    error_markers = [
        "[LLM error:", "Traceback (", "Error:", "Exception:",
        "All providers in chain", "model '", "not found", "Connection refused",
        "HTTP 503", "HTTP 500", "Rate limit", "timeout", "Unable to connect",
    ]
    lower = text.lower()
    return any(marker.lower() in lower for marker in error_markers)


# ── section generation ─────────────────────────────────────────────────────────

def generate_section(
    section_title: str,
    volume: str,
    rfp_context: str,
    rag_chunks: list[dict],
    kb_entries: list[dict],
    win_themes: Optional[list[str]] = None,
    pricing_context: Optional[str] = None,
    proposal_id: Optional[str] = None,
    mask_fn=None,
) -> dict:
    """Generate a proposal section draft using RAG context + LLM.

    Args:
        section_title:   e.g., "Technical Approach", "Management Plan"
        volume:          'technical' | 'management' | 'cost' | etc.
        rfp_context:     Relevant excerpts from the RFI/RFP document
        rag_chunks:      Top-k retrieved chunks from past proposals / KB
        kb_entries:      Matched KB entries (capabilities, past perf, etc.)
        win_themes:      List of win theme strings from win_themes table
        pricing_context: Formatted pricing scenario string (for cost volumes)
        proposal_id:     Links telemetry to the proposal
        mask_fn:         Optional callable(text) -> masked_text for exclusion

    Returns dict with draft, rag_sources used, model_used, prompt_hash.
    """
    from tools.rfx.exclusion_service import apply_mask

    # Format RAG context
    rag_text = ""
    rag_source_ids = []
    if rag_chunks:
        excerpts = []
        for c in rag_chunks[:5]:
            rag_source_ids.append(c.get("chunk_id") or c.get("entry_id", ""))
            src = c.get("filename") or c.get("title", "source")
            excerpts.append(f"[Source: {src}]\n{c['content'][:600]}")
        rag_text = "\n\n---\n\n".join(excerpts)

    kb_text = ""
    if kb_entries:
        kb_lines = []
        for e in kb_entries[:3]:
            kb_lines.append(f"• {e.get('title', '')}: {e.get('content', '')[:400]}")
        kb_text = "\n".join(kb_lines)

    themes_text = ""
    if win_themes:
        themes_text = "\n".join(f"• {t}" for t in win_themes[:5])

    cost_text = ""
    if volume == "cost" and pricing_context:
        cost_text = f"\n\nPRICING DATA TO INCORPORATE:\n{pricing_context}"

    prompt = f"""You are a senior government proposal writer. Write a compelling,
compliant {section_title} section for a government proposal.

VOLUME: {volume.replace('_', ' ').title()}
SECTION: {section_title}

RFP REQUIREMENTS (address all of these):
{rfp_context[:1500] if rfp_context else 'See full RFP document.'}

RELEVANT PAST PERFORMANCE AND CAPABILITIES:
{rag_text[:2000] if rag_text else 'N/A'}

COMPANY CAPABILITIES AND KB CONTENT:
{kb_text[:1000] if kb_text else 'N/A'}

WIN THEMES TO WEAVE IN:
{themes_text if themes_text else 'N/A'}
{cost_text}

INSTRUCTIONS:
- Write in active voice, present tense
- Use specific, quantified claims (percentages, timeframes, numbers)
- Reference company capabilities from KB content above
- Structure with clear headings using markdown (##, ###)
- End each major point with a benefit statement (e.g., "...ensuring mission success")
- Target 400-600 words
- Classification: CUI // SP-PROPIN

Write the {section_title} section now:"""

    # Mask sensitive terms before sending to LLM
    masked_prompt, mapping = apply_mask(prompt)

    draft_masked = _invoke(
        masked_prompt,
        function="proposal_generation",
        max_tokens=2048,
        proposal_id=proposal_id,
    )

    # Merge back
    from tools.rfx.exclusion_service import merge_back
    draft = merge_back(draft_masked, mapping)

    if _looks_like_error(draft):
        raise LLMUnavailableError(
            "AI generation returned an error-like response. Please try again."
        )

    return {
        "section_title": section_title,
        "volume": volume,
        "content_draft": draft,
        "rag_sources": rag_source_ids,
        "source_type": "hybrid" if rag_chunks else "ai",
        "prompt_hash": _sha256(masked_prompt),
    }


# ── requirement extraction (LLM-assisted) ─────────────────────────────────────

def extract_requirements_llm(text: str, doc_id: str,
                              proposal_id: Optional[str] = None) -> list[dict]:
    """Use LLM to extract requirements more accurately than regex alone.

    Returns list of requirement dicts compatible with requirement_extractor.
    """
    prompt = f"""Extract all explicit requirements from this RFI/RFP document excerpt.

For each requirement, return a JSON array where each item has:
  - req_text: the exact requirement sentence
  - section: one of "section_l", "section_m", "sow", "cdrl", "evaluation_criteria", "other"
  - req_type: one of "shall", "should", "will", "must", "may", "other"
  - volume: one of "technical", "management", "past_performance", "cost", null
  - priority: one of "critical", "high", "medium", "low"

DOCUMENT EXCERPT:
{text[:3000]}

Return ONLY a valid JSON array, no explanation:"""

    try:
        response = _invoke(prompt, function="requirement_extraction",
                           max_tokens=2000, proposal_id=proposal_id)
    except LLMUnavailableError:
        response = ""

    if response:
        try:
            # Extract JSON from response
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                items = json.loads(response[start:end])
                result = []
                for i, item in enumerate(items):
                    if not item.get("req_text"):
                        continue
                    result.append({
                        "id": str(uuid.uuid4()),
                        "document_id": doc_id,
                        "proposal_id": proposal_id,
                        "req_number": f"LLM-{i + 1:04d}",
                        "section": item.get("section", "other"),
                        "req_text": item["req_text"],
                        "req_type": item.get("req_type", "shall"),
                        "volume": item.get("volume"),
                        "priority": item.get("priority", "medium"),
                        "extracted_by": "ai",
                    })
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback to regex
    from tools.rfx.requirement_extractor import extract_regex
    return extract_regex(text, doc_id, proposal_id)


# ── research summarizer ────────────────────────────────────────────────────────

def summarize_research(research_results: list[dict],
                       query: str,
                       proposal_id: Optional[str] = None) -> str:
    """Distill raw web/gov research results into a usable brief."""
    if not research_results:
        return "No research results available."

    snippets = []
    for r in research_results[:8]:
        title = r.get("title") or r.get("award_id", "")
        body = (r.get("snippet") or r.get("description") or
                r.get("scope_description", ""))
        if title or body:
            snippets.append(f"• {title}: {str(body)[:300]}")

    combined = "\n".join(snippets)
    prompt = f"""You are a government proposal analyst. Summarize the following
research findings into a concise brief (150-200 words) relevant to: "{query}"

Focus on: agency priorities, incumbent competitors, contract values,
technical focus areas, and potential discriminators.

RESEARCH RESULTS:
{combined}

Concise brief:"""

    try:
        return _invoke(prompt, function="research_summarization",
                       max_tokens=500, proposal_id=proposal_id)
    except LLMUnavailableError:
        return "Research summarization is temporarily unavailable. Please try again later."


# ── section scorer ─────────────────────────────────────────────────────────────

def score_section(section_content: str, requirements: list[dict],
                  section_title: str,
                  proposal_id: Optional[str] = None) -> dict:
    """Evaluate a proposal section against RFP requirements.

    Returns {"score": 0-100, "strengths": [...], "gaps": [...],
             "recommendation": str}.
    """
    req_text = "\n".join(
        f"- [{r.get('req_number', '')}] {r.get('req_text', '')}"
        for r in requirements[:15]
    )

    prompt = f"""You are a government proposal evaluator. Score this {section_title}
section against the RFP requirements.

RFP REQUIREMENTS:
{req_text}

SECTION CONTENT:
{section_content[:2000]}

Return a JSON object with:
  - score: integer 0-100
  - strengths: list of 2-3 specific strengths (strings)
  - gaps: list of specific gaps or missing requirements (strings)
  - recommendation: one-sentence action to improve

Return ONLY valid JSON:"""

    response = _invoke(prompt, function="section_scoring",
                       max_tokens=600, proposal_id=proposal_id)

    try:
        start = response.find("{")
        end = response.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(response[start:end])
    except (json.JSONDecodeError, ValueError):
        pass

    return {
        "score": 0,
        "strengths": [],
        "gaps": ["Could not parse LLM response"],
        "recommendation": "Review section manually.",
    }
