"""Tech writing AI assist — RAG + KG backed research and drafting, diagram generation.

Intentionally never raises: all errors surface in ResearchResult.error / DiagramResult.error
so callers can degrade gracefully (air-gap, missing LLM, no embedding index).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

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

# Shared TRUST citation machinery. CLAUDE.md: "Build on the shared
# tools/quality/citation_grounding.py — do not re-implement citation
# parsing/validation." Imported like the other optional deps so a stripped
# install degrades instead of failing the whole drafting surface.
try:
    from tools.quality.citation_grounding import validate_citations
except Exception:
    validate_citations = None  # type: ignore[assignment]

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
    # The numbered register the draft cites against: [{id, kind, ref, label}].
    # `id` is "1".."N" — the RAG injected-source convention that
    # citation_grounding.validate_citations understands natively.
    sources: list[dict] = field(default_factory=list)
    # validate_citations() report over draft_content. Empty when no draft.
    citation_report: dict = field(default_factory=dict)


@dataclass
class DiagramResult:
    diagram_type: str = "mermaid"
    syntax: str = ""
    description: str = ""
    error: str = ""


# ── Citation grounding (TRUST) ───────────────────────────────────────────────
#
# CLAUDE.md names this surface explicitly: "Every LLM-generated artifact
# (proposals, RFI, DIC, Tech Writer, and any new drafting surface) MUST carry
# inline [source: …] citations validated against its evidence."
#
# The system prompt has always said "Cite your sources." — an instruction the
# model could not follow, because the context blocks were unnumbered ("[RAG] …",
# "[KG:type] …", "[WEB] url: …"). There was nothing to cite BY, no format asked
# for, and nothing checked the output. The intent was there; the mechanism was
# not. Numbering the register is what makes the existing instruction actionable
# and the result verifiable.
#
# Ids are "1".."N" — the RAG injected-source convention that
# citation_grounding.validate_citations() accepts as a bare int count.

def _register_source(result: "ResearchResult", kind: str, ref: str, label: str) -> str:
    """Add a retrieved item to the citable register and return its id."""
    sid = str(len(result.sources) + 1)
    result.sources.append({"id": sid, "kind": kind, "ref": ref or "", "label": label or ""})
    return sid


def _citation_instruction(source_count: int) -> str:
    """Tell the model how to cite, and to cite only what it was given."""
    if source_count <= 0:
        return ""
    return (
        f"\nThe Context above is numbered [source: 1] .. [source: {source_count}]. "
        "Cite every factual claim inline with the matching [source: N] tag. "
        "Use ONLY those numbers — never invent a source. "
        "If the Context does not support a claim, omit the claim rather than "
        "citing something that does not say it.\n"
    )


def _apply_citation_report(result: "ResearchResult") -> None:
    """Validate the draft's citations against the register; record defects.

    Reports rather than raises: this module's contract is that it never raises
    and surfaces problems on the result. A hallucinated citation is the serious
    case — it names evidence that was never retrieved — so it is a warning the
    caller can gate on, not a silent field.
    """
    if validate_citations is None or not result.draft_content:
        return
    try:
        report = validate_citations(result.draft_content, [s["id"] for s in result.sources])
    except Exception as exc:  # never raise out of the drafting path
        result.warnings.append(f"Citation validation unavailable: {exc}")
        logger.debug("citation validation failed: %s", exc)
        return

    result.citation_report = report
    if report.get("hallucinated_citations"):
        result.warnings.append(
            "Draft cites sources that were never retrieved: "
            + ", ".join(report["hallucinated_citations"])
        )
    elif result.sources and not report.get("cited_count"):
        # Uncited prose from a surface that had evidence to cite is the exact
        # thing the TRUST invariant exists to catch.
        result.warnings.append(
            f"Draft cites none of the {len(result.sources)} retrieved sources."
        )


# ── Standards-reference validation (ground-tw-04) ────────────────────────────

_WHITELIST_PATH = Path(__file__).resolve().parents[2] / "args" / "tw_standards_whitelist.yaml"
_whitelist_cache: dict | None = None

_NIST_RE = re.compile(r"\bNIST\s+(?:SP\s+)?800-(\d+[A-Za-z]?)?", re.IGNORECASE)
_CMMC_LEVEL_RE = re.compile(r"\bCMMC\s+(?:Level\s+|L)(\d+)", re.IGNORECASE)
_CMMC_PRACTICE_RE = re.compile(r"\b([A-Z]{2})\.L(\d)-(\d+\.\d+\.\d+)\b")
_FEDRAMP_RE = re.compile(r"\bFedRAMP\s+(Low|Moderate|High|LI-SaaS)\b", re.IGNORECASE)
_FEDRAMP_UNKNOWN_RE = re.compile(r"\bFedRAMP\s+([A-Z][A-Za-z-]+)\b")
_SRG_RE = re.compile(r"\bSRG-([A-Z]{2,4})-(\d+)\b")
_STIG_V_RE = re.compile(r"\bV-(\d+)\b")
_REFERENCES_RE = re.compile(
    r"^#{0,6}\s*(?:\d+\.?\s*)?References\b.*?$(.*?)(?=^#{1,6}\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _load_standards_whitelist() -> dict:
    """Load args/tw_standards_whitelist.yaml once. Empty dict on any failure."""
    global _whitelist_cache
    if _whitelist_cache is None:
        try:
            import yaml
            with open(_WHITELIST_PATH, encoding="utf-8") as fh:
                _whitelist_cache = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.warning("standards whitelist unavailable: %s", exc)
            _whitelist_cache = {}
    return _whitelist_cache


def validate_standards_references(text: str) -> list[str]:
    """Deterministic whitelist check of standards citations in a draft.

    Scans the References section when one exists, otherwise the whole text.
    Flags unknown or malformed NIST SP 800-*, CMMC, FedRAMP, and DISA
    SRG/STIG identifiers. Never raises; returns [] when the whitelist is
    missing or the text is empty.
    """
    if not text:
        return []
    wl = _load_standards_whitelist()
    if not wl:
        return []

    ref_match = _REFERENCES_RE.search(text)
    scope = ref_match.group(0) if ref_match else text
    warnings: list[str] = []
    seen: set[str] = set()

    def _warn(msg: str) -> None:
        if msg not in seen:
            seen.add(msg)
            warnings.append(msg)

    nist_pubs = {str(p).upper() for p in (wl.get("nist_sp_800") or [])}
    for m in _NIST_RE.finditer(scope):
        pub = (m.group(1) or "").upper()
        if not pub:
            _warn("Standards check: malformed NIST citation — 'NIST SP 800-' with no publication number")
        elif pub not in nist_pubs:
            _warn(f"Standards check: unknown NIST publication 'SP 800-{pub}' — not in whitelist")

    cmmc = wl.get("cmmc") or {}
    levels = {int(v) for v in (cmmc.get("levels") or [])}
    domains = {str(d).upper() for d in (cmmc.get("domains") or [])}
    for m in _CMMC_LEVEL_RE.finditer(scope):
        if int(m.group(1)) not in levels:
            _warn(f"Standards check: unknown CMMC level '{m.group(1)}' — valid levels are {sorted(levels)}")
    for m in _CMMC_PRACTICE_RE.finditer(scope):
        domain, level = m.group(1), int(m.group(2))
        if domain not in domains:
            _warn(f"Standards check: unknown CMMC practice domain '{domain}' in '{m.group(0)}'")
        elif level not in levels:
            _warn(f"Standards check: malformed CMMC practice id '{m.group(0)}' — level {level} is invalid")

    baselines = {str(b).lower() for b in ((wl.get("fedramp") or {}).get("baselines") or [])}
    known_fr = {m.group(1).lower() for m in _FEDRAMP_RE.finditer(scope)}
    for m in _FEDRAMP_UNKNOWN_RE.finditer(scope):
        token = m.group(1).lower()
        if token not in baselines and token not in known_fr and token not in {"authorized", "ready", "marketplace"}:
            _warn(f"Standards check: unknown FedRAMP baseline '{m.group(1)}' — not in whitelist")

    stig = wl.get("stig") or {}
    srg_families = {str(f).upper() for f in (stig.get("srg_families") or [])}
    v_min = int(stig.get("v_id_digits_min", 5))
    v_max = int(stig.get("v_id_digits_max", 6))
    for m in _SRG_RE.finditer(scope):
        family, num = m.group(1), m.group(2)
        if family not in srg_families:
            _warn(f"Standards check: unknown SRG family '{family}' in '{m.group(0)}'")
        elif len(num) != 6:
            _warn(f"Standards check: malformed SRG id '{m.group(0)}' — expected a 6-digit number")
    for m in _STIG_V_RE.finditer(scope):
        if not (v_min <= len(m.group(1)) <= v_max):
            _warn(f"Standards check: malformed STIG vulnerability id '{m.group(0)}' — expected V-{{{v_min}-{v_max} digits}}")

    return warnings


# ── Chain of Debate gating for ARCH_* templates (ground-tw-04) ────────────────

def _tw_cod_enabled() -> bool:
    """ICDEV_TW_COD_ENABLED gates Chain-of-Debate drafting. Default off."""
    return os.environ.get("ICDEV_TW_COD_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _cod_draft(user_msg: str, system_prompt: str) -> str:
    """Chain-of-Debate draft attempt. Returns '' on any failure (caller falls
    back to single-shot), mirroring tools/govcon/rfi_workbench._generate_draft."""
    from tools.llm.chain_orchestrator import ChainOrchestrator

    orch = ChainOrchestrator(router=LLMRouter())
    req = LLMRequest(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system_prompt,
        max_tokens=2048,
        temperature=0.4,
    )
    cod_result = orch.invoke_chain_of_debate("tech_writing_draft", req)
    content = (getattr(cod_result, "content", "") or "").strip()
    if content:
        models = getattr(cod_result, "models_used", None) or []
        logger.info("CoD tech-writing draft via %s", ",".join(models))
    return content


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
                    sid = _register_source(
                        result, "rag",
                        ref=chunk["chunk_id"] or chunk["doc_id"],
                        label=chunk["doc_id"] or "document",
                    )
                    context_parts.append(f"[source: {sid}] {chunk['text'][:800]}")
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
                        sid = _register_source(
                            result, "kg",
                            ref=entity["entity_id"],
                            label=f"{entity['type']}:{entity['label']}".strip(":"),
                        )
                        context_parts.append(
                            f"[source: {sid}] {entity['label']}: {entity['summary'][:400]}"
                        )
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
                    sid = _register_source(result, "web", ref=url, label=url)
                    context_parts.append(f"[source: {sid}] {url}: {snippet}")
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
        + _citation_instruction(len(result.sources))
        + f"Write the content for the '{section_heading}' section. "
        f"Be specific, accurate, and well-structured. "
        f"Classification: {classification}."
    )

    if LLMRouter is not None and LLMRequest is not None:
        tt = template_type.upper() if template_type else ""
        # Judgment-heavy architecture sections: Chain of Debate (debaters +
        # judge synthesis) when enabled; any failure falls back to single-shot.
        if tt.startswith("ARCH_") and _tw_cod_enabled():
            try:
                result.draft_content = _cod_draft(user_msg, system_prompt)
            except Exception as exc:
                result.warnings.append(f"CoD draft failed — fell back to single-shot: {exc}")
                logger.warning("CoD draft failed for %s (%s) — falling back to single-shot", tt, exc)
        if not result.draft_content:
            try:
                # Cortex adoption pilot (analysis item 1): route the tech-writer
                # draft through the GOVERNED cortex.complete facade instead of a
                # raw router.invoke. A compliance drafting surface should get the
                # TRUST chain — input/output PII/CUI redaction, provenance, an
                # append-only audit row, and per-tenant budget attribution — none
                # of which the direct router.invoke applied. The "tech_writing_draft"
                # routing function + system prompt are preserved.
                from tools.cortex import api as cortex_api
                from tools.cortex.schemas import CortexContext

                cx = cortex_api.complete(
                    user_msg,
                    function="tech_writing_draft",
                    ctx=CortexContext(
                        tenant_id=tenant_id or "default",
                        classification=classification or "CUI",
                        domain="document",
                        agent_id="dic-tech-writer",
                    ),
                    system_prompt=system_prompt,
                    max_tokens=2048,
                    temperature=0.4,
                )
                result.draft_content = (cx.text or "").strip()
                # Surface the governance value the raw path never gave: note any
                # egress spans Cortex masked so the WriteGuard sidebar can show it.
                _masked = getattr(getattr(cx, "governance", None), "redactions_applied", 0)
                if _masked:
                    result.warnings.append(
                        f"Cortex governance masked {_masked} sensitive span(s) in the draft."
                    )
            except Exception as exc:
                result.error = f"LLM draft failed: {exc}"
                logger.warning("LLM draft failed: %s", exc)
    else:
        result.error = "LLM not available."

    # Deterministic placeholder check — unresolved [BRACKETED] tokens surface
    # as warnings the WriteGuard sidebar / editor can display.
    if result.draft_content:
        try:
            from tools.quality.content_grounding import find_placeholders
            tokens = find_placeholders(result.draft_content)
            if tokens:
                result.warnings.append(
                    "Unresolved placeholders in draft: " + ", ".join(tokens[:8])
                )
        except Exception as exc:
            logger.debug("placeholder check failed: %s", exc)
        result.warnings.extend(validate_standards_references(result.draft_content))
        # Citations last: it belongs with the other deterministic post-draft
        # checks, and sitting here covers every drafting path (CoD, single-shot,
        # Cortex) rather than one branch.
        _apply_citation_report(result)

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
