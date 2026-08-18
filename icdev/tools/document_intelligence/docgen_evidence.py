# CUI // SP-CTI
"""DIC document generation through ONE governed evidence seam (cef-di-05).

``doc_generator.py`` drafts DIC documents: ``generate_document`` behind
``POST /api/generate`` and ``regenerate_section`` behind
``POST /api/generate/section``. The module itself is LLM-only — it holds no RAG
and no KG import — and its evidence came from one line in each entry point::

    engine = DICSearchEngine(tenant_id=tenant_id)
    search_results = engine.search(query, collection_id=..., top_k=...)

That is a better starting point than acoic's was (cef-di-03): a
``DICSearchResult`` already carries a citation pack, so a drafted
``[source: chunk N]`` already resolved to a real chunk. What it is not is
COMPLETE. It reaches exactly one rung. The currency store, the knowledge graph
and the KB all hold evidence bearing on a drafted document, and none of them
were asked, because asking would have meant doc_generator learning three more
table names.

This module is the seam that replaces those two lines: ONE call,
``cortex.resolve(query)``, fanning out over those rungs under the 8-gate TRUST
chain, writing a ``cortex_audit`` row and registering a
``source_citation_registry`` row for the evidence set. It is the third sibling
of ``tools/doc_modernization/evidence.py`` (cef-di-01) and
``tools/document_intelligence/ssp_evidence.py`` (cef-di-03) — deliberately a
sibling rather than a shared module, because the three ask different questions
("is this entity current", "what evidences this control", "what should this
document be written from") and share no lane reader.

THE CURRENCY RUNG IS THE POINT
------------------------------
A second RAG call would have added nothing. What routing through ``resolve``
buys is that the CURRENCY rung comes with it, and with it the packs.
``resolve`` runs every registered :class:`DomainPack` over its entity string and
returns their DETERMINISTIC assessments — so handing a DRAFTED SECTION back to
the same seam (:func:`screen_draft`) answers "did this draft just recommend
something the catalog says is dead?" with a pack verdict.

That case was previously invisible, and invisible in a way that looks like
success: :func:`verifier.verify` asks whether a claim is SUPPORTED by the
retrieved evidence, and a draft grounded in a 2019 runbook that recommends TLS
1.1 is fully supported by that runbook. Every gate downstream — attribution
score, confabulation risk, placeholder scan — agrees. The document ships
reintroducing a protocol the estate spent two years removing.

TRUST rule 1 is unchanged. The pack derives the verdict from typed catalog /
EOL / rulebook fields; no LLM decides what is deprecated, and the guard never
rewrites a claim. It annotates, cites the verdict, and flags for the human who
was always going to review the draft — every generated version lands
``pending_review`` and none is auto-published.

WHY THIS SEAM RETURNS ``DICSearchResult`` AND NOT A NEW SHAPE
------------------------------------------------------------
The epic's migration rule is "behaviour-preserving until proven otherwise", and
the cheapest way to mean that literally is to change the retrieval line and
NOTHING after it. So :attr:`DocgenEvidence.results` holds real
:class:`search_engine.DICSearchResult` objects, and every consumer downstream of
retrieval is untouched: ``_evidence_block`` and ``_targeted_evidence_block``
render them, ``verify([r.content for r in ...])`` replays them,
``[r.citation.to_dict() for r in ...]`` persists them, and the ``quality_gate``
hook derives ``allowed_source_ids`` from ``r.chunk_id`` exactly as before.

``chunk_id`` is set to the Cortex citation's ``source_id``. That is what makes
the existing citation contract hold on the new path without touching a prompt:
the drafting prompts already say "write ``[source: chunk {chunk_id}]``", the
model already cites the ids it was shown, and ``citation_grounding``'s
``validate_citations`` already checks those tags against the same set. A
governed source id is simply a better id than a chunk row id — it resolves to a
source TABLE and a provenance id as well.

The extra provenance rides along rather than being dropped:
:class:`GovernedCitation` SUBCLASSES the DIC ``Citation`` and its ``to_dict()``
returns a SUPERSET of the DIC citation dict. So the persisted
``citations_json`` gains ``source_type`` / ``source_table`` / ``provenance_id``
/ ``evidence_path`` and loses nothing, and no caller had to branch to get it.

``None`` IS THE LEGACY PATH
---------------------------
Every ``None`` this module returns means "the seam said nothing — do what you
did before". The toggle being off returns ``None``, a re-entrant ask returns
``None``, a spent budget returns ``None``, an absent Cortex returns ``None``,
and an unavailable ``search_engine`` returns ``None``. Each is logged with its
OWN reason, because "off", "recursive", "capped" and "absent" send you to four
different places. A resolution the governance chain REFUSED returns a bundle
carrying :attr:`DocgenEvidence.blocked` and no results instead — a refusal is a
fact about this query and the caller should be able to see it, while still
falling through because the bundle is empty.

So no drafting run can be FAILED by this module, and ``cortex.enabled: false``
in ``args/dic_docgen_config.yaml`` restores the pre-migration behaviour exactly.

TWO COPIES, ONE OF WHICH IS THE ONE DOC_GENERATOR USES
------------------------------------------------------
This module ships byte-identical at ``tools/document_intelligence/`` and
``icdev/tools/document_intelligence/``, like the rest of this canvas. They are
SEPARATE module objects (``tools.X is icdev.tools.X`` -> ``False``), so they have
separate :data:`_STATE` thread-locals: separate memo caches, separate budgets,
separate re-entrancy flags.

Reach the seam through ``doc_generator._evidence_module()``, which resolves ONE
of them (``icdev`` first) and is what every call inside ``doc_generator`` goes
through, so a process only ever touches one. Resetting or inspecting the run
state by importing a namespace directly can reset the copy nobody is using — and
a test that patches ``tools.document_intelligence.docgen_evidence`` while
``doc_generator`` holds the ``icdev`` one patches nothing. Patch what
``_evidence_module()`` returns.

WHAT IS DELIBERATELY NOT MIGRATED
---------------------------------
* The **Chain-of-Debate paths** — ``_cot_generate`` and ``_cod_compress``, both
  via ``ChainOrchestrator``. They consume an evidence STRING and do not care
  which chain produced it, so they work identically on both paths and were not
  touched. They stay behind their own pre-existing ``ICDEV_DIC_COT_ENABLED``
  env toggle.
* The **source-text fallback** in ``generate_document`` — the branch that
  scrapes ``"Source document content:"` out of the query when retrieval returns
  nothing at all. It is the path that lets a caller draft from a document that
  is not in the KB yet, it never went near retrieval, and a ranked evidence seam
  has nothing to offer it.
* ``regenerate_section``'s **section/adjacent-context reads** — direct
  ``dic_sections`` / ``dic_versions`` SELECTs for the document's own structure.
  Those are EXACT row lookups by primary key, not evidence retrieval; a ranked
  seam cannot return "the section before this one".
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Config filename. Flat ``args/dic_*.yaml`` like the rest of the DIC canvas.
CONFIG_FILENAME = "dic_docgen_config.yaml"


def _default_config_path() -> Path:
    """``args/dic_docgen_config.yaml``, found by walking up from this file.

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


