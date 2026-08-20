# CUI // SP-CTI
"""``cortex.resolve`` — the governed evidence-resolution facade (cef-rsv-01).

    resolve(entity, question, ctx) -> CortexResolution

Answers "is this entity still current, and what is the evidence?" for ONE
entity, over the registered Cortex backends, with a DETERMINISTIC verdict.

WHAT THIS MODULE IS NOT
-----------------------
It is not a second governance chain and it is not a second fan-out.

* Governance is inherited whole. ``api.resolve`` is registered through
  ``_governed_facade("cortex.resolve", ...)`` exactly as ``search`` and ``ask``
  are, so the 8-gate TRUST chain, the ``cortex_audit`` row with
  ``gates_json``/``outcome``/``blocked``/``provenance_id``, and real blocking
  all apply without a line of governance code here.
* Retrieval is inherited whole. Evidence comes from ``search_service.search``
  — the same strategy router, the same bounded parallel pool with per-backend
  timeouts, the same weighted RRF fusion, the same ``BackendResults.errors``
  annotation. This module passes an explicit rung set and nothing else.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
------------------------------------------
``base_pack`` TRUST rule 1: the verdict is derived from deterministic evidence
— catalog rows, EOL dates, rulebook matches, inventory counts — and NEVER from
an LLM. Concretely, in this module:

1. The verdict comes from ``DomainPack.evaluate()`` and from nothing else.
   ``verdict_source`` has exactly two values, ``pack_evaluate`` and ``none``.
   There is no vocabulary entry for an LLM-authored verdict, so no code path
   can produce one by accident.
2. ``resolve`` makes NO LLM call. It passes ``corrective=False`` to search, so
   even the CRAG query-rewrite — the one model call that lives inside
   retrieval — does not run. A resolution is reproducible from the database.
3. An ADVISORY hit (``search_service.is_advisory``: the ``sme`` rung, an
   opinion an LLM authored at query time) is excluded from citations and can
   never reach the verdict. It is surfaced under ``metadata["advisory"]`` so
   it is visible without being evidence.
4. Every ``[source: id]`` tag in the returned prose is validated against the
   resolution's own citation ids through the SHARED
   ``tools/quality/citation_grounding``. A tag naming anything else BLOCKS —
   it does not degrade. That is stricter than the analyst's grading (which
   flags and returns) because a resolution is acted on: a redline is drafted
   from it, and a citation that resolves to nothing is how an invented
   authority gets into a document.

EVERY FINDING IS CITED, AND EVERY RESOLUTION IS REGISTERED
----------------------------------------------------------
cef-rsv-03 closes the TRUST loop over the three surfaces the prose validation
above does not reach — see ``tools/cortex/resolution_provenance.py`` for the
reasoning; in this module it is three blocks and one write:

* every ``gaps`` and ``conflicts`` entry carries ``citations`` for the evidence
  that produced it, and an id one of them points at that is NOT in the
  resolution's own citation set BLOCKS, exactly as a prose tag does. Same
  allowed set for both surfaces, so the two can never disagree about what an id
  is;
* a ``superseded_by`` rendered as "Recommended replacement:" must be attested
  by a citation. An unbacked successor BLOCKS — the resolve-side analogue of
  ``redline_drafter``'s out-of-candidate hard block, since that line is the one
  a redline is drafted from;
* every resolution that RETURNS writes one ``source_citation_registry`` row of
  type ``cortex`` attesting its evidence SET, and every citation carries that
  row's id in ``provenance_id``. The governance chain's own provenance row
  hashes the egress prose and names no source, so before this the evidence a
  verdict rests on was never registered at all.

An ADVISORY hit is excluded from cross-backend claim building for the same
reason it is excluded from citations: it is not evidence. Feeding one to the
detector let an LLM-authored opinion become a SIDE of a reported conflict,
carrying a source id no citation covers.

UNKNOWN IS A FINDING
--------------------
A verdict of ``unknown`` always carries a ``gaps`` entry naming WHY, and the
reasons are never merged, for the reason this repository keeps re-learning:
``no_pack_matched`` (nothing can evaluate this kind of entity),
``no_evidence`` (the corpora genuinely matched nothing), ``backends_failed``
(retrieval broke) and ``packs_failed`` (a pack raised) send you to four
different fixes, and collapsing them turns an outage into a statement about
the corpus.

DISAGREEMENT IS A FINDING TOO
-----------------------------
``conflicts`` is populated by ``tools/cortex/entity_resolution.py``
(cef-rsv-02), which resolves hits from DIFFERENT backends onto the same
real-world entity and compares what each one claimed. It is the only thing in
the platform that can notice a retrieved document and the curated catalog
contradicting each other — RRF ranks them against one another and never reads
them. A conflict carries every side with its own provenance and picks no
winner; the verdict above is unaffected, since it comes from the packs and a
disagreement between two evidence sources is not a vote.
"""
from __future__ import annotations

