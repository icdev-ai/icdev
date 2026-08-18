# CUI // SP-CTI
"""Citation attribution and provenance persistence for one resolution (cef-rsv-03).

``cortex.resolve`` already derived a deterministic verdict and validated the
inline ``[source: id]`` tags in its prose (cef-rsv-01). Three things it did not
do, and this module is those three:

1. **A finding carried no citations.** A ``gaps`` entry named its reasons and a
   ``conflicts`` entry named every side, and neither pointed at the EVIDENCE
   that produced it. A gap and a conflict are the two outputs of a resolution a
   human acts on — one opens a data-quality ticket, the other opens an
   adjudication — and both arrived unattributable. Cited, they are checkable;
   uncited, they are assertions.
2. **Nothing persisted the evidence set.** The governance chain's provenance
   gate writes one ``source_citation_registry`` row per governed call whose
   ``source_hash`` is a sha256 of the EGRESS PROSE (``governance.py`` gate 8).
   That attests what was said. It names no source, so the evidence a verdict
   rests on was never registered, and ``Citation.provenance_id`` — a field that
   has existed on the schema since Cortex shipped — was empty on every citation
   a resolution ever returned.
3. **An unattested successor was rendered as an instruction.** ``render()``
   emits "Recommended replacement: X" straight from ``pack.recommend()``. That
   line is the one a redline is drafted from, and
   ``doc_modernization/redline_drafter.py`` hard-blocks a draft naming a
   replacement outside the candidate list for exactly that reason. The resolve
   side had no equivalent, so a pack naming a successor it could not back would
   have produced an actionable, uncited instruction.

NO CITATION PARSING LIVES HERE
------------------------------
``tools/quality/citation_grounding`` owns citation parsing and text validation,
and ``resolver`` already calls ``validate_citations`` for the prose. This module
never looks at text: a claim already carries its provenance as STRUCTURED
fields (``EntityClaim.source_id`` / ``source_table``), so attribution here is a
lookup and validation is set arithmetic against the SAME allowed-id set the
prose is validated against. One allowed set, two surfaces, one parser.

AN UNCITABLE SIDE IS REPORTED, NEVER INVENTED
---------------------------------------------
Some sides genuinely have no row to cite. The ``entity_currency`` store's
``others`` — the sources that LOST read-time resolution — name an authority and
carry no record id, and cef-rsv-02 deliberately left ``source_id`` empty there
rather than lend them the winning row's id. Such a side lands in
``uncited_sides`` with the reason stated. It is not given the nearest available
citation, and its absence is not folded into "this conflict has no evidence" —
a conflict with two cited sides and one uncitable one is a different object
from a conflict nothing supports.

The same rule shapes a gap's ``citation_basis``, which exists because an empty
``citations`` list on a gap has three causes and only one of them is a corpus
problem: :data:`BASIS_EVIDENCE` (sources mention the entity and none answers —
cite them), :data:`BASIS_NO_EVIDENCE` (nothing matched, so there is genuinely
nothing to cite) and :data:`BASIS_RETRIEVAL_FAILED` (the fan-out died, and an
outage must never read as a statement about the data). Structural, not a
tooltip.

WHY ONE REGISTRY ROW PER RESOLUTION AND NOT ONE PER CITATION
------------------------------------------------------------
``register_citation`` opens its own connection per call, so a row per citation
would put twenty-odd connections and inserts on a verb whose whole point is
being cheap enough to run over a document sweep. One row attests the SET: its
``source_hash`` is a digest of every citation's identity, recomputable from the
returned resolution by :func:`citation_digest`, and its id is stamped onto
every citation's ``provenance_id`` so the join runs both ways. Registering each
citation separately buys nothing the digest does not, and costs the verb its
latency budget.

A FAILED WRITE IS LEGIBLE, AND NEVER BLOCKS
-------------------------------------------
Three statuses, and the split is the cxo-trust-01 lesson: a ``ValueError`` from
``register_citation`` means the ``citation_type`` is not in ``CITATION_TYPES``
— a programming error that made the Cortex provenance gate write 0 of 285 rows
for its entire lifetime while recording a merely-flaky ``warn``. So
:data:`STATUS_MISCONFIGURED` logs at ERROR and records the ``fail`` outcome,
:data:`STATUS_UNAVAILABLE` (connection refused, table missing) records ``warn``,
and only :data:`STATUS_WRITTEN` records ``pass``. None of the three blocks:
``governance.py`` documents its provenance gate as never blocking and changing
that is a platform-wide decision, not one this module takes unilaterally. What
this module does guarantee is that the outcome is on the report either way.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from tools.logging.icdev_logger import get_logger

from .schemas import Citation, CortexContext

logger = get_logger("icdev.cortex.resolution_provenance")

#: ``source_citation_registry.citation_type``. An ALREADY-REGISTERED vocabulary
#: value (``tools/provenance/citation_types.CITATION_TYPES``), shared with the
#: governance gate's row. A new value here would need a migration rendered from
#: ``check_constraint_sql()`` and, until it had one, would raise before the
#: INSERT and land nothing — which is cxo-trust-01 verbatim.
CITATION_TYPE = "cortex"

#: ``source_citation_registry.source_table`` for a resolution's row.
#:
#: Distinct from the governance gate's ``cortex_governance`` on purpose: the two
#: rows attest different things about the same call (a prose hash vs an evidence
#: digest) and a reader must be able to tell them apart in one query. Both name
#: the logical producer rather than a physical table, following the gate's
#: existing convention.
SOURCE_TABLE = "cortex_resolution"

#: Sources mention the entity and none of them answers for it. The mentioning
#: evidence IS cited — the gap is about what that evidence does not say.
BASIS_EVIDENCE = "evidence_did_not_answer"
#: Nothing matched at all, so there is nothing to cite. The absence is the
#: finding.
BASIS_NO_EVIDENCE = "no_evidence_retrieved"
#: Retrieval died. Never merged with the one above — an outage is not a
#: statement about the corpus, and the two send you to different fixes.
BASIS_RETRIEVAL_FAILED = "retrieval_failed"

#: Why a conflict side could not be cited. One value, because there is one
#: honest cause: the source named an authority and no row.
SIDE_NO_ROW_ID = "source names an authority but no row id"

#: Provenance write outcomes. See the module docstring.
STATUS_WRITTEN = "written"
STATUS_UNAVAILABLE = "unavailable"
STATUS_MISCONFIGURED = "misconfigured"

#: ``STATUS_* -> GovernanceReport`` outcome, using ``governance``'s vocabulary
#: so one word means one thing on both reports.
STATUS_OUTCOME = {
    STATUS_WRITTEN: "pass",
    STATUS_UNAVAILABLE: "warn",
    STATUS_MISCONFIGURED: "fail",
}

#: Gate name recorded on the resolution's own report, matching
#: ``governance.GATE_PROVENANCE``.
GATE_PROVENANCE = "provenance"


# ---------------------------------------------------------------------------
# Late-bound seam — patchable without a database
# ---------------------------------------------------------------------------
def _register_citation(**kwargs) -> str:
    """The shared unified-registry writer. Late import, one seam, no wrapper."""
    from tools.provenance.registry import register_citation

    return register_citation(**kwargs)


# ---------------------------------------------------------------------------
# Attribution — a finding points at the evidence that produced it
# ---------------------------------------------------------------------------
def _citation_by_id(citations) -> dict:
    """``source_id -> Citation``, first occurrence wins.

    Keyed on ``source_id`` ALONE, deliberately. ``resolver._pack_citations``
    de-duplicates evidence by source across every assessment, so one rule cited
    by two packs yields ONE citation carrying the first pack's id in
    ``source_table``; keying on the pair would then fail to find the second
    pack's claim and report a legitimate resolution as citing an unknown source.
    It is also the key ``resolver._allowed_ids`` validates against, so
    attribution and validation cannot disagree about what an id is.
    """
    index: dict = {}
    for citation in citations or []:
        source_id = str(getattr(citation, "source_id", "") or "")
        if source_id and source_id not in index:
            index[source_id] = citation
    return index


def _as_dict(citation) -> dict:
    return citation.to_dict() if hasattr(citation, "to_dict") else dict(citation or {})


def _citation_identity(citation) -> list:
    """The three fields that identify a citation's SOURCE, for hashing."""
    return [
        str(getattr(citation, "source_id", "") or ""),
        str(getattr(citation, "source_table", "") or ""),
        str(getattr(citation, "source_type", "") or ""),
    ]


