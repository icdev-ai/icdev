# CUI // SP-CTI
"""Semantic entity resolution ACROSS Cortex backends (cef-rsv-02).

    resolve_entities(hits, assessments=..., backend_errors=...) -> dict

Cortex already fuses ranked results across backends by weighted RRF, and
``resolve`` already derives a deterministic verdict from the domain packs. What
neither of them does is COMPARE what two different sources actually SAID about
the same real-world entity. So a RAG chunk asserting "TLS 1.1 remains approved"
and an ``entity_currency`` row asserting ``deprecated`` both landed in one
result set, were ranked against each other by relevance, and the contradiction
between them was invisible — nothing in the platform could notice it, because
nothing in the platform ever put the two claims side by side.

This module is that comparison, and only that comparison.

WHAT IT DOES NOT DO
-------------------
It does not retrieve, it does not govern, it does not decide the verdict, and
above all **it does not pick a winner**. Given two incompatible claims it emits
both, each with its own provenance, and stops. There is no ``winner`` field, no
``resolved_value``, no confidence average and no authority tie-break anywhere in
:class:`~tools.cortex.schemas.EntityConflict` — a currency disagreement is a
finding a human acts on, and a detector that resolved it would delete the
finding and report the survivor as fact. The resolution's verdict continues to
come from ``DomainPack.evaluate()`` and from nothing else; a conflict is
reported ALONGSIDE it, never instead of it.

THREE THINGS IT REFUSES TO BLUR
-------------------------------
1. **The same source seen twice is not two sources agreeing.** A document
   retrieved by both ``rag`` and ``dic`` is ONE claim, recorded once with both
   backend names on ``EntityClaim.backends``. Identity is
   ``search_service.fusion_ident`` — the SAME predicate RRF fusion uses, not a
   parallel one, so the two can never disagree about what "the same source"
   means.

2. **An entity nobody answered for is a GAP, and a gap is not silence.** Per the
   decision taken for this card, unknown is a VISIBLE FINDING: an entity in the
   resolved set that drew no claim is reported, with a reason, so that "we
   checked and nothing knows" stops looking identical to "we found it is
   current". ``no_evidence`` (nothing mentioned it at all) and ``no_claim``
   (documents mention it and none states its currency) are kept apart, because
   the first is an ingestion problem and the second is a corpus-content one.

3. **A backend that DIED does not produce a gap.** It produces a
   ``backend_error``, and the entity it would have answered for is listed under
   ``unresolved`` with reason ``backends_failed``. Emitting a gap there is
   precisely how an outage reaches a reader as a statement about the data —
   this repository's most-repeated defect, and the one ``BackendResults.errors``
   was introduced to stop. When retrieval only PARTIALLY failed the gap is real
   (something did answer, and it did not cover this entity), so a gap is emitted
   and the failures are carried on the gap's own ``backends_failed`` field
   rather than smuggled into its reasons.

Note on scope: ``resolver._gaps`` answers a different question — "why is the
SUBJECT's verdict unknown" — and keeps its own reason vocabulary including
``backends_failed``. This module answers "did anything answer for this entity",
which is why the same word means something different here and the two are not
merged.

THE THREE CLAIM LANES
---------------------
``structured``    typed fields a backend handed over (the ``currency`` backend's
                  ``metadata["verdict"]`` / ``superseded_by`` / ``eol_date``, off
                  an ``entity_currency`` row, plus each disagreeing source the
                  store already carried under ``others``).
``pack_evaluate`` a registered ``DomainPack``'s deterministic assessment. Two
                  packs disagreeing is a conflict too — ``resolver`` reduces
                  them to one winner by ``_VERDICT_RANK``, and until now that
                  reduction was silent.
``text_pattern``  a declared, entity-ANCHORED pattern over retrieved prose. This
                  lane is what lets a RAG chunk disagree with the catalog at
                  all; nothing else can express a claim a document merely
                  states. It is deliberately narrow (see
                  :data:`TEXT_CLAIM_RULES`), it never invents an entity — it
                  only ever matches entities already in the resolved set — and
                  every claim it makes is stamped ``extraction="text_pattern"``
                  on the claim AND on every conflict side, so a reader can
                  discount it without the detector having quietly discounted it
                  first. Disable the lane with ``resolve.text_claims: false`` in
                  args/cortex_config.yaml.
"""
from __future__ import annotations

