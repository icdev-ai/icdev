# CUI // SP-CTI
"""Re-draft the suggestions that were written against a token (dwr-anchor-06).

WHAT WENT WRONG. ``redline_drafter.draft_redline`` was handed
``finding["entity_label"]`` -- a bare token like ``TLS 1.1`` -- as the passage to
rewrite, and stored ``section_id=""`` with ``anchor_basis="unanchored"``. The
model wrote a replacement having NEVER SEEN the surrounding sentence, so the
prose was not fitted to where it lands, and the row named no span to land it in.
Measured on the live PG board 2026-09-08: 58 pending ``dic_suggestions``, 58 with
an empty ``section_id``, 58 with a NULL ``anchor_basis``, all
``canvas_source='doc_modernization'``.

THOSE ROWS CANNOT BE REPAIRED IN PLACE. Back-filling an anchor would place text
that was never fitted to the span it is being pinned to -- a plausible-looking
edit nobody wrote. So each is SUPERSEDED and its finding is re-drafted from a
real passage, through the UNCHANGED TRUST gate chain in ``draft_redline``.

  prove -> act -> confirm, and the PROVE is the whole safety argument.

``probe`` re-derives, per suggestion, whether a re-draft can actually produce an
ANCHORED row, and nothing is superseded for a draft that structurally cannot
happen. Superseding without a replacement would trade 58 misleading proposals
for 58 absent ones and call it progress.

FIVE THINGS A RE-DRAFT NEEDS, each asked separately because each sends you to a
different fix, and each reported by name:

  drafter_does_not_anchor  the INSTALLED ``redline_drafter`` has no
                           ``resolve_passage`` -- it predates dwr-anchor-04 and
                           still writes ``section_id=""``. Re-running it would
                           mint fresh copies of the defect. Refused for the
                           WHOLE run, never per item.
  origin_unresolved        the suggestion's finding cannot be identified.
  origin_not_open          the finding's own row is not ``open``, so
                           ``draft_redline`` would refuse it.
  finding_has_no_span      the finding carries no ``anchor_start``/``anchor_end``.
                           dwr-anchor-01/02 made packs record one; a finding
                           written BEFORE that scan has none, and no re-draft can
                           invent it. THE FIX IS A RE-SCAN, not a re-draft.
  doc_has_no_sections      the document has no ``dic_sections`` row, so there is
                           nowhere for an anchor to point. THE FIX IS
                           ``section_deriver``, not a re-draft.

MEASURED ON THE LIVE BOARD 2026-09-08, and the numbers are the finding:
127 of 127 ``docmod_findings`` carry a NULL span, and the document holding 47 of
the 58 suggestions (``dic_doc_28e2ee4d984f3f35``) carries ZERO sections. So on
this board today the answer is 58 refusals and ZERO tokens spent -- which is the
correct outcome and NOT a clean bill of health. Do not "fix" it by relaxing a
precondition.

THE LOOP IS CLOSED BY CONSTRUCTION, not by a visited-set. A re-drafted row can
only re-enter this tool's target set by being unanchored, and
``suggestion_store.validate_anchor`` refuses to write an ``exact``/``relocated``
basis without a section -- so a written row is anchored or was never written.
The pre-04 drafter, which WOULD write an unanchored row, is refused at the run
level above. Pinned by test.

THE SUPERSEDING ROW IS APPEND-ONLY. ``supersede_suggestion`` (dwr-anchor-05,
reused -- never a second UPDATE) appends to ``dic_suggestion_decisions`` with
``decision='superseded'`` and ``decided_by`` naming the MECHANISM, so no reader
can mistake it for a person's verdict.

BOUNDED, AND THE BOUND IS REPORTED. ``max_redrafts_per_run``
(args/docmod/docmod_config.yaml, default 10) caps the LLM calls; deferred items
come back NAMED in ``deferred``, never as a silent truncation. Every outcome is
counted -- ``redrafted``, ``abstained``, ``blocked``, ``error``,
``redrafted_unanchored`` -- because a report of successes alone cannot say
whether the sweep worked.

CLI (DRY RUN FIRST -- ``--apply`` is the only thing that writes)::

    python -m tools.document_intelligence.suggestion_redraft --census
    python -m tools.document_intelligence.suggestion_redraft --plan [--json]
    python -m tools.document_intelligence.suggestion_redraft --apply [--limit N]
    python -m tools.document_intelligence.suggestion_redraft --apply --json

Exit 2 = the survey could not be produced, which is never the same as a clean
survey.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from tools.document_intelligence.suggestion_store import (
    APPLIABLE_BASES,
    get_suggestion,
    supersede_suggestion,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: ``decided_by`` on every superseding row this tool writes. Names the
#: mechanism, never a human -- the dwr-anchor-05 rule.
REDRAFT_ACTOR = "system:suggestion_redraft"

#: The reason recorded on the append-only decision row.
SUPERSEDE_REASON = "unanchored_draft"

#: The canvas whose rows this tool owns. A suggestion from any other surface was
#: not written by ``draft_redline`` and has no docmod finding to re-draft.
CANVAS_SOURCE = "doc_modernization"

#: ``[docmod:<finding_id>]`` -- the prefix ``draft_redline`` writes on every
#: rationale. The FALLBACK route to the origin finding; the structured
#: ``docmod_findings.redline_suggestion_id`` link is asked first.
_RATIONALE_FINDING = re.compile(r"\[docmod:([^\]\s]+)\]")

#: The one verdict that is acted on.
REDRAFTABLE = "redraftable"

#: Every verdict ``probe`` can refuse with. Enumerated so a reader can see the
#: whole set, and so a future verdict cannot be added silently.
REFUSALS = (
    "drafter_does_not_anchor",
    "origin_unresolved",
    "origin_not_open",
    "finding_has_no_span",
    "doc_has_no_sections",
)

DEFAULT_MAX_PER_RUN = 10


# -- Capability of the INSTALLED drafter --------------------------------------

def drafter_anchors() -> tuple[bool, str]:
    """Does the ``redline_drafter`` on this tree anchor what it writes?

    ``resolve_passage`` is dwr-anchor-04's seam: it is what turns an entity
    label into the sentence around it and proves a basis against the live
    section. A drafter without it writes ``section_id=""`` and
    ``anchor_basis="unanchored"`` -- exactly the rows this tool exists to
    replace -- so a run against it is refused rather than executed.

    Asked of the MODULE, never assumed from the tree: this tool has to give the
    right answer both before and after dwr-anchor-04 lands.
    """
    try:
        from tools.doc_modernization import redline_drafter
    except Exception as exc:                                  # pragma: no cover
        return False, f"redline_drafter is not importable: {exc}"
    if not hasattr(redline_drafter, "resolve_passage"):
        return False, ("the installed redline_drafter has no resolve_passage "
                       "(it predates dwr-anchor-04) and would write another "
                       "unanchored suggestion")
    return True, "resolve_passage present"


# -- Census -------------------------------------------------------------------

def basis_census(conn=None) -> dict:
    """Count ``dic_suggestions`` by status x anchor basis x anchored-ness.

    The before/after measurement. ``anchored`` is the question the board is
    actually asked -- an APPLIABLE basis AND a section to apply it to -- because
    either half alone is a row nothing can splice. Counted in Python from the
    raw rows so the two backends cannot disagree about a NULL.
    """
    own = conn is None
    if own:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT status, anchor_basis, section_id, anchor_section_id, "
            "canvas_source FROM dic_suggestions"
        ).fetchall()]
    finally:
        if own:
            conn.close()

    by_status: dict[str, dict] = {}
    for r in rows:
        status = r.get("status") or "<null>"
        basis = r.get("anchor_basis") or "<null>"
        bucket = by_status.setdefault(status, {"total": 0, "anchored": 0,
                                               "empty_section_id": 0, "by_basis": {}})
        bucket["total"] += 1
        bucket["by_basis"][basis] = bucket["by_basis"].get(basis, 0) + 1
        if not (r.get("section_id") or ""):
            bucket["empty_section_id"] += 1
        if basis in APPLIABLE_BASES and (r.get("anchor_section_id") or ""):
            bucket["anchored"] += 1
    return {"total": len(rows), "by_status": by_status}


# -- Targets and their origin findings ----------------------------------------

def is_target(suggestion: dict) -> bool:
    """A PENDING docmod suggestion that nothing can apply.

    Unanchored means EITHER the basis is not appliable OR there is no section of
    record -- both halves, because a basis without a section and a section
    without a basis are each unappliable, and dwr-anchor-05's accept path
    refuses both.
    """
    if (suggestion.get("status") or "") != "pending":
        return False
    if (suggestion.get("canvas_source") or "") != CANVAS_SOURCE:
        return False
    basis = suggestion.get("anchor_basis")
    section = suggestion.get("anchor_section_id") or suggestion.get("section_id") or ""
    return basis not in APPLIABLE_BASES or not section


def _targets(conn) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM dic_suggestions WHERE status = %s AND canvas_source = %s "
        "ORDER BY created_at",
        ("pending", CANVAS_SOURCE),
    ).fetchall()]
    return [r for r in rows if is_target(r)]


def _rollback(conn) -> None:
    """PG poisons a transaction after a failed statement; SQLite does not care."""
    try:
        conn.rollback()
    except Exception:
        pass


def origin_finding(conn, suggestion: dict) -> tuple[dict | None, str, str]:
    """``(finding_row, route, reason)`` for the finding a suggestion was drafted from.

    TWO INDEPENDENT ROUTES, structured first:

      ``link``       ``docmod_findings.redline_suggestion_id`` names this
                     suggestion; that row is the ``redline_drafted`` SUCCESSOR,
                     and its ``supersedes_id`` is the finding to re-draft.
      ``rationale``  the ``[docmod:<id>]`` prefix ``draft_redline`` writes.

    The routes are cross-checked when both answer: a disagreement is
    ``origin_unresolved``, never a pick between two. ``draft_redline`` requires
    the ORIGIN row (state ``open``), not the successor -- re-drafting the
    successor would refuse on state, so which row is returned is load-bearing.
    """
    sid = suggestion.get("suggestion_id")
    linked_origin = None
    try:
        row = conn.execute(
            "SELECT supersedes_id FROM docmod_findings WHERE redline_suggestion_id = %s "
            "ORDER BY created_at DESC",
            (sid,),
        ).fetchone()
        if row is not None:
            linked_origin = dict(row).get("supersedes_id") or None
    except Exception as exc:
        logger.warning("suggestion_redraft: finding-link read failed for %s: %s", sid, exc)
        _rollback(conn)

    m = _RATIONALE_FINDING.search(suggestion.get("rationale") or "")
    rationale_origin = m.group(1) if m else None

    if linked_origin and rationale_origin and linked_origin != rationale_origin:
        return None, "conflict", (f"the finding link names {linked_origin} and the "
                                  f"rationale names {rationale_origin}")
    finding_id = linked_origin or rationale_origin
    route = "link" if linked_origin else ("rationale" if rationale_origin else "none")
    if not finding_id:
        return None, route, "no finding link and no [docmod:...] rationale prefix"

    try:
        row = conn.execute(
            "SELECT * FROM docmod_findings WHERE finding_id = %s", (finding_id,)
        ).fetchone()
    except Exception as exc:
        logger.warning("suggestion_redraft: finding read failed for %s: %s", finding_id, exc)
        _rollback(conn)
        return None, route, f"finding {finding_id} could not be read"
    if row is None:
        return None, route, f"finding {finding_id} is not on this board"
    return dict(row), route, ""


def _doc_section_count(conn, doc_id: str | None) -> int | None:
    """How many ``dic_sections`` rows this document has. None is UNREADABLE."""
    if not doc_id:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM dic_sections WHERE doc_id = %s", (doc_id,)
        ).fetchone()
    except Exception as exc:
        logger.warning("suggestion_redraft: section count failed for %s: %s", doc_id, exc)
        _rollback(conn)
        return None
    d = dict(row) if row is not None else {}
    return int(d.get("n") or 0)


# -- Probe --------------------------------------------------------------------

def probe(conn, suggestion: dict, *, anchors: bool | None = None) -> dict:
    """Can this suggestion be re-drafted into an ANCHORED row? Writes nothing.

    Every precondition is asked of PRIMARY data and answered by name. The order
    is most-general first, so an item reports the reason a reader can act on: a
    drafter that cannot anchor is a merge, a missing span is a re-scan, a
    missing section is ``section_deriver``.

    An UNREADABLE section count is not a refusal -- ``resolve_passage`` asks the
    same question again and refuses for itself. Guessing "no sections" from a
    failed read would refuse an anchorable finding.
    """
    if anchors is None:
        anchors, _ = drafter_anchors()
    out = {
        "suggestion_id": suggestion.get("suggestion_id"),
        "doc_id": suggestion.get("doc_id"),
        "current_content": suggestion.get("current_content"),
        "finding_id": None,
        "origin_route": None,
        "finding_span": None,
        "doc_sections": None,
        "verdict": None,
        "reason": "",
    }
    if not anchors:
        _, why = drafter_anchors()
        out["verdict"] = "drafter_does_not_anchor"
        out["reason"] = why
        return out

    finding, route, reason = origin_finding(conn, suggestion)
    out["origin_route"] = route
    if finding is None:
        out["verdict"] = "origin_unresolved"
        out["reason"] = reason
        return out
    out["finding_id"] = finding.get("finding_id")

    if (finding.get("state") or "") != "open":
        out["verdict"] = "origin_not_open"
        out["reason"] = (f"finding state is {finding.get('state')!r}; draft_redline "
                         "only drafts an open finding")
        return out

    start, end = finding.get("anchor_start"), finding.get("anchor_end")
    out["finding_span"] = None if start is None or end is None else [start, end]
    if out["finding_span"] is None:
        out["verdict"] = "finding_has_no_span"
        out["reason"] = ("the finding carries no anchor_start/anchor_end -- it was "
                         "written before the packs recorded one (dwr-anchor-01/02). "
                         "Re-scan the document; a re-draft cannot invent a span")
        return out

    sections = _doc_section_count(conn, finding.get("doc_id") or suggestion.get("doc_id"))
    out["doc_sections"] = sections
    if sections == 0:
        out["verdict"] = "doc_has_no_sections"
        out["reason"] = ("the document carries no dic_sections row, so an anchor has "
                         "nowhere to point. Derive its sections (dwr-sect-01)")
        return out

    out["verdict"] = REDRAFTABLE
    out["reason"] = "origin finding is open, carries a span, and its document has sections"
    return out


# -- Plan ---------------------------------------------------------------------

def _cap(limit: int | None) -> int:
    if limit is not None:
        return max(0, int(limit))
    try:
        from tools.doc_modernization.pack_loader import load_config
        return int(load_config().get("max_redrafts_per_run") or DEFAULT_MAX_PER_RUN)
    except Exception:
        return DEFAULT_MAX_PER_RUN


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def plan(limit: int | None = None) -> dict:
    """Probe every target and report what a run WOULD do. Acts on nothing."""
    from tools.db.storage import get_connection

    anchors, anchors_reason = drafter_anchors()
    items: list[dict] = []
    with get_connection() as conn:
        before = basis_census(conn)
        for suggestion in _targets(conn):
            items.append(probe(conn, suggestion, anchors=anchors))

    cap = _cap(limit)
    redraftable = [i for i in items if i["verdict"] == REDRAFTABLE]
    by_verdict: dict[str, int] = {}
    for i in items:
        by_verdict[i["verdict"]] = by_verdict.get(i["verdict"], 0) + 1
    return {
        "measured_at": _now(),
        "drafter_anchors": anchors,
        "drafter_reason": anchors_reason,
        "targets": len(items),
        "redraftable": len(redraftable),
        "deferred": [i["suggestion_id"] for i in redraftable[cap:]],
        "max_per_run": cap,
        "by_verdict": by_verdict,
        "items": items,
        "census_before": before,
    }


# -- Apply --------------------------------------------------------------------

def redraft(limit: int | None = None, *, apply: bool = False) -> dict:
    """Supersede each unappliable docmod suggestion and re-draft its finding.

    ``apply=False`` (the default) runs the whole probe and reports; it opens no
    write and spends no token. With ``apply=True``, per item, in this order:

      1. ``supersede_suggestion`` -- one append-only decision row, mechanism-named
      2. ``draft_redline`` on the ORIGIN finding, through the UNCHANGED TRUST
         gate chain (its own connection, exactly as the sweep calls it)
      3. re-read the new row and CONFIRM it is anchored

    Superseding is first because a finding with two pending proposals is worse
    than a finding with none: the old row is provably unappliable, so nothing
    appliable is at risk, and a draft that abstains or blocks leaves the finding
    open for the next sweep. A supersede that lands with no replacement is
    reported by the draft's own status, never as a success.
    """
    report = plan(limit=limit)
    report["applied"] = bool(apply)
    cap = report["max_per_run"]
    queue = [i for i in report["items"] if i["verdict"] == REDRAFTABLE][:cap]
    outcomes: list[dict] = []

    for item in queue:
        sid, fid = item["suggestion_id"], item["finding_id"]
        entry = {"suggestion_id": sid, "finding_id": fid, "outcome": None,
                 "reason": "", "new_suggestion_id": None, "new_anchor_basis": None,
                 "new_section_id": None}
        if not apply:
            entry["outcome"] = "would_redraft"
            outcomes.append(entry)
            continue

        try:
            superseded = supersede_suggestion(
                sid, SUPERSEDE_REASON, superseded_by=REDRAFT_ACTOR,
                note=("drafted against the entity label with no span (dwr-anchor-06); "
                      "re-drafted from the passage"),
            )
        except Exception as exc:
            entry["outcome"] = "error"
            entry["reason"] = f"supersede failed: {exc}"
            outcomes.append(entry)
            continue
        if not superseded:
            entry["outcome"] = "supersede_refused"
            entry["reason"] = "no longer pending -- a concurrent decision wins"
            outcomes.append(entry)
            continue

        try:
            from tools.doc_modernization.redline_drafter import draft_redline
            result = draft_redline(fid).to_dict()
        except Exception as exc:
            entry["outcome"] = "error"
            entry["reason"] = f"draft_redline raised: {exc}"
            outcomes.append(entry)
            continue

        entry["reason"] = result.get("reason") or ""
        new_id = result.get("suggestion_id")
        if result.get("status") != "drafted" or not new_id:
            entry["outcome"] = result.get("status") or "error"
            outcomes.append(entry)
            continue

        fresh = get_suggestion(new_id) or {}
        entry["new_suggestion_id"] = new_id
        entry["new_anchor_basis"] = fresh.get("anchor_basis")
        entry["new_section_id"] = fresh.get("section_id") or fresh.get("anchor_section_id") or ""
        if is_target(fresh):
            # Cannot happen while validate_anchor stands and the pre-04 drafter is
            # refused at the run level -- REPORTED rather than assumed away.
            entry["outcome"] = "redrafted_unanchored"
            entry["reason"] = "the re-drafted row is itself unanchored"
        else:
            entry["outcome"] = "redrafted"
        outcomes.append(entry)

    by_outcome: dict[str, int] = {}
    for o in outcomes:
        by_outcome[o["outcome"]] = by_outcome.get(o["outcome"], 0) + 1
    report["outcomes"] = outcomes
    report["by_outcome"] = by_outcome
    report["census_after"] = basis_census() if apply else None
    return report


# -- CLI ----------------------------------------------------------------------

def _print_census(label: str, census: dict) -> None:
    print(f"{label}: {census['total']} suggestion(s)")
    for status, b in sorted(census["by_status"].items()):
        bases = ", ".join(f"{k}={v}" for k, v in sorted(b["by_basis"].items()))
        print(f"  {status:12s} n={b['total']:4d}  anchored={b['anchored']:4d}  "
              f"empty_section_id={b['empty_section_id']:4d}  [{bases}]")


def _print_report(report: dict) -> None:
    print(f"Drafter anchors: {report['drafter_anchors']} -- {report['drafter_reason']}")
    print(f"Targets (pending, docmod, unappliable): {report['targets']}")
    print("By verdict:")
    for verdict, n in sorted(report["by_verdict"].items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:24s} {n}")
    reasons: dict[str, str] = {}
    for i in report["items"]:
        reasons.setdefault(i["verdict"], i["reason"])
    for verdict, reason in sorted(reasons.items()):
        if verdict != REDRAFTABLE:
            print(f"  - {verdict}: {reason}")
    if report["deferred"]:
        print(f"Deferred over the {report['max_per_run']}-per-run budget "
              f"({len(report['deferred'])}): {', '.join(report['deferred'])}")
    print()
    _print_census("Census before", report["census_before"])
    for o in report.get("outcomes") or []:
        print(f"  {o['outcome']:22s} {o['suggestion_id']} -> "
              f"{o['new_suggestion_id'] or '-'} basis={o['new_anchor_basis'] or '-'} "
              f"section={o['new_section_id'] or '-'} {o['reason']}")
    if report.get("by_outcome"):
        print("By outcome:")
        for outcome, n in sorted(report["by_outcome"].items(), key=lambda kv: -kv[1]):
            print(f"  {outcome:22s} {n}")
    if report.get("census_after"):
        _print_census("Census after", report["census_after"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-draft docmod suggestions written against an entity label "
                    "instead of a passage (dwr-anchor-06). Dry run unless --apply.")
    parser.add_argument("--census", action="store_true",
                        help="count dic_suggestions by status and anchor basis; probe nothing")
    parser.add_argument("--plan", action="store_true",
                        help="probe every target and report what a run would do (default)")
    parser.add_argument("--apply", action="store_true",
                        help="supersede and re-draft. THE ONLY FLAG THAT WRITES")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap re-drafts this run (default: max_redrafts_per_run)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.census:
            census = basis_census()
            if args.json:
                print(json.dumps(census, indent=2))
            else:
                _print_census("Census", census)
            return 0
        report = redraft(limit=args.limit, apply=args.apply)
    except Exception as exc:
        print(f"suggestion_redraft: could not produce the survey: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
