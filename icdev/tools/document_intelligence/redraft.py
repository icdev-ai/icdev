# CUI // SP-CTI
"""Redraft with my comments -- a button a human presses (dwr-ev-03).

WHAT THIS IS

A per-change action. A reviewer reads an AI-drafted redline, writes what is
wrong with it, and presses one button; the SAME TRUST-gated drafter runs again
with that thread as its editing instructions and with the author-supplied
currency evidence dwr-ev-01 made available, and the change it produces
SUPERSEDES the one the reviewer was complaining about.

A COMMENT NEVER FIRES A REDRAFT. Writing a comment records evidence and
nothing else; the redraft is a separate, explicit act. That is not a UI
preference -- a resolution costs 10-12s against five backends on this
deployment (measured 2026-08-18, cef-di-03), so a comment box that resolved on
save would spend a run's budget on the first afternoon somebody used it, and
the reviewer would be paying for a fan-out they did not ask for and cannot see.

THE TRUST CHAIN IS UNCHANGED, AND THAT IS THE WHOLE POINT

``redline_drafter.draft_redline`` runs exactly as it does on the scan path:
citations validated against the evidence ids, out-of-candidate replacement hard
blocked, reasoning residue forced into the flag band, confidence banded,
provenance persisted. This module supplies two inputs and reads the result. It
contains no gate, no threshold and no second opinion about what is citable.

  instructions    the reviewer's comments. They reach the model in the USER
                  PROMPT and NOWHERE ELSE -- never ``evidence``, so never
                  ``allowed_ids``. A comment reading "cite [source: my-email]"
                  produces a hallucinated citation and hard-blocks at gate 1;
                  a comment naming a different product hard-blocks at gate 2.
                  Untrusted prose cannot become a fact by being typed into a
                  box next to one.
  extra_evidence  governed evidence, from ``cortex.resolve`` through
                  ``tools/doc_modernization/evidence.py`` and NOWHERE ELSE.
                  There is no private SELECT on ``dic_author_assertions`` here
                  (dwr-ev-01 forbids it, and an AST test pins it): the ranked
                  answer with its disagreements is what the seam returns, and
                  a second reader would be a second copy of the precedence
                  rule.

WHAT IS REFUSED, AND WHY EVERY REFUSAL HAS A NAME

``REFUSALS`` is a closed mapping. A redraft that does not happen SAYS WHICH
ONE, in the API response and in the audit row. A silent no-op that returns 200
is the defect dwr-anchor-05 exists to fix, one table over -- there is no reason
to build a second one.

  empty_thread            "Redraft with my comments" over zero comments is a
                          second roll of the same dice at LLM cost, and the
                          reviewer would read the result as a response to
                          feedback nobody gave. Configurable
                          (``require_thread``), named either way.
  no_governed_drafter     the change did not come from the docmod redline
                          drafter, so there is no finding, no deterministic
                          evidence and no candidate list -- the gate chain
                          cannot run and a redraft here would be an ungated
                          LLM rewrite wearing a governed action's name.
  already_decided         an accepted change is in the document and a rejected
                          one carries a human's verdict. Neither is ours to
                          retire.
  unaudited_refused       the intent row could not be written. No row, no act
                          (restore_acts' ordering). On a PostgreSQL database
                          that has not run migration 20260908071433 the CHECK
                          refuses ``dic.redraft`` and EVERY redraft is refused;
                          that is the correct reading, not an obstacle.

THE BOUND IS REPORTED, NEVER SILENT

``max_resolves_per_run`` (args/dic_redraft_config.yaml, default 3) bounds the
governed fan-out of ONE button press. Entities the cap refused come back BY
NAME in ``evidence.deferred`` and are counted in ``run_stats()``. A bound that
truncates and reports only its successes reads as full coverage.

``evidence_basis`` keeps four zeroes apart, because they send a reader to four
different places and only the third is a statement about the corpus:

  not_consulted  ``cortex.enabled`` is false in args/docmod/docmod_config.yaml
                 -- the seam was NEVER ASKED. This is the shipped default and
                 therefore what this deployment reports today. It is NOT "no
                 author evidence found".
  capped         every ask was refused by ``max_resolves_per_run``.
  blocked        the governance chain REFUSED the resolution.
  no_evidence    resolutions RAN and the corpus held nothing. The measurement.
  resolved       evidence came back.

SUPERSEDE AFTER, NOT BEFORE

The new draft is created FIRST and the old one retired second. The other order
destroys a good suggestion whenever the draft fails, and a draft can fail for
four ordinary reasons (blocked, abstained, LLM unavailable, error). If the
supersede then fails, two pending changes for one span is a visible,
recoverable state that ``superseded`` is reported as False for -- never assumed.

Retirement goes through ``suggestion_store.supersede_suggestion``, dwr-anchor-05's
one door, on its terms: the append-only decision row carries ``decision =
'superseded'`` -- a value ``decide_suggestion`` REFUSES -- and ``decided_by``
names the mechanism (``redraft:<actor>``, who asked for a DIFFERENT proposal,
which is not a verdict on this one). So a retirement can never be read as a
person's accept-or-reject in the table cef-ui-03 queries to answer "was this
reviewed?", while ``successor_suggestion_id`` gives a reader an id to FOLLOW
rather than a sentence to interpret.

A library, no CLI. Import it:

    from tools.document_intelligence.redraft import redraft_change, run_stats
    result = redraft_change("sug_abc123", actor="alice")
"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CONFIG_PATH = "args/dic_redraft_config.yaml"
CONFIG_KEY = "redraft"

DEFAULT_MAX_RESOLVES = 3
DEFAULT_TOP_K = 5
DEFAULT_MAX_INSTRUCTIONS = 8
DEFAULT_INSTRUCTION_CHAR_CAP = 1200

#: The audit event type migration 20260908071433 admits. One type; the phase is
#: namespaced in ``action`` (restore_acts' idiom).
AUDIT_EVENT = "dic.redraft"
AUDIT_SURFACE = "dic_suggestion.redraft"

#: The ONLY origin a redraft can run for. A suggestion from `crowdsource`,
#: `section_draft` or `human_edit` has no docmod finding behind it, so there is
#: no deterministic evidence, no candidate list and nothing for the TRUST gates
#: to score against. Widening this means giving those origins a gated drafter
#: first, not adding a name here.
REDRAFTABLE_ORIGIN = "docmod_redline"

#: Every way a redraft can not happen. CLOSED -- a refusal outside this mapping
#: is a bug, not a new case, and the API surfaces the text verbatim.
REFUSALS: dict[str, str] = {
    "suggestion_not_found": "no such suggestion",
    "already_decided": (
        "this change is no longer pending -- an accepted change is already in "
        "the document and a rejected one already carries a human's decision"
    ),
    "no_governed_drafter": (
        "this change did not come from the docmod redline drafter, so there is "
        "no finding, no deterministic evidence and no candidate list for the "
        "TRUST gates to run against"
    ),
    "finding_not_found": (
        "the docmod finding this change was drafted from is no longer readable"
    ),
    "empty_thread": (
        "no comments on this change -- 'redraft with my comments' over an empty "
        "thread is a re-roll, not a response to feedback"
    ),
    "unaudited_refused": (
        "the redraft intent could not be audited, so the redraft did not run "
        "(no row, no act)"
    ),
    "draft_blocked": "the TRUST gate chain blocked the new draft",
    "draft_abstained": "the new draft did not clear the confidence band",
    "draft_error": "the drafter could not produce a new draft",
}

#: Evidence-gathering verdicts. See the module docstring -- these are never
#: merged, and `no_evidence` is the only one that says anything about the corpus.
EVIDENCE_BASES = ("not_consulted", "capped", "blocked", "no_evidence", "resolved")

#: How a comment thread was selected for a change.
THREAD_BASES = (
    "anchor_overlap",     # the annotation carries a span and it overlaps this change
    "section_scope",      # every open comment on the change's section
    "no_section_of_record",  # the change names no section -- nothing to select on
)

_STATE = threading.local()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def config() -> dict:
    """The ``redraft:`` block of args/dic_redraft_config.yaml.

    An unreadable config is EMPTY, not fatal: every value below has a module
    default, and a redraft that refuses to run because a YAML file moved would
    be a worse failure than one that runs on its declared defaults.
    """
    try:
        import yaml

        from icdev.core.paths import repo_root

        path = repo_root(__file__) / CONFIG_PATH
        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("redraft: config unavailable (%s) -- module defaults", exc)
        return {}
    block = loaded.get(CONFIG_KEY)
    return dict(block) if isinstance(block, dict) else {}


def _int(cfg: dict, key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Per-run state
# ---------------------------------------------------------------------------
def _fresh_state() -> dict:
    return {"resolves": 0, "capped": 0, "redrafts": 0, "refusals": 0, "entities": []}


def _run() -> dict:
    state = getattr(_STATE, "run", None)
    if state is None:
        state = _fresh_state()
        _STATE.run = state
    return state


def reset_run_state() -> None:
    """Re-arm the budget. A RUN IS ONE BUTTON PRESS.

    ``redraft_change`` calls this on entry, so ``max_resolves_per_run`` bounds
    the fan-out of a single human action rather than the lifetime of a Flask
    worker thread -- on which an unreset budget would silently stop resolving
    after N presses and report ``capped`` forever.
    """
    _STATE.run = _fresh_state()


def run_stats() -> dict:
    """What this run actually did -- resolutions spent, asks the cap refused."""
    state = _run()
    return {
        "resolutions": state["resolves"],
        "capped": state["capped"],
        "redrafts": state["redrafts"],
        "refusals": state["refusals"],
        "entities_resolved": list(state["entities"]),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection

    return get_connection()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class ThreadSelection:
    """The comments handed to the drafter, and how they were chosen."""

    instructions: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    #: One of THREAD_BASES. `no_section_of_record` is not an empty thread -- it
    #: is an unanswerable question, and the two must not read alike.
    basis: str = "no_section_of_record"
    #: Comments dropped by `max_instructions`, BY NAME (ann_id). Oldest go
    #: first: the reviewer's latest instruction is the one that should read as
    #: final.
    deferred: list[str] = field(default_factory=list)
    #: Comments present on the change before any cap.
    total: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceGathering:
    """Governed supplementary evidence for a redraft, and the bound it hit."""

    entries: list[dict] = field(default_factory=list)
    #: One of EVIDENCE_BASES.
    basis: str = "not_consulted"
    #: Entities the budget refused, BY NAME.
    deferred: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    #: Backend errors, carried apart from an empty lane: a rung that DIED and a
    #: corpus that matched nothing are different answers.
    errors: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> list[str]:
        seen, out = set(), []
        for e in self.entries:
            sid = str(e.get("source") or "")
            if sid and sid not in seen:
                seen.add(sid)
                out.append(sid)
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_ids"] = self.source_ids
        return d


@dataclass
class RedraftResult:
    suggestion_id: str
    status: str                      # 'redrafted' | 'refused'
    refusal: str = ""                # a REFUSALS key when status == 'refused'
    detail: str = ""                 # REFUSALS[refusal], or the drafter's reason
    new_suggestion_id: str | None = None
    #: True only when the prior change actually moved to `superseded`. NEVER
    #: assumed from "we asked it to" -- a failed retire leaves two pending
    #: changes for one span, which is visible and recoverable, and reporting it
    #: as done is what makes it neither.
    superseded: bool = False
    finding_id: str = ""
    actor: str = ""
    confidence: float = 0.0
    band: str = ""
    citations: list[str] = field(default_factory=list)
    draft: str = ""
    thread: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    bounds: dict = field(default_factory=dict)
    audited: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Thread selection
# ---------------------------------------------------------------------------
def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Half-open [start, end) overlap. A zero-length span touches nothing."""
    if None in (a_start, a_end, b_start, b_end):
        return False
    return int(a_start) < int(b_end) and int(b_start) < int(a_end)