def _mentioning_ids(hits, label: str) -> list:
    """Source ids of the hits that MENTION ``label``, in retrieval order.

    Uses ``entity_resolution._mentions`` — the same predicate that decided the
    gap's reason. A second, locally written mention test could let a gap say
    ``no_claim`` ("the corpus HAS this entity") while its citation list says
    nothing mentioned it, which is one finding contradicting itself.
    """
    from .entity_resolution import _mentions

    ids: list = []
    for hit in hits or []:
        if not _mentions(hit, label):
            continue
        citation = getattr(hit, "citation", None)
        source_id = str(getattr(citation, "source_id", "") or "") if citation else ""
        if source_id and source_id not in ids:
            ids.append(source_id)
    return ids


def gap_basis(reasons, cited: bool) -> str:
    """Which of the three causes of an empty ``citations`` list this gap is.

    Derived from the gap's own reason vocabulary rather than declared beside it,
    so a fourth reason cannot arrive with no basis. ``backends_failed`` is read
    from ``reasons`` and NOT from the gap's ``backends_failed`` field:
    ``entity_resolution`` carries that field on a PARTIAL outage as context for
    a gap it is not the cause of, and treating it as the cause would relabel a
    genuine corpus gap as an outage.
    """
    from .entity_resolution import GAP_BACKENDS_FAILED

    if cited:
        return BASIS_EVIDENCE
    if GAP_BACKENDS_FAILED in list(reasons or ()):
        return BASIS_RETRIEVAL_FAILED
    return BASIS_NO_EVIDENCE


