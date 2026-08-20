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
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tools.document_intelligence.collection_registry import ensure_collection
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

# Confidence bands — coupled to the shared TRUST bands rather than re-hardcoded.
# citation_grounding is the single source of truth (>=0.7 include, 0.4-0.69 flag
# + HITL, <0.4 abstain); that module's docstring already records that it
# "Mirrors the DIC doc_generator thresholds. Kept here so every surface uses the
# same bands." Importing the constants here closes the loop so DIC drafting and
# the citation gate cannot silently drift apart when the bands are retuned. The
# literal fallback keeps this module importable if quality/ is unavailable.
try:
    from tools.quality.citation_grounding import (
        CONF_ABSTAIN as _CONF_ABSTAIN,
        CONF_INCLUDE as _CONF_INCLUDE,
    )
except Exception:  # pragma: no cover — defensive; preserves historical values
    _CONF_INCLUDE = 0.7
    _CONF_ABSTAIN = 0.4


def _dic_cot_enabled() -> bool:
    """Whether DIC drafting uses Chain-of-Thought / CoD compression.

    Default OFF: CoT leaks reasoner/critic scaffolding into output on many
    models; the direct _SECTION_PROMPT path produces clean, cited, TRUST-
    compliant prose. Set ICDEV_DIC_COT_ENABLED=true to opt into the richer
    (but leak-prone) CoT path.
    """
    import os
    return os.environ.get("ICDEV_DIC_COT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# Minimum section word count to trigger CoD compression
_COD_WORD_THRESHOLD = 800


# ── cef-di-05: the governed evidence seam ─────────────────────────────────────
def _evidence_module():
    """The governed evidence seam, or ``None`` when it cannot be imported.

    A DIC install without the seam module behaves exactly like one with the
    toggle off. Drafting must not stop because an evidence module failed to
    import — that is the same fail-open this file already applies to the
    verifier, the confabulation detector and the placeholder scan.

    ``icdev`` first, deliberately: ``tools.X is icdev.tools.X`` is False and the
    two copies have separate thread-local run state, so a caller that resets or
    inspects one by importing a namespace directly can be talking to the copy
    nobody is using. Every call inside this module goes through here, so a
    process only ever touches one — and a test must patch what THIS returns.
    """
    try:  # pragma: no cover - import shape varies by install layout
        from icdev.tools.document_intelligence import docgen_evidence
    except Exception:
        try:
            from tools.document_intelligence import docgen_evidence
        except Exception:
            return None
    return docgen_evidence


def _reset_evidence_run() -> None:
    """Start a fresh evidence run — drop the memo cache, re-arm the budget."""
    seam = _evidence_module()
    if seam is not None:
        seam.reset_run_state()


def _evidence_run_stats() -> dict:
    """Resolutions spent and asks refused by the cap, for a caller to report."""
    seam = _evidence_module()
    return seam.run_stats() if seam is not None else {}


def _governed_retrieval(
    query: str,
    *,
    collection_id: str | None,
    tenant_id: str,
    classification: str,
    top_k: int,
    legacy,
) -> tuple[list, str, dict, list]:
    """Evidence for one drafting query: governed seam first, legacy retrieval otherwise.

    Returns ``(search_results, path, detail, deprecated)``.

    * ``search_results`` — real ``DICSearchResult`` objects either way, which is
      what keeps this migration to ONE changed line per entry point: everything
      downstream (the evidence blocks, the verifier replay, the persisted
      citations, the quality gate's ``allowed_source_ids``) is untouched.
    * ``path``      — which chain produced them (``docgen_evidence.PATH_*``),
      recorded on every section. A drafting run whose evidence chain is
      unknowable afterwards is exactly what this migration is fixing.
    * ``detail``    — the backends consulted, the ones that DIED, and a
      governance refusal. Carried even when the legacy path was taken, so a
      thin governed answer is never mistaken for a thin corpus.
    * ``deprecated`` — pack currency findings about the QUERY, so the drafter
      can be told BEFORE it writes as well as screened after.

    ``legacy`` is a zero-arg callable performing the original
    ``DICSearchEngine`` retrieval. It is not called at all when the governed
    path answers, and it is called exactly as before in every other case.
    """
    seam = _evidence_module()
    if seam is None:
        # No seam module at all — indistinguishable from the toggle being off,
        # and treated identically.
        return legacy(), "legacy", {}, []

    bundle = seam.resolve_evidence(
        query,
        collection_id=collection_id,
        tenant_id=tenant_id,
        classification=classification,
        top_k=top_k,
    )
    if bundle is None:
        # Toggle off / re-entrant / budget spent / Cortex absent. Each of those
        # is logged with its own reason inside the seam; here they are one
        # answer, because they all mean "do what you did before".
        return legacy(), seam.PATH_LEGACY, {}, []

    detail = bundle.detail()
    if not bundle.is_empty:
        return list(bundle.results), seam.PATH_CORTEX, detail, list(bundle.deprecated)

    if seam.fallback_on_empty():
        # The governed fan-out answered with nothing to draft from — a
        # governance refusal, or a fan-out where every rung failed. Falling back
        # keeps the migration behaviour-preserving; `detail` keeps the reason
        # visible rather than laundering an outage into "no evidence found".
        return legacy(), seam.PATH_CORTEX_EMPTY_FALLBACK, detail, list(bundle.deprecated)
    return [], seam.PATH_CORTEX, detail, list(bundle.deprecated)


def _currency_constraint(deprecated: list) -> str:
    """A drafting-prompt constraint naming what the catalog says is dead.

    Advisory only, and never load-bearing: an LLM instruction is a request, so
    the guarantee lives in :func:`_apply_currency_guard`, which runs on the
    OUTPUT and is deterministic. This just makes the common case come out right
    the first time instead of being corrected afterwards.
    """
    if not deprecated:
        return ""
    lines = []
    for finding in deprecated[:10]:
        line = f"- {finding.get('entity', '')} is {finding.get('verdict', '')}"
        successor = finding.get("superseded_by", "")
        if successor:
            line += f"; use {successor} instead"
        lines.append(line)
    return (
        "\nCURRENCY CONSTRAINT — the following are verified deprecated by the "
        "currency catalog. Do NOT recommend or reintroduce them; if the evidence "
        "above still describes one, say so explicitly and name the successor:\n"
        + "\n".join(lines)
        + "\n"
    )


def _apply_currency_guard(
    text: str,
    *,
    tenant_id: str,
    classification: str,
) -> tuple[str, bool, list, dict]:
    """Screen a DRAFTED section for entities the currency backend calls dead.

    Returns ``(text, tripped, verdict_citations, report)``.

    This is the deterministic half, and the one that actually holds. The
    verifier cannot catch this case: it asks whether a claim is SUPPORTED by
    the retrieved evidence, and a draft grounded in a 2019 runbook that
    recommends TLS 1.1 is fully supported by that runbook. Every gate
    downstream agrees, and the document ships reintroducing it.

    ``tripped`` is False both when the guard found nothing AND when it did not
    run; ``report["screened"]`` is what tells those apart, and it is persisted,
    because "checked, clean" and "never checked" must not read the same.
    """
    seam = _evidence_module()
    if seam is None or not text:
        return text, False, [], {"screened": False}

    screen = seam.screen_draft(
        text, tenant_id=tenant_id, classification=classification,
    )
    if screen is None:
        return text, False, [], {"screened": False}

    report = {
        "screened": True,
        "findings": list(screen.findings),
        "action": screen.action,
        "errors": list(screen.errors),
    }
    if not screen.tripped:
        return text, False, [], report

    if screen.action == seam.ACTION_ABSTAIN:
        return (
            "(Abstained — draft named a deprecated entity: "
            + ", ".join(f.get("entity", "") for f in screen.findings)
            + ".)"
        ), True, list(screen.citations), report
    return f"{text}\n\n{screen.advisory()}", True, list(screen.citations), report


def _citation_report(
    text: str,
    search_results: list,
    evidence_path: str,
    evidence_detail: dict,
    currency: dict,
) -> dict:
    """Validate this section's citation tags against the sources it was shown.

    The pipeline has always VALIDATED citations at the publish gate; what it did
    not do is RECORD, on the section, that the check ran and what it said. So a
    draft whose ``[source: chunk N]`` tags named nothing looked identical to one
    whose tags all resolved, until somebody clicked publish.

    ``allowed`` is the same set the ``quality_gate`` hook derives — the
    ``chunk_id`` of every retrieved result — which on the governed path is the
    Cortex citation's ``source_id``. That is what lets this migration keep the
    citation contract without touching a prompt: the ids the model was shown
    are the ids it is checked against, on either path.

    ``evidence_path`` and ``evidence_detail`` answer the two questions a reader
    of a finished draft cannot otherwise answer: which chain retrieved this, and
    did any rung of it DIE. A thin section from a fan-out that lost three
    backends to a timeout is an infrastructure event, not a statement about the
    corpus, and only ``backend_errors`` can tell them apart.
    """
    report = {
        "evidence_path": evidence_path,
        "evidence_detail": evidence_detail,
        "source_count": len(search_results),
        "currency": currency,
    }
    allowed = {
        str(getattr(r, "chunk_id", "")) for r in search_results
        if getattr(r, "chunk_id", None)
    }
    try:
        from tools.quality.citation_grounding import validate_citations
        validation = validate_citations(text or "", allowed)
        report.update({
            "valid": bool(validation.get("valid")),
            "cited_count": validation.get("cited_count", 0),
            "available_count": validation.get("available_count", 0),
            "hallucinated_citations": validation.get("hallucinated_citations", []),
        })
    except Exception as exc:  # noqa: BLE001 — a scorer outage is not a defect
        # Reported, never silently omitted: an absent `valid` key that a reader
        # takes for False is the same class of bug as a skip that reads green.
        report["validation_error"] = str(exc)
    return report


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
    confabulation: dict = field(default_factory=dict)
    #: cef-di-05 — which evidence chain produced this section, whether its
    #: `[source: ...]` tags validated against that chain's own source ids, and
    #: what the currency screen found. Persisted so a drafting run is never
    #: ambiguous afterwards about which chain wrote it.
    citation_report: dict = field(default_factory=dict)


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
    quality_gate: dict = field(default_factory=dict)

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
                    "confabulation": s.confabulation,
                    "citation_report": s.citation_report,
                }
                for s in self.sections
            ],
            "origin": self.origin,
            "status": self.status,
            "error": self.error,
            "quality_gate": self.quality_gate,
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


