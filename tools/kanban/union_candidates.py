#!/usr/bin/env python3
# CUI // SP-CTI
"""Which undeclared file is costing `pr_watcher.union_refused` rows, and would a
union of it have been RIGHT? (kpr-watch-22)

THE DEFECT IS NOT THE RULE, IT IS THAT NOTHING READS THE TELEMETRY. The union
rung has resolved TWO conflicts in its lifetime while writing 37 refusals in
twelve hours, 33 of them `undeclared:`. `pr_watcher.union_refused` had exactly
ONE mention outside its writer and it was a docstring, so the only way an
undeclared append-shaped file was ever discovered was a human noticing a
stalled PR and reading audit rows by hand -- which is what happened on
2026-09-12, five hours after the stall began. kpr-watch-14 and kpr-watch-20
each fixed one instance by declaring one file pair; neither is a mechanism.
This module is the consumer. It BUILDS NO SURVEY and NO RULE: it attributes the
recorded refusals, asks the survey that already exists, and reports.

ATTRIBUTION IS TO THE UNDECLARED MEMBER, NEVER TO THE SET. A refusal names the
WHOLE conflict set and ONE undeclared file refuses the set, so a file declared
since kpr-watch-14 appears in refusals it did not cause -- measured over the 30
days to 2026-09-12, `CLAUDE.md` is named in 51 refusal rows and caused none of
them. Ranking files by refusal count would therefore propose declaring files
that are ALREADY DECLARED. So candidacy is re-asked of the SHIPPED
`match_declaration` per file: a file it accepts is COLLATERAL and can never be
a candidate, whatever set it is named in. The resolver's own message names only
the FIRST undeclared file it reached (it raises there), so the set is re-walked
rather than trusted -- a set with two undeclared members names one and costs
both.

EACH CANDIDATE ARRIVES WITH ITS SURVEY, and the survey is kpr-watch-20's, run
per hunk, imported and not re-implemented. Ground truth is WHAT LANDED; the
population is the merges GIT ITSELF conflicted on.

A HUNK THAT WAS REFUSED OR UNANCHORABLE COMPARED NOTHING, and `lost_content`
counts it as 0. That zero is the `None`-never-0 hazard one level down: measured
here, `tools/canvas_compliance/posture.py` surveys to ONE hunk, `refused`, and
so to `lost_content=0` -- a source module reading as clean because nothing was
ever compared. So the denominator is the DECISIVE hunks (every verdict except
`refused` and `unanchorable`), and a candidate with none of them is
`unmeasurable` and files NOTHING. A proposal without evidence is what this
module exists to replace.

THE RECOMMENDATION TURNS ON `lost_content`, AND IT IS CALIBRATED AGAINST THE
THREE FILES A HUMAN CORRECTLY DECLARED (measured 2026-09-12 on origin/main):

    tests/airgap/test_artifact_freshness.py   4 decisive hunks  0 base-touching  lost 0
    args/pinned_artifacts.yaml                1 decisive hunk   1 base-touching  lost 0
    CLAUDE.md                                14 decisive hunks  1 base-touching  lost 0
    tools/canvas_compliance/posture.py        0 decisive hunks  1 base-touching  UNMEASURABLE

Two of the three correct declarations have a hunk BOTH SIDES REWROTE, so
"append-shaped or nothing" would have declined two of three and the rung would
still have resolved two conflicts. `union_lost_content` -- text that landed,
came from a side, and the union drops -- is the only verdict that is a defect,
and it is the number both earlier cards actually decided on.

SHAPE IS REPORTED, NOT GUESSED, AND NEVER READ OFF THE PATH. `append_shaped`
means every measured hunk is a pure insertion on both sides with the base
region EMPTY; anything else is `rewrites_base`, carried with the count so a
reviewer sees 1-of-15 (CLAUDE.md, correctly declared) apart from 1-of-1
(a source module). It is the reviewer's decisive input and it is deliberately
not a veto: the calibration above is why.

NOTHING HERE WRITES `union_resolver.files`. The config is READ through
`load_declared_rules` and never written -- an actuator that edits its own
guardrail is the tier `restore_acts` deliberately does not have. Pinned by
`tests/kanban/test_union_candidates.py::test_nothing_writes_the_declaration_config`.

    python -m tools.kanban.union_candidates                    # 30 days, all candidates
    python -m tools.kanban.union_candidates --json
    python -m tools.kanban.union_candidates --window-hours 12  # tonight
    python -m tools.kanban.union_candidates --attribute-only   # no git, no survey
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

# sys.path bootstrap: run by path, sys.path[0] is this file's own directory.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.artifact_pin_union_survey import (  # noqa: E402
    AGREEING,
    refusal_files,
    survey,
)
from tools.kanban.union_resolver import (  # noqa: E402
    canonical_path,
    load_declared_rules,
    match_declaration,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.kanban.union_candidates")

#: The audit action this module consumes. It already exists; nothing here
#: writes one, and there is no new table.
REFUSAL_ACTION = "pr_watcher.union_refused"

#: 30 days. Long enough that a file which collides once per epic is visible,
#: short enough that a file declared weeks ago leaves the corpus on its own.
DEFAULT_WINDOW_HOURS = 720

#: The rules a proposal names, and the rules the survey is run WITH -- ONE
#: list, because a survey measured with rules other than the ones proposed is
#: evidence about a declaration nobody is being asked to make. The same pair
#: kpr-watch-14 and kpr-watch-20 declared.
DEFAULT_RULES = ("keep_both_blocks", "adjacent_edits")

#: Most candidates one run surveys. Each survey shells out to git once per
#: merge commit touching the file; the rest are reported as deferred and
#: arrive on the next cycle, worst-first.
DEFAULT_MAX_FILES = 6

SHAPE_APPEND = "append_shaped"
SHAPE_REWRITES_BASE = "rewrites_base"
SHAPE_UNMEASURED = "unmeasured"

RECOMMEND_DECLARE = "declare"
RECOMMEND_DO_NOT = "do_not_declare"

STATE_MEASURABLE = "measurable"
STATE_UNMEASURABLE = "unmeasurable"

#: Hunk verdicts that COMPARED NOTHING. `refused` is the engine declining the
#: whole file; `unanchorable` is a region the survey could not locate uniquely
#: and refuses to guess at. Both contribute 0 to `lost_content`, so a file
#: whose every hunk is one of these has a `lost_content` of 0 that MEANS "not
#: measured" -- the exact hazard `lost_content is None, never 0` exists to
#: stop, one level further down.
UNMEASURED_HUNK_VERDICTS = frozenset({"refused", "unanchorable"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- the corpus --------------------------------------------------------------
def refusal_rows(conn, *, window_hours: Optional[int] = DEFAULT_WINDOW_HOURS
                 ) -> List[Dict[str, Any]]:
    """Every recorded `pr_watcher.union_refused` row in the window.

    ``window_hours=None`` is LIFETIME. The payload is the writer's own
    ``WatcherAction`` dict: ``task_id``, ``pr_url`` and the rendered ``reason``
    that carries ``files=[...]``. Read-only -- the rows already exist, which is
    the whole point of this card.
    """
    pg = str(getattr(conn, "_backend", "")).startswith("postgres")
    details = "details::text" if pg else "details"
    sql = (f"SELECT {details} AS d, created_at FROM audit_trail "  # nosec B608
           "WHERE action = %s")
    params: tuple = (REFUSAL_ACTION,)
    if window_hours is not None:
        sql += " AND created_at >= %s"
        params = (*params, (_now() - timedelta(hours=int(window_hours))).isoformat())
    out: List[Dict[str, Any]] = []
    for rec in conn.execute(sql + " ORDER BY created_at", params).fetchall():
        row = dict(rec)
        payload = row.get("d")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        out.append({"task_id": payload.get("task_id"),
                    "pr_url": payload.get("pr_url"),
                    "reason": payload.get("reason") or "",
                    "created_at": row.get("created_at")})
    return out


# -- attribution -------------------------------------------------------------
def attribute(rows: Sequence[Mapping[str, Any]],
              declarations: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    """Split every file named in the corpus into CANDIDATES and COLLATERAL.

    A refusal names the whole conflict set, so a DECLARED file is named in
    refusals it did not cause. Candidacy is therefore re-asked of the SHIPPED
    ``match_declaration`` per file rather than read off the resolver's message,
    which names only the first undeclared file it reached.

    Counted per ROW, not per occurrence: a canonical path named twice in one
    set (the `tools/` copy and its `icdev/tools/` mirror, which fold onto one
    declaration) is ONE row that this file refused, not two.
    """
    if declarations is None:
        declarations = load_declared_rules().get("files") or []
    candidates: "collections.Counter[str]" = collections.Counter()
    collateral: "collections.Counter[str]" = collections.Counter()
    tasks: Dict[str, set] = collections.defaultdict(set)
    latest: Dict[str, Any] = {}
    unparsed = 0
    for row in rows:
        files = refusal_files(row.get("reason") or "")
        if not files:
            # A refusal whose message carries no file set: a verifier failure
            # or a malformed row. COUNTED, never silently dropped -- a corpus
            # that shrank without saying so is not a corpus.
            unparsed += 1
            continue
        for canon in sorted({canonical_path(f) for f in files}):
            if match_declaration(canon, declarations) is not None:
                collateral[canon] += 1
                continue
            candidates[canon] += 1
            if row.get("task_id"):
                tasks[canon].add(str(row["task_id"]))
            latest[canon] = row.get("created_at")
    return {
        "rows": len(rows),
        "unparsed_rows": unparsed,
        "candidates": dict(candidates.most_common()),
        "collateral": dict(collateral.most_common()),
        "tasks": {path: sorted(names) for path, names in tasks.items()},
        "last_refused_at": latest,
    }


# -- shape and verdict -------------------------------------------------------
def shape_of(hunks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Append-shaped, or does a hunk rewrite base lines? (kpr-watch-22)

    A conflict hunk's BASE region is the text both sides changed. Empty on
    every hunk means every observed collision was a pure insertion on both
    sides at one point -- the shape `keep_both_blocks` is for. A non-empty base
    region on ANY hunk means two cards rewrote the same lines, which is what a
    source module's collisions look like.

    Reported WITH THE COUNT, never as a bare flag: 1-of-15 (CLAUDE.md, which is
    correctly declared) and 1-of-1 (a source module) are the same flag and
    different facts.
    """
    total = len(hunks)
    rewriting = sum(1 for h in hunks if int(h.get("base_lines") or 0) > 0)
    if not total:
        verdict = SHAPE_UNMEASURED
    elif rewriting:
        verdict = SHAPE_REWRITES_BASE
    else:
        verdict = SHAPE_APPEND
    return {"shape": verdict, "hunks": total or None,
            "hunks_rewriting_base": rewriting if total else None}