from typing import Optional, Union

from tools.logging.icdev_logger import get_logger
from tools.quality.citation_grounding import validate_citations

from .config import load_cortex_config
from .entity_resolution import (
    GAP_BACKENDS_FAILED,
    GAP_NO_EVIDENCE,
    GAP_NO_PACK,
    GAP_PACKS_FAILED,
    entity_ident,
    resolve_entities,
)
from .finding_store import record_findings
from .resolution_provenance import (
    attach_conflict_citations,
    attach_gap_citations,
    finding_citation_report,
    register_resolution,
    replacement_attestation,
)
from .schemas import (
    Citation,
    CortexContext,
    CortexResolution,
    EntityAssessment,
)
from .search_service import is_advisory
from .search_service import search as _search_impl

logger = get_logger("icdev.cortex.resolver")

#: ``doc_modernization.constants.CURRENCY_VERDICTS`` -> ``schemas.RESOLVE_VERDICTS``.
#: The ONE place the two vocabularies meet.
#:
#: Two mappings are worth their comment:
#:
#: * ``eol`` and ``retired`` both land on ``deprecated`` and are promoted to
#:   ``superseded`` below only when the pack's ``recommend()`` NAMES a
#:   successor. "Past its life" and "here is what to move to" are different
#:   claims and the second one is what a consumer can act on.
#: * ``divergent`` lands on ``unknown``, NOT on ``deprecated``. It means the
#:   fielded estate disagrees with the curated catalog — a disagreement about
#:   deployment, not a finding that the entity is stale. Promoting it would
#:   make a disagreement auto-propose a redline. The pack's own word survives
#:   verbatim on ``EntityAssessment.pack_verdict``, so nothing is lost.
#:
#: A pack verdict absent from this map is ``unknown`` and logs a warning —
#: never guessed upward. Adding a seventh docmod verdict is an entry here.
PACK_VERDICT_MAP = {
    "current": "current",
    "deprecated": "deprecated",
    "eol": "deprecated",
    "retired": "deprecated",
    "divergent": "unknown",
    "unknown": "unknown",
}

#: Reduction order when several packs assess the same entity. Higher wins.
#:
#: ``superseded > deprecated`` because it is the same finding plus a successor.
#: ``deprecated > current`` because a currency check must fail TOWARD the
#: finding: one pack recognising a deprecation must not be masked by a broader
#: pack that recognised nothing wrong. ``current > unknown`` because a positive
#: assertion beats an absence of one.
_VERDICT_RANK = {"unknown": 0, "current": 1, "deprecated": 2, "superseded": 3}

#: Backends consulted for EVIDENCE when the deployment declares no
#: ``resolve.backends`` in args/cortex_config.yaml. In-boundary evidentiary
#: rungs only.
#:
#: ``external`` is absent for the same reason it is absent from
#: ``search.fan_out.backends``: it opens a socket to a host ICDEV does not run,
#: and a currency question arriving from a document sweep must not be the
#: trigger for that. ``sme`` is absent because it is ADVISORY — an opinion
#: cannot be evidence for a deterministic verdict. A deployment may add either
#: to ``resolve.backends`` having decided so; the advisory rung still cannot
#: reach the verdict even then, because the verdict does not come from
#: retrieval at all.
DEFAULT_RESOLVE_BACKENDS = ("currency", "rag", "dic", "graph", "kb")

#: Evidence hits requested per backend. Overridable per deployment.
DEFAULT_RESOLVE_TOP_K = 5

#: Gate name recorded on the resolution's own GovernanceReport, matching
#: ``governance.GATE_CITATION_GROUNDING`` so one vocabulary describes both.
GATE_CITATION = "citation_grounding"

#: Chars of a pack rationale / evidence detail carried into the prose.
_SNIPPET_CHARS = 240

#: Gap reasons. Never merged — each sends you to a different fix.
#:
#: Defined ONCE, in ``entity_resolution``, and re-exported here so
#: ``resolver.GAP_*`` keeps resolving for everything that already reads it. Two
#: copies of a vocabulary whose whole purpose is that its members stay
#: distinguishable is the obvious way to lose that property.
#:
#: The two modules answer DIFFERENT questions with it, which is why the same
#: word carries a different rule in each and they are not merged:
#:
#: * ``_gaps`` below asks "why is the SUBJECT's verdict unknown", and a dead
#:   fan-out is a legitimate answer to that — hence ``GAP_BACKENDS_FAILED``
#:   appears in its reason list (cef-rsv-01).
#: * ``entity_resolution`` asks "did anything ANSWER for this entity", where a
#:   dead fan-out is an outage rather than an answer, so it never emits a gap
#:   for one — it emits a ``backend_error`` and an ``unresolved`` record
#:   (cef-rsv-02, AC4). It also owns a fifth reason, ``GAP_NO_CLAIM``, which is
#:   not re-exported here because nothing on this path can produce it: it means
#:   the corpora MENTION the entity and none of them states its currency, which
#:   is a question about the evidence and not about the verdict.


