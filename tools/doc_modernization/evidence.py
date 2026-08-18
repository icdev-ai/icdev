# CUI // SP-CTI
"""DocMod evidence acquisition through ONE governed seam (cef-di-01).

``scanner.py`` and the packs under ``packs/`` read evidence by hand today —
``docmod_eol_products``, ``docmod_defacto_standards``, ``docmod_nist_pubs``,
``kg_nodes``, ``mc_net_eol_data``, ``ni_devices`` and the ``dic_*`` tables,
over an RLS-free canvas connection because those tables carry no ``tenant_id``.
Every pack that wants to reach past its own table has to learn how, which is
why only two of the seven ever do, and neither the same way.

This module is the seam that replaces that: ONE call,
``cortex.resolve(entity)``, which already fans out over the currency store,
RAG, DIC, the knowledge graph and the KB under the 8-gate TRUST chain, writes a
``cortex_audit`` row, and registers a ``source_citation_registry`` row for the
evidence set. A pack asks it and gets governed evidence for an entity without
learning a single table name.

WHAT DOES **NOT** CHANGE, AND MUST NOT
--------------------------------------
``base_pack`` TRUST rule 1. ``evaluate()`` still produces the verdict, still
deterministically, still from typed fields. This module hands a pack EVIDENCE;
the pack decides. Three properties keep that true rather than merely intended:

1. Everything returned here is a typed field a store already published —
   ``verdict`` / ``eol_date`` / ``eos_date`` / ``superseded_by`` off the
   ``currency`` backend's ``metadata``, resolved into
   ``entity_resolution.claims``. No prose is parsed and no answer is generated.
2. Only ``extraction == "structured"`` claims are handed to a pack.
   ``entity_resolution`` also produces ``text_pattern`` claims — read off a
   retrieved DOCUMENT's prose — and ``pack`` claims, which are the packs' own
   verdicts coming back around. Letting either reach ``evaluate()`` would make
   a document's sentence, or a pack's own earlier answer, the authority behind
   a deterministic verdict. :data:`STRUCTURED_EXTRACTION` is the whole filter.
3. ``resolve`` itself makes no model call (it passes ``corrective=False``, so
   even the CRAG rewrite does not run) and drops ADVISORY hits before they
   reach citations. So there is no LLM anywhere on this path, and
   ``tests/docmod/test_cortex_evidence_seam.py`` asserts it by arming the LLM
   router to raise.

THE CIRCULARITY, AND WHY THE GUARD IS NOT OPTIONAL
--------------------------------------------------
``cortex.resolve`` gets its verdict by running ``DomainPack.evaluate()`` —
``resolver.assess()`` loads the very packs that call this module. A pack
calling ``resolve`` inside ``evaluate`` therefore recurses without bound.

:data:`_STATE` is a thread-local flag set for the duration of the outbound
call, so a re-entrant ask (resolve -> assess -> pack.evaluate -> here) returns
``None`` immediately and the pack takes its legacy path. It is thread-local
rather than global because the search fan-out runs backends in a worker pool
and a global flag would suppress an unrelated concurrent sweep's evidence.

``None`` IS THE LEGACY PATH
---------------------------
Every caller reads ``None`` as "the seam said nothing — do what you did
before". The toggle being off returns ``None``, re-entrancy returns ``None``,
Cortex being absent returns ``None``, the governance chain BLOCKING returns a
bundle that carries the refusal and no evidence, and a dead backend returns a
bundle whose lanes are empty. So a document sweep can never be failed by this
module, and turning ``cortex.enabled`` off in
``args/docmod/docmod_config.yaml`` restores the pre-migration behaviour exactly
— the seam is not consulted at all.

WHAT WAS DELIBERATELY NOT MIGRATED
----------------------------------
The structured reads a verdict is DERIVED from stay where they are:
``docmod_eol_products`` (product+cycle rows), ``docmod_nist_pubs``
(``revision_num`` compared numerically), ``dic_chunk_links``/``rag_chunks``
(a hash equality), ``dic_documents`` (a timestamp comparison). Those are exact
values a retrieval seam cannot return — ``resolve`` answers with ranked
evidence, and "is 4 < 5" is not a ranking question. Replacing them would turn a
proven verdict into an approximation, which is the one outcome this card's
description forbids. What migrates is the evidence a pack reaches OUTSIDE its
own domain for — the entity-currency store and the knowledge graph — plus the
scanner's per-finding evidence attachment, which covers every pack at once.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: ``docmod_config.yaml`` block that governs this seam.
CONFIG_KEY = "cortex"

#: Evidence hits requested per Cortex backend. Small on purpose: a document
#: sweep asks about many entities, and the pack reads TYPED fields off the
#: winner rather than reading down a ranked list.
DEFAULT_TOP_K = 5

#: Upper bound on outbound resolutions per scan run. A sweep over a corpus can
#: name thousands of entities and each resolution is a five-backend fan-out;
#: without a ceiling a config flip turns a 30-second scan into an outage. The
#: cap is REPORTED (:func:`run_stats`) rather than silent — a bounded sweep that
#: reads as a complete one is the defect "no silent caps" names.
DEFAULT_MAX_RESOLVES = 250

#: The ONLY claim provenance a pack may derive a verdict from. See rule 2 above.
STRUCTURED_EXTRACTION = "structured"

#: Backend whose structured claims carry entity lifecycle fields.
CURRENCY_BACKEND = "currency"

_STATE = threading.local()


@dataclass
class CortexEvidence:
    """One entity's governed evidence. Plain data — no behaviour, no clock.

    ``currency`` and ``graph`` are the two lanes a pack reads. ``citations`` is
    the citation-shaped list (``{"source", "detail", "date"}``) that
    ``base_pack.Verdict.evidence`` is documented to hold, so a caller can
    extend a verdict's evidence with it and
    ``tools/quality/citation_grounding`` still gates whatever is drafted from
    the finding.

    ``errors`` is carried separately from an empty lane for the reason this
    repository keeps re-learning: a backend that DIED and a corpus that matched
    nothing are different answers, and merging them turns an outage into a
    statement about the data.
    """

    entity: str = ""
    citations: list = field(default_factory=list)
    currency: list = field(default_factory=list)
    graph: list = field(default_factory=list)
    backends: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    #: Non-empty when the governance chain REFUSED the resolution, carrying the
    #: ``resolver.BLOCK_*`` reason. A refusal is not an empty answer.
    blocked: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.citations or self.currency or self.graph)


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------
def cortex_config(config: dict | None = None) -> dict:
    """The ``cortex:`` block of ``args/docmod/docmod_config.yaml``."""
    if config is None:
        try:
            from .pack_loader import load_config

            config = load_config()
        except Exception as exc:  # noqa: BLE001 — an unreadable config is OFF
            logger.debug("docmod evidence: config unavailable: %s", exc)
            config = {}
    block = (config or {}).get(CONFIG_KEY)
    return dict(block) if isinstance(block, dict) else {}


def cortex_enabled(config: dict | None = None) -> bool:
    """Is the migrated path live? DEFAULT FALSE.

    Off is the shipped default and off means the seam is never consulted, so
    the legacy chain is restored by flipping this flag rather than by reverting
    a merge (the epic's migration rule).
    """
    return bool(cortex_config(config).get("enabled", False))


# ---------------------------------------------------------------------------
# Per-run state: memo cache, outbound budget, re-entrancy
# ---------------------------------------------------------------------------
def _fresh_state() -> dict:
    return {"cache": {}, "resolves": 0, "capped": 0, "active": False}


def _run() -> dict:
    """This thread's run state. Reset per scan by :func:`reset_run_state`."""
    state = getattr(_STATE, "run", None)
    if state is None:
        state = _fresh_state()
        _STATE.run = state
    return state


def reset_run_state() -> None:
    """Drop the memo cache and re-arm the outbound budget.

    Called once per scan run. The cache is per-run rather than per-process
    because the evidence it holds is live database state: memoising it for the
    lifetime of a long-running dashboard worker would make a catalog edit
    invisible until restart.
    """
    _STATE.run = _fresh_state()


def run_stats() -> dict:
    """What this run actually did — resolutions spent, asks refused by the cap."""
    state = _run()
    return {
        "resolutions": state["resolves"],
        "capped": state["capped"],
        "cached_entities": len(state["cache"]),
    }


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------
def _question(entity_type: str) -> str:
    """The retrieval framing. Fixed per entity type, so it is reproducible.

    It shapes the evidence query and nothing else — ``resolve`` never feeds it
    to a pack extractor, so it cannot move a verdict onto a different entity.
    """
    kind = (entity_type or "entity").replace("_", " ")
    return f"is this {kind} still current, superseded, or end of life?"


def _claims(resolution) -> list:
    """The structured claims a resolution's cross-backend report carried."""
    meta = getattr(resolution, "metadata", None) or {}
    report = meta.get("entity_resolution") or {}
    return [c for c in (report.get("claims") or []) if isinstance(c, dict)]


def _currency_lane(resolution) -> list:
    """Structured currency claims only — rule 2 in the module docstring.

    A ``text_pattern`` claim (read off a retrieved document's prose) and a
    ``pack`` claim (a pack's own verdict returning through the fan-out) are
    both excluded HERE, at the seam, rather than in each pack: a filter every
    caller has to remember is a filter one caller will forget.
    """
    out: list = []
    for claim in _claims(resolution):
        if claim.get("extraction") != STRUCTURED_EXTRACTION:
            continue
        if claim.get("backend") and claim["backend"] != CURRENCY_BACKEND:
            continue
        out.append(claim)
    # Authoritative first, then declared confidence, then source name so the
    # order is total and a rescan picks the same winner every time. This is the
    # store's own policy (args/entity_currency.yaml: authority ahead of
    # confidence), applied to the claims Cortex handed back.
    out.sort(
        key=lambda c: (
            bool(c.get("authoritative")),
            float(c.get("confidence") or 0.0),
            str(c.get("source") or ""),
        ),
        reverse=True,
    )
    return out


def _graph_lane(resolution) -> list:
    """Knowledge-graph citations — corroboration, never a verdict.

    The pack read this replaces (``SELECT id FROM kg_nodes ... LIMIT 1``) added
    a context line to the evidence list and could not change a verdict. Nothing
    here changes that: these are citations, and no caller derives a status from
    them.
    """
    out: list = []
    for citation in getattr(resolution, "citations", None) or []:
        source_type = str(getattr(citation, "source_type", "") or "")
        source_table = str(getattr(citation, "source_table", "") or "")
        if source_type.startswith("kg_") or source_table.startswith("kg_"):
            out.append(_citation_dict(citation))
    return out


def _citation_dict(citation) -> dict:
    """``Citation`` -> the citation-shaped dict ``Verdict.evidence`` holds.

    ``source`` is prefixed with the source TYPE because a docmod evidence id is
    read by humans and by ``citation_grounding`` alike, and a bare row id names
    nothing. Nothing is invented: a citation with no id gets an empty source and
    is dropped by the caller rather than given one.
    """
    source_id = str(getattr(citation, "source_id", "") or "")
    source_type = str(getattr(citation, "source_type", "") or "cortex")
    return {
        "source": "cortex:{}:{}".format(source_type, source_id) if source_id else "",
        "detail": str(getattr(citation, "snippet", "") or "")[:240],
        "date": "",
        "source_table": str(getattr(citation, "source_table", "") or ""),
        "title": str(getattr(citation, "title", "") or ""),
        "provenance_id": str(getattr(citation, "provenance_id", "") or ""),
    }


def resolve_evidence(
    entity_label: str,
    *,
    entity_type: str = "",
    config: dict | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
    top_k: int | None = None,
) -> "CortexEvidence | None":
    """Governed evidence for one entity, or ``None`` meaning "use the legacy path".

    ``None`` is returned — never an exception, never a partial verdict — when
    the toggle is off, when the ask is re-entrant (we are already inside a
    ``cortex.resolve`` that is running the packs), when the outbound budget for
    this run is spent, or when Cortex cannot be imported. Each case is logged
    with its own reason; they are not merged, because "off", "recursive",
    "capped" and "absent" send you to four different places.

    A resolution that was REFUSED by the governance chain returns a bundle
    carrying ``blocked`` and no evidence, rather than ``None``: a refusal is a
    fact about this entity and the caller should be able to see it, while still
    falling through to its legacy read because the bundle is empty.
    """
    label = (entity_label or "").strip()
    if not label:
        return None
    if not cortex_enabled(config):
        return None

    state = _run()
    if state["active"]:
        # Re-entrant: cortex.resolve is running the packs right now. Answering
        # would recurse without bound. See the module docstring.
        return None

    key = (label.casefold(), (entity_type or "").casefold())
    if key in state["cache"]:
        return state["cache"][key]

    settings = cortex_config(config)
    budget = int(settings.get("max_resolves_per_run", DEFAULT_MAX_RESOLVES) or 0)
    if budget and state["resolves"] >= budget:
        state["capped"] += 1
        logger.warning(
            "docmod evidence: outbound budget of %d resolutions spent — %r took the "
            "legacy path (reported in run_stats, never silent)", budget, label,
        )
        return None

    bundle = _resolve(
        label,
        entity_type=entity_type,
        settings=settings,
        tenant_id=tenant_id,
        classification=classification,
        top_k=top_k,
    )
    state["cache"][key] = bundle
    return bundle


def _resolve(label: str, *, entity_type: str, settings: dict,
             tenant_id, classification, top_k):
    """The outbound call, with the re-entrancy flag held for its duration."""
    state = _run()
    try:
        # Late import: a DocMod deployment without Cortex must degrade to the
        # legacy path, not fail to import. It is also the import that would
        # otherwise be a cycle — tools.cortex.resolver imports the pack tree.
        from tools.cortex.api import resolve as cortex_resolve
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        logger.warning("docmod evidence: cortex unavailable (%s) — legacy path", exc)
        return None

    ctx = CortexContext(
        tenant_id=tenant_id or "",
        classification=classification or "CUI",
    )
    state["active"] = True
    try:
        resolution = cortex_resolve(
            label,
            question=_question(entity_type),
            ctx=ctx,
            top_k=int(top_k or settings.get("top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K),
        )
    except Exception as exc:  # noqa: BLE001
        # Includes CortexResolutionBlocked and GovernanceBlockedError. A refusal
        # is REPORTED as a refusal and the pack still gets its deterministic
        # verdict off the legacy read — a governance block on supplementary
        # evidence must never take a document sweep offline.
        reason = getattr(exc, "reason", "") or type(exc).__name__
        logger.warning(
            "docmod evidence: resolve(%r) refused/failed (%s) — legacy path", label, exc
        )
        return CortexEvidence(entity=label, blocked=str(reason))
    finally:
        state["active"] = False
        state["resolves"] += 1

    citations = [
        c for c in (_citation_dict(x) for x in (resolution.citations or [])) if c["source"]
    ]
    return CortexEvidence(
        entity=label,
        citations=citations,
        currency=_currency_lane(resolution),
        graph=_graph_lane(resolution),
        backends=list(getattr(resolution, "backends_consulted", None) or []),
        errors=list(getattr(resolution, "backend_errors", None) or []),
    )


# ---------------------------------------------------------------------------
# Lane readers — what a pack actually consumes
# ---------------------------------------------------------------------------
def currency_assertion(bundle) -> "dict | None":
    """The winning structured currency claim, in ``entity_currency.resolve``'s shape.

    Returned in the LEGACY shape on purpose. ``packs/network_hardware.py``
    already knows how to read ``{verdict, eol_date, eos_date, source, as_of,
    confidence, conflict}`` — it has read exactly that off
    ``tools/currency/entity_currency.resolve`` since cef-fnd-04 — so the
    migration swaps WHERE the dict comes from and changes not one line of how
    the verdict is derived from it. That is what makes toggle-on and toggle-off
    comparable at all.

    ``verdict`` is the source's OWN word (``raw_status``), not the normalized
    one: the pack maps ``end_of_life``/``end_of_support`` itself, and handing it
    Cortex's coarser ``deprecated`` would silently downgrade an EOL finding to
    a support one.
    """
    if bundle is None or not bundle.currency:
        return None
    claim = bundle.currency[0]
    return {
        "verdict": claim.get("raw_status") or claim.get("status") or "",
        "eol_date": (claim.get("eol_date") or "") or None,
        "eos_date": (claim.get("eos_date") or "") or None,
        "superseded_by": claim.get("superseded_by") or "",
        "source": claim.get("source") or "",
        "as_of": claim.get("as_of") or "",
        "confidence": claim.get("confidence") or 0.0,
        "authoritative": bool(claim.get("authoritative")),
        # Two structured claims for one entity from different sources IS the
        # disagreement entity_currency reports as `conflict`. Preserved rather
        # than resolved away, same as the store does.
        "conflict": len({str(c.get("source") or "") for c in bundle.currency}) > 1,
    }


def graph_citations(bundle, label: str) -> list:
    """Knowledge-graph corroboration entries for ``Verdict.evidence``.

    Capped at one entry, matching what the hand-written ``LIMIT 1`` produced:
    corroboration is a signal that the graph knows the entity, and ten copies
    of that signal are not ten times the evidence.
    """
    if bundle is None or not bundle.graph:
        return []
    hit = bundle.graph[0]
    return [{
        "source": hit["source"],
        "detail": hit.get("title") or "KG node for {}".format(label),
        "date": "",
    }]
