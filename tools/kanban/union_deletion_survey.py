#!/usr/bin/env python3
# CUI // SP-CTI
"""Does the empty-side rung ever DISCARD A DELETION? Replayed against history (mfx-mrg-08).

THE DEFECT. `union_resolver._resolve_cluster` opens with two lines that run
BEFORE any declared rule and cannot be switched off:

    if not main_seg and card_seg:  return card_seg, other_side_when_empty
    if not card_seg and main_seg:  return main_seg, other_side_when_empty

Neither looks at `base_seg`. An empty side is read as "this branch had nothing
to say here", and that is also exactly what "this branch DELETED these lines"
looks like. `keep_both_blocks` and `table_rows` both open `if base_seg: return
None` -- "both REWROTE existing lines, not two appends" -- and the universal
rung, which runs FIRST and refuses nothing, does not.

TWO POPULATIONS, NEVER MERGED, because they answer different questions and only
one of them has ground truth:

  recorded   every `pr_watcher.union_resolved` / `union_refused` audit row,
             replayed against the branch head THAT ROW ACTUALLY HAD -- the
             reflog reconstruction of kpr-watch-15, never today's head. It
             answers HOW MANY hunks of each shape the resolver has really met.
             It has no ground truth: a refused row never produced a file, so
             nothing says what the right resolution would have been.

  landed     every conflicting merge commit on the default branch touching a
             DECLARED file, the kpr-watch-14 population. GROUND TRUTH IS WHAT
             THE HUMAN LANDED, so for an empty-side hunk over a NON-EMPTY base
             this can answer the question the recorded population cannot: were
             the restored lines actually in the merge commit's file?

FOUR HUNK SHAPES, and the split by side is the point:

  both_sides_present        neither side empty. Not this rung's business.
  base_empty_side_empty     THE INTENDED CASE -- a pure insertion on one side.
  base_nonempty_main_empty  MAIN deleted those lines; the rung restores them
                            from the card.
  base_nonempty_card_empty  THE CARD deleted them; the rung restores them from
                            main. THE FINDING.

`decided_by` says which branch of `merge_three_way` actually disposed of the
hunk, because three equality short-circuits run BEFORE `_resolve_cluster` and a
count of shapes that never reach the rung would overstate the exposure.

UNMEASURABLE IS ITS OWN BUCKET and is never folded into either other: a branch
whose ref was deleted has no reflog and cannot be asked, and reading it as
"no deletion" understates the finding.

    python -m tools.kanban.union_deletion_survey                 # both populations
    python -m tools.kanban.union_deletion_survey --json
    python -m tools.kanban.union_deletion_survey --recorded      # audit rows only
    python -m tools.kanban.union_deletion_survey --landed        # merge history only
    python -m tools.kanban.union_deletion_survey --landed --max-merges 1700

Exit 0 = a survey was produced, whatever it says. Exit 2 = it could not be,
which is never the same as a clean survey. Report only, no --gate (kpr-fix-03):
it measures the recorded corpus and the merge history, not a diff.
"""
from __future__ import annotations

import argparse
import ast
import collections
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

# The clustering, the index mapping and the declaration matching are IMPORTED,
# never re-derived: a survey that re-implements the engine measures its own
# copy and can agree with a resolver that disagrees with it.
from tools.kanban.union_resolver import (  # noqa: E402
    UnionRefused,
    _Change,
    _clusters,
    _declared_rules,
    _map_index,
    _opcodes,
    canonical_path,
    derived_source,
    load_declared_rules,
    match_declaration,
)

ACTIONS = ("pr_watcher.union_resolved", "pr_watcher.union_refused")
RULE_ADJACENT_EDITS = "adjacent_edits"

SHAPE_BOTH = "both_sides_present"
SHAPE_INTENDED = "base_empty_side_empty"
SHAPE_MAIN_DELETED = "base_nonempty_main_empty"
SHAPE_CARD_DELETED = "base_nonempty_card_empty"
SHAPES = (SHAPE_BOTH, SHAPE_INTENDED, SHAPE_MAIN_DELETED, SHAPE_CARD_DELETED)

_REFLOG_RE = re.compile(r"^([0-9a-f]+) .*@\{(\d+) ")
_FILES_RE = re.compile(r"files=(\[[^\]]*\])")
DEFAULT_MAX_MERGES = 400


def _root() -> Path:
    """The checkout this module belongs to. THE one resolver (xit-decl-03) and
    no fallback climb: `tools/__init__.py` already imports it, so a survey that
    guessed here would be guessing on a deployment where nothing else has to."""
    return Path(repo_root(__file__))