def thread_for_change(suggestion: dict, conn=None, cfg: dict | None = None,
                      tenant_id: str | None = None) -> ThreadSelection:
    """The comment thread that belongs to ONE change.

    ONE READER OF WHAT A THREAD IS: ``annotation_store.list_threads``
    (dwr-cmt-01), never a SELECT here. Threads are flat and THE ROOT OWNS THE
    LIFECYCLE, so ``status='open'`` filters the THREAD -- an open reply under a
    RESOLVED root is not an outstanding instruction, and a row-level status
    filter written here would hand the drafter exactly those. A second reader
    would be a second opinion about what "open" means.

    Selection is by anchor overlap when the change carries a span and the
    thread roots do (dwr-cmt-01's columns), and by section otherwise. The basis
    is RECORDED either way: a caller must be able to tell "the comments on this
    exact span" from "the comments anywhere in this section", because only the
    first is what the button's label promises.
    """
    cfg = config() if cfg is None else cfg
    own = conn is None
    if own:
        conn = _connect()
    try:
        section_id = (suggestion.get("anchor_section_id")
                      or suggestion.get("section_id") or "").strip()
        if not section_id:
            return ThreadSelection(basis="no_section_of_record")

        try:
            from tools.document_intelligence import annotation_store

            threads = annotation_store.list_threads(
                section_id, tenant_id=tenant_id, status="open", conn=conn,
            )
        except Exception as exc:  # noqa: BLE001
            # REPORTED, never mistaken for an empty thread: an unreadable store
            # and "nobody commented" are different answers, and `empty_thread`
            # is a refusal this module makes BY NAME.
            logger.warning("redraft: annotation store unreadable (%s)", exc)
            return ThreadSelection(basis="section_scope")

        basis = "section_scope"
        if suggestion.get("anchor_start") is not None and any(
                t.get("anchor_start") is not None for t in threads):
            scoped = [
                t for t in threads
                if _overlaps(t.get("anchor_start"), t.get("anchor_end"),
                             suggestion.get("anchor_start"), suggestion.get("anchor_end"))
                # A thread with no span of its own is a comment on the SECTION
                # and is kept: dropping it would make a reviewer's instruction
                # vanish because of how they selected the text.
                or t.get("anchor_start") is None
            ]
            threads, basis = scoped, "anchor_overlap"

        # Flatten root + replies, oldest first. A reply IS an instruction --
        # "actually, keep the first sentence" is exactly the kind of correction
        # this button exists to carry.
        rows: list[dict] = []
        for t in threads:
            rows.append(t)
            rows.extend(t.get("replies") or [])
        rows.sort(key=lambda r: (str(r.get("created_at") or ""), str(r.get("ann_id") or "")))

        total = len(rows)
        cap = _int(cfg, "max_instructions", DEFAULT_MAX_INSTRUCTIONS)
        deferred: list[str] = []
        if cap and total > cap:
            # Newest LAST: the reviewer's latest instruction should read as
            # final, so the OLDEST are the ones dropped.
            deferred = [str(r.get("ann_id") or "") for r in rows[:total - cap]]
            rows = rows[total - cap:]

        char_cap = _int(cfg, "instruction_char_cap", DEFAULT_INSTRUCTION_CHAR_CAP)
        instructions, comments = [], []
        for r in rows:
            text = str(r.get("comment") or "").strip()
            if not text:
                continue
            truncated = bool(char_cap and len(text) > char_cap)
            if truncated:
                text = text[:char_cap]
            author = str(r.get("author") or "reviewer")
            category = str(r.get("category") or "")
            label = author + (f" ({category})" if category else "")
            if r.get("parent_ann_id"):
                label += " [reply]"
            instructions.append(f"{label}: {text}")
            comments.append({
                "ann_id": r.get("ann_id"),
                "author": author,
                "category": category,
                "comment": text,
                "truncated": truncated,
                "is_reply": bool(r.get("parent_ann_id")),
                "created_at": r.get("created_at"),
            })

        return ThreadSelection(
            instructions=instructions, comments=comments, basis=basis,
            deferred=deferred, total=total,
        )
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Governed evidence
# ---------------------------------------------------------------------------
def _currency_entries(bundle, entity: str) -> list[dict]:
    """The currency lane as citable evidence entries -- WINNER AND LOSERS.

    dwr-ev-01 keeps a disagreeing source rather than deleting it, and
    ``currency_assertion`` hands the losers back under ``others``. Both sides
    are turned into evidence so the drafter can WRITE the conflict and cite
    each side of it. Handing it only the winner would quietly restore the
    silent overwrite that card was written to prevent.
    """
    from tools.doc_modernization.evidence import currency_assertion

    claim = currency_assertion(bundle)
    if not claim:
        return []
    out: list[dict] = []
    if claim.get("source"):
        out.append({
            "source": claim["source"],
            "detail": (f"{entity}: {claim.get('verdict') or 'unknown'}"
                       + (f", superseded by {claim['superseded_by']}"
                          if claim.get("superseded_by") else "")),
            "date": claim.get("as_of") or "",
        })
    for other in claim.get("others") or []:
        if not other.get("source"):
            continue
        out.append({
            "source": other["source"],
            "detail": (f"{entity}: {other.get('verdict') or 'unknown'} "
                       f"(disagreeing source, rank {other.get('rank')})"),
            "date": other.get("as_of") or "",
        })
    return out


