# CUI // SP-CTI
"""What ``cortex.resolve()`` found, in the shape the DocDrift page renders (cef-ui-01).

``/document-intelligence/docdrift`` lists 72 drift findings today and says four
things about each one: source, entity, severity, timestamp. It says nothing
about whether the entity it names is actually stale, what evidence says so, or
whether anything was able to answer at all — while ``cortex.resolve`` has been
producing exactly that, with a deterministic verdict, validated citations, and
a gap that names WHY when it cannot answer.

This module is the read model between the two. It calls the GOVERNED facade
(``tools.cortex.api.resolve``), persists the answer, and normalises it into one
dict per finding whose fields the template can render without deriving anything.

THE THREE AXES, AND WHY THEY ARE THREE
--------------------------------------
Everything in this module exists to keep these apart. Merging any two of them
is the defect the card was written for: "we checked and it is current" and
"nothing we have could answer" must stop looking identical, and a dead backend
must look like neither.

1. ``state`` — THE DETERMINISTIC VERDICT. Derived from ``verdict`` and
   ``verdict_source`` and from nothing else, so it carries ``base_pack`` TRUST
   rule 1 unchanged: a ``DomainPack.evaluate()`` produced it, no model did.
   ``current`` / ``deprecated`` / ``superseded`` / ``unknown`` are the four
   RESOLVE_VERDICTS; ``not_resolved`` and ``refused`` are the two states a
   resolution that never happened can be in, and neither renders as clean.

2. ``evidence_health`` — WHETHER THE EVIDENCE SWEEP WORKED. Derived from
   ``backend_errors`` and from nothing else. It is a SEPARATE field, not a
   qualifier on the verdict, because the live board proves the two move
   independently: measured 2026-08-18, ``TLS 1.1`` resolves ``superseded``
   (crypto_protocols, rule:crypto-tls-02) with FOUR of five backends timed out.
   The verdict is exactly as good as it looks — it came from a pack reading a
   rulebook, not from the rungs that died — and the sweep behind it is exactly
   as degraded as it looks. One field cannot say both.

3. ``advisory`` — AN OPINION, SUBORDINATE TO BOTH. The ``sme`` rung asks an ACE
   persona a question and an LLM answers it at query time. It is excluded from
   citations and from the verdict by ``resolver`` itself and it is excluded
   here too; this module only decides how to SHOW it. Its four states matter
   more than its content:

     ``not_consulted``  nobody asked. The shipped ``resolve.backends`` in
                        args/cortex_config.yaml deliberately omits ``sme``, so
                        ``metadata["advisory"]`` is structurally always empty
                        on a default deployment. Rendering that as "no SME
                        concerns" would be a fabrication.
     ``unavailable``    asked, and the rung ERRORED. This is what this
                        deployment returns today: ``ensure_sme`` needs one LLM
                        call to normalise the domain label and the
                        ``generative_intelligence`` module budget is spent
                        (420,375 of 400,000 monthly tokens, measured
                        2026-08-18). An outage, not an absence of opinion.
     ``no_opinion``     asked, ran, returned nothing.
     ``opinion``        asked, ran, answered.

   The first three all produce an empty opinion list and they are three
   different facts. ``search_sme``'s own docstring makes the same point about
   its half: "An empty opinion is not a neutral opinion."

UNKNOWN IS A FINDING, WITH A REASON
-----------------------------------
``resolver._gaps`` never emits a bare ``unknown``: it names
``no_pack_matched`` / ``no_evidence`` / ``backends_failed`` / ``packs_failed``,
because those are four different fixes. :func:`unknown_reasons` carries every
one through to the page with a human label attached, and :data:`GAP_LABELS` is
the only place they are worded. Flattening them to "unknown" here would undo
the distinction one layer below the one that drew it.

WHAT THIS MODULE DOES NOT DO
----------------------------
It forms no verdict, ranks nothing, and merges no two sources. Every value it
reports is read off the ``CortexResolution`` the governed facade returned; the
only computation is :func:`finding_state` and :func:`evidence_health`, both of
which are pure functions over fields that resolution already set. In
particular there is no path by which an advisory item can reach ``state``.

The module ships byte-identical in BOTH trees (``tools/`` and ``icdev/tools/``)
and they are separate module objects with separate caches — patch the copy the
caller actually imported.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: The rendered discriminator. The first four are ``schemas.RESOLVE_VERDICTS``;
#: the last two are the states a resolution that did not happen can be in.
#:
#: ``not_resolved`` exists because its absence is the bug. A finding nobody has
#: resolved must not render like a finding resolved as ``current`` — that is
#: the whole point of the card — so "we have not asked" is a value in this
#: vocabulary rather than a null the template has to guess about.
FINDING_STATES = (
    "current",
    "deprecated",
    "superseded",
    "unknown",
    "not_resolved",
    "refused",
)

#: States that mean "a pack reached a conclusion about this entity".
VERDICT_STATES = ("current", "deprecated", "superseded", "unknown")

#: States that are a FINDING a human should act on (as opposed to a clean bill
#: of health or an un-run check). ``unknown`` is in here deliberately: the card
#: decision was that unknowns are visible findings, not omissions.
ACTIONABLE_STATES = ("deprecated", "superseded", "unknown")

#: Did the evidence fan-out work? Independent of the verdict above.
#:
#: ``unmeasured`` is not "fine" — it is what a finding that was never resolved
#: reports, and it must not be rendered in the same colour as ``ok``.
EVIDENCE_HEALTH = ("ok", "degraded", "failed", "unmeasured")

#: The SME rung's four states. See the module docstring: the first three all
#: carry an empty opinion list and are three different facts.
ADVISORY_STATES = ("not_consulted", "unavailable", "no_opinion", "opinion")

#: ``resolver`` gap reasons -> the wording the page uses. The ONLY place these
#: are worded, so the page and any future consumer cannot describe the same
#: reason two ways.
GAP_LABELS = {
    "no_pack_matched": "No domain pack recognises this kind of entity",
    "no_evidence": "The corpora were searched and matched nothing",
    "backends_failed": "Retrieval broke — this is an outage, not an answer",
    "packs_failed": "A domain pack raised while evaluating",
    "no_claim": "Sources mentioned it but made no currency claim",
}

#: ``gap["citation_basis"]`` -> wording. An EMPTY citation list on a gap has
#: three causes (cef-rsv-03) and they are not one fact.
CITATION_BASIS_LABELS = {
    "mentioned_not_answered": "Sources mentioned the entity and did not answer for it",
    "no_retrieval_match": "Nothing in the corpora matched at all",
    "retrieval_failed": "Retrieval died before anything could be matched",
}

#: A citation whose ``source_type`` is this is a DomainPack's own verdict
#: rationale coming back through the fan-out. It is shown — it is the sentence
#: that justifies the verdict and a reader wants it — but it is LABELLED as
#: derived rather than presented as an independent corroborating source.
PACK_EVIDENCE_TYPE = "pack_evidence"

#: Config filename. Flat ``args/dic_*.yaml`` like the rest of the DIC canvas.
CONFIG_FILENAME = "dic_docdrift_config.yaml"

#: Fallbacks used when the config file is absent or unreadable. `enabled` is
#: TRUE here, unlike the sibling seams — see the comment in the shipped YAML.
DEFAULT_TOP_K = 5
DEFAULT_MAX_RESOLVES = 8
DEFAULT_STALE_HOURS = 24
DEFAULT_QUESTION = "Is {entity} still current, and what is the evidence?"

_CONFIG_CACHE: dict = {}

#: Process-wide counters, REPORTED by :func:`run_stats`. A bound that is not
#: reported is a silent cap.
_STATS: dict = {"resolutions": 0, "refusals": 0, "capped": 0, "advisory_asks": 0}


def _default_config_path() -> Path:
    """``args/dic_docdrift_config.yaml``, found by walking up from this file.

    A hardcoded ``parents[N]`` cannot be right in both trees: this module ships
    byte-identical at ``tools/document_intelligence/`` and
    ``icdev/tools/document_intelligence/``, which are different depths, and the
    two copies must stay identical or the mirror-drift gate fires on a
    difference that is correct. Walking up finds the one ``args/`` directory
    from either depth.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "args" / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return here.parents[2] / "args" / CONFIG_FILENAME