import importlib
import itertools
import re
from typing import Iterable, Optional

from tools.logging.icdev_logger import get_logger

from .schemas import EntityClaim, EntityConflict
from .search_service import fusion_ident

logger = get_logger("icdev.cortex.entity_resolution")

#: Namespace root this module was loaded under, so a sibling package is reached
#: in whichever tree is live. Same derivation as ``search_service._NS``.
_NS = __name__.rsplit(".cortex.", 1)[0]

# ---------------------------------------------------------------------------
# Gap vocabulary — the four reasons, never merged
# ---------------------------------------------------------------------------
#: No registered domain pack recognises this KIND of entity.
GAP_NO_PACK = "no_pack_matched"
#: Nothing in any corpus mentioned the entity.
GAP_NO_EVIDENCE = "no_evidence"
#: Retrieval broke. Used as an ``unresolved`` reason, NEVER as a gap reason.
GAP_BACKENDS_FAILED = "backends_failed"
#: A registered pack raised.
GAP_PACKS_FAILED = "packs_failed"
#: Sources mention the entity and none of them states its currency. Distinct
#: from ``no_evidence`` on purpose: the corpus HAS the entity, so the fix is
#: content (or a source that carries a verdict), not ingestion.
GAP_NO_CLAIM = "no_claim"

# ---------------------------------------------------------------------------
# Status normalization
# ---------------------------------------------------------------------------
#: Any source's currency word -> one of ``schemas.RESOLVE_VERDICTS``.
#:
#: WIDER than ``resolver.PACK_VERDICT_MAP`` on purpose, and not a copy of it.
#: That map translates ONE vocabulary (docmod's six ``CURRENCY_VERDICTS``) into
#: the verdict a caller branches on. This one has to accept whatever an EOL
#: feed, a curated catalog row or an English sentence spelled it as, none of
#: which a pack ever emits. ``tests/cortex/test_entity_resolution.py`` asserts
#: every ``PACK_VERDICT_MAP`` key is present here and agrees, so the two cannot
#: drift into contradicting each other about a word they both know.
#:
#: ``divergent`` -> ``unknown``, matching ``PACK_VERDICT_MAP``: it means the
#: fielded estate disagrees with the catalog about DEPLOYMENT, which is not a
#: claim that the entity is stale.
STATUS_ALIASES = {
    "current": "current",
    "active": "current",
    "supported": "current",
    "approved": "current",
    "deprecated": "deprecated",
    "eol": "deprecated",
    "end-of-life": "deprecated",
    "end_of_life": "deprecated",
    "retired": "deprecated",
    "obsolete": "deprecated",
    "unsupported": "deprecated",
    "prohibited": "deprecated",
    "superseded": "superseded",
    "replaced": "superseded",
    "divergent": "unknown",
    "unknown": "unknown",
}


def normalize_status(raw: object) -> str:
    """One source's currency word -> a comparable status. Pure, total.

    An unrecognised word is ``unknown`` with a warning — never guessed toward a
    finding, and never toward ``current`` either. A status this function cannot
    read must not be able to manufacture a conflict OR suppress one.
    """
    word = str(raw or "").strip().casefold().replace(" ", "_")
    if not word:
        return "unknown"
    mapped = STATUS_ALIASES.get(word) or STATUS_ALIASES.get(word.replace("_", "-"))
    if mapped is None:
        logger.warning(
            "cortex.entity_resolution: status %r is in no STATUS_ALIASES entry — "
            "reported as 'unknown' rather than guessed",
            raw,
        )
        return "unknown"
    return mapped


