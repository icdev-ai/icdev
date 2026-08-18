"""Tech writing AI assist — RAG + KG backed research and drafting, diagram generation.

Intentionally never raises: all errors surface in ResearchResult.error / DiagramResult.error
so callers can degrade gracefully (air-gap, missing LLM, no embedding index).

Two retrieval chains live here (cef-di-02), and which one runs is a config
decision — ``cortex.enabled`` in ``args/dic_techwriter.yaml``, default false:

* the LEGACY chain hand-wires ``RAGRetriever.search()`` and
  ``graph_rag.retrieve()``, two subsystems this module has to know the names
  of, neither of them governed;
* the MIGRATED chain asks ONE governed seam, ``cortex.resolve(entity, question,
  ctx)``, which fans out over the registered rungs under the 8-gate TRUST
  chain, writes a ``cortex_audit`` row, registers the evidence set in
  ``source_citation_registry``, and returns a deterministic currency verdict
  for the subject alongside the evidence.

Both chains are scoped by ``collection_id`` — see the COLLECTION SCOPE block
below, which is the defect this task existed to fix.
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
    # Which retrieval chain actually ran: "legacy" | "cortex". Reported rather
    # than inferred — a migration behind a toggle whose result cannot say which
    # side of the toggle produced it is not comparable to the other side.
    retrieval_path: str = "legacy"
    # What the collection scope did: {collection_id, enforced, in_collection,
    # dropped, dropped_by_type}. `enforced: false` with a non-empty
    # collection_id means membership could NOT be read and nothing was let
    # through — an unverifiable scope is not a scope, and it is not a silent one
    # either.
    scope: dict = field(default_factory=dict)
    # The governed resolution's own findings when the cortex chain ran:
    # {verdict, verdict_source, gaps, conflicts, backends_consulted, blocked}.
    # Empty on the legacy chain, which has no verdict to report.
    resolution: dict = field(default_factory=dict)
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


# ── Tech-writer config (cef-di-02) ───────────────────────────────────────────

_TW_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "dic_techwriter.yaml"
_tw_config_cache: dict | None = None

#: ``args/dic_techwriter.yaml`` block that governs the migrated retrieval chain.
CORTEX_CONFIG_KEY = "cortex"

#: Evidence hits requested per Cortex backend when the config declares none.
DEFAULT_CORTEX_TOP_K = 8

#: Over-fetch multiplier / ceiling when a collection scope is active. See the
#: `scope_overfetch` comment in args/dic_techwriter.yaml — a RECALL knob, never
#: a scope knob.
DEFAULT_SCOPE_OVERFETCH = 4
DEFAULT_MAX_TOP_K = 40

#: The Cortex domain lens the retrieval half runs under (cef-bck-04). It scopes
#: resolve's rung set to [rag, dic] and row-scopes to the ``dic_`` source
#: prefixes, which is what makes the fan-out answer about the DIC corpus rather
#: than about the 3,552 compliance-corpus chunks that share ``rag_chunks``.
#:
#: Deliberately NOT the lens the DRAFT runs under. ``cortex.complete()`` below
#: passes ``domain="document"`` — the broad documents persona — and changing it
#: would change the drafting voice, which this task is not about. Retrieval
#: scope and drafting persona are different decisions.
CORTEX_RETRIEVAL_DOMAIN = "document_intelligence"

#: Citation source types that name a DOCUMENT, and are therefore collection
#: scopable. Anything else a resolution can cite (``pack_evidence`` from a
#: catalog row, ``kg_node``, ``kb_entry``) names no document, so under a
#: collection scope it cannot be shown to be IN the collection — and the rule
#: below is that what cannot be shown in scope is out of it.
_CORPUS_SOURCE_TYPES = ("rag_chunk", "dic_document")


def _techwriter_config() -> dict:
    """Load ``args/dic_techwriter.yaml`` once. Empty dict on any failure."""
    global _tw_config_cache
    if _tw_config_cache is None:
        try:
            import yaml
            with open(_TW_CONFIG_PATH, encoding="utf-8") as fh:
                _tw_config_cache = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.debug("tech-writer config unavailable: %s", exc)
            _tw_config_cache = {}
    return _tw_config_cache


def cortex_retrieval_config(config: dict | None = None) -> dict:
    """The ``cortex:`` block of the tech-writer config."""
    block = (config if config is not None else _techwriter_config()).get(CORTEX_CONFIG_KEY)
    return dict(block) if isinstance(block, dict) else {}


def cortex_retrieval_enabled(config: dict | None = None) -> bool:
    """Is the migrated retrieval chain live? DEFAULT FALSE.

    Off is the shipped default and off means ``cortex.resolve`` is never
    consulted, so the legacy chain is restored by flipping this flag rather
    than by reverting a merge (the epic's migration rule).
    """
    return bool(cortex_retrieval_config(config).get("enabled", False))


# ── COLLECTION SCOPE (cef-di-02) ─────────────────────────────────────────────
#
# ``research_and_draft`` has accepted ``collection_id`` since it was written and
# has never passed it to anything. RAG was scoped by tenant alone and the KG was
# not scoped at all, so a section draft could ground itself in — and cite —
# chunks from ANY collection in the tenant. The parameter looked like a control
# and was decoration.
#
# The scope is now enforced in TWO places, and they are not redundant:
#
# * NATIVELY, by passing the scope each retriever already accepts.
#   ``rag_chunks.project_id`` is the DIC collection of record —
#   ``ingest_orchestrator`` writes ``project_id=collection_id`` and
#   ``DICSearchEngine._rag_search`` already filters on it — and
#   ``kg_graphs.project_id`` carries the collection for a DIC-derived graph.
#   Measured on the live corpus 2026-08-18: of the 434 ``dic_documents`` chunks
#   that join to a live document row, ``project_id`` equals that document's
#   ``collection_id`` 434 times and disagrees 0 times. Passing the scope means
#   the retriever spends its ``top_k`` budget on in-scope chunks instead of
#   filling it with another collection's and having them discarded here.
# * AT THIS SURFACE, by dropping every retrieved source that does not name a
#   document in the collection. A retriever that ignores the parameter — a
#   stub, a future backend, or the Cortex fan-out, which has no collection
#   filter at all — would otherwise put the defect straight back while the call
#   still LOOKED scoped. This is the half the test pins, and it is the same
#   two-place shape ``DICSearchEngine.search`` already uses.
#
# FAIL CLOSED: when the collection's membership cannot be read, NOTHING is in
# scope. An unverifiable scope is not a scope. The drop is counted and reported
# on ``ResearchResult.scope`` rather than being silent, because a draft that
# quietly lost its evidence reads as bad recall.
#
# An empty ``collection_id`` requests no scope at all, and every path is then
# byte-for-byte what it was before this change.

def _collection_doc_ids(collection_id: str) -> set | None:
    """Doc ids in *collection_id*, or None when membership could not be read.

    None and ``set()`` are different answers and are never merged: the first
    means "we could not check" (fail closed, warn), the second means "the
    collection is empty" (nothing to retrieve). They send you to different
    fixes.

    Reads through ``get_connection()`` — ``dic_documents`` carries both
    ``tenant_id`` and ``classification``, so the global RLS predicate applies
    and the caller's own tenant/clearance scoping comes for free.
    """
    if not collection_id:
        return None
    conn = None
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT doc_id FROM dic_documents WHERE collection_id = %s",
            (collection_id,),
        ).fetchall()
        out = set()
        for row in rows or []:
            doc_id = row["doc_id"] if hasattr(row, "keys") else row[0]
            if doc_id:
                out.add(str(doc_id))
        return out
    except Exception as exc:  # noqa: BLE001 — never raise out of the drafting path
        logger.warning(
            "collection scope: membership of %r unreadable (%s) — scoping FAILS "
            "CLOSED, no evidence is admitted", collection_id, exc,
        )
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — best effort
                pass


class _Scope:
    """The collection-scope decision for one call. Pure once constructed.

    ``requested`` is what the caller asked for; ``enforced`` is whether we could
    actually establish membership. The two are separate because a scope that
    was requested and could not be established must not read as "no scope
    requested" — that is the exact confusion this class exists to prevent.
    """

    def __init__(self, collection_id: str):
        self.collection_id = (collection_id or "").strip()
        self.requested = bool(self.collection_id)
        self.doc_ids = _collection_doc_ids(self.collection_id) if self.requested else None
        self.enforced = self.requested and self.doc_ids is not None
        self.kept = 0
        self.dropped_by_type: dict[str, int] = {}

    def admits(self, doc_id: str, source_type: str = "") -> bool:
        """Is this retrieved source admissible under the scope?

        No scope requested -> everything. Scope requested -> only a source that
        NAMES a document in the collection. A source naming no document, or a
        document outside the collection, or any source at all when membership
        could not be read, is refused and counted.
        """
        if not self.requested:
            self.kept += 1
            return True
        if self.doc_ids is not None and doc_id and str(doc_id) in self.doc_ids:
            self.kept += 1
            return True
        key = source_type or "unattributed"
        self.dropped_by_type[key] = self.dropped_by_type.get(key, 0) + 1
        return False

    @property
    def dropped(self) -> int:
        return sum(self.dropped_by_type.values())

    def report(self) -> dict:
        return {
            "collection_id": self.collection_id,
            "requested": self.requested,
            "enforced": self.enforced,
            "in_collection": len(self.doc_ids) if self.doc_ids is not None else None,
            "kept": self.kept,
            "dropped": self.dropped,
            "dropped_by_type": dict(self.dropped_by_type),
        }

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.requested and not self.enforced:
            out.append(
                f"Collection scope '{self.collection_id}' could not be verified — "
                "no evidence was admitted (scoping fails closed)."
            )
        elif self.dropped:
            out.append(
                f"Collection scope '{self.collection_id}' dropped {self.dropped} "
                f"retrieved source(s) from outside the collection."
            )
        return out


# ── Governed retrieval via cortex.resolve() (cef-di-02) ──────────────────────

def _resolve_top_k(top_k: int, scoped: bool, settings: dict) -> int:
    """Per-backend evidence hits to request. Recall only — never scope."""
    base = int(settings.get("top_k", top_k or DEFAULT_CORTEX_TOP_K) or DEFAULT_CORTEX_TOP_K)
    if not scoped:
        return max(1, base)
    overfetch = int(settings.get("scope_overfetch", DEFAULT_SCOPE_OVERFETCH) or 1)
    ceiling = int(settings.get("max_top_k", DEFAULT_MAX_TOP_K) or DEFAULT_MAX_TOP_K)
    return max(1, min(base * max(1, overfetch), ceiling))


def _cortex_retrieve(
    result: "ResearchResult",
    query: str,
    section_heading: str,
    scope: "_Scope",
    tenant_id: str,
    classification: str,
    top_k: int,
    settings: dict,
) -> list[str]:
    """Retrieve through the governed ``cortex.resolve()`` seam.

    Returns the numbered context parts, having registered every admitted source
    on ``result.sources`` exactly as the legacy chain does — the register is the
    thing the draft's ``[source: N]`` tags resolve against, and it must not
    change shape when the retrieval chain does.

    A refusal or a failure yields NO evidence and a warning. It deliberately
    does NOT fall back to the legacy chain: a governance chain you can route
    around by failing is decoration, and this evidence is the draft's grounding
    rather than a supplement to it.
    """
    context_parts: list[str] = []
    try:
        from tools.cortex.api import resolve as cortex_resolve
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"Governed retrieval unavailable: {exc}")
        logger.warning("cortex retrieval unavailable: %s", exc)
        return context_parts

    try:
        resolution = cortex_resolve(
            query,
            question=section_heading or "",
            ctx=CortexContext(
                tenant_id=tenant_id or "default",
                classification=classification or "CUI",
                domain=CORTEX_RETRIEVAL_DOMAIN,
                agent_id="dic-tech-writer",
                # The caller's own air-gap verdict, threaded on rather than
                # re-derived. The lens already keeps the `external` rung out of
                # the fan-out; this is the belt to that pair of braces.
                air_gap=bool(result.is_airgap),
            ),
            top_k=_resolve_top_k(top_k, scope.requested, settings),
        )
    except Exception as exc:  # noqa: BLE001 — includes a governance refusal
        reason = getattr(exc, "reason", "") or type(exc).__name__
        result.resolution = {"blocked": str(reason), "message": str(exc)}
        result.warnings.append(f"Governed retrieval refused or failed ({reason}): {exc}")
        logger.warning("cortex.resolve for %r failed: %s", query, exc)
        return context_parts

    for citation in getattr(resolution, "citations", None) or []:
        source_type = str(getattr(citation, "source_type", "") or "")
        doc_id = str(getattr(citation, "source_id", "") or "")
        # Only a corpus citation can be attributed to a collection at all; the
        # scope refuses everything else when one is requested.
        if not scope.admits(doc_id if source_type in _CORPUS_SOURCE_TYPES else "", source_type):
            continue
        text = str(getattr(citation, "snippet", "") or "")
        title = str(getattr(citation, "title", "") or "")
        entry = {
            "chunk_id": "",
            "doc_id": doc_id,
            "text": text,
            "score": 0.0,
            "source_type": source_type,
            "backend": "cortex",
        }
        if source_type.startswith("kg_"):
            result.kg_entities.append({
                "entity_id": doc_id, "type": source_type,
                "label": title, "summary": text,
            })
        else:
            result.rag_chunks.append(entry)
        if not text:
            continue
        sid = _register_source(result, source_type or "cortex", ref=doc_id, label=title or doc_id)
        context_parts.append(f"[source: {sid}] {text[:800]}")

    # The verdict, the gaps and the conflicts are the half of a resolution the
    # legacy chain could not produce at all. Surfaced on the result (and the
    # actionable ones as warnings) rather than injected into the prompt: they
    # are findings ABOUT the subject, and feeding a deterministic verdict back
    # into a generative context is how it stops being one.
    result.resolution = {
        "verdict": str(getattr(resolution, "verdict", "") or ""),
        "verdict_source": str(getattr(resolution, "verdict_source", "") or ""),
        "gaps": list(getattr(resolution, "gaps", None) or []),
        "conflicts": list(getattr(resolution, "conflicts", None) or []),
        "backends_consulted": list(getattr(resolution, "backends_consulted", None) or []),
        "backend_errors": list(getattr(resolution, "backend_errors", None) or []),
        "blocked": "",
    }
    if result.resolution["verdict"] in ("deprecated", "superseded"):
        result.warnings.append(
            f"Governed resolution: '{query}' is {result.resolution['verdict']} — "
            "the draft may be describing something that has been replaced."
        )
    if result.resolution["conflicts"]:
        result.warnings.append(
            f"Governed resolution: {len(result.resolution['conflicts'])} source(s) "
            "disagree about this subject; no winner was picked."
        )
    if result.resolution["backend_errors"]:
        # A rung that DIED and a corpus that matched nothing are different
        # answers. Left merged, a cold embedding provider reads to the writer as
        # "the collection has nothing on this", which is a statement about the
        # data it has no basis for.
        failed = sorted({str(e.get("backend") or "?")
                         for e in result.resolution["backend_errors"]})
        result.warnings.append(
            "Governed retrieval degraded: backend(s) " + ", ".join(failed) +
            " failed — thin evidence here is an outage, not an empty collection."
        )
    return context_parts


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
    """Retrieve → [web if not air-gapped] → LLM draft.

    Retrieval is either the LEGACY chain (RAGRetriever + graph_rag, hand-wired)
    or the governed ``cortex.resolve()`` seam, decided by ``cortex.enabled`` in
    ``args/dic_techwriter.yaml`` (default false). ``ResearchResult.retrieval_path``
    says which one ran.

    ``collection_id`` scopes retrieval on BOTH chains and fails closed — see the
    COLLECTION SCOPE block above. It was accepted and ignored before cef-di-02,
    which let a section draft ground itself in any collection in the tenant.

    The air-gap gate, the placeholder scan, the standards-reference check and
    the citation report are unchanged and run on every path.

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

    # ── 2. Collection scope ──────────────────────────────────────────────────
    # Established ONCE, before any retrieval, and applied by both chains. See
    # the COLLECTION SCOPE block above for why it is enforced here as well as
    # passed down natively.
    scope = _Scope(collection_id)

    if cortex_retrieval_enabled():
        # ── 3a. Governed retrieval — ONE seam (cef-di-02) ────────────────────
        result.retrieval_path = "cortex"
        context_parts.extend(_cortex_retrieve(
            result, query, section_heading, scope, tenant_id, classification,
            top_k, cortex_retrieval_config(),
        ))
    else:
        # ── 3b. Legacy chain — RAG then KG, hand-wired ───────────────────────
        if RAGRetriever is not None:
            try:
                retriever = RAGRetriever(tenant_id=tenant_id or "default")
                # `project_id` is the DIC collection of record on rag_chunks.
                # Passed only when a scope was requested, so an unscoped call
                # is byte-for-byte the pre-existing one.
                rag_kwargs: dict = {"top_k": top_k}
                if scope.requested:
                    rag_kwargs["project_id"] = scope.collection_id
                search_results = retriever.search(query, **rag_kwargs)
                for sr in search_results[:top_k]:
                    # `doc_id` first for the fakes and adapters that publish it;
                    # `source_id` is what a real rag SearchResult carries, and
                    # reading only `doc_id` is why every chunk this surface
                    # registered was labelled "document".
                    doc_id = getattr(sr, "doc_id", "") or getattr(sr, "source_id", "")
                    chunk = {
                        "chunk_id": getattr(sr, "chunk_id", ""),
                        "doc_id": doc_id,
                        "text": getattr(sr, "text", "") or getattr(sr, "content", ""),
                        "score": float(getattr(sr, "score", 0)),
                    }
                    if not scope.admits(doc_id, "rag_chunk"):
                        continue
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

        if kg_retrieve is not None:
            try:
                # `kg_graphs.project_id` carries the collection for a
                # DIC-derived graph, so this is a GRAPH-level scope where the
                # RAG lane's is chunk-level. A KG node names no document, so
                # the surface-level predicate cannot re-check it — a collection
                # with no graph of its own correctly contributes no entities.
                kg_kwargs: dict = {"top_k": top_k, "compress": False}
                if scope.requested:
                    kg_kwargs["project_id"] = scope.collection_id
                kg_result = kg_retrieve(query, **kg_kwargs)
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

    result.scope = scope.report()
    result.warnings.extend(scope.warnings())

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
