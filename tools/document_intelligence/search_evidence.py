# CUI // SP-CTI
"""DIC search candidates through ONE governed seam (cef-di-04).

``DICSearchEngine.search()`` is the DIC canvas's retrieval surface -- the
dashboard search page, ``dic_chat``, ``doc_generator``, ``output_generators``
and ACE all reach the corpus through it. Its candidate half was
:meth:`DICSearchEngine._rag_search`: one ``RAGRetriever.search(...)`` call,
i.e. exactly ONE rung. The currency store, the knowledge graph and the KB hold
evidence about the same entities and none of them were ever asked, because
asking would have meant the search engine learning four more table names.

This module is the seam that replaces it: ONE call, ``cortex.resolve(query)``,
which fans out over those rungs under the 8-gate TRUST chain, writes a
``cortex_audit`` row, and registers a ``source_citation_registry`` row for the
evidence set. It is the third application of the pattern
``tools/doc_modernization/evidence.py`` (cef-di-01) and
``tools/document_intelligence/ssp_evidence.py`` (cef-di-03) established -- a
sibling module rather than a shared one, because the three answer different
questions (a pack asks "is this entity current", acoic asks "what evidences
this control", search asks "what in the corpus matches this query") and share
no lane reader.

ONLY *WHERE CANDIDATES COME FROM* MOVES
---------------------------------------
Everything ``search()`` does WITH a candidate is untouched and still runs, in
the same order, on both paths:

1. ``_chunk_meta`` / ``_doc_meta`` enrichment into the mandatory Citation pack
2. the collection post-filter
3. the CLEARANCE DROP -- still strictly BEFORE the ``top_k`` cap, so an
   accessible result is never starved by a withheld one
4. ``_rerank_by_attribution`` over the full accessible pool
5. the ``top_k`` cap

That ordering is preserved by not moving it: this module returns CANDIDATES in
the shape ``_rag_search`` already returned, and hands them to the same loop.
A migration that re-implemented the clearance drop would have to be re-proved;
one that never touches it cannot regress it.

The BM25 air-gap fallback (``_bm25_fallback``) is likewise untouched and is
still the floor under BOTH paths. The seam declining, Cortex being absent, the
governed resolution coming back empty -- every one of them lands on
``_rag_search``'s direct retriever, which falls to BM25 exactly as before.

``cortex.resolve`` makes no model call of its own (it passes
``corrective=False``, so even the CRAG rewrite does not run), so turning the
toggle on does not put an LLM anywhere one was not already.

THE CYCLE -- AND WHY THE INTERLOCK IS PROCESS-WIDE
--------------------------------------------------
Cortex's own ``dic`` rung IS ``DICSearchEngine.search()``
(``search_service.py::search_dic``), and ``dic`` is in ``resolve.backends`` in
``args/cortex_config.yaml``. So ``search -> resolve -> dic rung -> search`` is a
real cycle, and it recurses inside a BOUNDED ``ThreadPoolExecutor``
(``search_service._get_search_executor``), which makes the failure mode pool
exhaustion rather than a slow query.

The interlock is a PROCESS-WIDE depth counter, not a thread-local one, and that
is forced rather than preferred. ``_run_backends`` submits each backend onto the
shared pool, so the re-entrant call arrives on a DIFFERENT thread; the
thread-local guard cef-di-01 and cef-di-03 correctly use -- nothing calls those
two surfaces back, so their re-entrancy is same-thread, inside
``resolver.assess`` -- is structurally blind to a pool hop. A thread-local guard
here would look right, pass a single-threaded test, and recurse in production.

The rule it states: **the innermost DIC search inside a resolve fan-out is
always the raw rung.** DIC asks Cortex; Cortex asks DIC; DIC does not ask Cortex
again. Depth is bounded at 1 by construction, and the ``dic`` rung inside the
fan-out still contributes DIC-native results -- the interlock removes the
recursion, not the evidence.

Its cost is real and is REPORTED rather than hidden: while a seam-initiated
resolution is in flight, a CONCURRENT unrelated search on another thread also
takes the direct retriever. That is the pre-migration behaviour, so it is safe
degradation -- and it is counted as ``reentrant`` in :func:`run_stats`, never
silent.

``None`` IS THE LEGACY PATH
---------------------------
Every ``None`` this module returns means "the seam said nothing -- do what you
did before". The toggle being off returns ``None``, a re-entrant ask returns
``None``, a collection-scoped ask returns ``None``, a spent budget returns
``None``, and an absent Cortex returns ``None``. Each is logged with its OWN
reason and counted under its own key, because "off", "recursive", "scoped",
"capped" and "absent" send you to five different places. A resolution the
governance chain REFUSED returns a bundle carrying ``blocked`` and no
candidates instead -- a refusal is a fact about this query and a caller should
be able to see it, while still falling through because the bundle is empty.

So no search can be FAILED by this module, and ``cortex.enabled: false`` in
``args/dic_search_config.yaml`` restores the pre-migration behaviour exactly.

WHY A COLLECTION-SCOPED SEARCH DECLINES
---------------------------------------
``search(collection_id=...)`` must return evidence from THAT collection.
``cortex.resolve`` has no collection parameter: its ``dic`` rung calls
``engine.search(query, top_k, clearance)`` with no scope, and ``rag``,
``graph``, ``kb`` and ``currency`` have no notion of a DIC collection at all.
A governed candidate therefore carries no collection of record, so ``search()``'s
own post-filter (``if collection_id and col_id != collection_id``) drops every
one of them and a scoped governed search returns ZERO where the direct retriever
returned results. Declining is the SAFE answer and the honest one; the
``honour_collection_scope`` flag exists to measure that drop, not to ship it.

THE MEASURED DIFFERENCE
-----------------------
Evidence text is SHORTER on the governed path. A citation snippet is capped at
200 characters by ``tools/cortex/search_service.py`` (``_SNIPPET_CHARS``) where a
raw ``rag_chunks`` row runs to hundreds, and the candidate's ``content`` on this
path IS that snippet. Deliberate, and the same trade cef-di-03 documents: the
text a result shows must BE the text its citation records, or the persisted
provenance summarises what was retrieved instead of being it. It is also why
this toggle ships OFF -- it is a visible change to what the dashboard renders,
and it belongs to whoever turns it on.

TWO COPIES, ONE OF WHICH IS THE ONE THE ENGINE USES
---------------------------------------------------
This module ships byte-identical at ``tools/document_intelligence/`` and
``icdev/tools/document_intelligence/``, like the rest of this canvas. They are
SEPARATE module objects (``tools.X is icdev.tools.X`` -> ``False``) with separate
run state. Reach the seam through
:meth:`DICSearchEngine._search_evidence_module`, which resolves ONE of them
(``icdev`` first); a test that patches
``tools.document_intelligence.search_evidence`` while the engine holds the
``icdev`` one patches nothing. Patch what that method returns.

The interlock itself is the ONE piece of state that must NOT be per-copy -- a
recursion that entered through one copy and returns through the other would be
invisible to a counter held on either. The depth counter is therefore published
on a module the two copies share (``tools.rag.vector_store_provider``, which
both already import) under a private attribute, so both copies read and write
the SAME counter.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Config filename. Flat ``args/dic_*.yaml`` like the rest of the DIC canvas.
CONFIG_FILENAME = "dic_search_config.yaml"


def _default_config_path() -> Path:
    """``args/dic_search_config.yaml``, found by walking up from this file.

    A hardcoded ``parents[N]`` cannot be right in both trees: this module ships
    byte-identical at two different depths, and the two copies must stay
    identical or the mirror-drift gate fires on a difference that is correct.
    Walking up finds the one ``args/`` directory from either depth. An unfound
    config is ``{}``, which reads as the shipped default (OFF).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "args" / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return here.parents[2] / "args" / CONFIG_FILENAME