#: Why a resolution was refused. A CLOSED vocabulary, so a caller can branch on
#: the cause rather than on the message text, and so the three refusals stay
#: distinguishable in an audit row — an unresolvable prose tag, a finding
#: pointing outside the evidence set, and an unbacked successor send you to
#: three different fixes (a render bug, a detector bug, a pack bug).
BLOCK_HALLUCINATED_CITATION = "hallucinated_citation"
BLOCK_UNATTESTED_FINDING = "unattested_finding"
BLOCK_UNATTESTED_REPLACEMENT = "unattested_replacement"


class CortexResolutionBlocked(RuntimeError):
    """A resolution was refused rather than returned.

    Raised from INSIDE the governed operation, so ``GovernancePipeline``
    records the ``operation`` gate as failed and writes the audit row for the
    refusal before re-raising — the same shape as ``CortexQueryBlocked`` on the
    analyst path. Carries the citation report so a caller can say WHICH tag was
    unresolvable instead of "resolution failed".

    ``reason`` is one of the ``BLOCK_*`` values above and defaults to the
    citation case, which is the only one that existed in cef-rsv-01.
    """

    def __init__(self, message: str, *, entity: str = "",
                 report: Optional[dict] = None,
                 reason: str = BLOCK_HALLUCINATED_CITATION):
        super().__init__(message)
        self.entity = entity
        self.report = dict(report or {})
        self.reason = reason


# ---------------------------------------------------------------------------
# Late-bound seams — patchable without importing the heavy subsystems
# ---------------------------------------------------------------------------
def _load_packs() -> dict:
    """The registered domain packs, ``{pack_id: DomainPack}``.

    Late import: ``tools.doc_modernization`` pulls in the whole pack tree, and
    a Cortex deployment without it must degrade to ``unknown`` (a reported
    gap), not fail to import.
    """
    from tools.doc_modernization.pack_loader import load_packs

    return load_packs()


def _chunk_ref():
    """A synthetic ``ChunkRef`` for an entity that came from no document.

    ``extract()`` requires one; the packs only carry it through onto the
    candidate for reviewer display, so a resolution that did not originate in a
    DIC document names itself as the origin rather than borrowing a doc id.
    """
    from tools.doc_modernization.base_pack import ChunkRef

    return ChunkRef(doc_id="cortex.resolve", version_id="", section="entity")


def _evidence_connection():
    """A connection the packs read evidence on.

    Separate from anything Cortex writes on, for the reason the docmod scanner
    documents: on PostgreSQL one failed statement aborts the whole transaction,
    so a pack's evidence error must not poison a caller's connection.
    """
    from tools.db.storage import get_connection

    return get_connection()


# ---------------------------------------------------------------------------
# Deterministic assessment — DomainPack.evaluate(), and nothing else
# ---------------------------------------------------------------------------
def map_pack_verdict(pack_verdict: str, has_successor: bool) -> str:
    """One pack verdict -> one ``RESOLVE_VERDICTS`` value. Pure, total."""
    raw = (pack_verdict or "").strip().lower()
    mapped = PACK_VERDICT_MAP.get(raw)
    if mapped is None:
        logger.warning(
            "cortex.resolve: pack verdict %r is not in PACK_VERDICT_MAP — "
            "reported as 'unknown' rather than guessed",
            pack_verdict,
        )
        return "unknown"
    if mapped == "deprecated" and has_successor:
        return "superseded"
    return mapped


def in_scope(candidate, entity: str) -> bool:
    """Was this candidate DERIVED FROM the entity text being resolved?

    ``resolve`` answers about ONE entity. Most packs extract by matching the
    text, so their candidate's ``label``/``raw_match`` is literally a slice of
    it and this is True by construction. A DOCUMENT-scoped pack is the case
    this exists for: ``evidence_currency`` "ignores ``text`` entirely — the
    subject is the citation, not the prose", so run against the synthetic
    ChunkRef a resolution carries it asserts "(no evidence anchors)" about a
    document id (``cortex.resolve``) that is not a document. That is a
    fabricated finding AND a fabricated citation, and it would have made every
    resolution look grounded — the citation set was never empty.

    Written as a property of the CANDIDATE rather than a list of pack ids: a
    pack added later that is also document-scoped is excluded automatically,
    and nothing here names a pack.
    """
    subject = (entity or "").casefold()
    for field in ("raw_match", "label"):
        value = str(getattr(candidate, field, "") or "").strip().casefold()
        if value and value in subject:
            return True
    return False


