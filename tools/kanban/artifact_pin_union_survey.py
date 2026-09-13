#!/usr/bin/env python3
# CUI // SP-CTI
"""Would a union of the artifact-pin pair have been RIGHT? Replayed PER HUNK (kpr-watch-20).

`args/pinned_artifacts.yaml` and `tests/airgap/test_artifact_freshness.py` are
the two files every artifact-freshness card appends to -- one measurement
comment block to the manifest, one pinning test function to the suite. Four
cards landed in one epic and each collided with every sibling that landed
before it: 24 `pr_watcher.union_refused` rows across #2267/#2269/#2270, every
one of them `undeclared: args/pinned_artifacts.yaml matches no
union_resolver.files entry`, and a human resolved all three by hand.

THIS SURVEY IS HUNK-LEVEL, AND THAT IS THE POINT. kpr-watch-14 measured
CLAUDE.md whole-file, which is enough when every conflict in a file has the
same shape. It is NOT enough here: measured on this pair, one file-merge mixes
clean append hunks with a paragraph BOTH SIDES REWROTE, and a whole-file
verdict reports only the second. A per-hunk verdict says which shape is which,
and that is what decides whether a rule may be declared.

GROUND TRUTH IS WHAT LANDED. For each merge commit touching the file the three
sides are rebuilt from git (base = merge-base of the parents), the SHIPPED
`merge_three_way` runs, and the result is compared against the file the merge
commit recorded. Nothing here re-implements the union: asking the resolver
whether it agrees with itself would prove only that it is deterministic.

THE POPULATION IS THE CONFLICTING MERGES, AND GIT DECIDES WHICH THOSE ARE --
`git merge-file`, not us. GIT ALSO DECIDES THE HUNKS: the conflict regions are
read out of `git merge-file --diff3`, so a hunk is a thing git refused to
merge, never a thing this survey chose to call one. Each region is located in
the landed file and in the union output by the unchanged context lines that
bound it; a region that cannot be anchored UNIQUELY in both is `unanchorable`
and is never counted as agreement.

VERDICTS, worst last:
  exact               byte-for-byte what the human landed
  blank_lines_only    the same lines, differing only in blank lines at a seam
  order_only          the same lines, the two blocks in the other order
  union_kept_more     the union kept side text the human DROPPED while
                      resolving. Reported, never counted as agreement -- and
                      on this pair it caught a real loss that reached main
  human_also_edited   the only text the union lacks appears on NO side: the
                      human wrote it WHILE resolving. Not a union defect -- a
                      rebase resolution is not supposed to invent prose
  union_lost_content  THE FINDING. Text that landed, and that came from a
                      side, is absent from the union
  unanchorable        the region could not be located. UNMEASURED, not clean
  refused             the engine declined the file; the rung escalates, as today

    python -m tools.kanban.artifact_pin_union_survey
    python -m tools.kanban.artifact_pin_union_survey --json
    python -m tools.kanban.artifact_pin_union_survey --refusals   # what it BUYS
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.union_resolver import (  # noqa: E402
    UnionRefused,
    canonical_path,
    load_declared_rules,
    match_declaration,
    merge_three_way,
)

#: The pair this card declares. Both are append-shaped for the same reason
#: CLAUDE.md is: one block per card, inserted at the same point by every
#: sibling. `--file` surveys anything else without declaring it.
DEFAULT_FILES = (
    "args/pinned_artifacts.yaml",
    "tests/airgap/test_artifact_freshness.py",
)
DEFAULT_RULES = ("keep_both_blocks", "adjacent_edits")

_SEVERITY = {
    "exact": 0, "blank_lines_only": 1, "order_only": 2, "union_kept_more": 3,
    "human_also_edited": 4, "union_lost_content": 5, "unanchorable": 6, "refused": 7,
}
#: A verdict that says the union reproduced the human's content. `order_only`
#: is in: for an append-shaped file the ORDER of two inserted blocks is decided
#: by which side is main, and neither order drops a line.
AGREEING = frozenset({"exact", "blank_lines_only", "order_only"})

_MAX_ANCHOR = 40


def _git(root: pathlib.Path, *args: str) -> Optional[str]:
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def _blob(root: pathlib.Path, rev: str, rel: str) -> Optional[List[str]]:
    out = _git(root, "show", f"{rev}:{rel}")
    return None if out is None else out.splitlines(keepends=True)


def _merge_file_diff3(base: Sequence[str], a: Sequence[str],
                      b: Sequence[str]) -> Tuple[bool, List[str]]:
    """(git_conflicted, diff3 output). GIT answers both questions, not us."""
    with tempfile.TemporaryDirectory() as td:
        paths = {}
        for name, lines in (("base", base), ("a", a), ("b", b)):
            paths[name] = os.path.join(td, name)
            with open(paths[name], "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
        proc = subprocess.run(
            ["git", "merge-file", "-p", "--diff3", paths["a"], paths["base"], paths["b"]],
            capture_output=True)
    text = proc.stdout.decode("utf-8", "replace")
    return proc.returncode != 0, text.splitlines(keepends=True)


_OPEN = re.compile(r"^<{7}(?: |$)")
_MID = re.compile(r"^\|{7}(?: |$)")
_SEP = re.compile(r"^={7}(?: |$)")
_CLOSE = re.compile(r"^>{7}(?: |$)")


def _parse_diff3(lines: Sequence[str]) -> List[Dict[str, Any]]:
    """git's own conflict regions, with the clean text between them.

    Returns an alternating list of {"kind": "clean"|"conflict", ...}. A
    `conflict` carries the three sides git could not reconcile, which is what
    makes "did this text come from a side?" answerable PER HUNK.
    """
    out: List[Dict[str, Any]] = []
    clean: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        if not _OPEN.match(lines[i]):
            clean.append(lines[i])
            i += 1
            continue
        out.append({"kind": "clean", "lines": clean})
        clean = []
        i += 1
        ours: List[str] = []
        while i < n and not _MID.match(lines[i]) and not _SEP.match(lines[i]):
            ours.append(lines[i])
            i += 1
        bse: List[str] = []
        if i < n and _MID.match(lines[i]):
            i += 1
            while i < n and not _SEP.match(lines[i]):
                bse.append(lines[i])
                i += 1
        if i < n and _SEP.match(lines[i]):
            i += 1
        theirs: List[str] = []
        while i < n and not _CLOSE.match(lines[i]):
            theirs.append(lines[i])
            i += 1
        if i < n:
            i += 1
        out.append({"kind": "conflict", "ours": ours, "base": bse, "theirs": theirs})
    out.append({"kind": "clean", "lines": clean})
    return out


def _find_all(hay: Sequence[str], needle: Sequence[str], start: int = 0) -> List[int]:
    if not needle:
        return []
    hits, k, m = [], len(needle), len(hay)
    for idx in range(start, m - k + 1):
        if list(hay[idx:idx + k]) == list(needle):
            hits.append(idx)
    return hits


def _locate(doc: Sequence[str], before: Sequence[str],
            after: Sequence[str]) -> Optional[Tuple[int, int]]:
    """The (lo, hi) region of `doc` bounded by these exact anchors, or None.

    None means "not located UNIQUELY", which the caller reports as
    `unanchorable` rather than guessing -- an anchor matching twice would
    silently compare the wrong region and call the result agreement.
    """
    if before:
        starts = _find_all(doc, before)
        if len(starts) != 1:
            return None
        lo = starts[0] + len(before)
    else:
        lo = 0
    if after:
        ends = _find_all(doc, after, lo)
        if len(ends) != 1:
            return None
        hi = ends[0]
    else:
        hi = len(doc)
    return (lo, hi) if hi >= lo else None


def _anchored_region(doc: Sequence[str], before_pool: Sequence[str],
                     after_pool: Sequence[str]) -> Optional[List[str]]:
    """Grow the context anchors until the region is unique, or give up."""
    if not before_pool and not after_pool:
        return list(doc)      # no context anywhere: the whole file IS the region
    limit = min(_MAX_ANCHOR, max(len(before_pool), len(after_pool)))
    for k in range(1, limit + 1):
        before = list(before_pool[-k:]) if before_pool else []
        after = list(after_pool[:k]) if after_pool else []
        span = _locate(doc, before, after)
        if span is not None:
            return list(doc[span[0]:span[1]])
    return None


def _nonblank(lines: Sequence[str]) -> List[str]:
    return [ln for ln in lines if ln.strip()]


def _classify(landed: Sequence[str], union: Sequence[str],
              side_lines: Sequence[str]) -> Dict[str, Any]:
    """One hunk's verdict. `side_lines` is every line EITHER side offered here.

    The provenance question is the one a whole-file verdict cannot ask: a line
    that landed, is missing from the union, and appears on NO side was written
    by the human DURING the resolution. The union could not have produced it
    and its absence is not a union defect -- `human_also_edited`, never folded
    into `union_lost_content`.
    """
    if list(landed) == list(union):
        return {"verdict": "exact", "lost": 0, "extra": 0, "invented": 0}
    lb, ub = _nonblank(landed), _nonblank(union)
    if lb == ub:
        return {"verdict": "blank_lines_only", "lost": 0, "extra": 0, "invented": 0}
    lost = collections.Counter(lb) - collections.Counter(ub)
    extra = collections.Counter(ub) - collections.Counter(lb)
    if not lost and not extra:
        return {"verdict": "order_only", "lost": 0, "extra": 0, "invented": 0}
    known = collections.Counter(_nonblank(side_lines))
    from_side = sum(n for line, n in lost.items() if known.get(line))
    invented = sum(n for line, n in lost.items() if not known.get(line))
    extra_n = sum(extra.values())
    if from_side:
        verdict = "union_lost_content"
    elif invented:
        verdict = "human_also_edited"
    else:
        verdict = "union_kept_more"
    return {"verdict": verdict, "lost": from_side, "extra": extra_n, "invented": invented}


def _hunks(base: Sequence[str], main: Sequence[str], card: Sequence[str],
           landed: Sequence[str], rules: Sequence[str]) -> Dict[str, Any]:
    """Per-hunk verdicts for ONE orientation.

    `main` is the side the rung rebases ONTO; `card` is the commit replayed.
    """
    segs = _parse_diff3(_merge_file_diff3(base, main, card)[1])
    union: Optional[List[str]] = None
    notes: List[str] = []
    reason = ""
    try:
        union, notes = merge_three_way(base, main, card, list(rules))
    except UnionRefused as exc:
        # The refusal is per FILE, but it is still reported per hunk -- git's
        # own hunk count is the denominator either way, so a file that refuses
        # over three hunks is not filed as one datum beside a file that
        # resolved three.
        reason = str(exc)[:200]
    hunks: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segs):
        if seg["kind"] != "conflict":
            continue
        if union is None:
            hunks.append({"verdict": "refused", "lost": None, "extra": None,
                          "invented": None, "base_lines": len(seg["base"]),
                          "main_lines": len(seg["ours"]), "card_lines": len(seg["theirs"])})
            continue
        before = segs[idx - 1]["lines"] if idx and segs[idx - 1]["kind"] == "clean" else []
        after = (segs[idx + 1]["lines"]
                 if idx + 1 < len(segs) and segs[idx + 1]["kind"] == "clean" else [])
        r_landed = _anchored_region(landed, before, after)
        r_union = _anchored_region(union, before, after)
        if r_landed is None or r_union is None:
            out: Dict[str, Any] = {"verdict": "unanchorable", "lost": None,
                                   "extra": None, "invented": None}
        else:
            out = _classify(r_landed, r_union, list(seg["ours"]) + list(seg["theirs"]))
        out.update(base_lines=len(seg["base"]), main_lines=len(seg["ours"]),
                   card_lines=len(seg["theirs"]))
        hunks.append(out)
    worst = (max((h["verdict"] for h in hunks), key=lambda v: _SEVERITY[v])
             if hunks else ("refused" if union is None else "exact"))
    return {"file_verdict": worst, "reason": reason, "rules": notes, "hunks": hunks}


def _survey_one(root: pathlib.Path, rel: str, rules: Sequence[str],
                ref: str) -> Dict[str, Any]:
    log = _git(root, "log", "--merges", "--format=%H", ref, "--", rel)
    if log is None:
        return {"file": rel, "measurable": False,
                "reason": f"cannot read merge history for {rel} on {ref}",
                "merge_commits_touching_file": None, "skipped": {},
                "conflicting": None, "file_verdicts": {}, "cases": []}
    shas = [ln.strip() for ln in log.splitlines() if ln.strip()]
    cases: List[Dict[str, Any]] = []
    skipped: "collections.Counter[str]" = collections.Counter()
    for sha in shas:
        parents = (_git(root, "log", "-1", "--format=%P", sha) or "").split()
        if len(parents) != 2:
            skipped["not_two_parent"] += 1
            continue
        p1, p2 = parents
        mb = (_git(root, "merge-base", p1, p2) or "").strip()
        if not mb:
            skipped["no_merge_base"] += 1
            continue
        base = _blob(root, mb, rel)
        side_a, side_b = _blob(root, p1, rel), _blob(root, p2, rel)
        landed = _blob(root, sha, rel)
        if any(x is None for x in (base, side_a, side_b, landed)):
            skipped["file_absent_on_a_side"] += 1
            continue
        if side_a == base and side_b == base:
            skipped["neither_side_changed"] += 1
            continue
        if side_a == base or side_b == base:
            # Only one side touched it: git takes that side and there is no
            # conflict to resolve. Not evidence about the union either way.
            skipped["one_side_changed"] += 1
            continue
        if not _merge_file_diff3(base, side_a, side_b)[0]:
            skipped["git_merged_cleanly"] += 1
            continue
        orientations = {
            # `p2_as_main` is the RUNG'S orientation for these merges: the
            # human ran `git merge main` from the card branch, so p2 is main,
            # and the rung rebases the card ONTO main. The other is reported
            # beside it, because a merge commit does not record which was which
            # and an orientation that changed the CONTENT is a finding itself.
            "p1_as_main": _hunks(base, side_a, side_b, landed, rules),
            "p2_as_main": _hunks(base, side_b, side_a, landed, rules),
        }
        best = min(orientations, key=lambda n: _SEVERITY[orientations[n]["file_verdict"]])
        cases.append({
            "sha": sha[:9],
            "subject": (_git(root, "log", "-1", "--format=%s", sha) or "").strip()[:66],
            "file_verdict": orientations[best]["file_verdict"],
            "best_orientation": best,
            "rung_orientation": "p2_as_main",
            "rules": orientations[best]["rules"],
            "reason": orientations[best]["reason"],
            "hunks": orientations[best]["hunks"],
            "orientations": {n: o["file_verdict"] for n, o in orientations.items()},
        })
    return {
        "file": rel, "measurable": bool(cases),
        "merge_commits_touching_file": len(shas), "skipped": dict(skipped),
        "conflicting": len(cases),
        "file_verdicts": dict(collections.Counter(c["file_verdict"] for c in cases)),
        "cases": cases,
    }


def survey(files: Sequence[str] = DEFAULT_FILES, rules: Sequence[str] = DEFAULT_RULES,
           root: Optional[pathlib.Path] = None, ref: str = "origin/main") -> Dict[str, Any]:
    root = root or _REPO_ROOT
    reports = [_survey_one(root, rel, rules, ref) for rel in files]
    tally: "collections.Counter[str]" = collections.Counter()
    measured = False
    for rep in reports:
        for case in rep["cases"]:
            measured = True
            for hunk in case["hunks"]:
                tally[hunk["verdict"]] += 1
    return {
        "ref": ref, "rules": list(rules), "files": list(files),
        "measurable": measured,
        "hunks": sum(tally.values()) if measured else None,
        "hunk_tally": dict(tally),
        "agreeing": (sum(n for v, n in tally.items() if v in AGREEING)
                     if measured else None),
        # None, never 0, when nothing was measured: "no hunk lost content" and
        # "no hunk was measured" justify opposite decisions.
        "lost_content": (tally.get("union_lost_content", 0) if measured else None),
        "reports": reports,
    }


# ── what the declaration BUYS, over the RECORDED refusals ───────────────────
_FILES_RE = re.compile(r"files=\[([^\]]*)\]")


def refusal_files(reason: str) -> List[str]:
    m = _FILES_RE.search(reason or "")
    if not m:
        return []
    return [p.strip().strip("'\"") for p in m.group(1).split(",") if p.strip()]


def replay_refusals(declarations: Optional[Sequence[Dict[str, Any]]] = None,
                    rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Every recorded `union_refused` row, re-asked of the SHIPPED matcher.

    A refusal becomes a candidate only when EVERY file in its set is declared:
    kpr-watch-14 measured that a PARTIAL resolution buys zero, because
    `rebase_recovery` aborts and deletes the scratch worktree on any outcome
    that is not `resolved`.
    """
    if declarations is None:
        declarations = load_declared_rules().get("files") or []
    if rows is None:
        rows = _load_refusal_rows()
    if rows is None:
        return {"measurable": False, "reason": "board unreachable -- no refusal corpus",
                "rows": None, "now_declared": None, "still_refused": None,
                "share_now_declared": None, "undeclared_paths": {}, "by_task": {}}
    now = still = 0
    undeclared: "collections.Counter[str]" = collections.Counter()
    by_task: Dict[str, Dict[str, int]] = {}
    for row in rows:
        files = refusal_files(row.get("reason") or "")
        slot = by_task.setdefault(row.get("task_id") or "?",
                                  {"rows": 0, "now_declared": 0, "still_refused": 0})
        slot["rows"] += 1
        missing = [f for f in files if match_declaration(f, declarations) is None]
        for path in missing:
            undeclared[canonical_path(path)] += 1
        if missing or not files:
            still += 1
            slot["still_refused"] += 1
        else:
            now += 1
            slot["now_declared"] += 1
    total = len(rows)
    return {
        "measurable": bool(rows), "rows": total,
        "now_declared": now, "still_refused": still,
        "share_now_declared": (round(100.0 * now / total, 1) if total else None),
        "undeclared_paths": dict(undeclared.most_common()),
        "by_task": by_task,
    }