def statuses_conflict(a: str, b: str) -> bool:
    """Are two normalized statuses INCOMPATIBLE? Pure, symmetric.

    Two exclusions, each deliberate:

    * ``unknown`` against anything is not a disagreement. One source having no
      opinion is an absence, and an absence cannot contradict an assertion.
    * ``deprecated`` against ``superseded`` is not a disagreement. Superseded IS
      deprecated plus a named successor — the same finding at two levels of
      detail, which ``resolver.map_pack_verdict`` already treats as one thing.
      Reporting it would bury the real conflicts under a permanent false one.
    """
    if a == b or "unknown" in (a, b):
        return False
    return {a, b} != {"deprecated", "superseded"}


# ---------------------------------------------------------------------------
# Entity identity
# ---------------------------------------------------------------------------
_NORMALIZE_KEY = None


def _normalize_key(value: object) -> str:
    """``tools/currency/entity_currency.normalize_key`` — the store's own key.

    Reused rather than reimplemented: ``entity_currency`` already documents why
    it does nothing domain-specific ("two sources spelling the same entity
    differently stay different keys, which is honest — silently merging them
    would invent an agreement"), and a second normalizer here would decide that
    question differently the first time somebody tuned one of them.

    Resolved through both namespace roots the way ``search_service`` does, since
    ``tools/currency/`` has no ``icdev/tools/`` mirror.
    """
    global _NORMALIZE_KEY
    if _NORMALIZE_KEY is None:
        try:
            module = importlib.import_module(f"{_NS}.currency.entity_currency")
        except ModuleNotFoundError:
            module = importlib.import_module("tools.currency.entity_currency")
        _NORMALIZE_KEY = module.normalize_key
    return _NORMALIZE_KEY(value)


def entity_ident(label: object, version: object = "") -> str:
    """The join key two backends must agree on to be talking about one entity.

    Label plus version, because "TLS 1.1" and "TLS 1.2" are different entities
    and a version-blind key would report every release of a product
    contradicting every other one.

    Entity TYPE is carried on the claim but is deliberately NOT part of the key.
    Only the ``currency`` backend and the packs supply a type at all; requiring
    one would mean a RAG chunk could never join to a catalog row, which is the
    single case this module exists to make visible.
    """
    key = _normalize_key(label)
    ver = _normalize_key(version)
    return f"{key}@{ver}" if ver and ver not in key.split() else key


# ---------------------------------------------------------------------------
# The text lane — declared, entity-anchored, narrow
# ---------------------------------------------------------------------------
#: ``(template, status, successor_group)``. ``{e}`` is replaced with the
#: re-escaped entity label, so every rule is ANCHORED to the entity being
#: claimed about and reads its grammatical position.
#:
#: The anchoring is the whole safety property. "TLS 1.2 supersedes TLS 1.1" must
#: produce ``superseded`` for TLS 1.1 and ``current``-ish for nothing at all —
#: an unanchored keyword scan would read the sentence as evidence that TLS 1.2
#: is superseded, and then report a fabricated conflict against the catalog.
#: Hence two directional rules rather than one bag of words.
#:
#: ``{s}`` is the successor slot. It admits a dot only when a non-space follows
#: it, so "TLS 1.3" survives and the full stop ending the sentence does not —
#: an ordinary ``[^.]`` class truncates every dotted version number to "TLS 1",
#: which then reads as a DIFFERENT successor from the catalog's and fabricates a
#: ``superseded_by`` conflict out of two sources that agreed.
TEXT_CLAIM_RULES = (
    (
        r"{e}\s+(?:is|has been|was|will be)\s+(?:now\s+)?"
        r"(?:superseded|replaced|supplanted)\s+by\s+(?P<succ>{s})",
        "superseded",
        "succ",
    ),
    (
        r"(?P<succ>[A-Za-z0-9]{s}?)\s+(?:supersedes|replaces|supplants)\s+{e}\b",
        "superseded",
        "succ",
    ),
    (
        r"{e}\s+(?:is|has been|was|remains)\s+(?:now\s+)?"
        r"(?:deprecated|obsolete|retired|prohibited|disallowed|unsupported|"
        r"no longer (?:recommended|supported|approved|permitted))",
        "deprecated",
        None,
    ),
    (
        r"(?:deprecates|deprecated|prohibits|prohibited|disallows|disallowed)\s+"
        r"(?:the use of\s+)?{e}\b",
        "deprecated",
        None,
    ),
    (
        r"{e}\s+(?:is|remains|stays)\s+(?:still\s+)?(?:the\s+)?"
        r"(?:current|approved|recommended|supported|mandatory)",
        "current",
        None,
    ),
)