#: Resolved once at import. Behaviour config for this seam.
CONFIG_PATH = _default_config_path()

#: Block within that file which governs the evidence half.
CONFIG_KEY = "cortex"

#: Block which governs the currency guard.
GUARD_KEY = "currency_guard"

#: Evidence citations requested per Cortex backend. 10 is the legacy
#: ``engine.search(query, top_k=10)`` default in ``generate_document``, so both
#: paths ask for the same amount of evidence.
DEFAULT_TOP_K = 10

#: Upper bound on outbound resolutions per drafting run. REPORTED via
#: :func:`run_stats`, never silent.
DEFAULT_MAX_RESOLVES = 50

#: Characters of drafted text handed to a screening resolution.
DEFAULT_MAX_SCREEN_CHARS = 6000

#: Verdicts that trip the currency guard. ``superseded`` is ``deprecated`` plus
#: a known successor. ``unknown`` never trips it: it means no pack RECOGNISED
#: the entity, which is a gap and not a finding — treating it as one would flag
#: every draft on the board.
DEFAULT_DEPRECATED_VERDICTS = ("deprecated", "superseded")

#: Citation source_type that is a pack's OWN verdict rationale, not corpus
#: evidence. Never drafted from — same rule, same reason, as ``ssp_evidence``:
#: letting a derived verdict become a cited sentence would make it the ground
#: truth for the document it was derived from.
PACK_EVIDENCE_TYPE = "pack_evidence"