CONFIG_PATH = _default_config_path()


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def load_config(path=None) -> dict:
    """``args/dic_docdrift_config.yaml``, memoised per path.

    An unreadable or absent file is ``{}``. Note what that means HERE, which is
    the opposite of the sibling seams: ``cortex_enabled`` defaults TRUE, so a
    missing config leaves the panel live and every other value on its default.
    A config this module cannot parse must not silently blank the page.
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
    except Exception as exc:  # noqa: BLE001 — an unreadable config uses defaults
        logger.debug("docdrift evidence: config unavailable (%s) — defaults", exc)
    _CONFIG_CACHE[key] = data
    return data


def _block(name: str, config: dict | None = None) -> dict:
    if config is None:
        config = load_config()
    value = (config or {}).get(name)
    return dict(value) if isinstance(value, dict) else {}


def cortex_enabled(config: dict | None = None) -> bool:
    """Is the currency panel live? DEFAULT TRUE — see args/dic_docdrift_config.yaml.

    Off does not hide the panel. It makes every finding report
    ``not_resolved``, which the page states as "not checked" — the toggle
    changes whether we ask, never whether the reader can tell that we did not.
    """
    return bool(_block("cortex", config).get("enabled", True))


def advisory_enabled(config: dict | None = None) -> bool:
    """May the SME advisory rung be consulted? DEFAULT FALSE.

    It is the one part of this page that costs an LLM call, so it is opt-in.
    Off yields ``advisory_state == "not_consulted"``, never ``no_opinion``.
    """
    return bool(_block("advisory", config).get("enabled", False))


def question_for(entity: str, config: dict | None = None) -> str:
    """The framing handed to ``resolve``. Shapes the evidence query only."""
    template = str(_block("cortex", config).get("question_template") or DEFAULT_QUESTION)
    try:
        return template.format(entity=entity)
    except Exception:  # noqa: BLE001 — an operator's bad template is not fatal
        return DEFAULT_QUESTION.format(entity=entity)


def top_k(config: dict | None = None) -> int:
    return int(_block("cortex", config).get("top_k") or DEFAULT_TOP_K)


def max_resolves_per_batch(config: dict | None = None) -> int:
    return int(_block("cortex", config).get("max_resolves_per_batch") or DEFAULT_MAX_RESOLVES)


def stale_after_hours(config: dict | None = None) -> int:
    return int(_block("cortex", config).get("stale_after_hours") or DEFAULT_STALE_HOURS)


# --------------------------------------------------------------------------- #
# The two pure discriminators
# --------------------------------------------------------------------------- #

def finding_state(verdict: str, verdict_source: str) -> str:
    """``(verdict, verdict_source)`` -> one of :data:`FINDING_STATES`.

    Reads the DETERMINISTIC half only. ``backend_errors`` is deliberately not a
    parameter: a dead backend is not a verdict and cannot become one here.

    ``verdict_source`` matters because ``resolver`` returns ``unknown`` in two
    situations that are NOT the same — a pack ran and could not conclude
    (``pack_evaluate``), and no pack recognised the entity at all (``none``).
    Both land on ``unknown``; the gap reasons say which, and this function does
    not have to.
    """
    v = (verdict or "").strip().lower()
    if v in VERDICT_STATES:
        return v
    if not v:
        return "not_resolved"
    # A verdict outside RESOLVE_VERDICTS cannot have come from resolve(). Report
    # it as unknown rather than passing an unrecognised token to the template,
    # which would render as an unstyled state and read as a new category.
    logger.warning("docdrift evidence: unrecognised verdict %r — reporting unknown", verdict)
    return "unknown"


def evidence_health(backend_errors: list | None, backends_consulted: list | None,
                    resolved: bool = True) -> str:
    """``backend_errors`` -> one of :data:`EVIDENCE_HEALTH`. The other axis.

    ``failed`` means EVERY consulted backend errored, so nothing retrieved;
    ``degraded`` means some did. The two are separated because a resolution
    whose whole fan-out died has no corpus evidence at all behind it, while one
    that lost three of five rungs may still be fully cited from the packs — as
    ``TLS 1.1`` is.

    ``unmeasured`` is what an unresolved finding reports. It exists so a
    template never has to render "no errors" for a sweep that never ran, which
    would be the evidence-axis version of the exact bug this card fixes.
    """
    if not resolved:
        return "unmeasured"
    errors = [e for e in (backend_errors or []) if isinstance(e, dict)]
    if not errors:
        return "ok"
    failed = {str(e.get("backend") or "") for e in errors}
    consulted = {str(b or "") for b in (backends_consulted or []) if b}
    # `pack` errors arrive on the same list (resolver appends pack_errors to
    # backend_errors) and are not a retrieval rung, so they can never make the
    # fan-out look totally dead.
    retrieval_failed = {b for b in failed if b and b in consulted}
    if consulted and retrieval_failed >= consulted:
        return "failed"
    return "degraded"


def unknown_reasons(gaps: list | None) -> list:
    """Flatten the gap reasons into ``[{code, label}]``, order preserved, deduped.

    Deduped by CODE across gaps rather than merged into one reason: two gaps
    naming ``backends_failed`` is one fact about this resolution, but
    ``no_pack_matched`` alongside it is a second one and both survive.
    """
    out: list = []
    seen: set = set()
    for gap in gaps or []:
        if not isinstance(gap, dict):
            continue
        for code in gap.get("reasons") or []:
            code = str(code or "")
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({"code": code, "label": GAP_LABELS.get(code, code)})
    return out


def citation_bases(gaps: list | None) -> list:
    """Why a gap cites nothing — ``[{code, label}]``. Three causes, not one."""
    out: list = []
    seen: set = set()
    for gap in gaps or []:
        if not isinstance(gap, dict):
            continue
        code = str(gap.get("citation_basis") or "")
        if not code or code in seen:
            continue
        # A gap that DOES cite evidence has a basis naming how it was cited,
        # which is not an explanation for an absence. Only report the bases the
        # label table knows about.
        if code not in CITATION_BASIS_LABELS:
            continue
        seen.add(code)
        out.append({"code": code, "label": CITATION_BASIS_LABELS[code]})
    return out


# --------------------------------------------------------------------------- #
# The view a finding renders
# --------------------------------------------------------------------------- #

@dataclass
class FindingView:
    """One finding's currency answer. Plain data — no behaviour, no clock.

    Constructed either from a live :class:`CortexResolution` (:func:`from_resolution`)
    or from a persisted row (:func:`from_row`), and the two produce the same
    fields, so the template has one shape to render and cannot accidentally
    depend on freshness.
    """

    entity: str = ""
    entity_key: str = ""
    question: str = ""
    # Axis 1 — the deterministic verdict.
    state: str = "not_resolved"
    verdict: str = ""
    verdict_source: str = "none"
    superseded_by: str = ""
    replacement_source: str = ""
    grounded: bool = False
    assessments: list = field(default_factory=list)
    # Axis 2 — evidence.
    evidence_health: str = "unmeasured"
    citations: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    backend_errors: list = field(default_factory=list)
    backends_consulted: list = field(default_factory=list)
    # Axis 3 — advisory. Never evidence, never a verdict input.
    advisory_state: str = "not_consulted"
    advisory: dict = field(default_factory=dict)
    # Provenance / bookkeeping.
    text: str = ""
    provenance_id: str = ""
    error: str = ""
    duration_ms: int = 0
    resolved_at: str = ""
    stale: bool = False

    # -- derived, for the template ----------------------------------------- #
    @property
    def resolved(self) -> bool:
        return self.state in VERDICT_STATES

    @property
    def actionable(self) -> bool:
        return self.state in ACTIONABLE_STATES

    @property
    def citation_count(self) -> int:
        return len(self.citations)

    @property
    def corpus_citations(self) -> list:
        """Citations that are NOT a pack's own rationale.

        Kept apart so the page can say "3 citations, 1 of them the pack's own
        rule" instead of implying three independent sources corroborated the
        verdict. ``(no evidence anchors)`` resolves with exactly one citation
        and it is the pack's own — reporting that as corroboration would be the
        citation-count version of this card's bug.
        """
        return [c for c in self.citations
                if str((c or {}).get("source_type") or "") != PACK_EVIDENCE_TYPE]

    @property
    def unknown_reasons(self) -> list:
        return unknown_reasons(self.gaps)

    @property
    def citation_bases(self) -> list:
        return citation_bases(self.gaps)

    @property
    def backends_failed(self) -> list:
        return sorted({str(e.get("backend") or "") for e in self.backend_errors
                       if isinstance(e, dict) and e.get("backend")})

    def to_dict(self) -> dict:
        data = {
            "entity": self.entity,
            "entity_key": self.entity_key,
            "question": self.question,
            "state": self.state,
            "verdict": self.verdict,
            "verdict_source": self.verdict_source,
            "superseded_by": self.superseded_by,
            "replacement_source": self.replacement_source,
            "grounded": self.grounded,
            "assessments": list(self.assessments),
            "evidence_health": self.evidence_health,
            "citations": list(self.citations),
            "gaps": list(self.gaps),
            "conflicts": list(self.conflicts),
            "backend_errors": list(self.backend_errors),
            "backends_consulted": list(self.backends_consulted),
            "advisory_state": self.advisory_state,
            "advisory": dict(self.advisory),
            "text": self.text,
            "provenance_id": self.provenance_id,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "resolved_at": self.resolved_at,
            "stale": self.stale,
        }
        data.update({
            "resolved": self.resolved,
            "actionable": self.actionable,
            "citation_count": self.citation_count,
            "corpus_citation_count": len(self.corpus_citations),
            "unknown_reasons": self.unknown_reasons,
            "citation_bases": self.citation_bases,
            "backends_failed": self.backends_failed,
        })
        return data


def _citation_dict(citation: Any) -> dict:
    if isinstance(citation, dict):
        raw = citation
    elif hasattr(citation, "to_dict"):
        raw = citation.to_dict()
    else:
        raw = {}
    return {
        "source_id": str(raw.get("source_id") or ""),
        "source_type": str(raw.get("source_type") or ""),
        "source_table": str(raw.get("source_table") or ""),
        "snippet": str(raw.get("snippet") or ""),
        "provenance_id": str(raw.get("provenance_id") or ""),
        "score": raw.get("score"),
    }


def _assessment_dict(assessment: Any) -> dict:
    if isinstance(assessment, dict):
        raw = assessment
    elif hasattr(assessment, "to_dict"):
        raw = assessment.to_dict()
    else:
        raw = {}
    return {
        "pack_id": str(raw.get("pack_id") or ""),
        "verdict": str(raw.get("verdict") or ""),
        # The pack's OWN word, kept verbatim beside the mapped one, so "eol" and
        # "retired" are still distinguishable after both map onto `deprecated`.
        "pack_verdict": str(raw.get("pack_verdict") or ""),
        "rationale": str(raw.get("rationale") or ""),
        "severity": str(raw.get("severity") or ""),
        "confidence": raw.get("confidence"),
        "superseded_by": str(raw.get("superseded_by") or ""),
        "replacement_source": str(raw.get("replacement_source") or ""),
    }


def _entity_key(entity: str) -> str:
    """The normalised join key, from Cortex's own function when reachable.

    Falls back to a casefolded string rather than raising: this key is a cache
    lookup, and an unreachable Cortex must not stop the page reading rows it
    already has.
    """
    try:
        from tools.cortex.entity_resolution import entity_ident

        key = entity_ident(entity)
        if key:
            return str(key)
    except Exception:  # noqa: BLE001 — key is a lookup, not a claim
        pass
    return " ".join((entity or "").split()).casefold()


def from_resolution(resolution: Any, *, advisory: dict | None = None,
                    duration_ms: int = 0, resolved_at: str = "") -> FindingView:
    """A ``CortexResolution`` -> the view the page renders.

    Every value is READ off the resolution. The only computation is the two
    pure discriminators above, both over fields the resolution already set.
    """
    data = resolution.to_dict() if hasattr(resolution, "to_dict") else dict(resolution or {})
    metadata = data.get("metadata") or {}
    verdict = str(data.get("verdict") or "")
    verdict_source = str(data.get("verdict_source") or "none")
    backend_errors = [e for e in (data.get("backend_errors") or []) if isinstance(e, dict)]
    backends_consulted = [str(b) for b in (data.get("backends_consulted") or [])]
    citations = [_citation_dict(c) for c in (data.get("citations") or [])]
    assessments = [_assessment_dict(a) for a in (data.get("assessments") or [])]

    # `resolve` promotes a pack's `deprecated` to `superseded` only when
    # recommend() NAMES a successor, and the successor lives on the winning
    # assessment. Read it from the assessment that matches the verdict rather
    # than from the first one with a value: a non-winning pack's replacement is
    # not this resolution's recommendation.
    winner = next((a for a in assessments if a.get("verdict") == verdict
                   and a.get("superseded_by")), None)

    # An advisory the RESOLVER itself carried (only possible when an operator
    # has added `sme` to resolve.backends) and one this module asked for
    # separately are the same kind of thing and land in the same panel. The
    # resolver's own is preferred: it came through the governed fan-out.
    resolver_advisory = [a for a in (metadata.get("advisory") or []) if a]
    advisory = dict(advisory or {})
    if resolver_advisory:
        items = [_advisory_item(a) for a in resolver_advisory]
        advisory = {
            "state": "opinion",
            "items": items,
            "errors": list(advisory.get("errors") or []),
            "source": "resolve.backends",
            "reason": "the `sme` rung is configured in resolve.backends",
        }
    advisory.setdefault("state", "not_consulted")
    advisory.setdefault("items", [])
    advisory.setdefault("errors", [])

    view = FindingView(
        entity=str(data.get("entity") or ""),
        entity_key=_entity_key(str(data.get("entity") or "")),
        question=str(data.get("question") or ""),
        state=finding_state(verdict, verdict_source),
        verdict=verdict,
        verdict_source=verdict_source,
        superseded_by=str((winner or {}).get("superseded_by") or ""),
        replacement_source=str((winner or {}).get("replacement_source") or ""),
        grounded=bool(data.get("grounded")),
        assessments=assessments,
        evidence_health=evidence_health(backend_errors, backends_consulted, resolved=True),
        citations=citations,
        gaps=[g for g in (data.get("gaps") or []) if isinstance(g, dict)],
        conflicts=[c for c in (data.get("conflicts") or []) if isinstance(c, dict)],
        backend_errors=backend_errors,
        backends_consulted=backends_consulted,
        advisory_state=str(advisory.get("state") or "not_consulted"),
        advisory=advisory,
        text=str(data.get("text") or ""),
        provenance_id=_provenance_id(citations),
        duration_ms=int(duration_ms or 0),
        resolved_at=resolved_at or _now(),
    )
    return view


def _provenance_id(citations: list) -> str:
    """The ``source_citation_registry`` row id every citation carries.

    One row per resolution attests the whole evidence SET (cef-rsv-03), so any
    citation's id is the resolution's id. Reported once rather than per
    citation, and empty when the registration did not happen.
    """
    for citation in citations or []:
        pid = str((citation or {}).get("provenance_id") or "")
        if pid:
            return pid
    return ""


def unresolved_view(entity: str, reason: str = "") -> FindingView:
    """The view for a finding nobody has resolved.

    NOT an empty ``current``. ``state`` is ``not_resolved`` and
    ``evidence_health`` is ``unmeasured``, so both axes say "not measured"
    rather than "measured and fine".
    """
    return FindingView(
        entity=entity,
        entity_key=_entity_key(entity),
        state="not_resolved",
        evidence_health="unmeasured",
        advisory_state="not_consulted",
        advisory={"state": "not_consulted", "items": [], "errors": [],
                  "reason": reason or "no resolution has been requested for this finding"},
        error=reason,
    )


def refused_view(entity: str, error: str) -> FindingView:
    """The view for a resolution that RAISED.

    A refusal is not an unknown: ``CortexResolutionBlocked`` means the
    resolution was assembled and REJECTED (a hallucinated citation, an
    unattested replacement), which is a stronger statement than "nothing could
    answer" and sends a reader somewhere else entirely.
    """
    return FindingView(
        entity=entity,
        entity_key=_entity_key(entity),
        state="refused",
        evidence_health="unmeasured",
        advisory_state="not_consulted",
        advisory={"state": "not_consulted", "items": [], "errors": [],
                  "reason": "the resolution was refused before an opinion was sought"},
        error=str(error or ""),
        resolved_at=_now(),
    )


# --------------------------------------------------------------------------- #
# The advisory rung
# --------------------------------------------------------------------------- #

def _advisory_item(hit: Any) -> dict:
    """One ``CortexSearchResult`` -> the fields the advisory panel shows.

    ``source_id`` lives on the NESTED ``citation``, not at the top level of
    ``CortexSearchResult.to_dict()``. Reading it from the top level yielded ""
    silently — an advisory opinion with no attribution at all, which is the
    shape this whole page exists to refuse.
    """
    raw = hit.to_dict() if hasattr(hit, "to_dict") else dict(hit or {})
    citation = raw.get("citation")
    if not isinstance(citation, dict):
        citation = {}
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "text": str(raw.get("content") or raw.get("text") or "")[:2000],
        "backend": str(raw.get("backend") or "sme"),
        "source_id": str(citation.get("source_id") or raw.get("source_id") or ""),
        "persona": str(metadata.get("role_id") or ""),
        "domain": str(metadata.get("domain_label") or ""),
    }


def advisory_allows_request_override(config: dict | None = None) -> bool:
    """May a request turn the advisory rung ON when the config default is off?

    DEFAULT TRUE: ``advisory.enabled`` is a DEFAULT (do not spend an LLM call
    unless someone asks), not a prohibition, and the page's checkbox is a human
    asking. Set it false on a deployment that must guarantee no model call
    reaches this page at all — an air-gapped install where the guarantee has to
    hold regardless of what a request body says.
    """
    return bool(_block("advisory", config).get("allow_request_override", True))


def advisory_opinion(entity: str, question: str = "", ctx: Any = None,
                     config: dict | None = None, consult: bool | None = None) -> dict:
    """Ask the ``sme`` rung for an OPINION. Returns one of :data:`ADVISORY_STATES`.

    ``consult`` is the CALLER's decision: ``None`` follows ``advisory.enabled``,
    ``True`` asks anyway (subject to
    :func:`advisory_allows_request_override`), ``False`` never asks. Without
    this parameter the function re-read the config and refused every per-call
    opt-in, so the page's checkbox silently did nothing and every finding
    reported ``not_consulted`` — a control that appears to work and changes
    nothing, which is the failure mode this whole page argues against.

    Routed through the GOVERNED facade ``cortex.api.search`` with an explicit
    ``backends=["sme"]``, never the raw ``search_sme`` adapter: the adapter
    makes an LLM call, and reaching it directly would bypass the 8-gate TRUST
    chain, the ``cortex_audit`` row and output redaction. ``corrective=False``
    for the same reason ``resolve`` sets it — there is nothing to rewrite a
    query for when the backend is one expert.

    The rung is named EXPLICITLY here and nowhere else, which is the property
    ``ROUTE_LABEL_BACKENDS`` protects: no query pattern can cause an advisory
    call, only a human asking for one can.

    Never raises. The three non-answer states are distinguished:
    ``not_consulted`` (the toggle is off, or Cortex is unimportable),
    ``unavailable`` (the rung was called and errored), ``no_opinion`` (it ran
    and returned nothing).
    """
    if consult is None:
        consult = advisory_enabled(config)
    if not consult:
        return {"state": "not_consulted", "items": [], "errors": [],
                "reason": "the advisory rung was not requested for this finding"}
    if not advisory_enabled(config) and not advisory_allows_request_override(config):
        return {"state": "not_consulted", "items": [], "errors": [],
                "reason": "advisory.allow_request_override is false in "
                          "args/dic_docdrift_config.yaml — this deployment refuses "
                          "to consult an LLM-backed rung from a request"}
    try:
        from tools.cortex.api import search as cortex_search
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        return {"state": "not_consulted", "items": [], "errors": [],
                "reason": f"cortex is unavailable ({exc})"}

    context = ctx if ctx is not None else CortexContext()
    _STATS["advisory_asks"] += 1
    try:
        hits = cortex_search(
            question or f"Is {entity} still current?",
            top_k=int(_block("advisory", config).get("top_k") or 1),
            ctx=context,
            backends=["sme"],
            corrective=False,
        )
    except Exception as exc:  # noqa: BLE001 — an advisory outage is not fatal
        logger.warning("docdrift evidence: advisory rung raised: %s", exc)
        return {"state": "unavailable", "items": [], "source": "sme",
                "errors": [{"backend": "sme", "stage": "call", "message": str(exc)}],
                "reason": "the advisory rung raised"}

    errors = [e for e in (getattr(hits, "errors", None) or []) if isinstance(e, dict)]
    # `is_advisory` is the predicate the whole platform uses for this split. Ask
    # it rather than trusting the backend name: it is what guarantees an
    # evidentiary hit can never be presented in the advisory panel, or vice
    # versa, if the rung set here is ever widened.
    try:
        from tools.cortex.search_service import is_advisory
    except Exception:  # noqa: BLE001
        def is_advisory(_hit):  # type: ignore[misc]
            return True

    items = [_advisory_item(h) for h in (hits or []) if is_advisory(h)]
    if items:
        return {"state": "opinion", "items": items, "errors": errors,
                "source": "sme", "reason": "an ACE domain expert answered"}
    if errors:
        # An outage, NOT an absence of opinion. search_sme's own docstring makes
        # the same point: "An empty opinion is not a neutral opinion."
        return {"state": "unavailable", "items": [], "errors": errors,
                "source": "sme", "reason": "the advisory rung was consulted and could not answer"}
    return {"state": "no_opinion", "items": [], "errors": [],
            "source": "sme", "reason": "the expert was asked and returned no opinion"}


# --------------------------------------------------------------------------- #
# Resolve one finding
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_finding(entity: str, *, question: str | None = None,
                    tenant_id: str = "", classification: str = "CUI",
                    advisory: bool | None = None, persist: bool = True,
                    config: dict | None = None) -> FindingView:
    """Resolve ONE finding's entity through the governed facade.

    Args:
        entity: the drift finding's entity, verbatim off ``dic_drift_events``.
            Matched by the packs' own extractors, so only what a pack
            RECOGNISES is assessed — ``(no evidence anchors)`` is a real live
            value and resolves to ``unknown`` with a named reason, which is the
            point.
        advisory: ``None`` follows the config toggle; ``True``/``False``
            overrides it for this call so the page can offer the opt-in per
            finding without editing YAML.

    Never raises. A refusal, an outage and an unimportable Cortex all come back
    as a view whose ``state`` says which.
    """
    entity = (entity or "").strip()
    if not entity:
        return unresolved_view("", "no entity on this finding")
    if not cortex_enabled(config):
        return unresolved_view(
            entity, "cortex.enabled is false in args/dic_docdrift_config.yaml")

    try:
        from tools.cortex.api import resolve as cortex_resolve
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        logger.warning("docdrift evidence: cortex unavailable (%s)", exc)
        return unresolved_view(entity, f"cortex is unavailable ({exc})")

    ctx = CortexContext(tenant_id=tenant_id or "", classification=classification or "CUI")
    framing = question or question_for(entity, config)
    started = time.time()
    try:
        resolution = cortex_resolve(entity, question=framing, ctx=ctx,
                                    top_k=top_k(config))
    except Exception as exc:  # noqa: BLE001 — a refusal is a state, not a 500
        _STATS["refusals"] += 1
        logger.warning("docdrift evidence: resolve(%r) refused/failed: %s", entity, exc)
        view = refused_view(entity, exc)
        view.question = framing
        view.duration_ms = int((time.time() - started) * 1000)
        if persist:
            record_resolution(view, tenant_id=tenant_id, classification=classification)
        return view

    _STATS["resolutions"] += 1
    # `advisory` is the CALLER's per-finding choice and it is threaded all the
    # way down. It is not re-derived from the config inside advisory_opinion —
    # that is what made the page's checkbox inert.
    opinion = advisory_opinion(
        entity, framing, ctx, config,
        consult=None if advisory is None else bool(advisory),
    )

    view = from_resolution(resolution, advisory=opinion,
                           duration_ms=int((time.time() - started) * 1000))
    view.question = framing
    if persist:
        record_resolution(view, tenant_id=tenant_id, classification=classification)
    return view


def resolve_findings(entities: list, *, tenant_id: str = "", classification: str = "CUI",
                     advisory: bool | None = None,
                     config: dict | None = None) -> dict:
    """Resolve a BOUNDED batch. What was skipped is RETURNED, never silent.

    One resolution costs 10-12s against five backends on this deployment and
    the live board holds 72 findings, so an uncapped batch is an outage rather
    than a slow pass. The cap comes from ``cortex.max_resolves_per_batch``.
    """
    cap = max_resolves_per_batch(config)
    wanted = [str(e or "").strip() for e in (entities or []) if str(e or "").strip()]
    # Deduplicate on the same key the cache uses, so a batch does not spend the
    # budget resolving one entity twice under two spellings.
    seen: set = set()
    unique: list = []
    for entity in wanted:
        key = _entity_key(entity)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entity)

    todo, skipped = unique[:cap], unique[cap:]
    if skipped:
        _STATS["capped"] += len(skipped)
        logger.info("docdrift evidence: batch capped at %d, %d deferred", cap, len(skipped))
    views = [resolve_finding(entity, tenant_id=tenant_id, classification=classification,
                             advisory=advisory, config=config) for entity in todo]
    return {
        "resolved": [v.to_dict() for v in views],
        "requested": len(unique),
        "cap": cap,
        # Named, not counted: a reader has to know WHICH findings are still
        # unmeasured, or the page's "8 of 72 resolved" reads as coverage.
        "skipped": skipped,
        "duplicates_dropped": len(wanted) - len(unique),
    }


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def _conn():
    try:  # pragma: no cover - import shape varies by install layout
        from icdev.tools.db.storage import get_connection
    except Exception:  # backward-compat shim
        from tools.db.storage import get_connection
    return get_connection()


#: Mirrors db/migrations/20260819020723_dic_docdrift_resolutions/up.sql.
#: Kept here for the same reason acoic.py keeps its own: a fresh worktree
#: database renders instead of relying on a swallowed "no such table". It
#: CREATEs only — it never ALTERs, so a column added later needs a migration
#: (CREATE TABLE IF NOT EXISTS is a no-op against an existing table, which is
#: how an INSERT ends up naming a column that is not there).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_docdrift_resolutions (
    resolution_id        TEXT PRIMARY KEY,
    entity               TEXT NOT NULL,
    entity_key           TEXT NOT NULL,
    question             TEXT,
    state                TEXT NOT NULL,
    verdict              TEXT,
    verdict_source       TEXT,
    superseded_by        TEXT,
    replacement_source   TEXT,
    grounded             INTEGER NOT NULL DEFAULT 0,
    evidence_health      TEXT,
    citation_count       INTEGER NOT NULL DEFAULT 0,
    citations_json       TEXT,
    gaps_json            TEXT,
    conflicts_json       TEXT,
    assessments_json     TEXT,
    backend_errors_json  TEXT,
    backends_consulted_json TEXT,
    advisory_state       TEXT,
    advisory_json        TEXT,
    resolution_text      TEXT,
    provenance_id        TEXT,
    error                TEXT,
    duration_ms          INTEGER,
    resolved_at          TEXT NOT NULL,
    tenant_id            TEXT,
    classification       TEXT
)
"""


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    cur.execute(_SCHEMA)
    conn.commit()