def _assessment(pack_id: str, candidate, verdict, replacement) -> EntityAssessment:
    """``(CandidateEntity, Verdict, Replacement|None)`` -> EntityAssessment. Pure."""
    successor = str(getattr(replacement, "label", "") or "")
    evidence = [e for e in (getattr(verdict, "evidence", None) or []) if isinstance(e, dict)]
    evidence += [
        e for e in (getattr(replacement, "evidence", None) or []) if isinstance(e, dict)
    ]
    return EntityAssessment(
        entity=str(getattr(candidate, "label", "") or ""),
        entity_type=str(getattr(candidate, "entity_type", "") or ""),
        pack_id=pack_id,
        verdict=map_pack_verdict(
            getattr(verdict, "currency_verdict", ""), bool(successor)
        ),
        pack_verdict=str(getattr(verdict, "currency_verdict", "") or ""),
        finding_type=str(getattr(verdict, "finding_type", "") or ""),
        severity=str(getattr(verdict, "severity", "") or ""),
        confidence=float(getattr(verdict, "confidence", 0.0) or 0.0),
        rationale=str(getattr(verdict, "rationale", "") or ""),
        superseded_by=successor,
        replacement_source=str(getattr(replacement, "source", "") or ""),
        replacement_ref=str(getattr(replacement, "source_ref", "") or ""),
        evidence=evidence,
    )


def assess(entity: str) -> tuple:
    """Run every registered pack over ``entity``. Returns ``(assessments, errors, out_of_scope)``.

    The packs extract from the ENTITY STRING ONLY — never from the question.
    A question that mentions a second entity ("we replaced TLS 1.0 with this,
    is it ok?") would otherwise produce a verdict about the wrong subject,
    which is the one way a deterministic verdict can still be wrong.

    Candidates that did not come from the entity text are dropped by
    :func:`in_scope` and REPORTED (the third return value) rather than silently
    discarded — a drop nobody can see is how a scoping rule becomes a mystery.

    Never raises: a pack that blows up is recorded on ``errors`` (shaped like
    ``BackendResults.errors`` so a consumer reads both the same way) and the
    remaining packs still run.
    """
    assessments: list = []
    errors: list = []
    out_of_scope: list = []
    try:
        packs = _load_packs()
    except Exception as exc:  # noqa: BLE001 — no packs is a GAP, not a crash
        logger.warning("cortex.resolve: pack loading failed: %s", exc)
        return [], [{"backend": "packs", "stage": "load", "message": str(exc)}], []

    if not packs:
        return [], [], []

    chunk_ref = _chunk_ref()
    conn = None
    try:
        conn = _evidence_connection()
    except Exception as exc:  # noqa: BLE001
        logger.warning("cortex.resolve: evidence connection unavailable: %s", exc)
        errors.append({"backend": "packs", "stage": "connection", "message": str(exc)})

    def _rollback():
        if conn is None:
            return
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — best effort
            pass

    try:
        for pack_id, pack in sorted(packs.items()):
            try:
                candidates = pack.extract(entity, chunk_ref)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cortex.resolve: %s.extract failed: %s", pack_id, exc)
                errors.append(
                    {"backend": f"pack:{pack_id}", "stage": "extract", "message": str(exc)}
                )
                continue
            for candidate in candidates or []:
                if not in_scope(candidate, entity):
                    out_of_scope.append({
                        "pack_id": pack_id,
                        "label": str(getattr(candidate, "label", "") or ""),
                        "reason": "candidate not derived from the entity text",
                    })
                    continue
                try:
                    verdict = pack.evaluate(candidate, conn)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cortex.resolve: %s.evaluate failed: %s", pack_id, exc)
                    errors.append(
                        {"backend": f"pack:{pack_id}", "stage": "evaluate",
                         "message": str(exc)}
                    )
                    _rollback()
                    continue
                replacement = None
                try:
                    replacement = pack.recommend(candidate, verdict, conn)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("cortex.resolve: %s.recommend failed: %s", pack_id, exc)
                    errors.append(
                        {"backend": f"pack:{pack_id}", "stage": "recommend",
                         "message": str(exc)}
                    )
                    _rollback()
                assessments.append(_assessment(pack_id, candidate, verdict, replacement))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return assessments, errors, out_of_scope


def reduce_assessments(assessments: list) -> Optional[EntityAssessment]:
    """The winning assessment under :data:`_VERDICT_RANK`, or None.

    Deterministic on every axis so the same evidence always yields the same
    verdict: rank, then confidence, then pack_id, then entity label. No
    randomness, no dict iteration order, no clock.
    """
    if not assessments:
        return None
    return max(
        assessments,
        key=lambda a: (
            _VERDICT_RANK.get(a.verdict, 0),
            float(a.confidence or 0.0),
            a.pack_id,
            a.entity,
        ),
    )