#: Cortex ``source_table`` values whose ``source_id`` really is a DIC document
#: id, so the citation's ``archive_url`` resolves. Anything else leaves
#: ``doc_id`` empty rather than minting a link to a page that does not exist.
DIC_DOC_TABLES = ("dic_documents", "dic_sections", "dic_versions")

#: ``citation_report.evidence_path`` values persisted onto a section.
PATH_CORTEX = "cortex"
PATH_CORTEX_EMPTY_FALLBACK = "cortex_empty_fallback"
PATH_LEGACY = "legacy"

#: :attr:`CurrencyScreen.action` values.
ACTION_ANNOTATE = "annotate"
ACTION_ABSTAIN = "abstain"

_STATE = threading.local()
_CONFIG_CACHE: dict = {}


def _attr(obj, name: str, default=None):
    """Read ``name`` off a dataclass instance OR off a dict.

    ``CortexResolution`` round-trips through ``to_dict``/``from_dict`` in
    several places (the cache, the audit writer), so a consumer that only ever
    calls ``getattr`` silently reads ``default`` for every field the moment it
    is handed the dict form — which looks exactly like an empty resolution.
    """
    if isinstance(obj, dict):
        value = obj.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------
def _dic_types():
    """``(DICSearchResult, Citation)`` from the DIC search engine, or ``None``.

    Imported lazily and by VALUE rather than subclassed at module scope: this
    module must stay importable in a tree where ``search_engine`` is absent or
    broken, and the answer when it is absent is ``None`` — the legacy path —
    not an ImportError at drafting time.
    """
    try:
        from tools.document_intelligence.search_engine import Citation, DICSearchResult
    except Exception:  # noqa: BLE001
        try:
            from icdev.tools.document_intelligence.search_engine import (  # type: ignore[no-redef]
                Citation,
                DICSearchResult,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "docgen evidence: DIC search shapes unavailable (%s) — legacy path", exc
            )
            return None
    return DICSearchResult, Citation


def _governed_citation_class(citation_cls):
    """A ``Citation`` subclass whose ``to_dict()`` is a SUPERSET of the DIC one.

    Built here rather than declared at module scope because the base class only
    exists once :func:`_dic_types` has resolved. The subclass relationship is
    load-bearing in one place: ``DICSearchResult.citation`` is typed
    ``Citation``, and a duck-typed stand-in would be a lie the type checker
    cannot see and a reader has to discover.

    The extra keys are ADDITIVE. ``citations_json`` gains ``source_type``,
    ``source_table``, ``provenance_id`` and ``evidence_path``; nothing a
    pre-migration reader looked for is removed or renamed, so the UI, the export
    path and ``citation_grounding`` all keep working against the same keys.
    """

    class GovernedCitation(citation_cls):  # type: ignore[misc, valid-type]
        """A DIC citation that also records WHICH governed source it came from."""

        def __init__(self, *, source_type="", source_table="", provenance_id="",
                     evidence_path=PATH_CORTEX, **kwargs):
            super().__init__(**kwargs)
            self.source_type = source_type
            self.source_table = source_table
            self.provenance_id = provenance_id
            self.evidence_path = evidence_path

        def to_dict(self) -> dict:
            data = super().to_dict()
            data.update({
                "source_type": self.source_type,
                "source_table": self.source_table,
                "provenance_id": self.provenance_id,
                "evidence_path": self.evidence_path,
            })
            return data

    return GovernedCitation


