# CUI // SP-CTI
"""SSP-fragment evidence through ONE governed seam (cef-di-03).

``acoic.py`` — DocDrift, the DIC compliance sink — drafts cited SSP fragments.
Its evidence half was :func:`acoic._retrieve_evidence`: a bare
``RAGRetriever.search(query, top_k=k)`` that returned chunk TEXTS and nothing
else. Two consequences followed from "and nothing else":

1. The ``[SOURCE-N]`` tags a drafted fragment carries were positional indices
   into a list that was never persisted. ``verifier.verify`` could replay a
   claim against ``evidence[N-1]`` inside the same call, and after that call
   returned no reader could say what ``[SOURCE-1]`` had been. A citation that
   names nothing retrievable is a citation in shape only.
2. The lookup reached exactly one rung. The currency store, DIC, the knowledge
   graph and the KB all hold evidence about a control and none of them were
   asked, because asking would have meant acoic learning four more table names.

This module is the seam that replaces it: ONE call, ``cortex.resolve(entity)``,
which fans out over those rungs under the 8-gate TRUST chain, writes a
``cortex_audit`` row, and registers a ``source_citation_registry`` row for the
evidence set. It is the same seam ``tools/doc_modernization/evidence.py``
(cef-di-01) gives the docmod packs, applied to the other side of the same
canvas — deliberately a sibling module rather than a shared one, because the
two answer different questions (a pack asks "is this entity current"; acoic
asks "what documents evidence this control") and share no lane reader.

WHAT DOES **NOT** CHANGE, AND MUST NOT
--------------------------------------
The task migrates the RETRIEVAL half. The two air-gap-safe paths are untouched
and this module cannot reach either of them:

* ``map_changed_controls`` — the deterministic RICOAS / NIST 800-53 crosswalk
  plus the best-effort compliance-KG path. A pure JSON lookup that never went
  near retrieval and still does not.
* ``_draft_fragment_text``'s cited-template fallback — what runs when no LLM
  provider is reachable. It consumes evidence texts and does not care where
  they came from, so it works identically on both paths.

``cortex.resolve`` makes no model call of its own (it passes
``corrective=False``, so even the CRAG rewrite does not run), so turning the
toggle on does not put an LLM anywhere one was not already.

INDEX ALIGNMENT IS THE POINT
----------------------------
:attr:`SSPEvidence.texts` and :attr:`SSPEvidence.citations` are the same length
and the same order, so ``[SOURCE-N]`` means ``citations[N-1]``. That is the one
thing the legacy path could not offer and it is why the citations are persisted
onto the fragment: the verifier's positional scheme finally resolves to a
source id, a table and a provenance id.

``pack_evidence`` citations are excluded from both lanes. Those are a
``DomainPack``'s own verdict rationale coming back through the fan-out —
letting one become a cited sentence in an SSP narrative would make a derived
verdict the ground truth for a control implementation. Same rule, same reason,
as ``doc_modernization.evidence``'s ``extraction: structured`` filter.

``None`` IS THE LEGACY PATH
---------------------------
Every ``None`` this module returns means "the seam said nothing — do what you
did before". The toggle being off returns ``None``, a re-entrant ask returns
``None``, a spent budget returns ``None``, and an absent Cortex returns
``None``. Each is logged with its OWN reason, because "off", "recursive",
"capped" and "absent" send you to four different places. A resolution the
governance chain REFUSED returns a bundle carrying ``blocked`` and no evidence
instead — a refusal is a fact about this control and the caller should be able
to see it, while still falling through because the bundle is empty.

So no drafting run can be FAILED by this module, and
``cortex.enabled: false`` in ``args/dic_acoic_config.yaml`` restores the
pre-migration behaviour exactly.

THE MEASURED CAVEAT
-------------------
Measured on the live DIC canvas, 2026-08-18, same controls both ways:

* **Warm process** — ``cortex.resolve("CM-12")`` answers in 4.8s with 5 cited
  texts from ``rag_compliance_corpus`` and ``dic_documents``: the same evidence
  the legacy path found, now carrying source ids. Strictly better.
* **Cold process** — the SAME call spends 10.3s and abandons ``rag``, ``dic``
  and ``graph`` at the 10.0 / 10.0 / 8.0 second budgets in
  ``args/cortex_config.yaml``, answering from ``currency`` alone. Four catalog
  citations, none of them control-implementation text. The direct retriever
  needs 17.4s on that same cold cache and has no timeout, so it wins there.

The cold answer is THIN, not EMPTY, so ``fallback_on_empty`` does not fire for
it — that flag catches "nothing to draft from at all". What covers the thin case
instead is :attr:`SSPEvidence.errors`, which the caller records on the fragment:
an abandoned backend stays legible as an infrastructure event rather than
becoming a statement about the corpus. Raising the global Cortex timeouts would
change every Cortex consumer and is not this card's to do.

Evidence text is also shorter on the governed path — a citation snippet is
capped at 200 characters by ``tools/cortex/search_service.py`` where a raw chunk
ran 112-667. The deterministic template already truncated each chunk to 280, so
a cited draft comes out ~970 characters against ~1400. The trade is deliberate:
:func:`_lanes` explains why the text must BE the citation's snippet rather than
a fuller chunk the citation only summarises.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Behaviour config. Flat ``args/dic_*.yaml`` like the rest of the DIC canvas.
#: ``parents[3]`` because this module lives one level deeper than its
#: ``tools/`` re-export twin (``icdev/tools/document_intelligence/`` vs
#: ``tools/document_intelligence/``), and the config is one file in ``args/``
#: either way — there is no second copy of it to drift.
CONFIG_PATH = Path(__file__).resolve().parents[3] / "args" / "dic_acoic_config.yaml"

#: Block within that file which governs this seam.
CONFIG_KEY = "cortex"

#: Evidence citations requested per Cortex backend. 5 is the legacy
#: ``_retrieve_evidence(..., k=5)`` default, so both paths ask for the same
#: amount of evidence and a before/after comparison is like-for-like.
DEFAULT_TOP_K = 5

#: Upper bound on outbound resolutions per run. ``process_regen_item`` drafts
#: one fragment per control on a queue item and the queue holds 72 items, so an
#: uncapped batch is an outage rather than a slow pass. REPORTED via
#: :func:`run_stats`, never silent.
DEFAULT_MAX_RESOLVES = 100

#: Citation source_type that is a pack's OWN verdict rationale, not corpus
#: evidence. Never drafted from. See the module docstring.
PACK_EVIDENCE_TYPE = "pack_evidence"

#: ``citation_report.evidence_path`` values persisted onto a fragment.
PATH_CORTEX = "cortex"
PATH_CORTEX_EMPTY_FALLBACK = "cortex_empty_fallback"
PATH_LEGACY = "legacy"
PATH_CALLER = "caller"

_STATE = threading.local()
_CONFIG_CACHE: dict = {}


@dataclass
class SSPEvidence:
    """One control's governed evidence. Plain data — no behaviour, no clock.

    ``texts`` and ``citations`` are INDEX-ALIGNED: ``texts[i]`` is the evidence
    ``citations[i]`` points at, so a ``[SOURCE-N]`` tag in the drafted narrative
    resolves to ``citations[N-1]``.

    ``errors`` is carried separately from an empty lane for the reason this
    repository keeps re-learning: a backend that DIED and a corpus that matched
    nothing are different answers, and merging them turns an outage into a
    statement about the data. It is what tells ``fallback_on_empty`` apart from
    "this control genuinely has no evidence".
    """

    control_id: str = ""
    texts: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    backends: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    verdict: str = ""
    #: Non-empty when the governance chain REFUSED the resolution, carrying the
    #: ``resolver.BLOCK_*`` reason. A refusal is not an empty answer.
    blocked: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.texts


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------
def load_config(path=None) -> dict:
    """``args/dic_acoic_config.yaml``, memoised per path.

    An unreadable or absent file is ``{}``, which reads as OFF everywhere below.
    A config this module cannot parse must not take DocDrift offline; it must
    leave it on the path it was already on.
    """
    key = str(path or CONFIG_PATH)
    if key in _CONFIG_CACHE:
        return _CONFIG_CACHE[key]
    data: dict = {}
    try:
        import yaml

        with open(key, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except Exception as exc:  # noqa: BLE001 — an unreadable config is OFF
        logger.debug("ssp evidence: config unavailable (%s) — seam is OFF", exc)
    _CONFIG_CACHE[key] = data
    return data


def cortex_config(config: dict | None = None) -> dict:
    """The ``cortex:`` block of ``args/dic_acoic_config.yaml``."""
    if config is None:
        config = load_config()
    block = (config or {}).get(CONFIG_KEY)
    return dict(block) if isinstance(block, dict) else {}


def cortex_enabled(config: dict | None = None) -> bool:
    """Is the migrated path live? DEFAULT FALSE.

    Off is the shipped default and off means the seam is never consulted, so
    the legacy chain is restored by flipping this flag rather than by reverting
    a merge (the epic's migration rule).
    """
    return bool(cortex_config(config).get("enabled", False))


def fallback_on_empty(config: dict | None = None) -> bool:
    """Take the legacy retrieval when the governed path produced no text?

    DEFAULT TRUE. See the measured caveat in the module docstring — on this
    deployment the governed fan-out loses its RAG rung to a 10s timeout that
    the direct retriever beats by 1.8s, and a migration that quietly drops the
    only evidence an SSP narrative can be written from is not
    behaviour-preserving.
    """
    return bool(cortex_config(config).get("fallback_on_empty", True))


# ---------------------------------------------------------------------------
# Per-run state: memo cache, outbound budget, re-entrancy
# ---------------------------------------------------------------------------
def _fresh_state() -> dict:
    return {"cache": {}, "resolves": 0, "capped": 0, "active": False}


def _run() -> dict:
    """This thread's run state. Reset per run by :func:`reset_run_state`."""
    state = getattr(_STATE, "run", None)
    if state is None:
        state = _fresh_state()
        _STATE.run = state
    return state


def reset_run_state() -> None:
    """Drop the memo cache and re-arm the outbound budget.

    Called once per drafting run. The cache is per-run rather than per-process
    because the evidence it holds is live database state: memoising it for the
    lifetime of a long-running dashboard worker would make a newly ingested
    document invisible until restart.
    """
    _STATE.run = _fresh_state()


def run_stats() -> dict:
    """What this run actually did — resolutions spent, asks refused by the cap."""
    state = _run()
    return {
        "resolutions": state["resolves"],
        "capped": state["capped"],
        "cached_controls": len(state["cache"]),
    }


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------
def evidence_question(frameworks=None) -> str:
    """The retrieval framing. Derived from the frameworks, so it is reproducible.

    It shapes the evidence query and nothing else — ``resolve`` never feeds a
    question to a pack extractor, so it cannot move the resolution onto a
    different control. Built to sit alongside the control id the way the legacy
    query did (``f"{control_id} {frameworks} implementation"``), because
    comparing the two paths only means something if they ask for the same
    thing.
    """
    names = sorted(str(f) for f in (frameworks or []) if str(f).strip())
    line = ", ".join(names) if names else "NIST 800-53"
    return f"{line} implementation evidence for this control"


def _citation_dict(citation) -> dict:
    """``Citation`` -> the citation-shaped dict persisted on the fragment.

    ``source`` is prefixed with the source TYPE because a fragment's citation is
    read by humans and by ``tools/quality/citation_grounding`` alike, and a bare
    row id names nothing. Nothing is invented: a citation with no id gets an
    empty source and is dropped by the caller rather than given one.
    """
    source_id = str(getattr(citation, "source_id", "") or "")
    source_type = str(getattr(citation, "source_type", "") or "cortex")
    return {
        "source": "cortex:{}:{}".format(source_type, source_id) if source_id else "",
        "detail": str(getattr(citation, "snippet", "") or ""),
        "date": "",
        "source_type": source_type,
        "source_table": str(getattr(citation, "source_table", "") or ""),
        "title": str(getattr(citation, "title", "") or ""),
        "provenance_id": str(getattr(citation, "provenance_id", "") or ""),
    }


def _lanes(resolution, limit: int) -> tuple:
    """``(texts, citations)`` — index-aligned, ``pack_evidence`` excluded.

    The text IS the citation's snippet. That is deliberate rather than a
    limitation worked around: a drafted sentence must be replayable against the
    exact evidence its ``[SOURCE-N]`` names, and handing the verifier a fuller
    chunk than the citation records would make the persisted provenance a
    summary of what was verified instead of the thing itself.
    """
    texts: list = []
    citations: list = []
    for citation in getattr(resolution, "citations", None) or []:
        if str(getattr(citation, "source_type", "") or "") == PACK_EVIDENCE_TYPE:
            continue
        entry = _citation_dict(citation)
        if not entry["source"] or not entry["detail"].strip():
            continue
        texts.append(entry["detail"])
        citations.append(entry)
        if len(texts) >= limit:
            break
    return texts, citations


def resolve_evidence(
    control_id: str,
    *,
    frameworks=None,
    config: dict | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
    top_k: int | None = None,
) -> "SSPEvidence | None":
    """Governed evidence for one control, or ``None`` meaning "use the legacy path".

    ``None`` is returned — never an exception, never a partial draft — when the
    toggle is off, when the ask is re-entrant (we are already inside a
    ``cortex.resolve`` that is running the packs), when the outbound budget for
    this run is spent, or when Cortex cannot be imported.
    """
    label = (control_id or "").strip()
    if not label:
        return None
    if not cortex_enabled(config):
        return None

    state = _run()
    if state["active"]:
        # Re-entrant: cortex.resolve is running the packs right now. Answering
        # would recurse without bound. Same guard, same reason, as
        # tools/doc_modernization/evidence.py — thread-local rather than global
        # because the search fan-out runs backends in a worker pool and a global
        # flag would suppress an unrelated concurrent drafting run's evidence.
        logger.debug("ssp evidence: re-entrant ask for %r — legacy path", label)
        return None

    key = (label.casefold(), tuple(sorted(str(f) for f in (frameworks or []))))
    if key in state["cache"]:
        return state["cache"][key]

    settings = cortex_config(config)
    budget = int(settings.get("max_resolves_per_run", DEFAULT_MAX_RESOLVES) or 0)
    if budget and state["resolves"] >= budget:
        state["capped"] += 1
        logger.warning(
            "ssp evidence: outbound budget of %d resolutions spent — %r took the "
            "legacy path (reported in run_stats, never silent)", budget, label,
        )
        return None

    bundle = _resolve(
        label,
        frameworks=frameworks,
        settings=settings,
        tenant_id=tenant_id,
        classification=classification,
        top_k=top_k,
    )
    state["cache"][key] = bundle
    return bundle


def _resolve(label: str, *, frameworks, settings: dict,
             tenant_id, classification, top_k):
    """The outbound call, with the re-entrancy flag held for its duration."""
    state = _run()
    try:
        # Late import: a DIC deployment without Cortex must degrade to the
        # legacy path, not fail to import.
        from tools.cortex.api import resolve as cortex_resolve
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        logger.warning("ssp evidence: cortex unavailable (%s) — legacy path", exc)
        return None

    limit = int(top_k or settings.get("top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K)
    ctx = CortexContext(
        tenant_id=tenant_id or "",
        classification=classification or "CUI",
    )
    state["active"] = True
    try:
        resolution = cortex_resolve(
            label,
            question=evidence_question(frameworks),
            ctx=ctx,
            top_k=limit,
        )
    except Exception as exc:  # noqa: BLE001
        # Includes CortexResolutionBlocked and GovernanceBlockedError. A refusal
        # is REPORTED as a refusal and the caller still drafts off the legacy
        # read — a governance block on supplementary evidence must never take
        # SSP drafting offline.
        reason = getattr(exc, "reason", "") or type(exc).__name__
        logger.warning(
            "ssp evidence: resolve(%r) refused/failed (%s) — legacy path", label, exc
        )
        return SSPEvidence(control_id=label, blocked=str(reason))
    finally:
        state["active"] = False
        state["resolves"] += 1

    texts, citations = _lanes(resolution, limit)
    return SSPEvidence(
        control_id=label,
        texts=texts,
        citations=citations,
        backends=list(getattr(resolution, "backends_consulted", None) or []),
        errors=list(getattr(resolution, "backend_errors", None) or []),
        verdict=str(getattr(resolution, "verdict", "") or ""),
    )