# ---------------------------------------------------------------------------
# Evidence — the EXISTING fan-out, with an explicit in-boundary rung set
# ---------------------------------------------------------------------------
def resolve_backends(config: Optional[dict] = None) -> list:
    """The rung set consulted for evidence, from ``resolve.backends`` in config.

    Unknown names are dropped with a warning rather than raising: an operator
    typo in a YAML list must not take the verb offline, and ``search()`` would
    reject the whole call. An empty/absent declaration falls back to
    :data:`DEFAULT_RESOLVE_BACKENDS`.
    """
    from .search_service import BACKEND_ADAPTERS

    cfg = config if config is not None else load_cortex_config()
    declared = ((cfg or {}).get("resolve") or {}).get("backends") or []
    names = [str(b) for b in declared] or list(DEFAULT_RESOLVE_BACKENDS)
    kept = [b for b in names if b in BACKEND_ADAPTERS]
    dropped = [b for b in names if b not in BACKEND_ADAPTERS]
    if dropped:
        logger.warning(
            "cortex.resolve: unknown backend(s) %s in resolve.backends — skipped",
            dropped,
        )
    return kept or [b for b in DEFAULT_RESOLVE_BACKENDS if b in BACKEND_ADAPTERS]


def _evidence_query(entity: str, question: str) -> str:
    """The retrieval query. Entity first — it is the subject being resolved."""
    return f"{entity} {question}".strip() if question else entity


def _gather_evidence(entity: str, question: str, ctx: CortexContext,
                     top_k: int, config: Optional[dict]) -> tuple:
    """Fan out over the configured rungs. Returns ``(hits, errors, backends)``.

    ``corrective=False``: the CRAG rewrite is the only LLM call inside
    retrieval, and a resolution must be reproducible from the database alone.
    """
    backends = resolve_backends(config)
    try:
        hits = _search_impl(
            _evidence_query(entity, question),
            top_k=top_k,
            ctx=ctx,
            config=config,
            backends=backends,
            corrective=False,
        )
    except Exception as exc:  # noqa: BLE001 — retrieval failure is a REPORTED gap
        logger.warning("cortex.resolve: evidence retrieval failed: %s", exc)
        return [], [{"backend": "search", "stage": "fanout", "message": str(exc)}], backends
    return list(hits), list(getattr(hits, "errors", ()) or ()), backends


# ---------------------------------------------------------------------------
# Citations + prose
# ---------------------------------------------------------------------------
def _pack_citations(assessments: list) -> list:
    """Pack evidence dicts -> Citations.

    ``base_pack`` already documents ``Verdict.evidence`` as citation-shaped
    (``{"source": ..., "detail": ..., "date": ...}``) precisely so
    ``citation_grounding.validate_citations`` can gate a redline drafted from
    the finding. This is that mapping and nothing more — no source is invented,
    an entry without a ``source`` is dropped rather than given one.
    """
    out: list = []
    seen: set = set()
    for assessment in assessments:
        for entry in assessment.evidence or []:
            source = str(entry.get("source") or "").strip()
            if not source or source in seen:
                continue
            seen.add(source)
            detail = str(entry.get("detail") or "")
            out.append(Citation(
                source_id=source,
                source_type="pack_evidence",
                source_table=assessment.pack_id,
                title=assessment.entity or source,
                snippet=detail[:_SNIPPET_CHARS],
            ))
    return out


def _evidence_citations(hits: list) -> tuple:
    """Retrieved hits -> ``(citations, advisory, evidentiary)``.

    Advisory hits are split off, never cited. An ``sme`` opinion is authored by
    a model at query time; citing it would make an LLM the authority behind a
    deterministic verdict through the back door.

    ``evidentiary`` is the hits that survived that split, returned so every
    consumer downstream reads the SAME set the citations were built from. It is
    what cross-backend claim building and the gap reasons are given: a claim
    derived from an advisory hit would appear as a side of a reported conflict
    carrying a source id no citation covers, which is the same smuggling path
    through a different door (cef-rsv-03).
    """
    citations: list = []
    advisory: list = []
    evidentiary: list = []
    seen: set = set()
    for hit in hits:
        if is_advisory(hit):
            advisory.append(hit.to_dict() if hasattr(hit, "to_dict") else hit)
            continue
        evidentiary.append(hit)
        citation = getattr(hit, "citation", None)
        if citation is None:
            continue
        key = (citation.source_id, citation.source_table)
        if key in seen:
            continue
        seen.add(key)
        citations.append(citation)
    return citations, advisory, evidentiary


