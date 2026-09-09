#!/usr/bin/env python3
# CUI // SP-CTI
"""Would a union of CLAUDE.md have been RIGHT? Replayed against merge history (kpr-watch-14).

`CLAUDE.md` is the single largest cause of `pr_watcher.union_refused` -- 35 of
the 63 lifetime `undeclared:` refusals, 55.6% -- and it is exactly the
append-a-block shape `keep_both_blocks` handles. But a wrong union on THIS file
is a silent edit to the operating rules, so the declaration is not made on the
shape; it is made on a measurement.

GROUND TRUTH IS WHAT ACTUALLY LANDED. For every merge commit on the default
branch that touched the file, the survey reconstructs the three sides from git
(base = merge-base of the two parents, one side per parent), runs the SHIPPED
`merge_three_way`, and compares the result against the file the merge commit
recorded. Nothing here re-implements the union: asking the resolver whether it
agrees with itself would prove only that it is deterministic, which was never
in question.

THE POPULATION IS THE CONFLICTING MERGES, and GIT decides which those are.
A merge git resolved by itself is one the rung never sees, so counting it would
inflate the agreement rate with cases that were never in question.
`git merge-file` answers that, not us.

FIVE VERDICTS, and the three in the middle are the ones worth having:
  exact                 byte-for-byte what the human landed
  blank_lines_only      the same lines, differing only in blank lines at a
                        block seam. Not a content difference
  union_kept_more       the union kept text the merge commit dropped -- the
                        human ALSO edited while resolving. Reported, never
                        counted as agreement
  union_lost_content    THE FINDING. Text that landed is absent from the union
  refused               the engine declined; the rung would escalate, as today

Both parent orientations are tried and the BEST is reported with the other
beside it: a merge commit does not record which side was "main", and for an
append-shaped file the orientation decides only the ORDER of the two inserted
blocks. An orientation that changed the CONTENT would be a finding in itself.

    python -m tools.kanban.claude_md_union_survey
    python -m tools.kanban.claude_md_union_survey --json
    python -m tools.kanban.claude_md_union_survey --file docs/features/x.md --rules keep_both_blocks
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.union_resolver import UnionRefused, merge_three_way  # noqa: E402

DEFAULT_FILE = "CLAUDE.md"
DEFAULT_RULES = ("keep_both_blocks", "adjacent_edits")
# Worst last: the survey reports the BEST orientation, and "best" must be an
# ordering over how much the union would have got wrong, not an alphabetical one.
_SEVERITY = {"exact": 0, "blank_lines_only": 1, "union_kept_more": 2,
             "mixed_difference": 3, "union_lost_content": 4, "refused": 5}


def _git(root: pathlib.Path, *args: str) -> Optional[str]:
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def _blob(root: pathlib.Path, rev: str, rel: str) -> Optional[List[str]]:
    out = _git(root, "show", f"{rev}:{rel}")
    return None if out is None else out.splitlines(keepends=True)


def _git_would_conflict(base: Sequence[str], a: Sequence[str], b: Sequence[str]) -> bool:
    """Ask git, not us. `merge-file` returns >0 when it left conflict markers."""
    with tempfile.TemporaryDirectory() as td:
        paths = {}
        for name, lines in (("base", base), ("a", a), ("b", b)):
            paths[name] = os.path.join(td, name)
            with open(paths[name], "w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
        proc = subprocess.run(
            ["git", "merge-file", "-p", paths["a"], paths["base"], paths["b"]],
            capture_output=True)
    return proc.returncode != 0


def _compare(landed: Sequence[str], merged: Sequence[str]) -> Dict[str, Any]:
    """Set-level, so a block the union merely PLACED differently is not a loss."""
    if "".join(landed) == "".join(merged):
        return {"verdict": "exact", "lost": 0, "extra": 0}
    lost = collections.Counter(landed) - collections.Counter(merged)
    extra = collections.Counter(merged) - collections.Counter(landed)
    lost_ns = sum(n for line, n in lost.items() if line.strip())
    extra_ns = sum(n for line, n in extra.items() if line.strip())
    if not lost_ns and not extra_ns:
        verdict = "blank_lines_only"
    elif lost_ns and extra_ns:
        verdict = "mixed_difference"
    elif lost_ns:
        verdict = "union_lost_content"
    else:
        verdict = "union_kept_more"
    return {"verdict": verdict, "lost": lost_ns, "extra": extra_ns}


def survey(rel: str = DEFAULT_FILE, rules: Sequence[str] = DEFAULT_RULES,
           root: Optional[pathlib.Path] = None, ref: str = "origin/main") -> Dict[str, Any]:
    root = root or _REPO_ROOT
    log = _git(root, "log", "--merges", "--format=%H", ref, "--", rel)
    if log is None:
        return {"file": rel, "ref": ref, "rules": list(rules), "measurable": False,
                "reason": f"cannot read merge history for {rel} on {ref}",
                "merge_commits_touching_file": None, "skipped": {},
                "conflicting": None, "tally": {}, "lost_content": None, "cases": []}
    shas = [line.strip() for line in log.splitlines() if line.strip()]
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
        side_a = _blob(root, p1, rel)
        side_b = _blob(root, p2, rel)
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
        if not _git_would_conflict(base, side_a, side_b):
            skipped["git_merged_cleanly"] += 1
            continue
        orientations: Dict[str, Dict[str, Any]] = {}
        for name, x, y in (("p1_as_main", side_a, side_b), ("p2_as_main", side_b, side_a)):
            try:
                merged, notes = merge_three_way(base, x, y, list(rules))
            except UnionRefused as exc:
                orientations[name] = {"verdict": "refused", "reason": str(exc)[:200],
                                      "rules": [], "lost": None, "extra": None}
                continue
            out = _compare(landed, merged)
            out["rules"] = notes
            orientations[name] = out
        best = min(orientations, key=lambda n: _SEVERITY[orientations[n]["verdict"]])
        cases.append({
            "sha": sha[:9],
            "subject": (_git(root, "log", "-1", "--format=%s", sha) or "").strip()[:70],
            "verdict": orientations[best]["verdict"],
            "best_orientation": best,
            "orientations": orientations,
        })
    tally = collections.Counter(c["verdict"] for c in cases)
    return {
        "file": rel, "ref": ref, "rules": list(rules),
        "measurable": bool(cases),
        "merge_commits_touching_file": len(shas),
        "skipped": dict(skipped),
        "conflicting": len(cases),
        "tally": dict(tally),
        # None, never 0, when nothing was measured: "no case lost content" and
        # "no case was measured" justify opposite decisions.
        "lost_content": (tally.get("union_lost_content", 0) if cases else None),
        "cases": cases,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--rules", default=",".join(DEFAULT_RULES))
    ap.add_argument("--ref", default="origin/main")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    report = survey(args.file, rules, ref=args.ref)
    if args.json:
        print(json.dumps(report, indent=2))
    elif not report["measurable"]:
        print(f"UNMEASURABLE: no conflicting merge of {report['file']} on {report['ref']}"
              f" -- {report.get('reason') or report['skipped']}")
        print("This is not a clean bill of health.")
    else:
        print(f"{report['file']} on {report['ref']}  rules={report['rules']}")
        print(f"  {report['merge_commits_touching_file']} merge commits touch it; "
              f"{report['conflicting']} CONFLICTED in git and are the population")
        for key, num in sorted(report["skipped"].items(), key=lambda kv: -kv[1]):
            print(f"    skipped {num:4d}  {key}")
        print("  verdicts (best orientation):")
        for key, num in sorted(report["tally"].items(), key=lambda kv: -kv[1]):
            print(f"    {num:4d}  {key}")
        print()
        for case in report["cases"]:
            other = " ".join(f"{n}={o['verdict']}" for n, o in case["orientations"].items())
            print(f"  {case['sha']}  {case['verdict']:18s} {case['subject']}")
            print(f"             {other}")
    # Exit 2 = the survey could not be produced, which is never the same as a
    # clean survey. A finding is still exit 0: this reports, it does not gate.
    return 0 if report["measurable"] else 2


if __name__ == "__main__":
    sys.exit(main())