#: End-of-life / end-of-support DATE, anchored the same way. Kept separate from
#: the status rules because a date is a different claim: two sources can agree
#: an entity is deprecated and still disagree about WHEN, and that disagreement
#: is the one a migration schedule is built on.
TEXT_EOL_RULE = (
    r"{e}[^.;\n]{0,80}?(?:end[-\s]of[-\s](?:life|support)|\bEOL\b|\bEOS\b)"
    r"[^.;\n]{0,30}?(?P<date>\d{4}-\d{2}-\d{2})"
)

#: The successor slot substituted for ``{s}``: any run of characters that is not
#: a clause break, admitting an interior dot ("TLS 1.3") but not a terminal one.
_SUCCESSOR_SLOT = r"(?:[^.;:,\n]|\.(?=\S)){1,80}"

#: Longest prose fragment a successor name may be. A runaway capture would put a
#: whole sentence in ``superseded_by`` and make two paraphrases of one fact look
#: like two different successors.
_SUCCESSOR_CHARS = 80

_SNIPPET_CHARS = 240

#: Leading noise a successor capture picks up from natural prose.
_SUCCESSOR_LEADING = re.compile(
    r"^(?:the|a|an|its|their|to|with|by)\s+", re.IGNORECASE
)

#: Where a successor NAME stops and the sentence resumes. Without this the slot
#: happily swallows "TLS 1.3 per RFC 8996", and the same successor cited by two
#: documents with two different justifications reads as two rival successors.
_SUCCESSOR_TAIL = re.compile(
    r"\s+(?:per|in|as|for|which|and|or|because|according|see|effective|since|"
    r"by|from|under|with|on|at|starting|beginning|due|pursuant|instead)\b.*$",
    re.IGNORECASE,
)