def _allowed_ids(citations: list) -> set:
    """Every id an inline ``[source: id]`` tag may legitimately name."""
    allowed: set = set()
    for citation in citations:
        allowed.update(x for x in (citation.source_id, citation.source_table) if x)
    return allowed


def _conflict_line(conflict: dict) -> str:
    """One conflict -> one sentence naming EVERY side and who said it.

    No ``[source: id]`` tag is emitted. The prose is validated against the
    resolution's own citation ids and a conflict side may legitimately have no
    citation of its own — the ``others`` a currency row carries name a source
    but no row id. Emitting a tag for one would either block the resolution or
    attribute the losing source's claim to the winning row's id.
    """
    sides = []
    for side in conflict.get("sides") or []:
        who = side.get("source") or side.get("backend") or "unknown source"
        value = (side.get(conflict.get("kind") or "status")
                 or side.get("status") or "?")
        sides.append(f"{who} says {value}")
    return (
        f"Conflict ({conflict.get('kind')}): {conflict.get('entity_label')} — "
        + "; ".join(sides)
        + ". Both sides are reported; no winner is picked."
    )


def render(entity: str, verdict: str, winner: Optional[EntityAssessment],
           assessments: list, citations: list, gaps: list,
           conflicts: Optional[list] = None) -> str:
    """The resolution prose. DETERMINISTIC — assembled here, not generated.

    Every ``[source: id]`` tag emitted names an id that is already on
    ``citations``, so the validation below passes by construction. It is still
    validated, because "passes by construction" is what every un-gated
    invariant in this repository said about itself before it stopped holding.
    """
    lines: list = []
    if winner is None:
        lines.append(
            f"{entity}: no registered domain pack recognises this entity, so no "
            f"deterministic currency verdict could be derived. Verdict: unknown."
        )
    else:
        lines.append(f"{entity}: {verdict} ({winner.pack_id}).")
        if winner.rationale:
            lines.append(winner.rationale.strip())
        if winner.superseded_by:
            lines.append(
                f"Recommended replacement: {winner.superseded_by} "
                f"(source: {winner.replacement_source or 'unspecified'})."
            )
        seen: set = set()
        sources = []
        for entry in winner.evidence or []:
            source = entry.get("source") if isinstance(entry, dict) else None
            if source and source not in seen:
                seen.add(source)
                sources.append(source)
        if sources:
            lines.append(" ".join(f"[source: {s}]" for s in sources))

    other = [a for a in assessments if a is not winner]
    if other:
        lines.append(
            "Other pack assessments: "
            + "; ".join(f"{a.pack_id}={a.verdict}" for a in sorted(
                other, key=lambda a: (a.pack_id, a.entity)))
            + "."
        )

    if citations:
        backends = sorted({c.source_type for c in citations if c.source_type})
        lines.append(
            f"Supporting evidence: {len(citations)} citation(s)"
            + (f" from {', '.join(backends)}" if backends else "")
            + "."
        )
    for conflict in conflicts or []:
        lines.append(_conflict_line(conflict))
    for gap in gaps:
        lines.append(
            f"Gap: {gap.get('entity')} — " + ", ".join(gap.get("reasons") or []) + "."
        )
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# The operation the facade governs
# ---------------------------------------------------------------------------
def _gaps(entity: str, verdict: str, assessments: list, hits: list,
          backend_errors: list, pack_errors: list, backends: list) -> list:
    """``unknown`` -> a finding that says WHY. Empty for any other verdict.

    The reasons are a LIST because they co-occur: no pack recognised the entity
    AND the corpora matched nothing is a different situation from either alone,
    and a consumer deciding whether to escalate to a human needs both.
    """
    if verdict != "unknown":
        return []
    reasons: list = []
    if not assessments:
        reasons.append(GAP_NO_PACK)
    if pack_errors:
        reasons.append(GAP_PACKS_FAILED)
    if not hits:
        # A dead backend and an empty corpus are NOT the same answer. Reporting
        # `no_evidence` for a fan-out that failed is exactly how an
        # infrastructure outage reaches a reader as a statement about the data.
        reasons.append(GAP_BACKENDS_FAILED if backend_errors else GAP_NO_EVIDENCE)
    if not reasons:
        # Packs ran, hits came back, and the verdict is still unknown — the
        # honest reason is that what came back did not resolve the question.
        reasons.append(GAP_NO_EVIDENCE)
    return [{
        "entity": entity,
        "reasons": reasons,
        "backends_consulted": list(backends),
        "backends_failed": sorted({str(e.get("backend") or "") for e in backend_errors}),
    }]