def replay_delta(paths: Sequence[str] = DEFAULT_FILES,
                 rows: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What THESE declarations buy, over the corpus, against the same corpus
    WITHOUT them.

    A cumulative "86 of 118 now resolve" would credit this card with
    kpr-watch-14's CLAUDE.md entry and every entry before it. The delta is the
    only number that answers "what does declaring THIS pair buy".
    """
    declared = list(load_declared_rules().get("files") or [])
    canon = {canonical_path(p) for p in paths}
    without = [d for d in declared
               if canonical_path(str(d.get("path") or "")) not in canon]
    if rows is None:
        rows = _load_refusal_rows()
    after = replay_refusals(declared, rows)
    before = replay_refusals(without, rows)
    if not after["measurable"]:
        return {"measurable": False, "reason": after.get("reason"),
                "paths": list(paths), "before": before, "after": after,
                "delta_now_declared": None, "still_refused": None}
    return {
        "measurable": True, "paths": list(paths),
        "declared_entries_removed": len(declared) - len(without),
        "rows": after["rows"],
        "before_now_declared": before["now_declared"],
        "after_now_declared": after["now_declared"],
        "delta_now_declared": after["now_declared"] - before["now_declared"],
        "still_refused": after["still_refused"],
        "undeclared_paths": after["undeclared_paths"],
        "by_task": {t: s for t, s in after["by_task"].items()
                    if s["now_declared"] and not before["by_task"][t]["now_declared"]},
        "before": before, "after": after,
    }


def _load_refusal_rows() -> Optional[List[Dict[str, Any]]]:
    """The corpus, or None. None is UNMEASURABLE -- never an empty corpus."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT details FROM audit_trail "
                        "WHERE action = 'pr_watcher.union_refused' ORDER BY id")
            rows: List[Dict[str, Any]] = []
            for rec in cur.fetchall():
                raw = rec.get("details") if isinstance(rec, dict) else rec[0]
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except ValueError:
                        continue
                if isinstance(raw, dict):
                    rows.append(raw)
            return rows
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- a fresh worktree has no board
        print(f"# refusal corpus unavailable: {exc}", file=sys.stderr)
        return None