def declaration_line(path: str, rules: Sequence[str] = DEFAULT_RULES) -> str:
    """The exact one-line entry a human would add under `union_resolver.files`.

    Emitted ONLY on a card, and only ever as TEXT: see the module docstring on
    why nothing here writes the config.
    """
    return f"  - {{path: {canonical_path(path)}, rules: [{', '.join(rules)}]}}"


def assess(path: str, *, rules: Sequence[str] = DEFAULT_RULES,
           root: Optional[pathlib.Path] = None, ref: str = "origin/main",
           survey_fn=None) -> Dict[str, Any]:
    """One candidate: its survey, its shape, and whether the evidence supports
    a declaration.

    ``survey_fn`` is injectable so the verdict logic is testable on survey
    shapes without a git history.
    """
    run = survey_fn or survey
    report = run([path], list(rules), root=root, ref=ref)
    hunks: List[Dict[str, Any]] = []
    for rep in report.get("reports") or []:
        for case in rep.get("cases") or []:
            hunks.extend(case.get("hunks") or [])
    decisive = [h for h in hunks
                if str(h.get("verdict")) not in UNMEASURED_HUNK_VERDICTS]
    file_report = (report.get("reports") or [{}])[0]
    out: Dict[str, Any] = {
        "file": canonical_path(path),
        "rules": list(rules),
        "ref": ref,
        # None here means GIT ITSELF could not be read (`_survey_one` returns
        # None for the count when `git log` fails), which is a different fact
        # from "0 merge commits touch it" and is what makes a whole RUN
        # unmeasurable rather than this one candidate.
        "git_readable": file_report.get("merge_commits_touching_file") is not None,
        "merge_commits_touching_file": file_report.get("merge_commits_touching_file"),
        "conflicting_merges": file_report.get("conflicting"),
        "skipped": file_report.get("skipped") or {},
        "hunks": len(hunks) or None,
        "decisive_hunks": len(decisive) or None,
        "hunk_tally": dict(collections.Counter(str(h.get("verdict")) for h in hunks)),
        "agreeing": (sum(1 for h in decisive if str(h.get("verdict")) in AGREEING)
                     if decisive else None),
        **shape_of(hunks),
    }
    if not decisive:
        # NOTHING WAS COMPARED. `lost_content` is None -- never 0 -- and this
        # candidate files no card: a proposal without evidence is the thing
        # this module exists to replace.
        out.update(
            state=STATE_UNMEASURABLE,
            lost_content=None,
            recommendation=RECOMMEND_DO_NOT,
            reason=((f"no hunk of {out['file']} was COMPARED on {ref}: "
                     f"{len(hunks)} conflict hunk(s), every one of them "
                     f"{sorted(UNMEASURED_HUNK_VERDICTS)}")
                    if hunks else
                    (f"no conflicting merge of {out['file']} on {ref} -- "
                     f"{file_report.get('merge_commits_touching_file')} merge commit(s) "
                     "touch it and none reached the rung")),
        )
        return out
    lost = sum(int(h.get("lost") or 0) for h in decisive
               if str(h.get("verdict")) == "union_lost_content")
    out["lost_content"] = lost
    out["state"] = STATE_MEASURABLE
    if lost:
        out["recommendation"] = RECOMMEND_DO_NOT
        out["reason"] = (
            f"the union DROPS {lost} line(s) that landed and came from a side, over "
            f"{len(decisive)} decisive hunk(s): declaring it would resolve a real "
            "conflict by losing content, silently, on a branch nobody reviews")
    else:
        out["recommendation"] = RECOMMEND_DECLARE
        out["reason"] = (
            f"over {len(decisive)} decisive hunk(s) on {ref} the union never dropped "
            f"content that landed ({out['agreeing']} reproduced the human resolution "
            f"exactly or bar ordering/blank lines); shape is {out['shape']}"
            + (f" -- {out['hunks_rewriting_base']} of {out['hunks']} hunk(s) rewrite "
               "base lines, READ THEM before declaring (CLAUDE.md was correctly "
               "declared with 1 of 15)" if out["shape"] == SHAPE_REWRITES_BASE else ""))
    return out