def _resolution_id(entity_key: str, resolved_at: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{entity_key}|{resolved_at}".encode("utf-8")).hexdigest()[:16]
    return f"ddr_{digest}"


def record_resolution(view: FindingView, *, tenant_id: str = "",
                      classification: str = "CUI") -> str:
    """Persist one resolution. Returns the row id, or "" when nothing was written.

    Never raises: the page has already computed the answer it is about to
    render, and a storage failure must not turn a good resolution into a 500.
    It is LOGGED rather than swallowed silently — a write that reports success
    while persisting nothing is the failure mode CLAUDE.md's INSERT/schema rule
    exists for.

    ``classification`` is a LABEL ('CUI'), never a banner ('CUI // SP-CTI'):
    that column feeds the RLS predicate and a banner matches no label at any
    clearance, so the row would be written, retained and invisible.
    """
    if not view.entity:
        return ""
    resolved_at = view.resolved_at or _now()
    row_id = _resolution_id(view.entity_key, resolved_at)
    try:
        conn = _conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("docdrift evidence: no connection, resolution not persisted: %s", exc)
        return ""
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO dic_docdrift_resolutions ("
            "  resolution_id, entity, entity_key, question, state, verdict, verdict_source,"
            "  superseded_by, replacement_source, grounded, evidence_health, citation_count,"
            "  citations_json, gaps_json, conflicts_json, assessments_json,"
            "  backend_errors_json, backends_consulted_json, advisory_state, advisory_json,"
            "  resolution_text, provenance_id, error, duration_ms, resolved_at,"
            "  tenant_id, classification"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            "          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                row_id, view.entity, view.entity_key, view.question, view.state,
                view.verdict, view.verdict_source, view.superseded_by,
                view.replacement_source, 1 if view.grounded else 0,
                view.evidence_health, view.citation_count,
                json.dumps(view.citations), json.dumps(view.gaps),
                json.dumps(view.conflicts), json.dumps(view.assessments),
                json.dumps(view.backend_errors), json.dumps(view.backends_consulted),
                view.advisory_state, json.dumps(view.advisory),
                view.text, view.provenance_id, view.error, view.duration_ms,
                resolved_at, tenant_id or "default", classification or "CUI",
            ),
        )
        conn.commit()
        return row_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("docdrift evidence: could not persist resolution for %r: %s",
                       view.entity, exc)
        return ""
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _row_to_view(row: dict, *, stale_hours: int) -> FindingView:
    def _json(name, default):
        try:
            value = json.loads(row.get(name) or "null")
        except Exception:  # noqa: BLE001
            return default
        return value if isinstance(value, type(default)) else default

    advisory = _json("advisory_json", {})
    resolved_at = str(row.get("resolved_at") or "")
    view = FindingView(
        entity=str(row.get("entity") or ""),
        entity_key=str(row.get("entity_key") or ""),
        question=str(row.get("question") or ""),
        state=str(row.get("state") or "not_resolved"),
        verdict=str(row.get("verdict") or ""),
        verdict_source=str(row.get("verdict_source") or "none"),
        superseded_by=str(row.get("superseded_by") or ""),
        replacement_source=str(row.get("replacement_source") or ""),
        grounded=bool(row.get("grounded")),
        assessments=_json("assessments_json", []),
        evidence_health=str(row.get("evidence_health") or "unmeasured"),
        citations=_json("citations_json", []),
        gaps=_json("gaps_json", []),
        conflicts=_json("conflicts_json", []),
        backend_errors=_json("backend_errors_json", []),
        backends_consulted=_json("backends_consulted_json", []),
        advisory_state=str(row.get("advisory_state") or "not_consulted"),
        advisory=advisory,
        text=str(row.get("resolution_text") or ""),
        provenance_id=str(row.get("provenance_id") or ""),
        error=str(row.get("error") or ""),
        duration_ms=int(row.get("duration_ms") or 0),
        resolved_at=resolved_at,
        stale=_is_stale(resolved_at, stale_hours),
    )
    return view


