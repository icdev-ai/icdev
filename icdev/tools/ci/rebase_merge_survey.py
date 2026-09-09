#!/usr/bin/env python3
# CUI // SP-CTI
"""Was the branch carrying a MERGE COMMIT when that rebase failed -- and would a
different integration have cleared it? (kpr-watch-15)

THE DEFECT. `pr_watcher._maybe_rebase` runs a default `git rebase`, which does
NOT preserve merges. A branch whose head is a `merge -s ours` supersede -- the
documented recipe for "keep the rebased tree, discard the pre-rebase commit" --
is FLATTENED by that rebase, and the commit the supersede deliberately discarded
is REPLAYED alongside its own replacement. Every later attempt then fails for a
reason that has nothing to do with the conflict the watcher is trying to clear.

WHAT THIS MEASURES, and it is deliberately three separate questions:

  --classify   every recorded `pr_watcher.rebase_failed` row, bucketed
               merge_carried | linear | unmeasurable.
  --replay     for the merge-carrying rows ONLY, what each candidate lever
               would have done, replayed against the branch head and base that
               row actually had. A CONTROL arm (the unchanged plain rebase) runs
               beside them: if it does not reproduce the recorded failure the
               replay is not faithful and the comparison is worthless.
  --successes  the COST side. Every recorded `pr_watcher.rebase` SUCCESS,
               bucketed the same way -- a lever keyed on merge-carriage is only
               free if no rebase that ever WORKED was on such a branch.

HEAD RECONSTRUCTION IS FROM THE REFLOG, NOT FROM TODAY'S HEAD, and the
difference is the whole reliability of the survey. `refs/remotes/origin/kanban/
<id>` carries one reflog entry per fetched push, so the head a row saw is the
newest entry at or before that row's stamp. Asking today's head instead both
OVER-counts (a human's own RESCUE merge, added after the failure, reads as the
cause) and UNDER-counts (a later hand rebase makes the merge unreachable). Both
directions were observed: the head-based reading attributes rows to rmf-ui-05 /
rmf-ui-08 / mfx-sib-02 that were linear at the time, and misses mfx-sib-03's 11.
Each reconstruction is VALIDATED against the row's own audit reason -- the
commit `git rebase` said it "could not apply" must be reachable from the
reconstructed head -- and a row that fails that check is reported, never used.

UNMEASURABLE IS ITS OWN BUCKET and is never folded into `linear`: a deleted
branch has no reflog and cannot be asked, and reading it as clean understates
the finding.

    python -m tools.ci.rebase_merge_survey --classify
    python -m tools.ci.rebase_merge_survey --classify --json
    python -m tools.ci.rebase_merge_survey --successes
    python -m tools.ci.rebase_merge_survey --replay            # runs git; minutes
    python -m tools.ci.rebase_merge_survey --replay --json

Exit 0 = a survey was produced, whatever it says. Exit 2 = it could not be,
which is never the same as a clean survey. Report only, no --gate: it measures
the recorded corpus, not a diff (kpr-fix-03).
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FAIL_ACTION = "pr_watcher.rebase_failed"
OK_ACTION = "pr_watcher.rebase"

#: The three candidate levers this card had to choose between, plus the CONTROL.
#: `refuse` needs no git: it is deterministic on the classification alone.
ARMS = ("plain", "rebase_merges", "merge")

_IDENT = ["-c", "user.name=icdev-rebase-survey", "-c", "user.email=survey@localhost"]
_REFLOG_RE = re.compile(r"^([0-9a-f]+) .*@\{(\d+) ")
_APPLIED_RE = re.compile(r"Could not apply ([0-9a-f]{7,40})")


def _git(args: List[str], cwd: Optional[str] = None, timeout: int = 900):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _utc(value) -> datetime.datetime:
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def read_rows(action: str) -> List[Dict[str, Any]]:
    """Every audit row for `action`, newest last. Raises if the board cannot be read."""
    from tools.db.storage import get_connection

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT created_at, details FROM audit_trail "
            "WHERE action = %s ORDER BY created_at", (action,))
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
            "task_id": det.get("task_id") or "",
            "base_sha": (det.get("base_sha") or "").strip(),
            "reason": det.get("reason") or "",
            "pr_url": det.get("pr_url") or "",
        })
    return out


def _reflog(ref: str, cache: Dict[str, Optional[List[Tuple[str, int]]]]
            ) -> Optional[List[Tuple[str, int]]]:
    if ref not in cache:
        proc = _git(["reflog", "show", "--date=raw", ref], timeout=120)
        if proc.returncode != 0:
            cache[ref] = None
        else:
            hits = [(m.group(1), int(m.group(2)))
                    for m in (_REFLOG_RE.match(l) for l in proc.stdout.splitlines()) if m]
            cache[ref] = sorted(hits, key=lambda e: e[1]) or None
    return cache[ref]


def classify(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Bucket each row: merge_carried | linear | unmeasurable_<why>.

    `head_at` and `base_used` are the branch head and base the row ACTUALLY had,
    reconstructed from the remote-tracking reflogs. `validated` re-derives the
    reconstruction from the row's own reason and is None when the reason names
    no commit (a reason shape that carries nothing to check against).
    """
    cache: Dict[str, Optional[List[Tuple[str, int]]]] = {}
    out: List[Dict[str, Any]] = []
    for row in rows:
        rec = dict(row, bucket=None, head_at=None, base_used=None,
                   validated=None, merges=None)
        stamp = _utc(row["at"]).timestamp()
        log = _reflog("refs/remotes/origin/kanban/%s" % row["task_id"], cache)
        if not log:
            rec["bucket"] = "unmeasurable_no_reflog"
            out.append(rec)
            continue
        prior = [sha for sha, when in log if when <= stamp]
        if not prior:
            rec["bucket"] = "unmeasurable_no_prior_push"
            out.append(rec)
            continue
        rec["head_at"] = head = prior[-1]
        base = row["base_sha"]
        if not base:
            main = _reflog("refs/remotes/origin/main", cache) or []
            earlier = [sha for sha, when in main if when <= stamp]
            base = earlier[-1] if earlier else ""
            rec["base_reconstructed"] = bool(base)
        rec["base_used"] = base
        if not base or _git(["cat-file", "-e", head + "^{commit}"], timeout=60).returncode:
            rec["bucket"] = "unmeasurable_object_gone"
            out.append(rec)
            continue
        log2 = _git(["log", "--merges", "--format=%H", "%s..%s" % (base, head)], timeout=120)
        if log2.returncode != 0:
            rec["bucket"] = "unmeasurable_log_failed"
            out.append(rec)
            continue
        rec["merges"] = [s for s in log2.stdout.split() if s]
        rec["bucket"] = "merge_carried" if rec["merges"] else "linear"
        named = _APPLIED_RE.search(row["reason"])
        if named:
            rec["validated"] = _git(
                ["merge-base", "--is-ancestor", named.group(1), head], timeout=60
            ).returncode == 0
        out.append(rec)
    return out