def attach_gap_citations(gaps, hits, citations) -> list:
    """Give every gap the citations for the evidence that produced it.

    A gap says "nothing answered for this entity". The evidence behind that
    claim is whatever DID come back mentioning the entity without answering —
    so those hits are cited, and the gap becomes checkable: a reader can open
    the cited sources and see the silence for themselves.

    Pure. Returns new dicts; the input gaps are not mutated, because the
    detector's own gaps also travel on ``metadata["entity_resolution"]`` and a
    caller reading that report must see what the detector produced.
    """
    index = _citation_by_id(citations)
    out: list = []
    for gap in gaps or []:
        label = str(gap.get("entity") or "")
        ids = _mentioning_ids(hits, label) if label else []
        attached = [_as_dict(index[i]) for i in ids if i in index]
        entry = dict(gap)
        entry["citations"] = attached
        # Every id the gap POINTS AT, cited or not, so the validation below has
        # something to check even when the lookup found nothing.
        entry["citation_ids"] = list(ids)
        entry["citation_basis"] = gap_basis(gap.get("reasons"), bool(attached))
        out.append(entry)
    return out


def attach_conflict_citations(conflicts, citations) -> list:
    """Give every conflict the citations for the sides that produced it.

    Each side already carries its own ``source_id``/``source_table``; this
    resolves those pointers to the resolution's own citations so a consumer
    rendering the disagreement can link each claim to the row it came from
    without re-deriving anything.

    A side that names no row id is reported under ``uncited_sides`` rather than
    dropped or lent a neighbour's citation — see the module docstring. Pure.
    """
    index = _citation_by_id(citations)
    out: list = []
    for conflict in conflicts or []:
        ids: list = []
        uncited: list = []
        for side in conflict.get("sides") or []:
            source_id = str(side.get("source_id") or "")
            if not source_id:
                uncited.append({
                    "source": str(side.get("source") or ""),
                    "backend": str(side.get("backend") or ""),
                    "status": str(side.get("status") or ""),
                    "reason": SIDE_NO_ROW_ID,
                })
                continue
            if source_id not in ids:
                ids.append(source_id)
        entry = dict(conflict)
        entry["citations"] = [_as_dict(index[i]) for i in ids if i in index]
        entry["citation_ids"] = ids
        entry["uncited_sides"] = uncited
        out.append(entry)
    return out