def _is_stale(resolved_at: str, stale_hours: int) -> bool:
    """Is this persisted answer older than the configured window?

    An unparseable timestamp is STALE, not fresh. A row we cannot date is one
    we cannot vouch for, and defaulting to fresh would let a broken clock
    present an arbitrarily old `current` as today's.
    """
    if not resolved_at:
        return True
    try:
        when = datetime.fromisoformat(resolved_at)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return True
    return datetime.now(timezone.utc) - when > timedelta(hours=max(stale_hours, 0))


def latest_resolutions(entities: list | None = None, *, limit: int = 200,
                       config: dict | None = None) -> dict:
    """``{entity_key: FindingView}`` — the NEWEST persisted answer per entity.

    Degrades to ``{}`` when the table is absent or unreadable, which renders as
    every finding ``not_resolved``. That is the honest degradation: "we have no
    stored answer" is exactly what an unreachable store means, and it is a
    different rendering from ``current``.
    """
    stale_hours = stale_after_hours(config)
    wanted = {_entity_key(e) for e in (entities or []) if str(e or "").strip()}
    try:
        conn = _conn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("docdrift evidence: no connection for stored resolutions: %s", exc)
        return {}
    try:
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT resolution_id, entity, entity_key, question, state, verdict,"
            "  verdict_source, superseded_by, replacement_source, grounded,"
            "  evidence_health, citation_count, citations_json, gaps_json,"
            "  conflicts_json, assessments_json, backend_errors_json,"
            "  backends_consulted_json, advisory_state, advisory_json,"
            "  resolution_text, provenance_id, error, duration_ms, resolved_at"
            " FROM dic_docdrift_resolutions ORDER BY resolved_at DESC LIMIT %s",
            (int(limit),),
        )
        cols = [d[0] for d in cur.description]
        out: dict = {}
        for raw in cur.fetchall():
            row = {k: raw[k] for k in raw.keys()} if hasattr(raw, "keys") else dict(zip(cols, raw))
            key = str(row.get("entity_key") or "")
            if not key or key in out:
                continue  # ORDER BY resolved_at DESC — the first is the newest
            if wanted and key not in wanted:
                continue
            out[key] = _row_to_view(row, stale_hours=stale_hours)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("docdrift evidence: could not read stored resolutions: %s", exc)
        return {}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# The page context