# Per-attempt timeout guards against a hung CLI-bridge poll loop. Cloud providers
# (e.g. Kimi) can take well over the old 45s to synthesize a full 2k-token section,
# so the default is generous and env-overridable. Empty (not timed-out) responses
# are retried a bounded number of times because cloud models intermittently return
# an empty completion — a single empty answer must not silently abstain a section.
_LLM_TIMEOUT = int(os.environ.get("ICDEV_DIC_LLM_TIMEOUT", "90"))  # seconds per attempt
_LLM_EMPTY_RETRIES = int(os.environ.get("ICDEV_DIC_LLM_RETRIES", "2"))  # extra tries on empty


def _llm_generate(
    prompt: str, *, function: str = "document_qna", max_tokens: int = 2048
) -> str | None:
    """Call the ICDEV LLM router using the canonical ``invoke(fn, LLMRequest)`` API.

    Degrades gracefully to ``None`` when no LLM provider is available (air-gapped
    / headless mode) so the caller can abstain rather than hallucinate.
    Each attempt times out after _LLM_TIMEOUT seconds to avoid hanging on CLI-bridge
    poll loops. A non-empty answer returns immediately; an *empty* completion is
    retried up to _LLM_EMPTY_RETRIES times (transient cloud emptiness), while a
    timeout stops retrying (it would only double the wait).
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
        for attempt in range(1 + max(0, _LLM_EMPTY_RETRIES)):
            _ex = _cf.ThreadPoolExecutor(max_workers=1)
            _fut = _ex.submit(router.invoke, function, req)
            try:
                resp = _fut.result(timeout=_LLM_TIMEOUT)
                text = (resp.content.strip() if resp and getattr(resp, "content", None) else "")
                if text:
                    return text
                logger.warning(
                    "doc_generator: empty LLM completion (attempt %d/%d); retrying",
                    attempt + 1, 1 + max(0, _LLM_EMPTY_RETRIES),
                )
            except _cf.TimeoutError:
                break  # timed out — CLI-bridge not running / provider stalled; degrade
            finally:
                _ex.shutdown(wait=False)  # don't block on CLI-bridge poll thread
    except Exception as exc:
        logger.warning("doc_generator: LLM call failed: %s", exc)
    return None


# ── TRUST: leaked-reasoning scrubber ──────────────────────────────────────────
# Some models emit their Chain-of-Thought / Chain-of-Debate scaffolding
# ("Step 1: Analyze…", "[INSTRUCTION]", "Critique E1-E3", meta-narration) into
# the synthesizer output. That reasoning must never reach published section
# prose — it is not grounded, cited, or reader-facing. This deterministic
# scrubber removes it so generated content is TRUST-compliant.
# Bracketed control tags: named ([SYNTHESIS]) or "[X APPLIED]"/"[X CRITIQUE]" etc.
_ARTIFACT_TOKEN_RE = re.compile(
    r"\[(?:ARGUMENT|JUDGMENT|SYNTHESIS|INSTRUCTION|CRITIQUE|REASONING|TASK|"
    r"FINAL\s*ANSWER|ANSWER|THOUGHT|PLAN|STEP|SELF[- ]?CRITIQUE|SELF[- ]?CORRECTION)\]"
    r"|\[[A-Z][A-Z0-9 _/-]{1,28}?(?:APPLIED|CRITIQUE|CORRECTION|REVISION|REASONING|EDIT)\]",
    re.IGNORECASE,
)
_FINAL_MARKER_RE = re.compile(
    r"(?:^|\n)\s*\*{0,2}(?:final answer|final section|final response|final|"
    r"answer|output|section text|final draft|clean(?:ed)? (?:version|text|draft))\*{0,2}"
    r"\s*[:\-–—]\s*",
    re.IGNORECASE,
)
# A paragraph is reasoning scaffolding (not reader-facing prose) if it opens
# with any of these. Broadened from real observed CoT/CoD leaks.
_REASONING_PARA_RE = re.compile(
    r"^\s*[#>*_\s]*(?:"
    r"step\s+\d+\b"
    r"|critique\s+e?\d"
    r"|reasoning(?:\s+step)?s?\b"
    r"|(?:self[- ]?)?correction\b"
    r"|self[- ]?critique\b"
    r"|refining\b|revising\b|rewriting\b|drafting\b"
    r"|wait[,.\s]|hmm[,.\s]|okay[,.\s]|let'?s\b"
    r"|the (?:central|core|main) (?:error|issue|problem)\b"
    r"|the user (?:has )?(?:provided|requests|asks|wants)"
    r"|(?:this|the) (?:task|input|prompt|evidence) (?:description|requests?|asks?)"
    r"|the prompt (?:asks|requests|says|wants)"
    r"|to (?:be|make it) (?:\"?denser|more concise)"
    r"|i(?:'ll| will| need to| must| should| have to| can) (?:analyze|break down|identify|compress|reason|synthesize|ensure|make|combine|keep|stick|avoid|not)"
    r"|let me (?:analyze|break|think|start|see|re-?read)"
    r"|here(?:'s| is) (?:my|the) (?:reasoning|analysis|breakdown|thought|step-by-step)"
    r"|here(?:'s| is) (?:the )?step-by-step"
    r"|based on (?:my|the) (?:reasoning|analysis)"
    r"|looking at the\b"
    r")",
    re.IGNORECASE,
)


def _strip_reasoning_artifacts(text: str) -> str:
    """Remove leaked CoT/CoD reasoning scaffolding from generated section prose.

    - Keeps only what follows the last explicit final-answer/synthesis marker.
    - Drops individual lines that open with reasoning meta-narration (handles
      single-block reasoning, not just leading paragraphs).
    - Strips inline control tokens; collapses the blank lines left behind.
    Returns clean prose. If scrubbing would remove nearly everything (the output
    was pure reasoning), falls back to the token-stripped original so a real
    section is never silently emptied; a no-op for already-clean content.
    """
    if not text or not text.strip():
        return text
    original = text
    matches = list(_FINAL_MARKER_RE.finditer(text))
    if matches:
        text = text[matches[-1].end():]
    kept = [ln for ln in text.split("\n") if not _REASONING_PARA_RE.match(ln.strip())]
    text = _ARTIFACT_TOKEN_RE.sub("", "\n".join(kept))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 40 <= len(original.strip()):
        text = _ARTIFACT_TOKEN_RE.sub("", original).strip()
    return text


# Residual reasoning cues that survive scrubbing → the section is polluted and
# must be flagged for human review (never silently published as clean).
_RESIDUE_RE = re.compile(
    r"\bstep\s+\d+\b|\[(?:INSTRUCTION|CRITIQUE|SYNTHESIS|ARGUMENT|JUDGMENT)\b"
    r"|critique\s+e?\d|self[- ]?correction|self[- ]?critique|the (?:central|core) error"
    r"|the prompt (?:asks|requests|says)|i must ensure|the following (?:section|evidence|chunks)"
    r"|compress(?:ed|ion)?\b.*\bdenser|reasoning steps?\b|the task is\b.*\bcompress",
    re.IGNORECASE,
)


def _has_reasoning_residue(text: str) -> bool:
    """True if generation artifacts remain after scrubbing (flag for HITL)."""
    return bool(text) and bool(_RESIDUE_RE.search(text))


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
        if result and result.content:
            # Scrub any leaked CoT reasoning so only clean prose is returned.
            return _strip_reasoning_artifacts(result.content).strip() or None
        return None
    except Exception as exc:
        logger.debug("doc_generator: CoT failed (%s), falling back to direct LLM", exc)
        return None


def _cod_compress(text: str, heading: str, *, function: str = "document_qna") -> str:
    """Apply Chain-of-Density compression to long sections (>_COD_WORD_THRESHOLD words).

    Uses Chain-of-Thought (reasoner→critic→synthesizer) so the synthesizer step
    produces actual compressed prose — not Chain-of-Debate which produces
    argument evaluations rather than transformed text.

    Returns the compressed text, or the original if compression is unavailable.
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
            f"without losing any factual claims or citation references. "
            f"Output ONLY the compressed section text — no meta-commentary, no evaluation, "
            f"no preamble:\n\n{text[:4000]}"
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": compress_prompt}],
            max_tokens=1500,
            skip_injection_scan=True,
        )
        # Use CoT (not CoD): the synthesizer step produces actual transformed output.
        # CoD produces argument evaluations, not compressed prose.
        result = orchestrator.invoke_chain_of_thought(function, req)
        compressed = result.content.strip() if result and result.content else None
        # Scrub leaked CoT/CoD reasoning scaffolding (TRUST compliance).
        if compressed:
            compressed = _strip_reasoning_artifacts(compressed).strip()
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
    """Compute a [0, 1] confidence score from a verifier ``VerifyResult``.

    Only *cited* claims count. An uncited sentence makes no attributed
    assertion, so scoring it as supported inflates confidence — which is how
    every section came to score 1.0 and sail through the CONF_INCLUDE /
    CONF_ABSTAIN bands.

    A scoring failure returns 0.0, not 1.0. If we cannot tell whether a section
    is grounded, the safe reading is "not grounded"; the previous 1.0 meant an
    exception here silently published an unverified section at full confidence.
    """
    if verdict is None or getattr(verdict, "abstained", False):
        return 0.0
    try:
        claims = getattr(verdict, "claims", None) or []
        cited = [c for c in claims if getattr(c, "method", "") != "uncited"]
        if not cited:
            # Nothing was actually checked against a source.
            return 0.0
        supported = [c for c in cited if getattr(c, "supported", False)]
        return len(supported) / len(cited)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("doc_generator: confidence scoring failed: %s", exc)
        return 0.0


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
    target_doc_id: str | None = None,
    quality_gate=None,
) -> "GenerateResult":
    """Generate a document draft grounded in DIC search results.

    ``quality_gate`` (optional): a callable invoked right before persistence with
    ``(sections, allowed_source_ids, full_text)`` that returns the status string
    the version should be persisted with. Lets a caller (e.g. the modernization
    regeneration gate) withhold ``pending_review`` from a defective draft without
    the gate itself mutating dic_versions. Default None preserves the historical
    unconditional ``pending_review`` behavior for every other caller.

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

    # Fresh evidence run: drop the memo cache and re-arm the outbound budget.
    # Per RUN rather than per process — the cache holds live database state, and
    # memoising it for the lifetime of a dashboard worker would make a newly
    # ingested document invisible until restart.
    _reset_evidence_run()

    result = GenerateResult(
        query=query,
        collection_id=collection_id or "",
        origin="ai_generated",
        status="pending_review",
    )

    # 1. Retrieve evidence — governed seam first (cef-di-05), legacy otherwise.
    # The legacy chain is unchanged and is what runs whenever the seam declines:
    # full KB first, fall back to session-scoped.
    def _legacy_retrieval() -> list:
        engine = DICSearchEngine(tenant_id=tenant_id)
        results = engine.search(query, collection_id=None, top_k=10)
        if not results and collection_id:
            results = engine.search(query, collection_id=collection_id, top_k=10)
            if results:
                logger.info(
                    "doc_generator: full-KB search returned 0; fell back to collection %s (%d hits)",
                    collection_id, len(results),
                )
        return results

    search_results, _evidence_path, _evidence_detail, _deprecated = _governed_retrieval(
        query,
        collection_id=collection_id,
        tenant_id=tenant_id,
        classification=classification,
        top_k=10,
        legacy=_legacy_retrieval,
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

        # 4. Draft — direct clean-prose prompt by default; CoT only when opted in.
        # CoT (reasoner→critic→synthesizer) tends to leak its reasoning into the
        # output on many models; the direct _SECTION_PROMPT mandates clean prose
        # with [source:] citations, which is TRUST-compliant. Enable the richer
        # CoT path with ICDEV_DIC_COT_ENABLED=true (accepts the leakage risk).
        raw_text: str | None = None
        if _dic_cot_enabled() and len(sec_evidence) > _COT_EVIDENCE_THRESHOLD:
            raw_text = _cot_generate(heading, sec_evidence)

        if not raw_text:
            raw_text = _llm_generate(
                _SECTION_PROMPT.format(
                    title=title, heading=heading, summary=summary, evidence=sec_evidence,
                    chunk_id=search_results[0].chunk_id if search_results else "N/A",
                )
                # Advisory only — the guarantee is _apply_currency_guard below,
                # which is deterministic and runs on the output.
                + _currency_constraint(_deprecated)
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

        # 5. CoD compression for verbose sections (opt-in — the CoT-based
        # compressor also leaks reasoning; off by default for clean output).
        if _dic_cot_enabled():
            raw_text = _cod_compress(raw_text, heading)
        # TRUST: scrub so no leaked reasoning reaches published prose.
        raw_text = _strip_reasoning_artifacts(raw_text)

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
                    # `verified` must reflect the verdict, not merely the fact
                    # that verify() returned without raising.
                    verified = vr.verified
            except Exception as exc:
                # A verifier failure is not a pass. Leave verified False and
                # drop confidence to 0 so the HITL bands below can act on it.
                logger.warning("doc_generator: verifier error: %s", exc)
                confidence = 0.0
                verified = False

        # TRUST: scrub AFTER the verifier (which may reintroduce reasoning by
        # replacing raw_text with its verified_text) so the final stored/published
        # prose carries no leaked CoT/CoD scaffolding.
        if not abstained:
            raw_text = _strip_reasoning_artifacts(raw_text)

        # cef-di-05: deterministic currency guard. Runs on the FINAL prose, so
        # it also catches an entity the CoD compressor or the verifier's
        # verified_text reintroduced after the drafting prompt was obeyed.
        currency_report: dict = {"screened": False}
        currency_citations: list = []
        if not abstained:
            raw_text, _tripped, currency_citations, currency_report = _apply_currency_guard(
                raw_text, tenant_id=tenant_id, classification=classification,
            )
            if _tripped:
                # A section that reintroduces a deprecated entity is never
                # `verified`, whatever the verifier said — the verifier asked a
                # different question (is this claim supported by the retrieved
                # evidence) and a stale source answers it yes.
                verified = False
                low_confidence = True
                if heading not in flagged_headings:
                    flagged_headings.append(heading)
                names = ", ".join(f.get("entity", "") for f in currency_report["findings"])
                hitl_note = (
                    f"{hitl_note} ⚠ Currency: names deprecated {names} — "
                    f"confirm the successor before publishing."
                ).strip()
                if currency_report.get("action") == "abstain":
                    # DROP THE PROSE, not just the flag. CLAUDE.md describes
                    # this setting as "`on_deprecated: abstain` drops the
                    # prose instead" — and it did not. It set `abstained`
                    # and persisted the sentence naming the deprecated
                    # entity, which is the exact outcome the currency guard
                    # exists to prevent: the citation gate skips an
                    # abstained section (`_section_dicts` drops it), so the
                    # text shipped unexamined AND unverified.
                    abstained = True
                    confidence = 0.0
                    raw_text = (
                        "(Abstained — the draft named deprecated or superseded "
                        "entities (%s); no replacement was substituted.)"
                        % (names or "unspecified")
                    )

        # Deterministic placeholder check — unresolved [BRACKETED] tokens
        # force a HITL flag regardless of verifier confidence.
        placeholder_tokens: list = []
        try:
            from tools.quality.content_grounding import find_placeholders
            placeholder_tokens = find_placeholders(raw_text)
        except Exception:
            pass

        # Apply confidence threshold gate
        if not abstained:
            # TRUST: if generation artifacts survived scrubbing, never publish
            # silently — flag for human review / regeneration.
            if _has_reasoning_residue(raw_text) and not low_confidence:
                low_confidence = True
                hitl_note = (
                    "⚠ Generation artifacts detected (leaked reasoning) — "
                    "regenerate or edit this section before publishing."
                )
                if heading not in flagged_headings:
                    flagged_headings.append(heading)
            if placeholder_tokens and not low_confidence:
                low_confidence = True
                hitl_note = (
                    f"⚠ Unresolved placeholders {', '.join(placeholder_tokens[:6])} — "
                    f"resolve before publishing."
                )
                flagged_headings.append(heading)
                raw_text = f"{raw_text}\n\n> {hitl_note}"
            if confidence >= _CONF_INCLUDE:
                pass  # include normally
            elif confidence >= _CONF_ABSTAIN:
                low_confidence = True
                hitl_note = (
                    f"⚠ Confidence {confidence:.0%} — below {_CONF_INCLUDE:.0%} threshold; "
                    f"verify against source documents before publishing."
                )
                if heading not in flagged_headings:
                    flagged_headings.append(heading)
                raw_text = f"{raw_text}\n\n> {hitl_note}"
            else:
                # Very low confidence — exclude (abstain).
                #
                # THE PROSE MUST GO WITH THE FLAG. This branch set `abstained`
                # and left `raw_text` alone, so the section was "excluded" in
                # name only: the unverified sentence was still persisted, while
                # every consumer that trusts the flag skipped it. The
                # regeneration quality gate is one such consumer and says so in
                # its own code — `_section_dicts` drops abstained sections
                # "(they carry the '(Abstained — ...)' sentinel, not real
                # prose)". That assumption was false here, which is how an
                # UNCITED section reached a persisted version while the gate
                # reported `blocked: False, reasons: []`: it was never examined,
                # and the report could not say so.
                #
                # The verifier's own abstain path a few lines above already does
                # this. Doing it here too means "abstained" means one thing.
                abstained = True
                raw_text = (
                    "(Abstained — confidence %.0f%% is below the %.0f%% floor; "
                    "no supported, cited claim was found for this section.)"
                    % (confidence * 100, _CONF_ABSTAIN * 100)
                )
                logger.info(
                    "doc_generator: section '%s' excluded — confidence %.2f < %.2f",
                    heading, confidence, _CONF_ABSTAIN,
                )

        # halluc-03: non-blocking confabulation assessment (fabricated-citation
        # patterns, internal contradictions, hedging) — complements DIC's
        # confidence verifier, which does not target these specifically. Skips
        # abstained sections (no claims). Elevates hitl_note on high risk.
        confab: dict = {}
        if not abstained and raw_text:
            try:
                from tools.security.confabulation_detector import assess as _confab_assess
                confab = _confab_assess(raw_text)
                if confab.get("risk_level") == "high" and not low_confidence:
                    low_confidence = True
                    if heading not in flagged_headings:
                        flagged_headings.append(heading)
                    note = (
                        f"⚠ Confabulation risk {confab.get('risk_score')} "
                        f"({confab.get('findings_count')} finding(s)) — review before publishing."
                    )
                    hitl_note = f"{hitl_note} {note}".strip()
            except Exception:
                confab = {}

        # cef-di-05: the section CITES the currency verdict that flagged it —
        # structurally, as a `currency_verdict` citation, not as a bracketed tag
        # in the prose. A pack's evidence ref is a synthetic key, not a
        # retrievable chunk id, so tagging it would manufacture exactly the
        # hallucinated citation this pipeline exists to prevent.
        citations = citations + currency_citations

        generated_sections.append(GeneratedSection(
            heading=heading,
            content=raw_text,
            verified=verified,
            abstained=abstained,
            citations=citations,
            confidence=confidence,
            low_confidence=low_confidence,
            hitl_note=hitl_note,
            confabulation=confab,
            citation_report=_citation_report(
                raw_text, search_results, _evidence_path, _evidence_detail, currency_report,
            ),
        ))

    result.sections = generated_sections

    # 7. Persist to dic_versions as pending_review + dic_sections
    try:
        from tools.db.storage import get_connection

        # target_doc_id: append the NEXT version to an existing document (full
        # regeneration / reverse-bridge path) instead of minting a new doc.
        doc_id = target_doc_id or _hid("dic_gen", query, collection_id or "")
        version_id = f"ver-{uuid.uuid4().hex[:16]}"
        full_text = "\n\n".join(
            f"## {s.heading}\n\n{s.content}" for s in generated_sections if not s.abstained
        )
        sha = hashlib.sha256(full_text.encode()).hexdigest()

        # Pre-persist quality gate (optional): a caller may withhold the
        # pending_review status from a defective draft. The gate decides the
        # INITIAL persisted status (an append, not a dic_versions mutation);
        # default None keeps the historical unconditional pending_review.
        persist_status = "pending_review"
        if quality_gate is not None:
            try:
                allowed_source_ids = {
                    str(getattr(r, "chunk_id", "")) for r in search_results
                    if getattr(r, "chunk_id", None) is not None
                }
                gate_status = quality_gate(generated_sections, allowed_source_ids, full_text)
                if gate_status:
                    persist_status = gate_status
            except Exception as exc:
                logger.warning("doc_generator: quality_gate hook error: %s", exc)

        conn = get_connection()
        try:
            version_no = 1
            if target_doc_id:
                row = conn.execute(
                    "SELECT MAX(version_no) AS vmax FROM dic_versions WHERE doc_id = %s",
                    (target_doc_id,),
                ).fetchone()
                version_no = int((dict(row).get("vmax") if row else 0) or 0) + 1
            # An empty collection_id guaranteed an invisible document: the
            # Collections UI enumerates dic_collections, so "" has no container.
            # Fall back to "default" like every other ingest path, then register it.
            collection_id = (collection_id or "").strip() or "default"
            ensure_collection(conn, collection_id, tenant_id=tenant_id, classification=classification)
            conn.execute(
                "INSERT OR IGNORE INTO dic_documents "
                "(doc_id, collection_id, source_id, filename, content_type, provider, title, "
                "byte_size, content_sha256, page_count, created_at, tenant_id, classification) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (doc_id, collection_id, doc_id, "ai_generated.md", "text/markdown",
                 "ai_generator", title, len(full_text), sha, 1, _now_utc(), tenant_id, classification),
            )
            conn.execute(
                "INSERT OR IGNORE INTO dic_versions "
                "(version_id, doc_id, version_no, origin, status, assigned_to, review_notes, content_sha256, "
                "created_at, created_by, tenant_id, classification) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (version_id, doc_id, version_no, "ai_generated", persist_status, None, None, sha,
                 _now_utc(), created_by, tenant_id, classification),
            )
            for idx, sec in enumerate(generated_sections, start=1):
                section_id = f"sec-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    "INSERT OR REPLACE INTO dic_sections "
                    "(section_id, version_id, doc_id, heading, content, citations_json, status, origin, "
                    "created_at, created_by, tenant_id, classification) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (section_id, version_id, doc_id, sec.heading, sec.content,
                     json.dumps(sec.citations), persist_status, "ai_generated",
                     _now_utc(), created_by, tenant_id, classification),
                )
            conn.commit()
        finally:
            conn.close()

        result.doc_id = doc_id
        result.version_id = version_id
        result.status = persist_status
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

    _reset_evidence_run()

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT doc_id, title FROM dic_documents WHERE doc_id = "
            "(SELECT doc_id FROM dic_versions WHERE version_id = %s LIMIT 1)",
            (version_id,),
        )
        row = cur.fetchone()
        doc_id = row[0] if row else ""
        doc_title = (row[1] if row else "") or heading
        cur.execute(
            "SELECT heading, content FROM dic_sections WHERE version_id = %s ORDER BY section_id",
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

    # Governed seam first (cef-di-05), legacy targeted retrieval otherwise. The
    # query is the heading on BOTH paths, unchanged, so a before/after
    # comparison of what came back is like-for-like.
    def _legacy_retrieval() -> list:
        engine = DICSearchEngine(tenant_id=tenant_id)
        return engine.search(heading, collection_id=collection_id, top_k=8)

    search_results, _evidence_path, _evidence_detail, _deprecated = _governed_retrieval(
        heading,
        collection_id=collection_id,
        tenant_id=tenant_id,
        classification=classification,
        top_k=8,
        legacy=_legacy_retrieval,
    )
    evidence = _targeted_evidence_block(search_results)

    if not search_results:
        return {
            "version_id": version_id,
            "heading": heading,
            "content": "(Abstained — no targeted evidence found for this section.)",
            "citation_count": 0,
            "status": "pending_review",
            "abstained": True,
            # Which chain returned nothing is the whole question here: a
            # governed fan-out that lost every rung to a timeout and a corpus
            # that genuinely holds nothing produce the same empty list and need
            # opposite responses. `evidence_detail.backend_errors` separates them.
            "citation_report": _citation_report(
                "", [], _evidence_path, _evidence_detail, {"screened": False},
            ),
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
    # Advisory only — the guarantee is the deterministic guard below.
    prompt += _currency_constraint(_deprecated)
    raw_text = _llm_generate(prompt)
    if raw_text:
        raw_text = _strip_reasoning_artifacts(raw_text)  # TRUST: no leaked reasoning
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

    # cef-di-05: deterministic currency guard on the FINAL prose. Same reason as
    # in generate_document — the verifier asks whether a claim is supported by
    # the retrieved evidence, and a stale runbook supports a dead protocol.
    currency_citations: list = []
    currency_report: dict = {"screened": False}
    currency_tripped = False
    if not abstained:
        verified_text, currency_tripped, currency_citations, currency_report = (
            _apply_currency_guard(
                verified_text, tenant_id=tenant_id, classification=classification,
            )
        )
        if currency_tripped and currency_report.get("action") == "abstain":
            # Same defect as generate_document's band, same fix: the flag alone
            # left the deprecated sentence in `verified_text`, and every
            # consumer that trusts `abstained` then skipped it while it shipped.
            abstained = True
            _deprecated_names = ", ".join(
                f.get("entity", "") for f in (currency_report.get("findings") or [])
            )
            verified_text = (
                "(Abstained — the draft named deprecated or superseded entities "
                "(%s); no replacement was substituted.)"
                % (_deprecated_names or "unspecified")
            )

    citations = [r.citation.to_dict() for r in search_results[:5]] + currency_citations

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE dic_sections SET content = %s, citations_json = %s, status = %s, origin = %s, "
            "created_at = %s, created_by = %s WHERE version_id = %s AND heading = %s",
            (verified_text, json.dumps(citations), "pending_review", "ai_generated",
             _now_utc(), created_by, version_id, heading),
        )
        if cur.rowcount == 0:
            section_id = f"sec-{uuid.uuid4().hex[:12]}"
            cur.execute(
                "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
                "citations_json, status, origin, created_at, created_by, tenant_id, classification) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (section_id, version_id, doc_id, heading, verified_text, json.dumps(citations),
                 "pending_review", "ai_generated", _now_utc(), created_by, tenant_id, classification),
            )
        cur.execute(
            "SELECT heading, content FROM dic_sections WHERE version_id = %s ORDER BY section_id",
            (version_id,),
        )
        rows = cur.fetchall()
        full_text = "\n\n".join(
            f"## {r[0]}\n\n{r[1]}" for r in rows if r[1]
        )
        sha = hashlib.sha256(full_text.encode()).hexdigest()
        cur.execute(
            "UPDATE dic_versions SET content_sha256 = %s WHERE version_id = %s",
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
        # cef-di-05 — which chain retrieved this, whether its citation tags
        # validated, and what the currency screen found. `screened: false` means
        # the guard did not RUN; it is not the same as "ran and found nothing",
        # and the two must never be read as one.
        "citation_report": _citation_report(
            verified_text, search_results, _evidence_path, _evidence_detail, currency_report,
        ),
        "currency_flagged": currency_tripped,
    }