def finding_citation_report(gaps, conflicts, allowed) -> dict:
    """Validate every id a finding points at against the resolution's own set.

    Set arithmetic, not parsing: the ids are structured fields the claims and
    hits already carried, and ``allowed`` is the SAME set
    ``resolver._allowed_ids`` builds for the prose. The report shape reuses
    ``citation_grounding.validate_citations``' key names so a caller reads one
    vocabulary across both surfaces.

    A gap's ids come from hits that are already in the citation set, so that
    half passes by construction today. It is checked anyway, for the reason
    cef-rsv-01 gave for validating its own assembled prose: "passes by
    construction" is what every un-gated invariant in this repository said about
    itself before it stopped holding. The CONFLICT half is not
    by-construction — a side's pointer comes from a claim, and a claim can be
    built from a hit whose citation never entered the set. An ADVISORY hit is
    exactly that case, which is why ``resolver`` no longer feeds those to the
    detector at all.
    """
    available = {str(a) for a in (allowed or ())}
    cited: list = []
    for finding in list(gaps or ()) + list(conflicts or ()):
        for source_id in finding.get("citation_ids") or ():
            if str(source_id) not in cited:
                cited.append(str(source_id))
    hallucinated = sorted(set(cited) - available)
    return {
        "checked": len(cited),
        "available_count": len(available),
        "hallucinated_citations": hallucinated,
        "valid": not hallucinated,
        "uncited_conflict_sides": sum(
            len(c.get("uncited_sides") or ()) for c in (conflicts or ())
        ),
        "uncited_gaps": sum(1 for g in (gaps or ()) if not g.get("citations")),
    }


# ---------------------------------------------------------------------------
# The replacement claim must be attested
# ---------------------------------------------------------------------------
def replacement_attestation(winner, allowed) -> dict:
    """Is the successor this resolution RECOMMENDS backed by cited evidence?

    Returns ``{"claimed", "attested", "successor", "ref"}``.

    The analogue of ``redline_drafter``'s gate 2 on the resolve side. There the
    LLM could name a product outside the candidate list; here there is no LLM
    and the candidate comes from ``pack.recommend()``, so the failure mode is
    the other one: a pack naming a successor it cannot point at. Every shipped
    pack sets ``Replacement.source_ref`` to its evidence's source, so this is
    tight rather than aspirational — nothing in the tree trips it, and a NEW
    pack that names an unbacked successor is refused instead of having its guess
    rendered as "Recommended replacement:".

    Attestation is ``replacement_ref`` being in the resolution's own citation
    ids. Not "some evidence exists": the verdict's rule attests the
    DEPRECATION, which is a different claim from the successor, and accepting it
    here would let one citation launder two assertions.
    """
    successor = str(getattr(winner, "superseded_by", "") or "") if winner is not None else ""
    ref = str(getattr(winner, "replacement_ref", "") or "") if winner is not None else ""
    if not successor:
        return {"claimed": False, "attested": True, "successor": "", "ref": ref}
    available = {str(a) for a in (allowed or ())}
    return {
        "claimed": True,
        "attested": bool(ref) and ref in available,
        "successor": successor,
        "ref": ref,
    }