def _print(report: Dict[str, Any]) -> None:
    print(f"rules={report['rules']}  ref={report['ref']}")
    for rep in report["reports"]:
        print()
        print(rep["file"])
        if not rep["measurable"]:
            detail = (rep.get("reason")
                      or (rep["skipped"] if rep["skipped"]
                          else f"{rep['merge_commits_touching_file']} merge commit(s) "
                               f"touch it on {report['ref']}; none reached the rung"))
            print(f"  UNMEASURABLE: {detail}")
            continue
        print(f"  {rep['merge_commits_touching_file']} merge commits touch it; "
              f"{rep['conflicting']} CONFLICTED in git and are the population")
        for key, num in sorted(rep["skipped"].items(), key=lambda kv: -kv[1]):
            print(f"    skipped {num:3d}  {key}")
        for case in rep["cases"]:
            both = " ".join(f"{n}={v}" for n, v in case["orientations"].items())
            print(f"  {case['sha']}  {case['file_verdict']:18s} {case['subject']}")
            print(f"             {both}   rung={case['rung_orientation']}")
            if case["reason"]:
                print(f"             {case['reason']}")
            for num, hunk in enumerate(case["hunks"], 1):
                print(f"      hunk {num}  {hunk['verdict']:18s} "
                      f"base={hunk['base_lines']:3d} main={hunk['main_lines']:3d} "
                      f"card={hunk['card_lines']:3d}  lost={hunk['lost']} "
                      f"extra={hunk['extra']} invented={hunk['invented']}")
    print()
    if not report["measurable"]:
        print("UNMEASURABLE: no conflicting merge of any surveyed file.")
        print("This is not a clean bill of health.")
        return
    print(f"HUNKS: {report['hunks']}   agreeing={report['agreeing']}   "
          f"lost_content={report['lost_content']}")
    for key, num in sorted(report["hunk_tally"].items(), key=lambda kv: -kv[1]):
        print(f"   {num:4d}  {key}")