# -- the report --------------------------------------------------------------
def candidates(conn=None, *, window_hours: Optional[int] = DEFAULT_WINDOW_HOURS,
               declarations: Optional[Sequence[Mapping[str, Any]]] = None,
               rules: Sequence[str] = DEFAULT_RULES,
               max_files: int = DEFAULT_MAX_FILES,
               root: Optional[pathlib.Path] = None, ref: str = "origin/main",
               rows: Optional[Sequence[Mapping[str, Any]]] = None,
               survey_fn=None, end_read_txn=None) -> Dict[str, Any]:
    """Attribute the corpus, then survey the top candidates, worst-first.

    UNMEASURABLE IS NOT CLEAN. An unreadable corpus and an EMPTY one are both
    reported ``measurable=False`` with the reason: the rung may be idle, the
    watcher may be down, or the audit writer may be bypassed, and none of those
    is "no file needs declaring".
    """
    own = conn is None and rows is None
    if own:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        if rows is None:
            try:
                rows = refusal_rows(conn, window_hours=window_hours)
            except Exception as exc:  # noqa: BLE001 -- a fresh worktree has no board
                return {"measurable": False, "window_hours": window_hours,
                        "reason": f"refusal corpus unreadable: {type(exc).__name__}: {exc}",
                        "rows": None, "candidates": [], "collateral": {},
                        "candidate_files": None, "deferred": 0, "unparsed_rows": None}
        # The surveys shell out to git for tens of seconds; a read txn held
        # across that is killed by idle_in_transaction_session_timeout. The
        # caller's own closer is preferred so this shares the detector's one
        # declaration of what "finish a read" means.
        if conn is not None:
            try:
                (end_read_txn or (lambda c: c.rollback()))(conn)
            except Exception as exc:  # noqa: BLE001 -- nothing was written to lose
                logger.debug("union_candidates: could not end the read txn: %s", exc)
        if not rows:
            return {"measurable": False, "window_hours": window_hours,
                    "reason": (f"no {REFUSAL_ACTION} rows in the last {window_hours}h"
                               if window_hours else
                               f"no {REFUSAL_ACTION} row has ever been recorded"),
                    "rows": 0, "candidates": [], "collateral": {},
                    "candidate_files": 0, "deferred": 0, "unparsed_rows": 0}
        if declarations is None:
            declarations = load_declared_rules().get("files") or []
        attribution = attribute(rows, declarations)
        ranked = list(attribution["candidates"].items())
        deferred = max(0, len(ranked) - int(max_files))
        assessed: List[Dict[str, Any]] = []
        for path, refusals in ranked[:int(max_files)]:
            # Re-asserted at the point of use: a file the shipped matcher
            # accepts can NEVER become a candidate, whatever attribution said.
            if match_declaration(path, declarations) is not None:  # pragma: no cover
                logger.error("union_candidates: %s is declared and was ranked -- dropped",
                             path)
                continue
            entry = assess(path, rules=rules, root=root, ref=ref, survey_fn=survey_fn)
            entry["refusals"] = refusals
            entry["tasks"] = attribution["tasks"].get(path, [])
            entry["last_refused_at"] = attribution["last_refused_at"].get(path)
            entry["declaration"] = (declaration_line(path, rules)
                                    if entry["recommendation"] == RECOMMEND_DECLARE
                                    else None)
            assessed.append(entry)
        return {
            "measurable": True,
            "window_hours": window_hours,
            "rows": attribution["rows"],
            "unparsed_rows": attribution["unparsed_rows"],
            "candidate_files": len(ranked),
            "deferred": deferred,
            "candidates": assessed,
            "collateral": attribution["collateral"],
            "rules": list(rules),
            "ref": ref,
        }
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _print(report: Mapping[str, Any]) -> None:
    if not report.get("measurable"):
        print(f"UNMEASURABLE: {report.get('reason')}")
        print("This is not a clean bill of health.")
        return
    print(f"{report['rows']} {REFUSAL_ACTION} row(s) in the last "
          f"{report['window_hours']}h  ({report['unparsed_rows']} carried no file set)")
    print(f"{report['candidate_files']} UNDECLARED file(s) named; "
          f"{len(report['candidates'])} surveyed, {report['deferred']} deferred")
    print()
    for entry in report["candidates"]:
        print(f"  {entry['refusals']:4d}  {entry['file']}")
        print(f"        state={entry['state']}  shape={entry['shape']}"
              f" ({entry['hunks_rewriting_base']}/{entry['hunks']} rewrite base)"
              f"  decisive={entry['decisive_hunks']}  lost_content={entry['lost_content']}")
        print(f"        {entry['recommendation'].upper()}: {entry['reason']}")
        if entry["declaration"]:
            print(f"        {entry['declaration']}")
        if entry["tasks"]:
            print(f"        tasks: {', '.join(entry['tasks'][:6])}")
        print()
    print("COLLATERAL -- named in refusals it did not cause, ALREADY DECLARED:")
    for path, num in list(report["collateral"].items())[:12]:
        print(f"  {num:4d}  {path}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS,
                    help="0 or negative for LIFETIME")
    ap.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    ap.add_argument("--rules", default=",".join(DEFAULT_RULES))
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--attribute-only", action="store_true",
                    help="attribute the corpus and stop: no git, no survey")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    window = args.window_hours if args.window_hours and args.window_hours > 0 else None
    rules = [r.strip() for r in args.rules.split(",") if r.strip()]

    if args.attribute_only:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = refusal_rows(conn, window_hours=window)
        finally:
            conn.close()
        rep = attribute(rows)
        rep["window_hours"] = window
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        else:
            print(f"{rep['rows']} row(s), {rep['unparsed_rows']} without a file set")
            print("CANDIDATES (undeclared):")
            for path, num in rep["candidates"].items():
                print(f"  {num:4d}  {path}")
            print("COLLATERAL (declared, named anyway):")
            for path, num in rep["collateral"].items():
                print(f"  {num:4d}  {path}")
        return 0 if rep["rows"] else 2

    report = candidates(window_hours=window, rules=rules, max_files=args.max_files,
                        ref=args.ref)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print(report)
    # Exit 2 = the report could not be produced, which is never the same as a
    # clean report. A candidate is still exit 0: this reports, it does not gate.
    return 0 if report.get("measurable") else 2


if __name__ == "__main__":
    sys.exit(main())
