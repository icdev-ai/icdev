# CUI // SP-CTI
"""Is this open PR's work ALREADY LANDED through a MERGED sibling? (mfx-mrg-02)

THE DEFECT, four times in two days. #2015's single commit merged in #2014
FORTY-TWO SECONDS earlier; #1985's in #1983, eighty-two seconds earlier; #2056
was the kpr-stale-06 branch re-opened after #2053 had already squashed it; and
#2049 (kpr-stale-05) had both of its commits absorbed into #2053. Each then sat
CONFLICTING or "ahead" forever, and -- because it was still OPEN -- each held
`no_sibling_conflict` against every other PR touching the same files. #2015
blocked #2016 on compliance_server.py. Every one was diagnosed by a human
running `gh pr view <sibling> --json commits` and closed by hand.

NOTHING IN THE WATCHER COULD SEE IT, and each of the existing guards asks a
question that is nearly this one and is not:

  landed_check      task -> MAIN, by grepping commit SUBJECTS for the task id.
                    #2053's subject names kpr-stale-06, so kpr-stale-05's work
                    landing inside it is invisible.
  behind_main       how far the branch is behind. A duplicate at the SAME head
                    sha as a merged PR can be 0 behind and still be a duplicate.
  sibling_conflict  which OPEN PRs share a file. It is the thing the duplicate
                    was jamming, not the thing that finds it.
  _supersede_stale_prs (reflexes/kanban.py) closes a task's OTHER OPEN PRs at
                    PR-open time, same task only. Three of the four cases here
                    are a MERGED sibling, and one is a different task.

THE SIGNAL IS THE COMMIT LIST, and it is exact. GitHub keeps a merged PR's
commit oids whatever merge method was used, so a squash -- which leaves NOTHING
on main with the branch's shas -- is still answerable. Measured 2026-09-05 over
the live board: of the 15 open PRs, EIGHT were duplicates of an already-merged
PR at an IDENTICAL head sha, and the two closed-unmerged PRs in the recent
window were the two known incidents. See
docs/audits/mfx-mrg-02-superseded-pr-survey.md.

TWO LEGS, and the second is a corroborator, not a substitute.

  shared_commits  our head sha is IN a merged family sibling's commit list, AND
                  every commit we can see is in that list. Requiring the HEAD
                  is what closes the truncation hole: `gh` caps the commits
                  connection, and a short list of OURS would make the subset
                  test trivially true.
  pure_revert     git says every commit on the branch is already upstream BY
                  PATCH ID (`git cherry`, all `-`) while the two-dot diff
                  against main is NOT empty -- i.e. merging it would remove what
                  main has. This is the cherry-picked / rebased duplicate, whose
                  shas differ. It requires a NAMED family sibling so the close
                  can always cite one. MEASURED CONTRIBUTION ON THIS BOARD: 0 --
                  it fires on 3 of 15 open PRs and all three are a strict subset
                  of the first leg. It is kept because the four cases the card
                  was written for are not the only shape, and because it costs
                  two local git calls; it is NOT evidence that it works, and it
                  is off by default (`superseded_revert_leg`).

FAMILY IS REQUIRED for both legs. A merged PR is a sibling when it is on the
SAME head branch, or when its title/body names our head branch or our PR
number. Measured: dropping the requirement adds ZERO fires on this board, so
it costs nothing -- and it is what guarantees the close can name a PR.

FAIL-OPEN EVERYWHERE. This module CLOSES pull requests. An unreadable forge
answer, a missing head sha, an empty commit list and an unreachable git each
produce `checked: False`, which is never a finding -- the same posture
`landed_check` takes for the same class of question.

CLI (survey only; it acts on nothing):

    python -m tools.ci.pr_superseded --survey --json
    python -m tools.ci.pr_superseded --survey --state closed
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess  # nosec B404 — fixed argv, shell=False
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The fields a supersede decision needs. `commits` is the expensive one and the
#: reason `DEFAULT_MERGED_LIMIT` is not larger — see `fetch_merged_prs`.
GH_FIELDS = "number,url,title,body,headRefName,headRefOid,mergedAt,commits"

#: Measured 2026-09-05 against this repo: `--limit 60` is refused by GitHub's
#: GraphQL node budget ("up to 600,000 possible nodes ... maximum limit of
#: 500,000") and `--limit 40` succeeds. The budget scales with how many commits
#: the page's PRs carry, so this is a working default and not a constant of the
#: API — which is why a refusal HALVES and retries rather than giving up.
DEFAULT_MERGED_LIMIT = 40

BASIS_SHARED_COMMITS = "shared_commits"
BASIS_PURE_REVERT = "pure_revert"

_NODE_LIMIT_RE = re.compile(r"exceeds the maximum limit", re.IGNORECASE)


@dataclass
class SupersedeVerdict:
    """Three-valued, and `checked` is the one that matters.

    `checked=False` means the question could not be ASKED — an unreadable forge
    answer, a PR with no head sha, an empty commit list. It is never `superseded
    is False` wearing a different name, because the two justify opposite acts:
    one leaves the PR alone this cycle, the other says the PR is fine.
    """

    checked: bool = False
    superseded: bool = False
    basis: str = ""
    reason: str = ""
    family: str = ""
    sibling_number: Optional[int] = None
    sibling_url: str = ""
    sibling_title: str = ""
    shared_commits: List[str] = field(default_factory=list)
    two_dot_stat: str = ""
    pr_number: Optional[int] = None
    pr_url: str = ""
    head_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checked": self.checked,
            "superseded": self.superseded,
            "basis": self.basis,
            "reason": self.reason,
            "family": self.family,
            "sibling_number": self.sibling_number,
            "sibling_url": self.sibling_url,
            "sibling_title": self.sibling_title,
            "shared_commits": list(self.shared_commits),
            "two_dot_stat": self.two_dot_stat,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "head_ref": self.head_ref,
        }


# ────────────────────────────────────────────────────────────────────────────
# Family
# ────────────────────────────────────────────────────────────────────────────
def family_kind(pr: dict, sibling: dict) -> str:
    """How ``sibling`` is related to ``pr``. ``""`` means it is not.

    Cheapest first, and each is a FACT rather than a similarity score: the same
    head branch, our branch NAMED in the sibling's prose, or our PR number named
    in it. Nothing here guesses from a title resemblance — two cards that
    happened to touch the same subsystem must never look like one PR
    superseding the other.
    """
    try:
        if int(pr.get("number") or 0) == int(sibling.get("number") or -1):
            return ""
    except (TypeError, ValueError):
        return ""
    branch = (pr.get("headRefName") or "").strip()
    if branch and branch == (sibling.get("headRefName") or "").strip():
        return "same_branch"
    text = "%s\n%s" % (sibling.get("body") or "", sibling.get("title") or "")
    if branch and branch in text:
        return "named_branch"
    number = pr.get("number")
    if number and re.search(r"#%d\b" % int(number), text):
        return "named_pr"
    return ""


def _oids(pr: dict) -> List[str]:
    return [str(c.get("oid") or "").strip()
            for c in (pr.get("commits") or []) if (c or {}).get("oid")]


# ────────────────────────────────────────────────────────────────────────────
# The decision
# ────────────────────────────────────────────────────────────────────────────
def decide_superseded(
    pr: dict,
    merged_siblings: Optional[List[dict]],
    *,
    revert: Optional[dict] = None,
) -> SupersedeVerdict:
    """Has this PR's work already landed through a merged sibling?

    ``merged_siblings`` is ``None`` when the listing could not be read — that
    is UNCHECKED, never "no sibling found".
    """
    v = SupersedeVerdict(
        pr_number=pr.get("number"),
        pr_url=pr.get("url") or "",
        head_ref=(pr.get("headRefName") or "").strip(),
        two_dot_stat=str((revert or {}).get("two_dot_stat") or ""),
    )
    if merged_siblings is None:
        v.reason = "merged-PR listing unreadable"
        return v

    head = (pr.get("headRefOid") or "").strip()
    ours = _oids(pr)
    if not head or not ours:
        v.reason = ("no head sha on the PR" if not head
                    else "no commit list on the PR")
        return v

    v.checked = True
    ours_set = set(ours)
    best_family = ""
    best_sibling: Optional[dict] = None

    for sib in merged_siblings:
        if not (sib or {}).get("mergedAt"):
            continue                       # only a MERGED sibling is evidence
        fam = family_kind(pr, sib)
        if not fam:
            continue
        if best_sibling is None:
            best_family, best_sibling = fam, sib
        theirs = set(_oids(sib))
        if head in theirs and ours_set <= theirs:
            v.superseded = True
            v.basis = BASIS_SHARED_COMMITS
            v.family = fam
            v.sibling_number = sib.get("number")
            v.sibling_url = sib.get("url") or ""
            v.sibling_title = sib.get("title") or ""
            v.shared_commits = sorted(ours_set & theirs)
            v.reason = (
                "every commit on %s (%d, head %s) is in merged PR #%s"
                % (v.head_ref or "the branch", len(ours), head[:12],
                   v.sibling_number))
            return v

    # LEG B — the cherry-picked / rebased duplicate, whose shas differ.
    ev = revert or {}
    if (best_sibling is not None
            and ev.get("checked") is True
            and ev.get("all_patches_upstream") is True
            and ev.get("would_revert") is True):
        v.superseded = True
        v.basis = BASIS_PURE_REVERT
        v.family = best_family
        v.sibling_number = best_sibling.get("number")
        v.sibling_url = best_sibling.get("url") or ""
        v.sibling_title = best_sibling.get("title") or ""
        v.shared_commits = []
        v.reason = (
            "every commit on %s is already upstream by patch id, and the "
            "two-dot diff against the base is not empty — merging it would "
            "REVERT what the base has (sibling #%s)"
            % (v.head_ref or "the branch", v.sibling_number))
        return v

    v.family = best_family
    v.reason = ("no merged sibling carries this branch's commits"
                if best_family else "no merged sibling in this task family")
    return v


# ────────────────────────────────────────────────────────────────────────────
# Evidence gathering
# ────────────────────────────────────────────────────────────────────────────
def fetch_merged_prs(
    *,
    runner: Optional[Callable] = None,
    gh_bin: str = "gh",
    limit: int = DEFAULT_MERGED_LIMIT,
    repo: Optional[str] = None,
    state: str = "merged",
) -> Optional[List[dict]]:
    """Recently merged PRs with their commit lists. ``None`` = UNREADABLE.

    ONE call per poll, not one per PR. A GraphQL node-budget refusal HALVES the
    page and retries once: the budget depends on how many commits the page's PRs
    happen to carry, so a fixed limit that works today silently stops working on
    a week of large branches, and the check would then be permanently unchecked
    with nothing on screen to say so.
    """
    run = runner or subprocess.run
    attempts = []
    page = max(1, int(limit))
    while True:
        cmd = [gh_bin, "pr", "list", "--state", state,
               "--json", GH_FIELDS, "--limit", str(page)]
        if repo:
            cmd += ["--repo", repo]
        try:
            proc = run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
        except Exception as exc:  # noqa: BLE001 — advisory, never fatal
            logger.debug("pr_superseded: gh pr list failed: %s", exc)
            return None
        attempts.append(page)
        if getattr(proc, "returncode", 1) == 0:
            try:
                data = json.loads(getattr(proc, "stdout", "") or "[]")
            except json.JSONDecodeError as exc:
                logger.debug("pr_superseded: gh returned non-JSON: %s", exc)
                return None
            return data if isinstance(data, list) else None
        stderr = getattr(proc, "stderr", "") or ""
        if _NODE_LIMIT_RE.search(stderr) and page > 1 and len(attempts) < 3:
            page = max(1, page // 2)
            logger.debug(
                "pr_superseded: gh refused the page on a node budget; "
                "retrying at --limit %d", page)
            continue
        logger.debug("pr_superseded: gh pr list exit=%s stderr=%s",
                     getattr(proc, "returncode", None), stderr[:200])
        return None


def revert_evidence(
    head_ref: str,
    base: str = "main",
    *,
    git_runner: Optional[Callable] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Does this branch add anything main does not already have?

    ``git cherry`` compares by PATCH ID, so a rebase, a cherry-pick and a
    squash-that-kept-one-commit all answer correctly where a sha comparison
    cannot. Every line marked ``-`` means "an equivalent patch is already
    upstream"; a single ``+`` means the branch holds work that is not, and this
    must then never fire.

    ``would_revert`` is the second half and is a different fact: a branch that
    adds nothing AND whose tree differs from the base is one whose merge would
    REMOVE what the base has. A branch that adds nothing and matches the base
    merges to a no-op — worth closing, but not a revert, and calling it one
    would overstate the evidence in the comment.

    Every field is ``None`` when the probe could not run. FAIL-OPEN.
    """
    out: Dict[str, Any] = {
        "checked": False, "all_patches_upstream": None, "would_revert": None,
        "ahead": None, "two_dot_stat": "", "reason": "",
    }
    if not head_ref:
        out["reason"] = "no head branch"
        return out
    run = git_runner or subprocess.run
    root = cwd or str(pathlib.Path(__file__).resolve().parents[2])

    def _git(argv):
        return run(argv, cwd=root, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=120,
                   shell=False)  # nosec B603 — fixed argv, shell=False

    try:
        fetch = _git(["git", "fetch", "--quiet", "origin", base, head_ref])
        if getattr(fetch, "returncode", 1) != 0:
            out["reason"] = "could not fetch %s / %s" % (base, head_ref)
            return out
        cherry = _git(["git", "cherry",
                       "origin/%s" % base, "origin/%s" % head_ref])
        if getattr(cherry, "returncode", 1) != 0:
            out["reason"] = "git cherry failed"
            return out
        diff = _git(["git", "diff", "--shortstat",
                     "origin/%s..origin/%s" % (base, head_ref)])
        if getattr(diff, "returncode", 1) != 0:
            out["reason"] = "git diff failed"
            return out
    except Exception as exc:  # noqa: BLE001 — advisory, never fatal
        out["reason"] = "git probe errored: %s" % exc
        return out

    lines = [ln for ln in (getattr(cherry, "stdout", "") or "").splitlines()
             if ln.strip()]
    stat = (getattr(diff, "stdout", "") or "").strip()
    out["checked"] = True
    out["ahead"] = len(lines)
    out["two_dot_stat"] = stat
    out["all_patches_upstream"] = bool(lines) and not any(
        ln.startswith("+") for ln in lines)
    out["would_revert"] = bool(out["all_patches_upstream"]) and bool(stat)
    return out