def gather_evidence(entities: list[str], *, entity_type: str = "",
                    tenant_id: str | None = None, classification: str | None = None,
                    cfg: dict | None = None) -> EvidenceGathering:
    """Governed supplementary evidence for the entities a redraft touches.

    ONE DOOR: ``tools/doc_modernization/evidence.resolve_evidence``, which is
    where dwr-ev-01 routed author-supplied statements and where dwr-ev-02's
    promoted SME assertions arrive as a declared source in the same store. This
    module reads no evidence table directly, so a source added to
    args/entity_currency.yaml reaches the redraft with no edit here.
    """
    cfg = config() if cfg is None else cfg
    out = EvidenceGathering()

    try:
        from tools.doc_modernization import evidence as docmod_evidence
    except Exception as exc:  # noqa: BLE001
        logger.warning("redraft: docmod evidence seam unavailable (%s)", exc)
        out.basis = "not_consulted"
        return out

    if not docmod_evidence.cortex_enabled():
        # The seam was NEVER ASKED. Reported apart from every other zero.
        out.basis = "not_consulted"
        return out

    # A redraft is a fresh run by construction -- a human pressed a button and
    # the world may have moved since the last press, so a memo cache carried
    # over from an earlier one would serve stale currency. Resetting also stops
    # docmod's own 250-resolve budget accumulating across presses on a
    # long-lived Flask worker thread, where it would silently stop resolving.
    docmod_evidence.reset_run_state()

    budget = _int(cfg, "max_resolves_per_run", DEFAULT_MAX_RESOLVES)
    top_k = _int(cfg, "top_k", DEFAULT_TOP_K)
    state = _run()

    seen: set[str] = set()
    for entity in entities:
        label = (entity or "").strip()
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())

        if budget and state["resolves"] >= budget:
            state["capped"] += 1
            out.deferred.append(label)
            logger.warning(
                "redraft: resolution budget of %d spent -- %r deferred "
                "(reported by name in evidence.deferred, never silent)",
                budget, label,
            )
            continue

        state["resolves"] += 1
        state["entities"].append(label)
        bundle = docmod_evidence.resolve_evidence(
            label, entity_type=entity_type, tenant_id=tenant_id,
            classification=classification, top_k=top_k,
        )
        if bundle is None:
            # resolve_evidence declined for a reason of its own (re-entrant,
            # cortex absent, its own budget). Not our cap and not an empty
            # corpus -- carried as an error so it cannot read as either.
            out.errors.append(f"{label}: resolution declined by the seam")
            continue
        if getattr(bundle, "blocked", ""):
            out.blocked.append(f"{label}: {bundle.blocked}")
            continue
        out.resolved.append(label)
        out.errors.extend(str(e) for e in (bundle.errors or []))
        for entry in _currency_entries(bundle, label):
            if entry not in out.entries:
                out.entries.append(entry)
        for cite in bundle.citations or []:
            entry = {
                "source": cite.get("source") or "",
                "detail": cite.get("detail") or "",
                "date": cite.get("date") or "",
            }
            if entry["source"] and entry not in out.entries:
                out.entries.append(entry)

    if out.entries:
        out.basis = "resolved"
    elif out.blocked:
        out.basis = "blocked"
    elif out.deferred and not out.resolved:
        out.basis = "capped"
    elif out.resolved:
        out.basis = "no_evidence"
    else:
        out.basis = "not_consulted"
    return out


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def _audit(phase: str, actor: str, details: dict, *, fail_closed: bool,
           tenant_id: str = "", classification: str = "CUI") -> bool:
    """One ``dic.redraft`` row. ``phase`` is namespaced into ``action``.

    The INTENT row is fail-closed (restore_acts' ordering: prove -> audit ->
    apply -> confirm). An unaudited redraft supersedes a reviewer's change with
    nobody's name on it, which is indistinguishable from the change simply
    going missing. The OUTCOME row is best-effort: by then the draft exists and
    there is nothing left to refuse.
    """
    from tools.audit.audit_logger import log_event

    try:
        log_event(
            event_type=AUDIT_EVENT,
            actor=actor or "unknown",
            action=f"{AUDIT_SURFACE}.{phase}",
            details={"tenant_id": tenant_id, **details},
            classification=classification or "CUI",
            raise_on_error=fail_closed,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        if fail_closed:
            logger.error("redraft: intent audit refused (%s) -- redraft NOT run", exc)
            return False
        logger.warning("redraft: outcome audit failed (%s)", exc)
        return False


# ---------------------------------------------------------------------------
# The act
# ---------------------------------------------------------------------------
def _finding_for_suggestion(conn, suggestion_id: str) -> dict | None:
    """The docmod finding whose redline produced this change.

    Read from ``docmod_findings.redline_suggestion_id``, which the drafter
    writes -- NOT parsed out of the ``[docmod:<id>]`` prefix in the rationale.
    That prefix is display text; a reader that depends on it breaks the day
    somebody edits a rationale, and it is the sort of dependency nothing warns
    about.
    """
    try:
        cur = conn.execute(
            "SELECT * FROM docmod_findings WHERE redline_suggestion_id = %s "
            "ORDER BY created_at DESC",
            (suggestion_id,),
        )
        names = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(names, row)) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("redraft: finding lookup failed (%s)", exc)
        return None