#: Resolved once at import. Behaviour config for this seam.
CONFIG_PATH = _default_config_path()

#: Block within that file which governs this seam.
CONFIG_KEY = "cortex"

#: Floor on evidence citations requested per Cortex backend. ``search()`` asks
#: for ``top_k * 2`` candidates so the clearance drop and the collection filter
#: have headroom; the seam is handed that same number.
DEFAULT_TOP_K = 10

#: Upper bound on outbound resolutions per run.
DEFAULT_MAX_RESOLVES = 50

#: Citation source_type that is a pack's OWN verdict rationale, not corpus
#: evidence. Never returned as a candidate. Same rule, same reason, as
#: ``ssp_evidence``: a derived verdict must not become the evidence a search
#: result cites.
PACK_EVIDENCE_TYPE = "pack_evidence"

#: ``origin`` values recording which chain produced a result set.
PATH_CORTEX = "cortex"
PATH_CORTEX_EMPTY_FALLBACK = "cortex_empty_fallback"
PATH_LEGACY = "legacy"

_STATE = threading.local()
_CONFIG_CACHE: dict = {}


# ---------------------------------------------------------------------------
# The process-wide interlock
# ---------------------------------------------------------------------------
# Held on a module BOTH copies of this file import, so a recursion that enters
# through `tools.` and returns through `icdev.tools.` is still seen. See the
# module docstring; a per-copy counter would be two counters and neither would
# be the one the cycle crosses.
_DEPTH_ATTR = "_dic_search_evidence_depth"
_DEPTH_LOCK_ATTR = "_dic_search_evidence_depth_lock"
#: Fires of the interlock. Process-wide for the SAME reason the depth is, and
#: measured to be so: the re-entrant ask arrives on a pool worker thread, so a
#: per-run (thread-local) counter increments on a thread nobody reads and the
#: caller's ``run_stats()`` reports 0 for an interlock that fired every time.
#: Observed exactly that on the live canvas before this was moved out
#: (2026-08-18): three governed searches, three fan-outs, ``reentrant: 0``.
_REENTRANT_ATTR = "_dic_search_evidence_reentrant"