# ────────────────────────────────────────────────────────────────────────────
# The comment
# ────────────────────────────────────────────────────────────────────────────
def comment_body(v: SupersedeVerdict) -> str:
    """The evidence, on the PR, before it is closed.

    A close with no derivation is indistinguishable from a bot losing somebody's
    work, so this names the sibling, the shared sha and the two-dot stat, prints
    the command that re-derives it, and says how to undo it.
    """
    lines = [
        "**Superseded — closing.** This PR's work is already on the default "
        "branch through a merged sibling, so merging it now could only "
        "re-apply a diff against a tree that has moved on.",
        "",
        "| evidence | value |",
        "| --- | --- |",
        "| merged sibling | #%s%s |" % (
            v.sibling_number or "?",
            (" — %s" % v.sibling_title[:80]) if v.sibling_title else ""),
        "| relationship | `%s` |" % (v.family or "unknown"),
        "| basis | `%s` |" % (v.basis or "unknown"),
    ]
    if v.shared_commits:
        shown = ", ".join("`%s`" % c[:12] for c in v.shared_commits[:6])
        if len(v.shared_commits) > 6:
            shown += " (+%d more)" % (len(v.shared_commits) - 6)
        lines.append("| shared commit(s) | %s |" % shown)
    if v.two_dot_stat:
        lines.append("| two-dot diff vs base | %s |" % v.two_dot_stat)
    lines += [
        "",
        "Re-derive it:",
        "",
        "```",
        "gh pr view %s --json commits" % (v.sibling_number or "<sibling>"),
        "git cherry origin/main origin/%s" % (v.head_ref or "<branch>"),
        "```",
        "",
        "_Closed by `pr_watcher` (mfx-mrg-02). If this is wrong, **reopen** it "
        "— nothing was force-pushed, deleted or merged, and the branch is "
        "untouched._",
    ]
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Survey CLI — measures, acts on nothing
# ────────────────────────────────────────────────────────────────────────────
def survey(
    *,
    state: str = "open",
    limit: int = DEFAULT_MERGED_LIMIT,
    runner: Optional[Callable] = None,
    repo: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay the classifier over a PR population. Closes nothing."""
    merged = fetch_merged_prs(runner=runner, limit=limit, repo=repo)
    population = fetch_merged_prs(runner=runner, limit=limit, repo=repo,
                                  state=state)
    if population is None:
        return {"checked": False, "state": state,
                "reason": "could not list %s PRs" % state}
    if state == "closed":
        population = [p for p in population if not p.get("mergedAt")]
    rows = []
    for pr in population:
        v = decide_superseded(pr, merged)
        rows.append(v.to_dict())
    fires = [r for r in rows if r["superseded"]]
    unchecked = [r for r in rows if not r["checked"]]
    total = len(rows)
    return {
        "checked": True,
        "state": state,
        "population": total,
        "fires": len(fires),
        # None, never 0.0, over an empty denominator (args/perfect_score_gate.yaml).
        "fire_rate_pct": round(100.0 * len(fires) / total, 2) if total else None,
        "unchecked": len(unchecked),
        "merged_considered": len(merged or []),
        "rows": rows,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--survey", action="store_true",
                    help="replay the classifier over a PR population")
    ap.add_argument("--state", default="open", choices=("open", "closed"),
                    help="which population to survey (default: open)")
    ap.add_argument("--limit", type=int, default=DEFAULT_MERGED_LIMIT)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.survey:
        ap.error("this module acts on nothing; pass --survey")

    out = survey(state=args.state, limit=args.limit, repo=args.repo)
    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if out.get("checked") else 2
    if not out.get("checked"):
        print("UNMEASURABLE: %s" % out.get("reason"))
        return 2
    rate = out["fire_rate_pct"]
    print("population=%s (%s)  merged considered=%s  fires=%s  unchecked=%s  "
          "rate=%s" % (out["population"], out["state"], out["merged_considered"],
                       out["fires"], out["unchecked"],
                       "n/a" if rate is None else "%.2f%%" % rate))
    for r in out["rows"]:
        if not r["superseded"]:
            continue
        print("  #%-6s %-36s -> #%-6s %-14s %s"
              % (r["pr_number"], r["head_ref"], r["sibling_number"],
                 r["family"], r["basis"]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