# --------------------------------------------------------------------------- #

def attach_resolutions(drift_events: list, *, config: dict | None = None) -> dict:
    """Drift events -> ``{"findings": [...], "summary": {...}}`` for the template.

    Every event gets a view. An event with no stored answer gets
    :func:`unresolved_view`, so the template never has to branch on ``None``
    and can never render a missing answer as a clean one.

    The summary counts each state SEPARATELY and never sums them into a single
    "N healthy" — the count of `current` and the count of `not_resolved` are
    the two numbers this page exists to keep apart.
    """
    events = [e for e in (drift_events or []) if isinstance(e, dict)]
    entities = [str(e.get("entity") or "") for e in events]
    stored = latest_resolutions(entities, config=config)
    enabled = cortex_enabled(config)

    findings: list = []
    for event in events:
        entity = str(event.get("entity") or "")
        view = stored.get(_entity_key(entity))
        if view is None:
            view = unresolved_view(
                entity,
                "no resolution has been requested for this finding"
                if enabled else
                "cortex.enabled is false in args/dic_docdrift_config.yaml",
            )
        finding = dict(event)
        finding["resolution"] = view.to_dict()
        findings.append(finding)

    summary = {state: 0 for state in FINDING_STATES}
    health = {state: 0 for state in EVIDENCE_HEALTH}
    advisory_counts = {state: 0 for state in ADVISORY_STATES}
    for finding in findings:
        res = finding["resolution"]
        summary[res["state"]] = summary.get(res["state"], 0) + 1
        health[res["evidence_health"]] = health.get(res["evidence_health"], 0) + 1
        advisory_counts[res["advisory_state"]] = advisory_counts.get(res["advisory_state"], 0) + 1

    return {
        "findings": findings,
        "summary": summary,
        "evidence_health": health,
        "advisory": advisory_counts,
        "enabled": enabled,
        "advisory_enabled": advisory_enabled(config),
        "total": len(findings),
        # Distinct entities, because 72 events name far fewer entities and
        # "resolve everything" costs one call per ENTITY, not per row.
        "distinct_entities": len({_entity_key(str(e.get("entity") or "")) for e in events}),
        "batch_cap": max_resolves_per_batch(config),
        "stale_after_hours": stale_after_hours(config),
    }


def run_stats() -> dict:
    """Counters for this process. A bound that is not reported is a silent cap."""
    return dict(_STATS)


def reset_run_stats() -> None:
    for key in _STATS:
        _STATS[key] = 0