def _clean_successor(raw: str) -> str:
    """A captured successor name -> a comparable label."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip(" \t\"'()[]")
    text = _SUCCESSOR_LEADING.sub("", text)
    text = _SUCCESSOR_TAIL.sub("", text).strip(" \t\"'()[]")
    return text[:_SUCCESSOR_CHARS].strip()


def text_claims(content: str, entity_label: str) -> list[dict]:
    """Declared patterns over ONE hit's prose for ONE known entity.

    Returns raw claim fragments (``status`` / ``superseded_by`` / ``eol_date``),
    never :class:`EntityClaim` objects — provenance is the caller's to attach,
    so this stays a pure text function that a test can drive directly.
    """
    text = str(content or "")
    label = str(entity_label or "").strip()
    if not text or not label:
        return []
    slot = re.escape(label)
    found: list[dict] = []
    for template, status, group in TEXT_CLAIM_RULES:
        pattern = template.replace("{e}", slot).replace("{s}", _SUCCESSOR_SLOT)
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        claim = {"status": status, "raw_status": status,
                 "snippet": match.group(0)[:_SNIPPET_CHARS]}
        if group:
            claim["superseded_by"] = _clean_successor(match.group(group))
            if not claim["superseded_by"]:
                # "superseded by" with nothing readable after it is a deprecation
                # statement, not a supersession one. Naming no successor beats
                # naming a fragment.
                claim["status"] = "deprecated"
        found.append(claim)
    eol = re.search(TEXT_EOL_RULE.replace("{e}", slot), text, re.IGNORECASE)
    if eol is not None:
        found.append({"status": "unknown", "raw_status": "",
                      "eol_date": eol.group("date"),
                      "snippet": eol.group(0)[:_SNIPPET_CHARS]})
    return found


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------
def _meta(hit) -> dict:
    value = getattr(hit, "metadata", None)
    return value if isinstance(value, dict) else {}


def _citation_of(hit):
    return getattr(hit, "citation", None)


def _mentions(hit, label: str) -> bool:
    """Does this hit talk about the entity at all? Substring, case-folded.

    Used only to tell ``no_claim`` from ``no_evidence``. Deliberately looser
    than the claim patterns: "the corpus mentions it" is a weaker statement than
    "the corpus asserts something about it", and conflating the two would report
    an ingestion gap for a document that plainly names the thing.
    """
    needle = str(label or "").strip().casefold()
    if not needle:
        return False
    haystack = str(getattr(hit, "content", "") or "").casefold()
    citation = _citation_of(hit)
    if citation is not None:
        haystack += " " + str(getattr(citation, "title", "") or "").casefold()
    meta = _meta(hit)
    for field_name in ("entity_label", "entity_key", "product"):
        haystack += " " + str(meta.get(field_name) or "").casefold()
    return needle in haystack


def _structured_claims(hit) -> list[EntityClaim]:
    """Typed currency fields a backend handed over -> claims.

    Reads ``metadata`` keys the ``currency`` backend already publishes
    (cef-bck-01) rather than a new contract, and turns each entry the store
    carried under ``others`` into a first-class claim of its own. The store
    already preserved that disagreement (cef-fnd-04) and Cortex already carried
    it as a boolean flag nothing acted on; promoting the losing sources to
    claims is what makes them comparable against the OTHER backends too.
    """
    meta = _meta(hit)
    label = str(meta.get("entity_label") or "").strip()
    citation = _citation_of(hit)
    if not label and citation is not None:
        label = str(getattr(citation, "title", "") or "").strip()
    if not label:
        label = str(meta.get("entity_key") or "").strip()
    has_claim = any(
        meta.get(k) for k in ("verdict", "superseded_by", "eol_date", "eos_date")
    )
    if not label or not has_claim:
        return []

    version = str(meta.get("entity_version") or "")
    key = entity_ident(label, version)
    backend = str(getattr(hit, "backend", "") or "")
    strategy = str(getattr(hit, "strategy", "") or "")
    source_id = str(getattr(citation, "source_id", "") or "") if citation else ""
    source_table = str(getattr(citation, "source_table", "") or "") if citation else ""

    def _confidence(value) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    claims = [EntityClaim(
        entity_key=key,
        entity_label=label,
        entity_type=str(meta.get("entity_type") or ""),
        entity_version=version,
        status=normalize_status(meta.get("verdict")),
        raw_status=str(meta.get("verdict") or ""),
        superseded_by=str(meta.get("superseded_by") or ""),
        eol_date=str(meta.get("eol_date") or "")[:10],
        eos_date=str(meta.get("eos_date") or "")[:10],
        backend=backend,
        backends=[backend] if backend else [],
        strategy=strategy,
        source=str(meta.get("source") or ""),
        source_id=source_id,
        source_table=source_table,
        authoritative=bool(meta.get("authoritative")),
        confidence=_confidence(meta.get("confidence")),
        as_of=str(meta.get("as_of") or "")[:10],
        extraction="structured",
        snippet=str(getattr(hit, "content", "") or "")[:_SNIPPET_CHARS],
    )]

    for other in meta.get("others") or []:
        if not isinstance(other, dict) or not other.get("verdict"):
            continue
        claims.append(EntityClaim(
            entity_key=key,
            entity_label=label,
            entity_type=str(meta.get("entity_type") or ""),
            entity_version=version,
            status=normalize_status(other.get("verdict")),
            raw_status=str(other.get("verdict") or ""),
            superseded_by=str(other.get("superseded_by") or ""),
            eol_date=str(other.get("eol_date") or "")[:10],
            backend=backend,
            backends=[backend] if backend else [],
            strategy=strategy,
            source=str(other.get("source") or ""),
            # The store's `others` carry no record id — reporting the WINNER's
            # id here would attribute a losing source's claim to the winning
            # row. The table is named; the row is honestly absent.
            source_id="",
            source_table=source_table,
            authoritative=bool(other.get("authoritative")),
            confidence=_confidence(other.get("confidence")),
            as_of=str(other.get("as_of") or "")[:10],
            extraction="structured",
            snippet=f"{other.get('source')}={other.get('verdict')}",
        ))
    return claims


def _text_claims_for_hit(hit, entities: dict) -> list[EntityClaim]:
    """Declared patterns over one hit, anchored to the KNOWN entity set."""
    content = str(getattr(hit, "content", "") or "")
    if not content:
        return []
    citation = _citation_of(hit)
    backend = str(getattr(hit, "backend", "") or "")
    out: list[EntityClaim] = []
    for key, label in entities.items():
        for fragment in text_claims(content, label):
            out.append(EntityClaim(
                entity_key=key,
                entity_label=label,
                status=fragment.get("status", "unknown"),
                raw_status=fragment.get("raw_status", ""),
                superseded_by=fragment.get("superseded_by", ""),
                eol_date=fragment.get("eol_date", ""),
                backend=backend,
                backends=[backend] if backend else [],
                strategy=str(getattr(hit, "strategy", "") or ""),
                source=str(getattr(citation, "source_type", "") or "") if citation else "",
                source_id=str(getattr(citation, "source_id", "") or "") if citation else "",
                source_table=str(getattr(citation, "source_table", "") or "") if citation else "",
                extraction="text_pattern",
                snippet=fragment.get("snippet", ""),
            ))
    return out


def claims_from_assessments(assessments: Iterable) -> list[EntityClaim]:
    """``EntityAssessment`` -> claim. One per pack that assessed the entity.

    Two packs disagreeing about one entity is a genuine cross-source conflict
    and was previously invisible: ``resolver.reduce_assessments`` picks the
    highest-ranked verdict and the loser is reported only as
    ``pack_id=verdict`` in the prose. Promoting each assessment to a claim is
    what makes that reduction auditable.
    """
    out: list[EntityClaim] = []
    for assessment in assessments or []:
        label = str(getattr(assessment, "entity", "") or "").strip()
        if not label:
            continue
        pack_id = str(getattr(assessment, "pack_id", "") or "")
        evidence = [
            e for e in (getattr(assessment, "evidence", None) or []) if isinstance(e, dict)
        ]
        first = evidence[0] if evidence else {}
        out.append(EntityClaim(
            entity_key=entity_ident(label),
            entity_label=label,
            entity_type=str(getattr(assessment, "entity_type", "") or ""),
            status=normalize_status(getattr(assessment, "verdict", "")),
            raw_status=str(getattr(assessment, "pack_verdict", "") or ""),
            superseded_by=str(getattr(assessment, "superseded_by", "") or ""),
            backend=f"pack:{pack_id}" if pack_id else "pack",
            backends=[f"pack:{pack_id}"] if pack_id else [],
            strategy="evaluate",
            source=str(getattr(assessment, "replacement_source", "") or "") or pack_id,
            source_id=str(first.get("source") or ""),
            source_table=pack_id,
            # A pack's assessment is deterministic evidence by construction —
            # base_pack TRUST rule 1 — which is a stronger statement than a
            # source merely being declared authoritative, and is recorded as
            # such so a reader weighing two sides can see it.
            authoritative=True,
            confidence=float(getattr(assessment, "confidence", 0.0) or 0.0),
            extraction="pack_evaluate",
            snippet=str(getattr(assessment, "rationale", "") or "")[:_SNIPPET_CHARS],
        ))
    return out


def dedupe_claims(claims: Iterable[EntityClaim]) -> list[EntityClaim]:
    """Collapse the SAME source seen through two backends into ONE claim.

    Identity is ``(entity_key, extraction, source identity, claimed values)``,
    where source identity prefers ``source_id`` and falls back to the asserting
    ``source`` name — the same "prefer the citation id, fall back to content"
    shape :func:`search_service.fusion_ident` uses on a hit. Surviving backends
    are unioned onto ``backends``, so one document retrieved twice reports both
    and still counts once.

    Order is preserved so the output is deterministic for a deterministic input.
    """
    merged: dict = {}
    order: list = []
    for claim in claims:
        ident = claim.source_id or claim.source or claim.snippet
        key = (claim.entity_key, claim.extraction, ident,
               claim.status, claim.superseded_by, claim.eol_date)
        existing = merged.get(key)
        if existing is None:
            merged[key] = claim
            order.append(key)
            continue
        for backend in claim.backends or ([claim.backend] if claim.backend else []):
            if backend and backend not in existing.backends:
                existing.backends.append(backend)
    return [merged[k] for k in order]


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------
def _claimed_value(claim: EntityClaim, kind: str) -> str:
    if kind == "status":
        return claim.status if claim.status != "unknown" else ""
    if kind == "superseded_by":
        return _normalize_key(claim.superseded_by)
    if kind == "eol_date":
        return str(claim.eol_date or "")[:10]
    return ""


def _conflict_for(kind: str, claims: list[EntityClaim]) -> Optional[EntityConflict]:
    """One ``(entity, kind)`` -> a conflict carrying EVERY side, or None."""
    buckets: dict = {}
    for claim in claims:
        value = _claimed_value(claim, kind)
        if value:
            buckets.setdefault(value, []).append(claim)
    if len(buckets) < 2:
        return None
    values = sorted(buckets)
    if kind == "status" and not any(
        statuses_conflict(a, b) for a, b in itertools.combinations(values, 2)
    ):
        return None
    sides = [c for value in values for c in buckets[value]]
    backends = sorted({b for c in sides for b in (c.backends or [c.backend]) if b})
    return EntityConflict(
        entity_key=sides[0].entity_key,
        entity_label=sides[0].entity_label,
        kind=kind,
        values=values,
        sides=sides,
        backends=backends,
        cross_backend=len(backends) > 1,
    )


def find_conflicts(claims: Iterable[EntityClaim]) -> list[EntityConflict]:
    """Every incompatible claim pair, grouped one conflict per (entity, kind).

    Deterministic: entities in key order, kinds in a fixed order, sides ordered
    by claimed value. Nothing here consults authority, confidence or recency —
    those travel ON the sides for a human to weigh, and weighing them here would
    be the winner-picking this module refuses to do.
    """
    by_entity: dict = {}
    for claim in claims:
        by_entity.setdefault(claim.entity_key, []).append(claim)
    conflicts: list[EntityConflict] = []
    for key in sorted(by_entity):
        for kind in ("status", "superseded_by", "eol_date"):
            conflict = _conflict_for(kind, by_entity[key])
            if conflict is not None:
                conflicts.append(conflict)
    return conflicts


# ---------------------------------------------------------------------------
# Gaps and outages
# ---------------------------------------------------------------------------
def _answered(claims: list[EntityClaim]) -> bool:
    """Did anything actually ANSWER for this entity?

    A claim whose status is ``unknown`` and which names no successor and no EOL
    date asserts nothing — counting it as an answer is how "answered: current"
    and "nobody knows" became indistinguishable in the first place.
    """
    return any(
        c.status != "unknown" or c.superseded_by or c.eol_date for c in claims
    )


def _failed_backends(backend_errors: Iterable[dict], backends: Iterable[str]) -> set:
    """Which CONSULTED backends died.

    Pack errors (``backend="pack:x"``) are excluded — a pack is not a retrieval
    rung, and letting one count toward "retrieval died" would suppress a gap the
    corpora genuinely have.
    """
    consulted = {str(b) for b in backends or ()}
    failed = set()
    for error in backend_errors or ():
        name = str((error or {}).get("backend") or "")
        if name in consulted:
            failed.add(name)
        elif name == "search":
            # The whole fan-out raised before any backend ran.
            failed |= consulted or {"search"}
    return failed


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------
def text_claims_enabled(config: Optional[dict]) -> bool:
    """``resolve.text_claims`` — default True. Absent config enables the lane."""
    declared = ((config or {}).get("resolve") or {}).get("text_claims")
    return True if declared is None else bool(declared)


def resolve_entities(
    hits: Iterable,
    assessments: Iterable = (),
    backend_errors: Iterable[dict] = (),
    entities: Iterable[str] = (),
    backends: Iterable[str] = (),
    config: Optional[dict] = None,
) -> dict:
    """Resolve hits across backends onto entities, then compare their claims.

    Args:
        hits: the fused ``CortexSearchResult`` set from ``search_service``.
        assessments: ``EntityAssessment`` objects from the domain packs.
        backend_errors: ``BackendResults.errors`` — backends that DIED.
        entities: entity labels the caller already knows about (the resolution
            subject, the pack candidates). The text lane will only ever claim
            about these; it never invents an entity out of prose.
        backends: the rungs that were consulted, so "every one of them failed"
            is answerable.
        config: ``args/cortex_config.yaml``, read for ``resolve.text_claims``.

    Returns:
        ``{"entities", "claims", "conflicts", "gaps", "unresolved",
        "backends_consulted", "backends_failed", "text_claims"}`` — plain
        JSON-safe dicts, since the whole report travels on a
        ``CortexResolution`` that is serialized to the REST/MCP surface.
    """
    hits = list(hits or [])
    backends = [str(b) for b in (backends or ())]
    backend_errors = list(backend_errors or ())

    known: dict = {}
    for label in entities or ():
        text = str(label or "").strip()
        if text:
            known.setdefault(entity_ident(text), text)

    # Same source, two backends -> one hit, before any claim is read off it.
    seen_sources: set = set()
    unique_hits = []
    for hit in hits:
        ident = fusion_ident(hit)
        if ident and ident in seen_sources:
            continue
        if ident:
            seen_sources.add(ident)
        unique_hits.append(hit)

    claims: list[EntityClaim] = list(claims_from_assessments(assessments))
    structured: list[EntityClaim] = []
    for hit in unique_hits:
        structured.extend(_structured_claims(hit))
    claims.extend(structured)

    # A structured claim NAMES its entity, so the store's own labels join the
    # known set — and the text lane may then anchor to them. Discovered here
    # rather than assumed by the caller: a currency row for "TLS 1.1" makes that
    # entity resolvable even when the caller only asked about a document.
    for claim in structured:
        known.setdefault(claim.entity_key, claim.entity_label)

    use_text = text_claims_enabled(config)
    if use_text and known:
        for hit in unique_hits:
            claims.extend(_text_claims_for_hit(hit, known))

    claims = dedupe_claims(claims)
    for claim in claims:
        known.setdefault(claim.entity_key, claim.entity_label)

    by_entity: dict = {}
    for claim in claims:
        by_entity.setdefault(claim.entity_key, []).append(claim)

    failed = _failed_backends(backend_errors, backends)
    retrieval_died = bool(failed) and (not backends or failed >= set(backends))

    entity_rows: list[dict] = []
    gaps: list[dict] = []
    unresolved: list[dict] = []
    for key in sorted(known):
        label = known[key]
        entity_claims = by_entity.get(key, [])
        answered = _answered(entity_claims)
        entity_rows.append({
            "entity_key": key,
            "entity_label": label,
            "answered": answered,
            "claim_count": len(entity_claims),
            "backends": sorted({b for c in entity_claims
                                for b in (c.backends or [c.backend]) if b}),
            "statuses": sorted({c.status for c in entity_claims if c.status != "unknown"}),
        })
        if answered:
            continue
        if retrieval_died:
            # AC4. A dead fan-out is an OUTAGE, not a gap in the corpus. It is
            # reported here and in backend_errors; calling it a gap would let an
            # infrastructure failure read as a statement about the data.
            unresolved.append({
                "entity": label,
                "entity_key": key,
                "reason": GAP_BACKENDS_FAILED,
                "backends_consulted": list(backends),
                "backends_failed": sorted(failed),
            })
            continue
        mentioned = any(_mentions(hit, label) for hit in unique_hits)
        gaps.append({
            "entity": label,
            "entity_key": key,
            "reasons": [GAP_NO_CLAIM if mentioned else GAP_NO_EVIDENCE],
            "backends_consulted": list(backends),
            # Carried as a FIELD, never folded into `reasons`: a partial outage
            # is context for the gap, not the gap's cause.
            "backends_failed": sorted(failed),
        })

    conflicts = find_conflicts(claims)
    return {
        "entities": entity_rows,
        "claims": [c.to_dict() for c in claims],
        "conflicts": [c.to_dict() for c in conflicts],
        "gaps": gaps,
        "unresolved": unresolved,
        "backends_consulted": list(backends),
        "backends_failed": sorted(failed),
        "text_claims": use_text,
    }