@dataclass
class DocgenEvidence:
    """One drafting run's governed evidence. Plain data — no behaviour, no clock.

    ``results`` are real :class:`DICSearchResult` objects so the whole pipeline
    downstream of retrieval is untouched. ``deprecated`` carries the pack
    assessments the resolution already made about the QUERY — a currency finding
    the drafter is told about BEFORE it writes, as well as after
    (:func:`screen_draft`).

    ``errors`` is carried separately from an empty result set for the reason
    this repository keeps re-learning: a backend that DIED and a corpus that
    matched nothing are different answers, and merging them turns an outage into
    a statement about the data.
    """

    query: str = ""
    results: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    deprecated: list = field(default_factory=list)
    backends: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    verdict: str = ""
    #: Non-empty when the governance chain REFUSED the resolution, carrying the
    #: ``resolver.BLOCK_*`` reason. A refusal is not an empty answer.
    blocked: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.results

    def detail(self) -> dict:
        """The infrastructure facts a caller persists alongside the draft.

        Carried even when the caller then takes the legacy path, so a THIN
        governed answer is never mistaken for a thin corpus.
        """
        return {
            "backends": list(self.backends),
            "backend_errors": list(self.errors),
            "blocked": self.blocked,
            "resolve_verdict": self.verdict,
        }