def _git(args: List[str], timeout: int = 180, root: Optional[Path] = None):
    return subprocess.run(["git", *args], cwd=str(root or _root()),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _blob(rev: str, rel: str) -> Optional[List[str]]:
    proc = _git(["show", "{0}:{1}".format(rev, rel)])
    if proc.returncode != 0:
        return None
    return proc.stdout.splitlines(keepends=True)


def _utc(value) -> datetime.datetime:
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


# -- the hunk enumerator -----------------------------------------------------
def hunks(base: Sequence[str], main: Sequence[str], card: Sequence[str],
          rules: Sequence[str]) -> List[Dict[str, Any]]:
    """Every cluster `merge_three_way` would form, with its shape and disposal.

    This walks the SAME clustering and index mapping the resolver walks, and
    then reproduces only the three equality short-circuits that precede
    `_resolve_cluster` -- it never decides a hunk, so it cannot be made to
    agree with a resolver whose decisions have changed underneath it.
    """
    ops_m = _opcodes(base, main)
    ops_c = _opcodes(base, card)
    changes = [_Change("main", op) for op in ops_m if op[0] != "equal"]
    changes += [_Change("card", op) for op in ops_c if op[0] != "equal"]
    adjacent_ok = RULE_ADJACENT_EDITS in rules

    out: List[Dict[str, Any]] = []
    for cluster in _clusters(changes, adjacent_ok):
        lo = min(c.i1 for c in cluster)
        hi = max(c.i2 for c in cluster)
        mem_m = {c.op for c in cluster if c.side == "main"}
        mem_c = {c.op for c in cluster if c.side == "card"}
        m1 = _map_index(ops_m, lo, mem_m, False, len(main))
        m2 = _map_index(ops_m, hi, mem_m, True, len(main))
        c1 = _map_index(ops_c, lo, mem_c, False, len(card))
        c2 = _map_index(ops_c, hi, mem_c, True, len(card))
        base_seg = list(base[lo:hi])
        main_seg = list(main[m1:m2])
        card_seg = list(card[c1:c2])

        if main_seg == base_seg:
            decided = "early_main_unchanged"
        elif card_seg == base_seg:
            decided = "early_card_unchanged"
        elif main_seg == card_seg:
            decided = "early_sides_agree"
        else:
            decided = "resolve_cluster"

        if main_seg and card_seg:
            shape = SHAPE_BOTH
        elif not base_seg:
            shape = SHAPE_INTENDED
        elif not main_seg:
            shape = SHAPE_MAIN_DELETED
        else:
            shape = SHAPE_CARD_DELETED

        restored: List[str] = []
        if shape in (SHAPE_MAIN_DELETED, SHAPE_CARD_DELETED):
            restored = list(card_seg) if not main_seg else list(main_seg)

        out.append({
            "line": lo + 1,
            "base_lines": len(base_seg), "main_lines": len(main_seg),
            "card_lines": len(card_seg),
            "shape": shape, "decided_by": decided,
            # The lines the rung would RESTORE -- the deletion it discards.
            "restored": restored,
        })
    return out


def _tally(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    shapes = collections.Counter(h["shape"] for h in records)
    reaching = collections.Counter(
        h["shape"] for h in records if h["decided_by"] == "resolve_cluster")
    return {
        "hunks": len(records),
        "by_shape": {k: shapes.get(k, 0) for k in SHAPES},
        "reaching_resolve_cluster": {k: reaching.get(k, 0) for k in SHAPES},
    }


# -- population 1: the recorded audit rows -----------------------------------
def read_rows() -> List[Dict[str, Any]]:
    """Every recorded union row, oldest first. Raises if the board is unreadable."""
    from tools.db.storage import get_connection

    placeholders = ", ".join(["%s"] * len(ACTIONS))
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT created_at, action, details FROM audit_trail "
            "WHERE action IN ({0}) ORDER BY created_at".format(placeholders),
            tuple(ACTIONS))
        raw = [dict(r) for r in cur.fetchall()]
    out = []
    for row in raw:
        det = row.get("details")
        if isinstance(det, str):
            try:
                det = json.loads(det)
            except (TypeError, ValueError):
                det = {}
        det = det or {}
        out.append({
            "at": _utc(row["created_at"]).isoformat(),
            "action": row["action"],
            "task_id": det.get("task_id") or "",
            "base_sha": (det.get("base_sha") or "").strip(),
            "pr_url": det.get("pr_url") or "",
            "reason": det.get("reason") or "",
        })
    return out


def files_from_reason(reason: str) -> List[str]:
    """The unmerged file list the row itself recorded, or []."""
    m = _FILES_RE.search(reason or "")
    if not m:
        return []
    try:
        got = ast.literal_eval(m.group(1))
    except (ValueError, SyntaxError):
        return []
    return [str(x) for x in got] if isinstance(got, (list, tuple)) else []


def _reflog(ref: str, cache: Dict[str, Optional[List[Tuple[str, int]]]]):
    if ref not in cache:
        proc = _git(["reflog", "show", "--date=raw", ref], timeout=120)
        if proc.returncode != 0:
            cache[ref] = None
        else:
            hits = [(m.group(1), int(m.group(2)))
                    for m in (_REFLOG_RE.match(l) for l in proc.stdout.splitlines()) if m]
            cache[ref] = sorted(hits, key=lambda e: e[1]) or None
    return cache[ref]


def survey_recorded(rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Replay each recorded row against the head it ACTUALLY had."""
    if rows is None:
        try:
            rows = read_rows()
        except Exception as exc:  # noqa: BLE001 -- an unreadable board is unmeasurable
            return {"measurable": False, "reason": "cannot read audit_trail: %s" % exc,
                    "rows": None, "measured_rows": None, "cases": [], "tally": {},
                    "unmeasurable": {}, "unmeasurable_rows": None}
    cache: Dict[str, Optional[List[Tuple[str, int]]]] = {}
    decls = (load_declared_rules().get("files") or [])
    cases: List[Dict[str, Any]] = []
    unmeasurable: "collections.Counter[str]" = collections.Counter()
    all_hunks: List[Dict[str, Any]] = []

    for row in rows:
        stamp = _utc(row["at"]).timestamp()
        log = _reflog("refs/remotes/origin/kanban/%s" % row["task_id"], cache)
        if not log:
            unmeasurable["no_reflog"] += 1
            cases.append(dict(row, bucket="unmeasurable_no_reflog", files=[]))
            continue
        prior = [sha for sha, when in log if when <= stamp]
        if not prior:
            unmeasurable["no_prior_push"] += 1
            cases.append(dict(row, bucket="unmeasurable_no_prior_push", files=[]))
            continue
        head = prior[-1]
        base_sha = row["base_sha"]
        if not base_sha:
            main_log = _reflog("refs/remotes/origin/main", cache) or []
            earlier = [sha for sha, when in main_log if when <= stamp]
            base_sha = earlier[-1] if earlier else ""
        if not base_sha or _git(["cat-file", "-e", head + "^{commit}"]).returncode:
            unmeasurable["object_gone"] += 1
            cases.append(dict(row, bucket="unmeasurable_object_gone", files=[]))
            continue
        merge_base = (_git(["merge-base", head, base_sha]).stdout or "").strip()
        if not merge_base:
            unmeasurable["no_merge_base"] += 1
            cases.append(dict(row, bucket="unmeasurable_no_merge_base", files=[]))
            continue

        per_file: List[Dict[str, Any]] = []
        for rel in files_from_reason(row["reason"]):
            canon = canonical_path(rel)
            decl = match_declaration(canon, decls)
            if decl is None:
                per_file.append({"file": canon, "status": "undeclared"})
                continue
            if derived_source(decl):
                per_file.append({"file": canon, "status": "derived_copy"})
                continue
            try:
                rules = _declared_rules(decl, canon)
            except UnionRefused as exc:
                per_file.append({"file": canon, "status": "bad_declaration: %s" % exc})
                continue
            base = _blob(merge_base, canon)
            main = _blob(base_sha, canon)
            cardv = _blob(head, canon)
            if base is None or main is None or cardv is None:
                per_file.append({"file": canon, "status": "blob_missing"})
                continue
            got = hunks(base, main, cardv, rules)
            for h in got:
                h["file"] = canon
                h["task_id"] = row["task_id"]
                h["at"] = row["at"]
            all_hunks.extend(got)
            per_file.append({"file": canon, "status": "measured", "rules": rules,
                             "tally": _tally(got),
                             "hunks": [{k: v for k, v in h.items() if k != "restored"}
                                       for h in got]})
        cases.append(dict(row, bucket="measured", head=head[:9], base=base_sha[:9],
                          merge_base=merge_base[:9], files=per_file))

    measured_rows = [c for c in cases if c["bucket"] == "measured"]
    return {
        "measurable": bool(all_hunks),
        "rows": len(cases),
        "measured_rows": len(measured_rows),
        "unmeasurable": dict(unmeasurable),
        "unmeasurable_rows": sum(unmeasurable.values()),
        "tally": _tally(all_hunks) if all_hunks else {},
        "cases": cases,
    }


# -- population 2: what actually landed --------------------------------------
def _declared_paths(decls: Sequence[Dict[str, Any]], ref: str) -> List[str]:
    """Every tracked file on `ref` a non-derived declaration claims."""
    proc = _git(["ls-tree", "-r", "--name-only", ref], timeout=300)
    if proc.returncode != 0:
        return []
    out = []
    for rel in proc.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        canon = canonical_path(rel)
        if canon != rel:          # the mirror copy folds onto the same declaration
            continue
        decl = match_declaration(canon, decls)
        if decl is None or derived_source(decl):
            continue
        out.append(rel)
    return out


def _merge_touches(ref: str, limit: int) -> Tuple[List[Tuple[str, List[str], List[str]]], bool]:
    """(sha, parents, files) for the newest `limit` merge commits on `ref`.

    ONE `git log` pass rather than a `--` query per declared file: 1,222 files
    against 1,686 merges is 1,222 log invocations for an answer one walk gives.
    `--diff-merges=first-parent` is REQUIRED: `--name-only` on a merge commit
    prints NOTHING by default, which reads as "this merge touched no declared
    file" for every merge in the history.
    """
    proc = _git(["log", "--merges", "-%d" % (limit + 1), "--format=%x00%H %P",
                 "--diff-merges=first-parent", "--name-only", ref], timeout=600)
    if proc.returncode != 0:
        return [], False
    out: List[Tuple[str, List[str], List[str]]] = []
    for chunk in proc.stdout.split("\x00"):
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]
        if not lines:
            continue
        head = lines[0].split()
        out.append((head[0], head[1:], lines[1:]))
    truncated = len(out) > limit
    return out[:limit], truncated


def survey_landed(ref: str = "origin/main", max_merges: int = DEFAULT_MAX_MERGES
                  ) -> Dict[str, Any]:
    """Conflicting merges of declared files, with GROUND TRUTH = what landed."""
    decls = (load_declared_rules().get("files") or [])
    merges, truncated = _merge_touches(ref, max_merges)
    if not merges:
        return {"measurable": False,
                "reason": "no merge commit readable on %s" % ref,
                "ref": ref, "max_merges": max_merges, "truncated": truncated,
                "merges_examined": 0, "declared_file_merges": 0,
                "conflicting": 0, "deletion_hunks": None,
                "restore_was_right": None, "restore_was_wrong": None, "cases": []}

    all_hunks: List[Dict[str, Any]] = []
    declared_file_merges = 0
    conflicting = 0
    for sha, parents, files in merges:
        if len(parents) != 2:
            continue
        candidates = []
        for rel in files:
            canon = canonical_path(rel)
            if canon != rel:      # the mirror copy folds onto the same declaration
                continue
            decl = match_declaration(canon, decls)
            if decl is None or derived_source(decl):
                continue
            try:
                candidates.append((rel, _declared_rules(decl, rel)))
            except UnionRefused:
                continue
        declared_file_merges += len(candidates)
        if not candidates:
            continue
        mb = (_git(["merge-base", parents[0], parents[1]]).stdout or "").strip()
        if not mb:
            continue
        # Both-sides-changed is asked with TWO name-only diffs rather than four
        # blob reads per file: on this host a `git show` costs ~40 ms and a
        # merge can name dozens of declared files.
        touched = []
        for parent in parents:
            proc = _git(["diff", "--name-only", mb, parent], timeout=300)
            touched.append({canonical_path(l.strip()) for l in proc.stdout.splitlines()
                            if l.strip()} if proc.returncode == 0 else None)
        if any(t is None for t in touched):
            continue
        both = touched[0] & touched[1]
        for rel, rules in candidates:
            if rel not in both:
                continue          # only one side touched it; git takes that side
            base = _blob(mb, rel)
            a = _blob(parents[0], rel)
            b = _blob(parents[1], rel)
            landed = _blob(sha, rel)
            if any(x is None for x in (base, a, b, landed)):
                continue
            if a == base or b == base:
                continue
            conflicting += 1
            landed_join = "".join(landed or [])
            for orient, main, cardv in (("p1_as_main", a, b), ("p2_as_main", b, a)):
                for h in hunks(base, main, cardv, rules):
                    if h["shape"] not in (SHAPE_MAIN_DELETED, SHAPE_CARD_DELETED):
                        continue
                    if h["decided_by"] != "resolve_cluster":
                        continue
                    # GROUND TRUTH: are the lines the rung would restore in the
                    # file the merge commit actually recorded?
                    restored = [ln for ln in h["restored"] if ln.strip()]
                    kept = sum(1 for ln in restored if ln in landed_join)
                    h.pop("restored", None)
                    h.update(file=rel, sha=sha[:9], orientation=orient,
                             restored_lines=len(restored),
                             restored_lines_in_landed=kept,
                             restore_was_right=(None if not restored
                                                else kept == len(restored)))
                    all_hunks.append(h)
    right = [h for h in all_hunks if h["restore_was_right"] is True]
    wrong = [h for h in all_hunks if h["restore_was_right"] is False]
    return {
        "measurable": conflicting > 0,
        "ref": ref, "max_merges": max_merges, "truncated": truncated,
        "merges_examined": len(merges),
        "declared_file_merges": declared_file_merges,
        "conflicting": conflicting,
        # None, never 0, over a population nothing was measured in.
        "deletion_hunks": (len(all_hunks) if conflicting else None),
        "restore_was_right": (len(right) if all_hunks else None),
        "restore_was_wrong": (len(wrong) if all_hunks else None),
        "cases": all_hunks,
    }


def _print_recorded(rep: Dict[str, Any]) -> None:
    print("RECORDED ROWS -- every pr_watcher.union_resolved / union_refused row")
    if not rep.get("rows"):
        print("  UNMEASURABLE: %s" % (rep.get("reason") or "no recorded union row"))
        print("  This is not a clean bill of health.")
        return
    print("  %d row(s); %d replayed, %d UNMEASURABLE %s"
          % (rep["rows"], rep["measured_rows"], rep["unmeasurable_rows"],
             rep["unmeasurable"] or ""))
    t = rep.get("tally") or {}
    if not t:
        print("  no hunk could be reconstructed -- UNMEASURABLE, not clean.")
        return
    print("  %d hunk(s) over the declared files:" % t["hunks"])
    for shape in SHAPES:
        print("    %5d  %-26s  (%d reach _resolve_cluster)"
              % (t["by_shape"][shape], shape, t["reaching_resolve_cluster"][shape]))


def _print_landed(rep: Dict[str, Any]) -> None:
    print("LANDED -- conflicting merges of declared files; ground truth is what merged")
    if not rep.get("measurable"):
        print("  UNMEASURABLE: %s" % (rep.get("reason") or "no conflicting merge"))
        print("  This is not a clean bill of health.")
        return
    print("  %d merge(s) examined, %d touch a declared file, %d where BOTH sides "
          "changed it" % (rep["merges_examined"], rep["declared_file_merges"],
                          rep["conflicting"]))
    if rep["truncated"]:
        print("  TRUNCATED at --max-merges %d; older merges were not examined"
              % rep["max_merges"])
    print("  empty-side-over-non-empty-base hunks reaching the rung: %s"
          % rep["deletion_hunks"])
    print("    restoring was RIGHT (every restored line is in what landed): %s"
          % rep["restore_was_right"])
    print("    restoring was WRONG (a restored line is NOT in what landed): %s"
          % rep["restore_was_wrong"])
    for case in rep["cases"][:40]:
        print("      %s  %s  %s  %s  restored=%d kept=%d right=%s"
              % (case["sha"], case["file"], case["shape"], case["orientation"],
                 case["restored_lines"], case["restored_lines_in_landed"],
                 case["restore_was_right"]))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recorded", action="store_true", help="the audit-row population only")
    ap.add_argument("--landed", action="store_true", help="the merge-history population only")
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--max-merges", type=int, default=DEFAULT_MAX_MERGES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    want_rec = args.recorded or not args.landed
    want_land = args.landed or not args.recorded
    report: Dict[str, Any] = {}
    if want_rec:
        report["recorded"] = survey_recorded()
    if want_land:
        report["landed"] = survey_landed(args.ref, args.max_merges)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        if want_rec:
            _print_recorded(report["recorded"])
            print()
        if want_land:
            _print_landed(report["landed"])
    measurable = any(rep.get("measurable") for rep in report.values())
    # Exit 2 = the survey could not be produced, which is never the same as a
    # clean survey.
    return 0 if measurable else 2


if __name__ == "__main__":
    sys.exit(main())