def _bucket_counts(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = collections.Counter(r["bucket"] for r in records)
    merged = {"merge_carried": counts.get("merge_carried", 0),
              "linear": counts.get("linear", 0),
              "unmeasurable": sum(v for k, v in counts.items()
                                  if k.startswith("unmeasurable"))}
    merged["unmeasurable_detail"] = {k: v for k, v in sorted(counts.items())
                                     if k.startswith("unmeasurable")}
    return merged


def _arm(worktree: str, head: str, base: str, arm: str) -> Dict[str, Any]:
    _git(["rebase", "--abort"], cwd=worktree, timeout=120)
    _git(["merge", "--abort"], cwd=worktree, timeout=120)
    checkout = _git(["-c", "checkout.workers=0", "checkout", "--detach", "-f", head],
                    cwd=worktree)
    if checkout.returncode != 0:
        return {"outcome": "setup_failed", "detail": checkout.stderr[-200:]}
    _git(["clean", "-fdq"], cwd=worktree)
    if arm == "plain":
        proc = _git([*_IDENT, "rebase", base], cwd=worktree)
    elif arm == "rebase_merges":
        proc = _git([*_IDENT, "rebase", "--rebase-merges", base], cwd=worktree)
    else:
        proc = _git([*_IDENT, "merge", "--no-edit", base], cwd=worktree)
    if proc.returncode == 0:
        head_now = _git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        # ALREADY-CURRENT IS A PROPERTY OF THE PAIR, NOT OF THE ARM, and it is
        # asked the same way of all three -- `git merge` says "Already up to
        # date" and `git rebase` says nothing of the sort, so reading it off
        # stdout credits the rebase arms with work no arm did. The branch
        # already contained the base and the forge's conflict was never in the
        # tree: that is the `phantom` class, not a clear.
        already = _git(["merge-base", "--is-ancestor", base, head],
                       cwd=worktree, timeout=60).returncode == 0
        result = {"outcome": "already_current" if already else "integrated",
                  "new_sha": head_now}
    else:
        unmerged = _git(["diff", "--name-only", "--diff-filter=U"],
                        cwd=worktree).stdout.split()
        stopped = _git(["rev-parse", "REBASE_HEAD"], cwd=worktree).stdout.strip() or None
        result = {"outcome": "conflict", "conflicted_files": unmerged,
                  "n_conflicted": len(unmerged), "stopped_on": stopped}
    _git(["rebase", "--abort"], cwd=worktree, timeout=120)
    _git(["merge", "--abort"], cwd=worktree, timeout=120)
    return result


def replay(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Replay every ARM over the merge-carrying rows, deduped by (head, base).

    The scratch worktree is the same isolation `rebase_recovery` uses: a
    throwaway detached checkout under the system temp dir, never the caller's.
    """
    targets = [r for r in records if r["bucket"] == "merge_carried"]
    pairs: List[Tuple[str, str]] = []
    for rec in targets:
        key = (rec["head_at"], rec["base_used"])
        if key not in pairs:
            pairs.append(key)
    tmp = os.path.join(tempfile.gettempdir(), "icdev-rebase-merge-survey")
    shutil.rmtree(tmp, ignore_errors=True)
    _git(["worktree", "prune"])
    add = _git(["-c", "checkout.workers=0", "worktree", "add", "--detach", tmp, "HEAD"])
    if add.returncode != 0:
        raise RuntimeError("scratch worktree failed: " + add.stderr[-300:])
    try:
        outcomes = {}
        for head, base in pairs:
            outcomes["%s|%s" % (head, base)] = {a: _arm(tmp, head, base, a) for a in ARMS}
    finally:
        _git(["worktree", "remove", "--force", tmp])
    per_arm = {a: collections.Counter() for a in ARMS}
    for rec in targets:
        got = outcomes["%s|%s" % (rec["head_at"], rec["base_used"])]
        rec["replay"] = got
        for arm in ARMS:
            per_arm[arm][got[arm]["outcome"]] += 1
    # `refuse` is deterministic: it attempts nothing, so it clears nothing and
    # its whole cost is on the SUCCESS corpus, which --successes measures.
    per_arm["refuse"] = collections.Counter({"not_attempted": len(targets)})
    return {
        "rows": len(targets),
        "distinct_pairs": len(pairs),
        "control_faithful": per_arm["plain"].get("conflict", 0) == len(targets),
        "by_arm": {a: dict(c) for a, c in per_arm.items()},
        # PER ROW, because "what would each lever have done" is a question
        # about each recorded failure and a per-task count cannot answer it.
        "rows_detail": [
            {"task_id": r["task_id"], "at": r["at"], "pr_url": r["pr_url"],
             "head_at": r["head_at"], "base_used": r["base_used"],
             "base_reconstructed": r.get("base_reconstructed", False),
             "arms": {a: r["replay"][a]["outcome"] for a in ARMS},
             "conflicted_files": r["replay"]["merge"].get("conflicted_files", [])}
            for r in targets
        ],
        "by_task": {
            task: {a: dict(collections.Counter(
                r["replay"][a]["outcome"] for r in targets if r["task_id"] == task))
                for a in ARMS}
            for task in sorted({r["task_id"] for r in targets})
        },
        "targets": targets,
    }


def _pct(n: int, total: int) -> Optional[float]:
    # None, never 0.0 or 100.0, over an empty denominator (args/perfect_score_gate.yaml).
    return round(100.0 * n / total, 2) if total else None


def _print_classification(label: str, records: List[Dict[str, Any]]) -> None:
    counts = _bucket_counts(records)
    total = len(records)
    print("%s: %d row(s)" % (label, total))
    if not total:
        print("  UNMEASURABLE: no rows recorded — not a clean bill of health.")
        return
    for key in ("merge_carried", "linear", "unmeasurable"):
        print("  %-14s %4d  %s%%" % (key, counts[key], _pct(counts[key], total)))
    if counts["unmeasurable_detail"]:
        print("    why: %s" % counts["unmeasurable_detail"])
    checked = [r for r in records if r["validated"] is not None]
    bad = [r for r in checked if r["validated"] is False]
    print("  reconstruction validated: %d/%d measurable row(s); %d failed"
          % (len(checked) - len(bad), len(checked), len(bad)))
    for rec in bad:
        print("    UNVALIDATED %s %s" % (rec["task_id"], rec["at"]))
    per_task = collections.Counter(r["task_id"] for r in records
                                   if r["bucket"] == "merge_carried")
    if per_task:
        print("  merge-carrying rows by task:")
        for task, n in per_task.most_common():
            print("    %-24s %d" % (task, n))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[2])
    ap.add_argument("--classify", action="store_true",
                    help="bucket every recorded rebase FAILURE (default)")
    ap.add_argument("--successes", action="store_true",
                    help="bucket every recorded rebase SUCCESS — the cost side")
    ap.add_argument("--replay", action="store_true",
                    help="replay each candidate lever over the merge-carrying rows")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not (args.classify or args.successes or args.replay):
        args.classify = True

    payload: Dict[str, Any] = {}
    try:
        if args.classify or args.replay:
            failures = classify(read_rows(FAIL_ACTION))
            payload["failures"] = {"total": len(failures), **_bucket_counts(failures)}
        if args.successes:
            successes = classify(read_rows(OK_ACTION))
            payload["successes"] = {"total": len(successes), **_bucket_counts(successes)}
        if args.replay:
            payload["replay"] = replay(failures)
    except Exception as exc:  # noqa: BLE001 — exit 2 is "could not measure"
        print("rebase_merge_survey: could not produce the survey: %s" % exc,
              file=sys.stderr)
        return 2

    if args.json:
        def _plain(obj):
            if isinstance(obj, dict):
                return {k: _plain(v) for k, v in obj.items() if k != "targets"}
            if isinstance(obj, list):
                return [_plain(v) for v in obj]
            return obj
        print(json.dumps(_plain(payload), indent=2, default=str))
        return 0

    if args.classify or args.replay:
        _print_classification("pr_watcher.rebase_failed", failures)
    if args.successes:
        print()
        _print_classification("pr_watcher.rebase (SUCCESSES)", successes)
        carried = payload["successes"]["merge_carried"]
        print("  -> a lever keyed on merge-carriage would have changed %d of the "
              "%d measurable success(es)."
              % (carried, payload["successes"]["merge_carried"]
                 + payload["successes"]["linear"]))
    if args.replay:
        rep = payload["replay"]
        print()
        print("REPLAY over %d merge-carrying row(s) (%d distinct head/base pair(s))"
              % (rep["rows"], rep["distinct_pairs"]))
        if not rep["control_faithful"]:
            print("  CONTROL NOT FAITHFUL — the plain-rebase arm did not reproduce "
                  "every recorded failure; the comparison below is UNSOUND.")
        else:
            print("  control: the unchanged plain rebase reproduces %d of %d "
                  "recorded failures." % (rep["rows"], rep["rows"]))
        for arm in (*ARMS, "refuse"):
            got = rep["by_arm"][arm]
            print("  %-14s integrated=%-3s already_current=%-3s conflict=%-3s %s"
                  % (arm, got.get("integrated", 0), got.get("already_current", 0),
                     got.get("conflict", 0),
                     "not_attempted=%d" % got["not_attempted"]
                     if "not_attempted" in got else ""))
        print("  by task:")
        for task, arms in rep["by_task"].items():
            print("    %-24s %s" % (task, {a: dict(v) for a, v in arms.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