@dataclass
class CurrencyScreen:
    """What the currency guard found in one drafted section.

    ``findings`` is empty for the overwhelming majority of sections. It is
    non-empty only when a registered pack DETERMINISTICALLY judged an entity the
    draft names to be deprecated or superseded.
    """

    findings: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    action: str = ACTION_ANNOTATE
    errors: list = field(default_factory=list)

    @property
    def tripped(self) -> bool:
        return bool(self.findings)

    def advisory(self) -> str:
        """The blockquote appended to an annotated section.

        Deliberately carries NO ``[source: chunk N]`` tag. A pack's evidence ref
        is a synthetic key (``entity_currency:nist``), not a retrieved chunk id,
        so tagging it would put an id in the prose that
        ``validate_citations`` cannot match — a hallucinated citation by
        construction, manufactured by the very guard meant to raise trust. The
        verdict is cited STRUCTURALLY instead, as a ``currency_verdict`` entry in
        the section's citations, and named in plain prose here.
        """
        lines = []
        for finding in self.findings:
            entity = finding.get("entity", "")
            verdict = finding.get("verdict", "")
            successor = finding.get("superseded_by", "")
            source = finding.get("source", "")
            line = f"**{entity}** is {verdict}"
            if successor:
                line += f" — superseded by **{successor}**"
            if source:
                line += f" (per {source})"
            lines.append(line)
        return (
            "> ⚠ Currency: this draft names "
            + "; ".join(lines)
            + ". Verified against the currency catalog, not against the retrieved "
              "document — a source may predate the change. Resolve before publishing."
        )


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------
def load_config(path=None) -> dict:
    """``args/dic_docgen_config.yaml``, memoised per path.

    An unreadable or absent file is ``{}``, which reads as OFF everywhere below.
    A config this module cannot parse must not take document generation
    offline; it must leave it on the path it was already on.
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
        logger.debug("docgen evidence: config unavailable (%s) — seam is OFF", exc)
    _CONFIG_CACHE[key] = data
    return data


def cortex_config(config: dict | None = None) -> dict:
    """The ``cortex:`` block of ``args/dic_docgen_config.yaml``."""
    if config is None:
        config = load_config()
    block = (config or {}).get(CONFIG_KEY)
    return dict(block) if isinstance(block, dict) else {}


def guard_config(config: dict | None = None) -> dict:
    """The ``currency_guard:`` block of ``args/dic_docgen_config.yaml``."""
    if config is None:
        config = load_config()
    block = (config or {}).get(GUARD_KEY)
    return dict(block) if isinstance(block, dict) else {}


def cortex_enabled(config: dict | None = None) -> bool:
    """Is the migrated path live? DEFAULT FALSE.

    Off is the shipped default and off means the seam is never consulted, so
    the legacy chain is restored by flipping this flag rather than by reverting
    a merge (the epic's migration rule).
    """
    return bool(cortex_config(config).get("enabled", False))


def fallback_on_empty(config: dict | None = None) -> bool:
    """Take the legacy retrieval when the governed path produced no results?

    DEFAULT TRUE. A migration that quietly drops the only evidence a document
    can be written from is not behaviour-preserving.
    """
    return bool(cortex_config(config).get("fallback_on_empty", True))


def currency_guard_enabled(config: dict | None = None) -> bool:
    """Is the drafted-section currency screen live? DEFAULT TRUE.

    Gated by :func:`cortex_enabled` at every call site, so with the master
    toggle off this never runs and the shipped default is still "off".
    """
    return bool(guard_config(config).get("enabled", True))


def on_deprecated(config: dict | None = None) -> str:
    """``"annotate"`` (default) or ``"abstain"``.

    An unrecognised value reads as ``annotate`` — the weaker action — because a
    typo in a config must not silently blank every section of a document.
    """
    value = str(guard_config(config).get("on_deprecated", ACTION_ANNOTATE) or "").strip().lower()
    return value if value in (ACTION_ANNOTATE, ACTION_ABSTAIN) else ACTION_ANNOTATE


def deprecated_verdicts(config: dict | None = None) -> tuple:
    """Verdicts that trip the guard. See :data:`DEFAULT_DEPRECATED_VERDICTS`."""
    raw = guard_config(config).get("verdicts")
    if not isinstance(raw, list) or not raw:
        return DEFAULT_DEPRECATED_VERDICTS
    return tuple(str(v).strip().lower() for v in raw if str(v).strip())


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
        "cached_queries": len(state["cache"]),
    }


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------
def evidence_question(collection_id: str | None = None) -> str:
    """The retrieval framing.

    It shapes the evidence query and nothing else — ``resolve`` never feeds a
    question to a pack extractor, so it cannot move the currency assessment onto
    a different entity. Naming the collection here is framing for the RANKER; it
    is NOT a scope filter, and the RLS predicate on the underlying reads remains
    the only thing enforcing what this tenant may see.
    """
    scope = (collection_id or "").strip()
    if scope and scope != "default":
        return f"source material for drafting a document in the {scope} collection"
    return "source material for drafting a technical document"


def _to_results(resolution, limit: int, path: str) -> tuple:
    """``(results, citation_dicts)`` — index-aligned, ``pack_evidence`` excluded.

    A citation with no source id or no snippet is DROPPED rather than given a
    synthetic one. An id nothing can be looked up by is worse than one fewer
    piece of evidence: it passes ``validate_citations`` (it is in the allowed
    set, because we put it there) while resolving to nothing.
    """
    shapes = _dic_types()
    if shapes is None:
        return [], []
    result_cls, citation_cls = shapes
    governed_cls = _governed_citation_class(citation_cls)

    results: list = []
    citation_dicts: list = []
    for rank, citation in enumerate(_attr(resolution, "citations", []) or []):
        source_type = str(_attr(citation, "source_type", "") or "")
        if source_type == PACK_EVIDENCE_TYPE:
            continue
        source_id = str(_attr(citation, "source_id", "") or "")
        snippet = str(_attr(citation, "snippet", "") or "")
        if not source_id or not snippet.strip():
            continue
        source_table = str(_attr(citation, "source_table", "") or "")
        governed = governed_cls(
            doc_id=source_id if source_table in DIC_DOC_TABLES else "",
            doc_title=str(_attr(citation, "title", "") or source_type or "cortex"),
            chunk_id=source_id,
            source_uri=str(_attr(citation, "url", "") or ""),
            classification=str(_attr(citation, "classification", "") or "CUI"),
            source_type=source_type or "cortex",
            source_table=source_table,
            provenance_id=str(_attr(citation, "provenance_id", "") or ""),
            evidence_path=path,
        )
        results.append(result_cls(
            chunk_id=source_id,
            doc_id=governed.doc_id,
            doc_title=governed.doc_title,
            content=snippet,
            # Descending so the pre-existing `results[:8]` / `results[:3]`
            # truncations downstream keep the resolution's own ranking.
            score=max(0.0, 1.0 - (rank / 100.0)),
            citation=governed,
        ))
        citation_dicts.append(governed.to_dict())
        if len(results) >= limit:
            break
    return results, citation_dicts


def _findings(resolution, verdicts: tuple) -> list:
    """Deterministic pack assessments that trip the guard.

    Read off ``resolution.assessments`` — ``DomainPack.evaluate()`` output, and
    the ONLY thing permitted to carry a currency verdict (TRUST rule 1). The
    resolution's own top-level ``verdict`` is deliberately not used: it is the
    REDUCED winner across every entity found, so a section naming one dead
    protocol and four live ones reduces to ``deprecated`` and names nothing.
    """
    findings: list = []
    for assessment in _attr(resolution, "assessments", []) or []:
        verdict = str(_attr(assessment, "verdict", "") or "").strip().lower()
        if verdict not in verdicts:
            continue
        entity = str(_attr(assessment, "entity", "") or "").strip()
        if not entity:
            continue
        evidence = _attr(assessment, "evidence", []) or []
        first = evidence[0] if evidence else {}
        findings.append({
            "entity": entity,
            "entity_type": str(_attr(assessment, "entity_type", "") or ""),
            "verdict": verdict,
            "pack_verdict": str(_attr(assessment, "pack_verdict", "") or ""),
            "pack_id": str(_attr(assessment, "pack_id", "") or ""),
            "superseded_by": str(_attr(assessment, "superseded_by", "") or ""),
            "replacement_source": str(_attr(assessment, "replacement_source", "") or ""),
            "rationale": str(_attr(assessment, "rationale", "") or ""),
            "severity": str(_attr(assessment, "severity", "") or ""),
            "source": str(_attr(first, "source", "") or "") if isinstance(first, dict) else "",
        })
    return findings


def _verdict_citations(findings: list) -> list:
    """Citation-shaped records for the verdicts, for the section's citation list.

    This is how a generated section CITES a currency verdict. ``chunk_id`` is
    deliberately EMPTY: these are not retrievable chunks, and giving one an id
    would put it in the allowed-source set, where a model could then "cite" it.
    ``source_type`` names what it is so a reader (and the UI) can tell a verdict
    apart from a retrieved passage.
    """
    citations = []
    for finding in findings:
        detail = f"{finding['entity']} is {finding['verdict']}"
        if finding.get("superseded_by"):
            detail += f"; superseded by {finding['superseded_by']}"
        if finding.get("rationale"):
            detail += f" — {finding['rationale']}"
        citations.append({
            "doc_id": "",
            "doc_title": f"Currency verdict: {finding['entity']}",
            "version_id": "",
            "page": 0,
            "section": "",
            "chunk_id": "",
            "source_uri": "",
            "classification": "CUI",
            "archive_url": "#",
            "source_type": "currency_verdict",
            "source_table": finding.get("source", ""),
            "provenance_id": finding.get("pack_id", ""),
            "evidence_path": PATH_CORTEX,
            "detail": detail,
        })
    return citations


def _budget_available(settings: dict, label: str) -> bool:
    """Is there an outbound resolution left in this run's budget?

    A refusal is COUNTED and warned, never silent — an evidence seam that
    quietly stops resolving halfway through a batch produces a document whose
    later sections are worse for a reason nobody can see afterwards.
    """
    state = _run()
    budget = int(settings.get("max_resolves_per_run", DEFAULT_MAX_RESOLVES) or 0)
    if budget and state["resolves"] >= budget:
        state["capped"] += 1
        logger.warning(
            "docgen evidence: outbound budget of %d resolutions spent — %s took the "
            "legacy path (reported in run_stats, never silent)", budget, label,
        )
        return False
    return True


def resolve_evidence(
    query: str,
    *,
    collection_id: str | None = None,
    config: dict | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
    top_k: int | None = None,
) -> "DocgenEvidence | None":
    """Governed evidence for one drafting query, or ``None`` meaning "legacy path".

    ``None`` is returned — never an exception, never a partial draft — when the
    toggle is off, when the ask is re-entrant, when the outbound budget for this
    run is spent, or when Cortex cannot be imported.
    """
    label = (query or "").strip()
    if not label:
        return None
    if not cortex_enabled(config):
        return None

    state = _run()
    if state["active"]:
        # Re-entrant: cortex.resolve is running the packs right now. Answering
        # would recurse without bound. Thread-local rather than global because
        # the search fan-out runs backends in a worker pool and a global flag
        # would suppress an unrelated concurrent drafting run's evidence.
        #
        # THREAD-LOCAL IS CORRECT HERE, and it is NOT correct everywhere — check
        # before copying this. cef-di-04 found that `search_service._run_backends`
        # submits every backend onto a shared ThreadPoolExecutor, so a surface
        # that IS one of Cortex's own rungs gets the re-entrant call back on a
        # DIFFERENT thread and a thread-local guard is structurally blind to it
        # (it passes a single-threaded test and then exhausts the pool in
        # production). The rungs are `BACKEND_ADAPTERS` in
        # tools/cortex/search_service.py — rag, graph, dic, kb, currency,
        # external, sme. `doc_generator` is a DRAFTING surface and none of those
        # adapters reaches it, so the only re-entrancy possible is a
        # `DomainPack.evaluate()` calling back — and `resolver.assess()` runs the
        # packs SYNCHRONOUSLY on the calling thread, which this flag sees.
        # Same carve-out as acoic (cef-di-03) and the docmod packs (cef-di-01).
        logger.debug("docgen evidence: re-entrant ask for %r — legacy path", label[:80])
        return None

    key = ("evidence", label.casefold()[:512], (collection_id or "").strip())
    if key in state["cache"]:
        return state["cache"][key]

    settings = cortex_config(config)
    if not _budget_available(settings, f"query {label[:60]!r}"):
        return None

    limit = int(top_k or settings.get("top_k", DEFAULT_TOP_K) or DEFAULT_TOP_K)
    resolution, blocked = _resolve(
        label,
        question=evidence_question(collection_id),
        tenant_id=tenant_id,
        classification=classification,
        top_k=limit,
    )
    if resolution is None and not blocked:
        return None
    if resolution is None:
        bundle = DocgenEvidence(query=label, blocked=blocked)
        state["cache"][key] = bundle
        return bundle

    results, citations = _to_results(resolution, limit, PATH_CORTEX)
    bundle = DocgenEvidence(
        query=label,
        results=results,
        citations=citations,
        deprecated=_findings(resolution, deprecated_verdicts(config)),
        backends=list(_attr(resolution, "backends_consulted", []) or []),
        errors=list(_attr(resolution, "backend_errors", []) or []),
        verdict=str(_attr(resolution, "verdict", "") or ""),
    )
    state["cache"][key] = bundle
    return bundle


def screen_draft(
    text: str,
    *,
    config: dict | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> "CurrencyScreen | None":
    """Deterministic currency screen over one DRAFTED section.

    Returns ``None`` when the screen did not run at all (toggle off, guard off,
    re-entrant, budget spent, Cortex absent) — which is NOT the same as a
    :class:`CurrencyScreen` with no findings, and the caller must not merge
    them: "nothing checked" and "checked, nothing wrong" differ by exactly the
    assurance this card exists to add.

    The draft is passed as the resolution's ENTITY, because that is the only
    string ``resolve`` runs the pack extractors over (a ``question`` is never
    handed to an extractor, precisely so a second entity mentioned in framing
    cannot move a verdict). ``top_k=1`` keeps the retrieval fan-out that comes
    with it cheap: this call wants the pack ASSESSMENTS, and the evidence rungs
    are already covered by :func:`resolve_evidence`.

    ``ctx.trusted_source`` is deliberately NOT set. The DIC ingest path sets it
    for first-party documents inside the tenant boundary; this string is
    freshly LLM-generated prose, so it goes through the input injection screen
    like any other model output.
    """
    body = (text or "").strip()
    if not body:
        return None
    if not cortex_enabled(config) or not currency_guard_enabled(config):
        return None

    state = _run()
    if state["active"]:
        logger.debug("docgen evidence: re-entrant currency screen — skipped")
        return None

    settings = cortex_config(config)
    limit = int(settings.get("max_screen_chars", 0) or 0)
    if limit <= 0:
        limit = int(guard_config(config).get("max_screen_chars", DEFAULT_MAX_SCREEN_CHARS)
                    or DEFAULT_MAX_SCREEN_CHARS)
    body = body[:limit]

    key = ("screen", body.casefold())
    if key in state["cache"]:
        return state["cache"][key]

    if not _budget_available(settings, "currency screen"):
        return None

    resolution, blocked = _resolve(
        body,
        question="currency of the technologies this draft names",
        tenant_id=tenant_id,
        classification=classification,
        top_k=1,
    )
    if resolution is None:
        # A refused or failed screen is NOT a clean screen. Report it as an
        # error-carrying result so the caller records "not screened" on the
        # section rather than persisting silence that reads as "screened, fine".
        if blocked:
            screen = CurrencyScreen(
                action=on_deprecated(config),
                errors=[{"backend": "cortex.resolve", "stage": "screen", "message": blocked}],
            )
            state["cache"][key] = screen
            return screen
        return None

    findings = _findings(resolution, deprecated_verdicts(config))
    screen = CurrencyScreen(
        findings=findings,
        citations=_verdict_citations(findings),
        action=on_deprecated(config),
        errors=list(_attr(resolution, "backend_errors", []) or []),
    )
    state["cache"][key] = screen
    return screen


def _resolve(label: str, *, question: str, tenant_id, classification, top_k):
    """``(resolution, blocked_reason)``, with the re-entrancy flag held throughout.

    ``(None, "")`` means "could not ask" — the caller falls through silently.
    ``(None, reason)`` means the governance chain REFUSED, which is a fact worth
    persisting even though the caller still falls through.
    """
    state = _run()
    try:
        # Late import: a DIC deployment without Cortex must degrade to the
        # legacy path, not fail to import.
        from tools.cortex.api import resolve as cortex_resolve
        from tools.cortex.schemas import CortexContext
    except Exception as exc:  # noqa: BLE001
        logger.warning("docgen evidence: cortex unavailable (%s) — legacy path", exc)
        return None, ""

    ctx = CortexContext(
        tenant_id=tenant_id or "",
        classification=classification or "CUI",
    )
    state["active"] = True
    try:
        resolution = cortex_resolve(label, question=question, ctx=ctx, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        # Includes CortexResolutionBlocked and GovernanceBlockedError. A refusal
        # is REPORTED as a refusal and the caller still drafts off the legacy
        # read — a governance block on supplementary evidence must never take
        # document generation offline.
        reason = _attr(exc, "reason", "") or type(exc).__name__
        logger.warning(
            "docgen evidence: resolve(%r) refused/failed (%s) — legacy path",
            label[:80], exc,
        )
        return None, str(reason)
    finally:
        state["active"] = False
        state["resolves"] += 1

    return resolution, ""