# ---------------------------------------------------------------------------
# Persistence — one registry row per resolution
# ---------------------------------------------------------------------------
def citation_digest(entity: str, verdict: str, citations) -> str:
    """A deterministic sha256 over the resolution's IDENTITY and evidence set.

    Recomputable from the returned ``CortexResolution`` alone, which is what
    makes the registry row checkable rather than merely present: a reader
    re-runs this over the citations they were handed and compares.

    Deterministic on every axis — sorted, no clock, no dict order, no uuid — so
    the same entity resolved twice against the same evidence produces the same
    hash and a CHANGED hash means the evidence changed. A citation's identity
    (id / table / type) is hashed and its snippet is not: a snippet is display
    text, and folding it in would make an unchanged evidence set hash
    differently after a rewording.
    """
    payload = {
        "entity": str(entity or ""),
        "verdict": str(verdict or ""),
        "citations": sorted(_citation_identity(c) for c in (citations or ())),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def resolution_summary(entity: str, verdict: str, citations, gaps, conflicts) -> str:
    """The registry row's ``source_doc`` — human-readable, greppable, no JSON.

    Names the operation so ``source_doc LIKE 'cortex.resolve%'`` finds every
    resolution, and carries the counts that make an empty evidence set visible
    in the row itself rather than only inside the digest.
    """
    return (
        f"cortex.resolve: {entity} = {verdict} "
        f"({len(citations or ())} citation(s), {len(gaps or ())} gap(s), "
        f"{len(conflicts or ())} conflict(s))"
    )


def register_resolution(result, ctx: Optional[CortexContext] = None) -> dict:
    """Persist ONE ``source_citation_registry`` row for ``result``. Never raises.

    Stamps the returned registry id onto every citation's ``provenance_id`` on
    success, so the resolution a caller holds can be joined back to the row that
    attests it. Records the outcome on ``result.governance`` under the
    ``provenance`` gate name, using ``governance``'s own outcome vocabulary.

    Returns the record dict also left on ``result.metadata["provenance"]``:
    ``{status, registry_id, digest, citation_count, citations_stamped, detail}``.
    """
    citations = list(getattr(result, "citations", None) or ())
    entity = str(getattr(result, "entity", "") or "")
    verdict = str(getattr(result, "verdict", "") or "")
    gaps = list(getattr(result, "gaps", None) or ())
    conflicts = list(getattr(result, "conflicts", None) or ())
    context = ctx if isinstance(ctx, CortexContext) else CortexContext()

    digest = citation_digest(entity, verdict, citations)
    record = {
        "status": STATUS_UNAVAILABLE,
        "registry_id": "",
        "digest": digest,
        "citation_type": CITATION_TYPE,
        "source_table": SOURCE_TABLE,
        # Deterministic, so re-resolving the same entity against the same
        # evidence names the same record rather than minting a fresh id nothing
        # can correlate.
        "source_record_id": f"cres-{digest[:16]}",
        "citation_count": len(citations),
        "citations_stamped": 0,
        "detail": "",
    }
    try:
        registry_id = _register_citation(
            citation_type=CITATION_TYPE,
            source_table=SOURCE_TABLE,
            source_record_id=record["source_record_id"],
            source_doc=resolution_summary(entity, verdict, citations, gaps, conflicts),
            source_hash=digest,
            classification=context.classification or "CUI",
            project_id=context.tenant_id or None,
            # trust_score is left at its default. A resolution HAS no measured
            # trust score, and writing `1.0 if grounded else 0.0` would put a
            # declared prior in a column readers take for a measurement — the
            # distinction args/entity_currency.yaml exists to keep.
        )
    except ValueError as exc:
        # The citation_type is not in CITATION_TYPES. A programming error, not a
        # degradation, and the two must not look alike (cxo-trust-01).
        record["status"] = STATUS_MISCONFIGURED
        record["detail"] = str(exc)
        logger.error(
            "cortex.resolve provenance MISCONFIGURED (not a transient failure): %s",
            exc,
        )
    except Exception as exc:  # noqa: BLE001 — provenance never breaks a resolution
        record["detail"] = str(exc)
        logger.warning("cortex.resolve provenance write failed: %s", exc)
    else:
        if registry_id:
            record["status"] = STATUS_WRITTEN
            record["registry_id"] = registry_id
            for citation in citations:
                if isinstance(citation, Citation) or hasattr(citation, "provenance_id"):
                    citation.provenance_id = registry_id
                    record["citations_stamped"] += 1
        else:
            # register_citation swallows database errors and returns "". An
            # empty id is a FAILED write, and reporting it as written is how a
            # missing table becomes invisible.
            record["detail"] = "registry insert returned no id"

    governance = getattr(result, "governance", None)
    if governance is not None:
        if GATE_PROVENANCE not in governance.gates_run:
            governance.gates_run.append(GATE_PROVENANCE)
        governance.outcomes[GATE_PROVENANCE] = STATUS_OUTCOME[record["status"]]
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, dict):
        metadata["provenance"] = record
    return record
