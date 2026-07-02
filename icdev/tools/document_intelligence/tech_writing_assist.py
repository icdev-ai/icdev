"""Tech writing AI assist — RAG + KG backed research and drafting, diagram generation.

Intentionally never raises: all errors surface in ResearchResult.error / DiagramResult.error
so callers can degrade gracefully (air-gap, missing LLM, no embedding index).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── Optional deps — imported at module level so tests can patch them ──────────
try:
    from tools.airgap.detector import is_airgap
except Exception:
    def is_airgap(**kwargs):  # type: ignore[misc]
        return True

try:
    from tools.rag.retriever import RAGRetriever
except Exception:
    RAGRetriever = None  # type: ignore[assignment,misc]

try:
    from tools.knowledge_graph.graph_rag import retrieve as kg_retrieve
except Exception:
    kg_retrieve = None  # type: ignore[assignment]

try:
    from tools.chat_router.url_analyzer import fetch_content
except Exception:
    fetch_content = None  # type: ignore[assignment]

try:
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest
except Exception:
    LLMRouter = None  # type: ignore[assignment,misc]
    LLMRequest = None  # type: ignore[assignment,misc]

# ── System prompts by template type ──────────────────────────────────────────
_SYSTEM_PROMPTS: dict[str, str] = {
    "STANDARD_GUIDE": (
        "You are a senior technical writer producing a cloud-agnostic Standard Guide. "
        "Reference all four cloud providers (AWS, Azure, GCP, Oracle) where applicable. "
        "Use clear section headings, numbered steps, and consistent terminology. "
        "Cite all sources in a References section."
    ),
    "SOP": (
        "You are a technical writer creating a Standard Operating Procedure. "
        "Use imperative voice and numbered steps. Include prerequisites, verification steps, "
        "and a rollback procedure. State who is responsible for each major action."
    ),
    "RUNBOOK": (
        "You are a site reliability engineer writing a runbook. "
        "Use imperative voice, numbered steps, and clear pre-flight checks. "
        "Include escalation paths and rollback instructions."
    ),
    "ARCH_NETWORK": (
        "You are a network architect. Include rationale for design decisions, "
        "note security implications, and reference relevant standards (NIST, CMMC, FedRAMP). "
        "Describe traffic flows, segmentation strategy, and key control points."
    ),
    "ARCH_APPLICATION": (
        "You are a software architect. Include rationale for design decisions, "
        "API contracts, data flow, deployment architecture, and security considerations. "
        "Note any trade-offs and constraints."
    ),
    "ARCH_SYSTEM": (
        "You are a systems architect. Describe mission, stakeholders, system boundary, "
        "key components, interfaces, and quality attributes. "
        "Include a decision log section with rationale for major choices."
    ),
}

_DEFAULT_SYSTEM = (
    "You are a technical writer. Produce clear, accurate, well-structured content. "
    "Cite your sources. Maintain a professional tone."
)

# Diagram flavors per template type
_DIAGRAM_FLAVORS: dict[str, str] = {
    "ARCH_NETWORK": "flowchart TD",
    "ARCH_APPLICATION": "sequenceDiagram",
    "ARCH_SYSTEM": "flowchart LR",
    "SOP": "flowchart TD",
    "RUNBOOK": "flowchart TD",
    "STANDARD_GUIDE": "mindmap",
}


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class ResearchResult:
    draft_content: str = ""
    rag_chunks: list[dict] = field(default_factory=list)
    kg_entities: list[dict] = field(default_factory=list)
    web_sources: list[dict] = field(default_factory=list)
    is_airgap: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class DiagramResult:
    diagram_type: str = "mermaid"
    syntax: str = ""
    description: str = ""
    error: str = ""


# ── Main functions ────────────────────────────────────────────────────────────

def research_and_draft(
    query: str,
    section_heading: str,
    template_type: str = "",
    collection_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
    web_urls: list[str] | None = None,
    top_k: int = 8,
) -> ResearchResult:
    """RAG → KG → [web if not air-gapped] → LLM draft.

    Never raises. Returns partial result on any step failure.
    """
    result = ResearchResult()
    context_parts: list[str] = []

    # ── 1. Air-gap check (use_cache=False prevents stale results in tests) ──
    try:
        result.is_airgap = is_airgap(use_cache=False)
    except Exception as exc:
        logger.debug("air-gap check failed: %s", exc)
        result.is_airgap = True  # fail safe: assume air-gapped

    # ── 2. RAG retrieval ─────────────────────────────────────────────────────
    if RAGRetriever is not None:
        try:
            retriever = RAGRetriever(tenant_id=tenant_id or "default")
            search_results = retriever.search(query, top_k=top_k)
            for sr in search_results[:top_k]:
                chunk = {
                    "chunk_id": getattr(sr, "chunk_id", ""),
                    "doc_id": getattr(sr, "doc_id", ""),
                    "text": getattr(sr, "text", "") or getattr(sr, "content", ""),
                    "score": float(getattr(sr, "score", 0)),
                }
                result.rag_chunks.append(chunk)
                if chunk["text"]:
                    context_parts.append(f"[RAG] {chunk['text'][:800]}")
        except Exception as exc:
            result.warnings.append(f"RAG unavailable: {exc}")
            logger.debug("RAG retrieval failed: %s", exc)

    # ── 3. KG retrieval ──────────────────────────────────────────────────────
    if kg_retrieve is not None:
        try:
            kg_result = kg_retrieve(query, top_k=top_k, compress=False)
            if isinstance(kg_result, dict):
                nodes = kg_result.get("nodes", []) or []
                for node in nodes[:10]:
                    entity = {
                        "entity_id": node.get("node_id", ""),
                        "type": node.get("entity_type", ""),
                        "label": node.get("label", ""),
                        "summary": node.get("summary", ""),
                    }
                    result.kg_entities.append(entity)
                    if entity["summary"]:
                        context_parts.append(f"[KG:{entity['type']}] {entity['label']}: {entity['summary'][:400]}")
        except Exception as exc:
            result.warnings.append(f"KG unavailable: {exc}")
            logger.debug("KG retrieval failed: %s", exc)

    # ── 4. Web research (only when NOT air-gapped) ───────────────────────────
    if not result.is_airgap and fetch_content is not None:
        urls_to_fetch = list(web_urls or [])
        for url in urls_to_fetch[:3]:
            try:
                content = fetch_content(url)
                if content:
                    snippet = str(content)[:1000]
                    result.web_sources.append({"url": url, "snippet": snippet})
                    context_parts.append(f"[WEB] {url}: {snippet}")
            except Exception as exc:
                result.warnings.append(f"Web fetch failed for {url}: {exc}")
                logger.debug("Web fetch failed: %s", exc)

    # ── 5. LLM draft ─────────────────────────────────────────────────────────
    if not context_parts and not query:
        result.error = "No query and no context retrieved."
        return result

    system_prompt = _SYSTEM_PROMPTS.get(template_type.upper() if template_type else "", _DEFAULT_SYSTEM)
    context_block = "\n\n".join(context_parts[:20]) if context_parts else ""

    user_msg = (
        f"Section: {section_heading}\n\n"
        f"Research query: {query}\n\n"
        + (f"Context:\n{context_block}\n\n" if context_block else "")
        + f"Write the content for the '{section_heading}' section. "
        f"Be specific, accurate, and well-structured. "
        f"Classification: {classification}."
    )

    if LLMRouter is not None and LLMRequest is not None:
        try:
            router = LLMRouter()
            req = LLMRequest(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system_prompt,
                max_tokens=2048,
                temperature=0.4,
            )
            response = router.invoke("tech_writing_draft", req)
            result.draft_content = (response.content or "").strip()
        except Exception as exc:
            result.error = f"LLM draft failed: {exc}"
            logger.warning("LLM draft failed: %s", exc)
    else:
        result.error = "LLM not available."

    return result


def generate_diagram_syntax(
    description: str,
    diagram_type: str = "mermaid",
    template_type: str = "",
    classification: str = "CUI",
) -> DiagramResult:
    """LLM generates Mermaid syntax from a natural-language description.

    diagram_type is always 'mermaid' for now (Excalidraw is handled client-side).
    Never raises.
    """
    result = DiagramResult(diagram_type=diagram_type, description=description)

    flavor = _DIAGRAM_FLAVORS.get(template_type.upper() if template_type else "", "flowchart TD")

    system_prompt = (
        "You are a Mermaid diagram expert. Return ONLY valid Mermaid syntax — no markdown fences, "
        "no explanation, no commentary. Start with the diagram type keyword on line 1. "
        f"Prefer {flavor} diagrams for this content unless the description asks for something else. "
        "Keep node labels short (≤5 words). Do not use parentheses inside node labels."
    )

    user_msg = (
        f"Generate a Mermaid diagram for: {description}\n"
        f"Template type: {template_type or 'general'}\n"
        f"Classification: {classification}\n"
        "Return only the Mermaid syntax."
    )

    if LLMRouter is not None and LLMRequest is not None:
        try:
            router = LLMRouter()
            req = LLMRequest(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system_prompt,
                max_tokens=512,
                temperature=0.2,
            )
            response = router.invoke("diagram_generation", req)
            syntax = (response.content or "").strip()
            # Strip accidental markdown fences
            if syntax.startswith("```"):
                lines = syntax.splitlines()
                syntax = "\n".join(
                    ln for ln in lines
                    if not ln.strip().startswith("```")
                ).strip()
            result.syntax = syntax
        except Exception as exc:
            result.error = f"Diagram generation failed: {exc}"
            logger.warning("Diagram generation failed: %s", exc)
    else:
        result.error = "LLM not available."

    return result