def _print_refusals(rep: Dict[str, Any]) -> None:
    if not rep["measurable"]:
        print(f"UNMEASURABLE: {rep.get('reason')}")
        print("This is not a clean bill of health.")
        return
    print(f"recorded pr_watcher.union_refused rows: {rep['rows']}")
    print(f"  now fully declared -- the rung would run : {rep['now_declared']}"
          f"  ({rep['share_now_declared']}%)")
    print(f"  still refused                           : {rep['still_refused']}")
    print("  undeclared paths still named (every occurrence):")
    for path, num in list(rep["undeclared_paths"].items())[:20]:
        print(f"    {num:4d}  {path}")
    print("  by task:")
    for task, slot in sorted(rep["by_task"].items(), key=lambda kv: -kv[1]["rows"]):
        print(f"    {slot['rows']:4d} rows  now={slot['now_declared']:4d} "
              f"still={slot['still_refused']:4d}  {task}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", action="append", dest="files",
                    help="repeatable; defaults to the declared artifact-pin pair")
    ap.add_argument("--rules", default=",".join(DEFAULT_RULES))
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--refusals", action="store_true",
                    help="replay the recorded union_refused rows through the SHIPPED matcher")
    ap.add_argument("--delta", action="store_true",
                    help="the same replay WITH and WITHOUT these declarations")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.delta:
        rep = replay_delta(tuple(args.files or DEFAULT_FILES))
        if args.json:
            print(json.dumps(rep, indent=2))
        elif not rep["measurable"]:
            print(f"UNMEASURABLE: {rep.get('reason')}")
            print("This is not a clean bill of health.")
        else:
            print(f"recorded pr_watcher.union_refused rows: {rep['rows']}")
            print(f"  resolvable WITHOUT these {rep['declared_entries_removed']} "
                  f"entries : {rep['before_now_declared']}")
            print(f"  resolvable WITH them                   : {rep['after_now_declared']}")
            print(f"  DELTA -- what declaring this pair buys : {rep['delta_now_declared']}")
            print(f"  still refused                          : {rep['still_refused']}")
            print("  tasks that clear only because of these entries:")
            for task, slot in sorted(rep["by_task"].items(), key=lambda kv: -kv[1]["rows"]):
                print(f"    {slot['now_declared']:4d} of {slot['rows']:4d} rows  {task}")
        return 0 if rep["measurable"] else 2

    if args.refusals:
        rep = replay_refusals()
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            _print_refusals(rep)
        return 0 if rep["measurable"] else 2

    rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    report = survey(tuple(args.files or DEFAULT_FILES), rules, ref=args.ref)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    # Exit 2 = the survey could not be produced, which is never the same as a
    # clean survey. A finding is still exit 0: this reports, it does not gate.
    return 0 if report["measurable"] else 2


if __name__ == "__main__":
    sys.exit(main())