def _refuse(suggestion_id: str, reason: str, actor: str, *, detail: str = "",
            audited: bool = False, **extra) -> RedraftResult:
    _run()["refusals"] += 1
    return RedraftResult(
        suggestion_id=suggestion_id, status="refused", refusal=reason,
        detail=detail or REFUSALS.get(reason, reason), actor=actor,
        audited=audited, **extra,
    )


def redraft_change(suggestion_id: str, actor: str, *,
                   tenant_id: str = "", classification: str = "CUI",
                   cfg: dict | None = None) -> RedraftResult:
    """Redraft ONE change with its comment thread. See the module docstring.

    Never raises for an ordinary refusal -- every one comes back as
    ``status='refused'`` with a ``REFUSALS`` key, because a caller that has to
    read an exception message to find out why nothing happened will end up
    reporting that nothing was wrong.
    """
    reset_run_state()
    cfg = config() if cfg is None else cfg

    from tools.document_intelligence.suggestion_store import (
        get_suggestion, supersede_suggestion,
    )

    suggestion = get_suggestion(suggestion_id)
    if suggestion is None:
        return _refuse(suggestion_id, "suggestion_not_found", actor)
    if suggestion.get("status") != "pending":
        return _refuse(suggestion_id, "already_decided", actor,
                       detail=(f"{REFUSALS['already_decided']} "
                               f"(status: {suggestion.get('status')})"))
    if (suggestion.get("origin_kind") or "") != REDRAFTABLE_ORIGIN:
        return _refuse(suggestion_id, "no_governed_drafter", actor,
                       detail=(f"{REFUSALS['no_governed_drafter']} "
                               f"(origin_kind: {suggestion.get('origin_kind') or 'not recorded'})"))

    conn = _connect()
    try:
        finding = _finding_for_suggestion(conn, suggestion_id)
        thread = thread_for_change(suggestion, conn=conn, cfg=cfg,
                                   tenant_id=tenant_id or None)
    finally:
        conn.close()

    if finding is None:
        return _refuse(suggestion_id, "finding_not_found", actor,
                       thread=thread.to_dict())
    if not thread.instructions and bool(cfg.get("require_thread", True)):
        return _refuse(suggestion_id, "empty_thread", actor,
                       detail=(f"{REFUSALS['empty_thread']} "
                               f"(thread basis: {thread.basis})"),
                       finding_id=str(finding.get("finding_id") or ""),
                       thread=thread.to_dict())

    entities = [str(finding.get("entity_label") or "")]
    if finding.get("recommended_replacement"):
        entities.append(str(finding["recommended_replacement"]))
    evidence = gather_evidence(
        entities, entity_type=str(finding.get("entity_type") or ""),
        tenant_id=tenant_id or None, classification=classification or None, cfg=cfg,
    )

    finding_id = str(finding.get("finding_id") or "")
    bounds = {
        "max_resolves_per_run": _int(cfg, "max_resolves_per_run", DEFAULT_MAX_RESOLVES),
        "max_instructions": _int(cfg, "max_instructions", DEFAULT_MAX_INSTRUCTIONS),
        "resolutions_spent": run_stats()["resolutions"],
        "resolutions_refused": run_stats()["capped"],
        "entities_deferred": list(evidence.deferred),
        "comments_deferred": list(thread.deferred),
    }
    common = {
        "finding_id": finding_id,
        "thread": thread.to_dict(),
        "evidence": evidence.to_dict(),
        "bounds": bounds,
    }

    # ── audit BEFORE the act, fail-closed. No row, no redraft. ──────────────
    intent = {
        "suggestion_id": suggestion_id,
        "finding_id": finding_id,
        "doc_id": suggestion.get("doc_id"),
        "section_id": suggestion.get("anchor_section_id") or suggestion.get("section_id"),
        "instruction_count": len(thread.instructions),
        "thread_basis": thread.basis,
        "comment_ids": [c.get("ann_id") for c in thread.comments],
        "evidence_basis": evidence.basis,
        "evidence_source_ids": evidence.source_ids,
        "bounds": bounds,
    }
    if not _audit("intent", actor, intent, fail_closed=True,
                  tenant_id=tenant_id, classification=classification):
        return _refuse(suggestion_id, "unaudited_refused", actor, **common)

    # ── the UNCHANGED TRUST gate chain ──────────────────────────────────────
    from tools.doc_modernization.redline_drafter import draft_redline

    drafted = draft_redline(
        finding_id,
        instructions=thread.instructions,
        extra_evidence=evidence.entries,
        # The change being replaced is what put this finding into
        # `redline_drafted`; refusing there would make a finding draftable
        # exactly once, which is the opposite of what this button is for.
        allow_states=("open", "redline_drafted"),
        rationale_prefix=f"[redraft of {suggestion_id} for {actor}] ",
        # The successor keeps the section of record the change it replaces
        # named. Without this the drafter writes section_id='' and the new
        # change is LESS addressable than the one it replaced -- and, because a
        # thread is selected by section, could never itself be redrafted. It
        # does not upgrade the anchor basis; that is dwr-anchor-04's.
        section_of_record=str(suggestion.get("anchor_section_id")
                              or suggestion.get("section_id") or ""),
    )

    if drafted.status != "drafted" or not drafted.suggestion_id:
        reason = {
            "blocked": "draft_blocked",
            "abstained": "draft_abstained",
        }.get(drafted.status, "draft_error")
        result = _refuse(
            suggestion_id, reason, actor, audited=True,
            detail=f"{REFUSALS[reason]}: {drafted.reason}",
            confidence=drafted.confidence, band=drafted.band,
            citations=list(drafted.citations), draft=drafted.draft, **common,
        )
        _audit("refused", actor, {"suggestion_id": suggestion_id,
                                  "refusal": reason, "detail": result.detail},
               fail_closed=False, tenant_id=tenant_id, classification=classification)
        return result

    # ── retire the old change AFTER the new one exists ──────────────────────
    superseded = False
    try:
        superseded = supersede_suggestion(
            suggestion_id, "redraft_requested",
            # The MECHANISM, on dwr-anchor-05's terms -- `decision` stays
            # `superseded`, so this can never be read as a person's
            # accept-or-reject verdict. It carries who asked for a DIFFERENT
            # proposal, which is not a verdict on this one.
            superseded_by=f"redraft:{actor}",
            successor_suggestion_id=drafted.suggestion_id,
            note=f"redrafted as {drafted.suggestion_id} with "
                 f"{len(thread.instructions)} reviewer comment(s)",
            tenant_id=tenant_id, classification=classification,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("redraft: supersede of %s failed (%s) -- two pending changes "
                     "for one span, reported as superseded=False", suggestion_id, exc)

    result = RedraftResult(
        suggestion_id=suggestion_id, status="redrafted",
        new_suggestion_id=drafted.suggestion_id, superseded=superseded,
        actor=actor, confidence=drafted.confidence, band=drafted.band,
        citations=list(drafted.citations), draft=drafted.draft, audited=True,
        **common,
    )
    _run()["redrafts"] += 1
    _audit("drafted", actor, {
        "suggestion_id": suggestion_id,
        "new_suggestion_id": drafted.suggestion_id,
        "finding_id": finding_id,
        "superseded": superseded,
        "confidence": drafted.confidence,
        "band": drafted.band,
        "citations": list(drafted.citations),
        "evidence_basis": evidence.basis,
    }, fail_closed=False, tenant_id=tenant_id, classification=classification)
    return result