def _shared():
    """The module the two copies of this file share, for interlock state."""
    from tools.rag import vector_store_provider

    return vector_store_provider


def _depth_lock() -> threading.Lock:
    shared = _shared()
    lock = getattr(shared, _DEPTH_LOCK_ATTR, None)
    if lock is None:
        # Racy only in the sense that two threads could each build a Lock on a
        # cold module; setattr is atomic and the loser's lock is discarded
        # before either has guarded anything.
        lock = threading.Lock()
        setattr(shared, _DEPTH_LOCK_ATTR, lock)
    return lock


def resolve_depth() -> int:
    """How many seam-initiated resolutions are in flight in this PROCESS."""
    try:
        return int(getattr(_shared(), _DEPTH_ATTR, 0) or 0)
    except Exception:  # noqa: BLE001 -- an unreadable interlock must not block
        return 0


def _enter() -> None:
    try:
        with _depth_lock():
            setattr(_shared(), _DEPTH_ATTR, resolve_depth() + 1)
    except Exception:  # noqa: BLE001
        logger.debug("dic search evidence: interlock enter failed", exc_info=True)


def _leave() -> None:
    try:
        with _depth_lock():
            setattr(_shared(), _DEPTH_ATTR, max(0, resolve_depth() - 1))
    except Exception:  # noqa: BLE001
        logger.debug("dic search evidence: interlock leave failed", exc_info=True)