def resolve(
    entity: str,
    question: str = "",
    ctx: Union[CortexContext, dict, None] = None,
    top_k: int = DEFAULT_RESOLVE_TOP_K,
) -> CortexResolution:
    """Resolve one entity's currency against the registered backends.

    This is the RAW implementation. Import the GOVERNED facade
    (``tools.cortex.resolve`` / ``tools.cortex.api.resolve``) — calling this
    directly bypasses the TRUST chain, the audit row and the provenance record.

    Args:
        entity: the thing being resolved ("TLS 1.1", "Catalyst 6500",
            "NIST SP 800-53 Rev. 4"). Matched by the packs' own extractors, so
            it may be raw document text; only what a pack RECOGNISES is
            assessed.
        question: optional natural-language framing. It shapes the evidence
            query and NOTHING else — in particular it is never fed to a pack
            extractor, so it cannot move the verdict onto a different entity.
        ctx: caller identity/policy. Threaded into retrieval for RLS and into
            the domain lens (``ctx.domain`` intersects the rung set).
        top_k: evidence hits requested per backend.

    Raises:
        ValueError: ``entity`` is empty.
        CortexResolutionBlocked: the assembled prose cites a source that is not
            in the resolution's own citation set.
    """
    entity = (entity or "").strip()
    if not entity:
        raise ValueError("resolve() requires a non-empty 'entity'")
    question = (question or "").strip()
    context = ctx if isinstance(ctx, CortexContext) else CortexContext.from_dict(ctx or {})
    config = load_cortex_config()

    assessments, pack_errors, out_of_scope = assess(entity)
    winner = reduce_assessments(assessments)
    verdict = winner.verdict if winner is not None else "unknown"
    verdict_source = "pack_evaluate" if winner is not None else "none"

    hits, backend_errors, backends = _gather_evidence(
        entity, question, context, top_k, config
    )
    # `evidentiary` is `hits` minus the advisory ones, and it is what everything
    # below reads. An `sme` opinion must not count as a corpus match either:
    # answering `no_evidence` off a response that contained nothing BUT an
    # LLM-authored opinion is the same category error as citing it.
    evidence_citations, advisory, evidentiary = _evidence_citations(hits)
    citations = _pack_citations(assessments) + evidence_citations
    gaps = _gaps(entity, verdict, assessments, evidentiary, backend_errors,
                 pack_errors, backends)

    # cef-rsv-02 — resolve hits from different backends onto the SAME
    # real-world entity and compare what each one CLAIMED. This is the only
    # thing in the platform that can notice a retrieved document and the
    # curated catalog contradicting each other; RRF ranks them against each
    # other and never reads them.
    #
    # It cannot move the verdict. `verdict` is already fixed above, from the
    # packs, and nothing below reassigns it — a conflict is reported ALONGSIDE
    # the verdict, never instead of it and never as a tie-break.
    entity_report = resolve_entities(
        evidentiary,
        assessments=assessments,
        backend_errors=backend_errors,
        entities=[entity] + [a.entity for a in assessments],
        backends=backends,
        config=config,
    )
    conflicts = entity_report["conflicts"]
    # The subject's own gap is `_gaps`' to report — it answers "why is the
    # verdict unknown", which is a different question and a different reason
    # vocabulary. Taking both would put two contradictory findings about one
    # entity in one list.
    subject_key = entity_ident(entity)
    gaps = gaps + [
        gap for gap in entity_report["gaps"] if gap.get("entity_key") != subject_key
    ]

    # cef-rsv-03 — a finding that names no evidence is an assertion. Both
    # surfaces get the citations for what PRODUCED them, from the resolution's
    # own citation set: a gap cites the sources that mentioned the entity and
    # did not answer for it, a conflict cites the row behind each side.
    allowed = _allowed_ids(citations)
    gaps = attach_gap_citations(gaps, evidentiary, citations)
    conflicts = attach_conflict_citations(conflicts, citations)

    text = render(entity, verdict, winner, assessments, citations, gaps, conflicts)

    # Citation validation through the SHARED module — and this one BLOCKS.
    report = validate_citations(text, allowed)
    if report.get("hallucinated_citations"):
        raise CortexResolutionBlocked(
            f"resolution for {entity!r} cites unknown source(s): "
            f"{report['hallucinated_citations']}",
            entity=entity,
            report=report,
            reason=BLOCK_HALLUCINATED_CITATION,
        )

    # The same rule over the STRUCTURED ids the findings point at, against the
    # same allowed set. A conflict side citing a row outside the resolution's
    # evidence is the identical defect as a prose tag doing it, so it gets the
    # identical answer: refusal, not a degraded field.
    finding_report = finding_citation_report(gaps, conflicts, allowed)
    if finding_report.get("hallucinated_citations"):
        raise CortexResolutionBlocked(
            f"a finding in the resolution for {entity!r} cites unknown source(s): "
            f"{finding_report['hallucinated_citations']}",
            entity=entity,
            report=finding_report,
            reason=BLOCK_UNATTESTED_FINDING,
        )

    # The successor named in "Recommended replacement:" is the actionable claim
    # in a resolution — it is what a redline is drafted from — so it is the one
    # that must not be unbacked. redline_drafter hard-blocks the mirror image of
    # this (a draft naming a replacement outside the candidate list).
    attestation = replacement_attestation(winner, allowed)
    if attestation["claimed"] and not attestation["attested"]:
        raise CortexResolutionBlocked(
            f"resolution for {entity!r} recommends {attestation['successor']!r} "
            "with no cited evidence backing the replacement",
            entity=entity,
            report=attestation,
            reason=BLOCK_UNATTESTED_REPLACEMENT,
        )

    result = CortexResolution(
        text=text,
        citations=citations,
        entity=entity,
        question=question,
        verdict=verdict,
        verdict_source=verdict_source,
        assessments=assessments,
        gaps=gaps,
        # Detection HAS run (cef-rsv-02), so an empty list now means what it
        # always had to mean: every source that made a claim about this entity
        # made a compatible one. Each entry carries every side and its
        # provenance and names no winner — see schemas.EntityConflict.
        conflicts=conflicts,
        backend_errors=backend_errors + pack_errors,
        backends_consulted=list(backends),
        # A resolution is grounded when it has citations, none are hallucinated,
        # and a pack actually produced the verdict. An `unknown` from no pack at
        # all is a REPORT, not a grounded answer.
        grounded=bool(citations) and bool(report.get("valid"))
        and verdict_source == "pack_evaluate",
    )
    result.metadata.update({
        "citation_report": report,
        # The structured half of the same check: what the gaps and conflicts
        # pointed at, how much of it resolved, and how many sides/gaps are
        # honestly uncitable. Reported rather than folded into the prose report,
        # because "no gap cites anything" and "no tag was hallucinated" are
        # different facts.
        "finding_citation_report": finding_report,
        "replacement_attestation": attestation,
        "verdict_source": verdict_source,
        "pack_ids": sorted({a.pack_id for a in assessments}),
        "evidence_hits": len(hits),
        # Which backends actually PRODUCED a hit (cef-ci-01). Deliberately not
        # `backends_consulted`, which is a read of `resolve.backends` in the
        # config and therefore says only what the deployment declared: counting
        # a config list as consumption is the exact defect capability_liveness
        # exists to catch, and it would report every declared rung live on a
        # platform where none of them ever returned anything.
        #
        # Built from ALL hits, advisory included: an `sme` opinion is excluded
        # from citations and from the verdict, and it is still evidence that the
        # rung was reached. Consumption and citability are different questions.
        "backends_used": sorted({
            str(getattr(h, "backend", "") or "") for h in hits
        } - {""}),
        # Candidates a pack produced that were NOT about this entity (see
        # in_scope). Reported, never silent.
        "out_of_scope": out_of_scope,
        # An ADVISORY opinion is carried, visibly, and is not a citation.
        "advisory": advisory,
        # The cross-backend entity resolution behind `conflicts` and the extra
        # `gaps`: every claim with its provenance, the per-entity roll-up, and
        # `unresolved` — entities that drew no claim because RETRIEVAL DIED
        # rather than because nothing knows. That last list is deliberately not
        # gaps: an outage must not read as a statement about the corpus.
        "entity_resolution": entity_report,
    })
    result.governance.gates_run.append(GATE_CITATION)
    result.governance.outcomes[GATE_CITATION] = (
        "pass" if report.get("valid") and citations else "warn"
    )

    # One source_citation_registry row per resolution, attesting the EVIDENCE
    # SET, with its id stamped onto every citation. Runs LAST and only on a
    # resolution that survived the three blocks above — a refused resolution has
    # no evidence set worth registering, and its refusal is already in the
    # cortex_audit row the pipeline writes for the failed operation.
    #
    # Never raises and never blocks; it records `provenance` = pass | warn |
    # fail on the report above, and `fail` specifically means MISCONFIGURED
    # rather than momentarily unavailable.
    register_resolution(result, context)

    # cef-ui-02 — the conflicts and gaps above exist only on the object this
    # function returns, so the only reader of a finding is whoever triggered the
    # resolution. Project them into `cortex_entity_findings` so a human can
    # browse them on /document-intelligence/explorer long after the request.
    #
    # Runs on EVERY resolution including the clean ones: that write is the
    # denominator, and without it an empty findings table cannot be told apart
    # from a surface nothing ever looked at. Never raises, never blocks, and
    # stores NO WINNER — the sides are persisted whole, exactly as detected.
    result.metadata["finding_store"] = record_findings(result, context, config)
    return result