def interlock_fires() -> int:
    """How many asks the interlock has sent to the direct retriever, PROCESS-WIDE.

    Monotonic since process start (or the last :func:`reset_interlock`) rather
    than per-run, because the event it counts happens on a Cortex pool worker
    thread and a per-run counter would tally it on a thread no caller reads.
    """
    try:
        return int(getattr(_shared(), _REENTRANT_ATTR, 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _count_reentrant() -> None:
    try:
        with _depth_lock():
            setattr(_shared(), _REENTRANT_ATTR, interlock_fires() + 1)
    except Exception:  # noqa: BLE001
        logger.debug("dic search evidence: interlock count failed", exc_info=True)


def reset_interlock() -> None:
    """Zero the depth and the fire count. For tests and for a fresh worker."""
    try:
        with _depth_lock():
            shared = _shared()
            setattr(shared, _DEPTH_ATTR, 0)
            setattr(shared, _REENTRANT_ATTR, 0)
    except Exception:  # noqa: BLE001
        logger.debug("dic search evidence: interlock reset failed", exc_info=True)


@dataclass
class SearchEvidence:
    """One query's governed candidate set. Plain data -- no behaviour, no clock.

    ``candidates`` are in the shape ``DICSearchEngine._rag_search`` already
    returned (``chunk_id`` / ``content`` / ``source_id`` / ``final_score``), so
    the caller's enrichment, collection filter, clearance drop, attribution
    rerank and cap all run over them unchanged. ``candidates[i]`` is described
    by ``citations[i]`` -- index-aligned, like ``ssp_evidence``.

    ``errors`` is carried separately from an empty candidate list for the reason
    this repository keeps re-learning: a backend that DIED and a corpus that
    matched nothing are different answers, and merging them turns an outage into
    a statement about the data. It is what tells ``fallback_on_empty`` apart from
    "this query genuinely matches nothing".
    """

    query: str = ""
    candidates: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    backends: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    verdict: str = ""
    #: Non-empty when the governance chain REFUSED the resolution, carrying the
    #: refusal reason. A refusal is not an empty answer.
    blocked: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.candidates


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------
def load_config(path=None) -> dict:
    """``args/dic_search_config.yaml``, memoised per path.

    An unreadable or absent file is ``{}``, which reads as OFF everywhere below.
    A config this module cannot parse must not take DIC search offline; it must
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
    except Exception as exc:  # noqa: BLE001 -- an unreadable config is OFF
        logger.debug("dic search evidence: config unavailable (%s) -- seam is OFF", exc)
    _CONFIG_CACHE[key] = data
    return data


def cortex_config(config: dict | None = None) -> dict:
    """The ``cortex:`` block of ``args/dic_search_config.yaml``."""
    if config is None:
        config = load_config()
    block = (config or {}).get(CONFIG_KEY)
    return dict(block) if isinstance(block, dict) else {}


def cortex_enabled(config: dict | None = None) -> bool:
    """Is the migrated path live? DEFAULT FALSE.

    Off is the shipped default and off means the seam is never consulted, so the
    legacy chain is restored by flipping this flag rather than by reverting a
    merge (the epic's migration rule).
    """
    return bool(cortex_config(config).get("enabled", False))


def fallback_on_empty(config: dict | None = None) -> bool:
    """Take the direct retriever when the governed path produced no candidate?

    DEFAULT TRUE. See the measured caveat in the module docstring -- a cold
    process loses its ``rag`` and ``dic`` rungs to the shared Cortex timeouts,
    and a migration that quietly drops the only evidence a search can return is
    not behaviour-preserving.
    """
    return bool(cortex_config(config).get("fallback_on_empty", True))


def honour_collection_scope(config: dict | None = None) -> bool:
    """Ask the seam for a collection-scoped search? DEFAULT FALSE.

    False is the SAFE answer, not a stub -- see the module docstring. A governed
    candidate carries no collection of record, so the caller's post-filter drops
    every one of them and a scoped governed search returns ZERO.
    """
    return bool(cortex_config(config).get("honour_collection_scope", False))


# ---------------------------------------------------------------------------
# Per-run state: memo cache, outbound budget, declined-ask counters
# ---------------------------------------------------------------------------
def _fresh_state() -> dict:
    # `reentrant` is deliberately NOT here — see :func:`interlock_fires`.
    return {
        "cache": {},
        "resolves": 0,
        "capped": 0,
        "declined_collection_scoped": 0,
    }


def _run() -> dict:
    """This thread's run state. Reset per run by :func:`reset_run_state`."""
    state = getattr(_STATE, "run", None)
    if state is None:
        state = _fresh_state()
        _STATE.run = state
    return state


def reset_run_state() -> None:
    """Drop the memo cache and re-arm the outbound budget.

    The cache is per-run rather than per-process because the evidence it holds
    is live database state: memoising it for the lifetime of a long-running
    dashboard worker would make a newly ingested document invisible until
    restart.
    """
    _STATE.run = _fresh_state()


def run_stats() -> dict:
    """What this run actually did -- spent, capped, declined.

    ``reentrant`` and ``declined_collection_scoped`` are the two ways a governed
    deployment quietly serves the legacy path, so both are counted. A migration
    whose engagement rate cannot be measured is the defect this platform ships
    most.

    The two are counted at DIFFERENT scopes and that is not an inconsistency:
    ``declined_collection_scoped``, ``resolutions`` and ``capped`` are facts
    about THIS run on THIS thread, while ``reentrant`` is a fact about the
    process (:func:`interlock_fires`) because the ask it counts arrives on a
    Cortex pool worker thread. Tallying it per-run reported 0 for an interlock
    that fired on every fan-out.
    """
    state = _run()
    return {
        "resolutions": state["resolves"],
        "capped": state["capped"],
        "reentrant": interlock_fires(),
        "declined_collection_scoped": state["declined_collection_scoped"],
        "cached_queries": len(state["cache"]),
        "depth": resolve_depth(),
    }


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------
def evidence_question(collection_id: str | None = None) -> str:
    """The retrieval framing. Constant, so a resolution is reproducible.

    It shapes the evidence query and nothing else -- ``resolve`` never feeds a
    question to a pack extractor, so it cannot move the resolution onto a
    different subject. The collection is deliberately NOT interpolated: a
    collection id is an opaque uuid no corpus contains, so putting it in the
    query would add noise to a retrieval it cannot scope.
    """
    return "supporting evidence from the document corpus"


def _citation_dict(citation) -> dict:
    """``Citation`` -> the citation-shaped dict carried alongside a candidate.

    Nothing is invented: a citation with no id gets an empty source and is
    dropped by :func:`_candidates` rather than given one.
    """
    source_id = str(getattr(citation, "source_id", "") or "")
    source_type = str(getattr(citation, "source_type", "") or "cortex")
    return {
        "source": "cortex:{}:{}".format(source_type, source_id) if source_id else "",
        "source_id": source_id,
        "detail": str(getattr(citation, "snippet", "") or ""),
        "source_type": source_type,
        "source_table": str(getattr(citation, "source_table", "") or ""),
        "title": str(getattr(citation, "title", "") or ""),
        "url": str(getattr(citation, "url", "") or ""),
        "classification": str(
            getattr(citation, "clearance_required", "")
            or getattr(citation, "classification", "")
            or ""
        ),
        "provenance_id": str(getattr(citation, "provenance_id", "") or ""),
    }


def _candidates(resolution, limit: int) -> tuple:
    """``(candidates, citations)`` -- index-aligned, ``pack_evidence`` excluded.

    ``resolution.citations`` arrive in RRF-fused rank order, so position is the
    only ranking signal the resolution carries -- a ``Citation`` has no score
    field. ``final_score`` is therefore a descending rank score in (0, 1], which
    is enough for the caller: ``_rerank_by_attribution`` blends it at 0.3 behind
    a 0.7 attribution term it recomputes from the content itself, so the order
    this function emits breaks near-ties and nothing more.

    The candidate's ``content`` IS the citation's snippet. Deliberate, and the
    trade documented at the top of this module: a rendered result must BE the
    text its citation records.
    """
    try:
        from tools.rag.vector_store_provider import SearchResult
    except Exception as exc:  # noqa: BLE001 -- no shape to return candidates in
        logger.warning("dic search evidence: SearchResult unavailable (%s)", exc)
        return [], []

    entries: list = []
    for citation in getattr(resolution, "citations", None) or []:
        if str(getattr(citation, "source_type", "") or "") == PACK_EVIDENCE_TYPE:
            continue
        entry = _citation_dict(citation)
        if not entry["source"] or not entry["detail"].strip():
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break

    total = len(entries)
    candidates: list = []
    for index, entry in enumerate(entries):
        result = SearchResult(
            chunk_id=entry["source_id"],
            content=entry["detail"],
            source_id=entry["source_id"],
            source_table=entry["source_table"],
            source_type=entry["source_type"],
            classification=entry["classification"] or "CUI",
        )
        result.final_score = (total - index) / float(total)
        candidates.append(result)
    return candidates, entries


def resolve_evidence(
    query: str,
    *,
    collection_id: str | None = None,
    config: dict | None = None,
    tenant_id: str | None = None,
    clearance: str | None = None,
    top_k: int | None = None,
) -> "SearchEvidence | None":
    """Governed candidates for one query, or ``None`` meaning "use the legacy path".

    ``None`` is returned -- never an exception, never a partial result set --
    when the toggle is off, when the ask is re-entrant (this process is already
    inside a seam-initiated ``cortex.resolve``, whose ``dic`` rung is what is
    asking), when the ask is collection-scoped and the seam cannot honour a
    scope, when the outbound budget for this run is spent, or when Cortex cannot
    be imported.
    """
    label = (query or "").strip()
    if not label:
        return None
    if not cortex_enabled(config):
        return None

    state = _run()

    if collection_id and not honour_collection_scope(config):
        state["declined_collection_scoped"] += 1
        logger.debug(
            "dic search evidence: %r is collection-scoped (%s) -- direct retriever",
            label[:60], collection_id,
        )
        return None

    if resolve_depth() > 0:
        # We are inside a seam-initiated resolution right now, and this ask is
        # very likely its own `dic` rung calling back (search_service.search_dic
        # -> DICSearchEngine.search -> here) on a pool worker thread. Answering
        # would recurse without bound inside a bounded pool. See the module
        # docstring for why this counter cannot be thread-local.
        _count_reentrant()
        logger.debug(
            "dic search evidence: re-entrant ask for %r -- direct retriever", label[:60]
        )
        return None

    key = (label.casefold(), str(collection_id or ""), str(clearance or ""))
    if key in state["cache"]:
        return state["cache"][key]

    settings = cortex_config(config)
    budget = int(settings.get("max_resolves_per_run", DEFAULT_MAX_RESOLVES) or 0)
    if budget and state["resolves"] >= budget:
        state["capped"] += 1
        logger.warning(
            "dic search evidence: outbound budget of %d resolutions spent -- %r took "
            "the direct retriever (reported in run_stats, never silent)",
            budget, label[:60],
        )
        return None

    bundle = _resolve(
        label,
        collection_id=collection_id,
        settings=settings,
        tenant_id=tenant_id,
        clearance=clearance,
        top_k=top_k,
    )
    state["cache"][key] = bundle
    return bundle


def _resolve(label: str, *, collection_id, settings: dict,
             tenant_id, clearance, top_k):
    """The outbound call, with the process-wide interlock held for its duration."""
    state = _run()
    try:
        # Late import: a DIC deployment without Cortex must degrade to the
        # direct retriever, not fail to import.
        from tools.cortex.api import resolve as cortex_resolve
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "dic search evidence: cortex unavailable (%s) -- direct retriever", exc
        )
        return None

    limit = int(top_k or settings.get("top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K)
    # `clearance` is threaded through as the context classification so the
    # fan-out's own read-down applies at every rung -- in particular the `dic`
    # rung, which passes it straight back into `engine.search(clearance=...)`.
    # The caller's clearance drop still runs over whatever comes back; this is a
    # second, earlier screen, never a replacement for it.
    ctx = CortexContext(
        tenant_id=tenant_id or "",
        classification=clearance or "CUI",
    )
    _enter()
    try:
        resolution = cortex_resolve(
            label,
            question=evidence_question(collection_id),
            ctx=ctx,
            top_k=limit,
        )
    except Exception as exc:  # noqa: BLE001
        # Includes CortexResolutionBlocked and GovernanceBlockedError. A refusal
        # is REPORTED as a refusal and the caller still searches off the direct
        # retriever -- a governance block on supplementary evidence must never
        # take DIC search offline.
        reason = getattr(exc, "reason", "") or type(exc).__name__
        logger.warning(
            "dic search evidence: resolve(%r) refused/failed (%s) -- direct retriever",
            label[:60], exc,
        )
        return SearchEvidence(query=label, blocked=str(reason))
    finally:
        _leave()
        state["resolves"] += 1

    candidates, citations = _candidates(resolution, limit)
    return SearchEvidence(
        query=label,
        candidates=candidates,
        citations=citations,
        backends=list(getattr(resolution, "backends_consulted", None) or []),
        errors=list(getattr(resolution, "backend_errors", None) or []),
        verdict=str(getattr(resolution, "verdict", "") or ""),
    )
