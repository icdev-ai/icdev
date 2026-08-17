#!/usr/bin/env python3
# CUI // SP-CTI
"""Fire-rate survey for the sibling-conflict hold (kpr-watch-08).

THE QUESTION
============
``pr_watcher``'s ``hold_on_sibling_conflict`` serializes two open PRs that touch
the same source file. ``tools/git/coordination_paths.py`` EXCLUDES the
union-merged paths from that check, on the stated ground that concurrent appends
there resolve without a human.

kpr-watch-07 showed the second half of that is false for the forge: GitHub does
not apply ``.gitattributes`` merge drivers, so two PRs appending to
``args/ci_test_files/core.txt`` genuinely do conflict on the PR. #1777 merging on
2026-08-17 re-DIRTIED all four of its siblings at once.

The obvious response — stop excluding union paths — is exactly the change this
repo has already been burned by. ``GENERATED_PATH_MARKERS`` records it: on
2026-08-09 one shared generated file made every open PR a sibling of every other
and the guard refused all six. One universal file is enough to stop the board,
and ``core.txt`` is plausibly MORE universal than that, because CLAUDE.md
requires every PR that adds a test file to append to it.

So this measures before anything is armed, in the discipline
``tools/hooks/fire_rate_survey.py`` established: a check that is nominally
correct and never measured is not proven.

WHAT IT MEASURES
================
Two postures over the same corpus:

  current   what ships today — union-merged paths are excluded from the check
  widened   union-merged paths COUNT as a collision

For each MERGED PR, the survey reconstructs which PRs were open at the instant
it merged, and asks whether the posture would have HELD it at that moment. It
also reports the largest mutually-conflicting clique, and — the question that
actually decides it — whether any PR was free to proceed at each moment.

The union patterns are read from ``.gitattributes`` rather than hardcoded, so
this cannot drift from the merge rules it is measuring.

Usage::

    python tools/ci/sibling_hold_survey.py --json
    python tools/ci/sibling_hold_survey.py --limit 120
    python tools/ci/sibling_hold_survey.py --open-only
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 — fixed argv, shell=False
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.git.coordination_paths import (  # noqa: E402
    is_coordination_path,
    normalize,
)

#: Postures the survey compares. `current` is what ships.
POSTURES = ("current", "widened")


# ── the union rules, read from the source of truth ──────────────────────────
def union_patterns(repo_root: Optional[Path] = None) -> List[str]:
    """Glob patterns declared ``merge=union`` in ``.gitattributes``.

    Parsed rather than hardcoded: a second list of union paths would be free to
    drift from the merge rules it claims to describe, and this survey exists
    precisely because a claim about union drifted from what the forge does.

    Returns [] when the file is absent — which makes `widened` identical to
    `current`, and the survey says so rather than inventing a difference.
    """
    root = repo_root or _REPO_ROOT
    path = Path(root) / ".gitattributes"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    patterns: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "merge=union" not in line:
            continue
        pattern = line.split()[0]
        if pattern:
            patterns.append(pattern)
    return patterns


def _pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    """gitattributes glob -> regex, where ``*`` does NOT cross ``/``.

    ``fnmatch`` is the wrong tool here and quietly gives the wrong answer:
    its ``*`` matches ``/``, so ``tools/manifest/*.md`` would also claim
    ``tools/manifest/a/b.md`` — a path no union rule covers. Over-claiming
    would make `widened` look milder than it is, which is the one direction
    this survey must not be wrong in.

    A pattern containing no ``/`` matches a basename at any depth, per
    gitattributes; every pattern in use here has one, but the rule is
    implemented rather than assumed.
    """
    pat = normalize(pattern)
    body = "".join(
        "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch)
        for ch in pat
    )
    if "/" not in pat:
        return re.compile(rf"(?:.*/)?{body}$")
    return re.compile(rf"{body}$")


def is_union_merged(path: str, patterns: Sequence[str]) -> bool:
    """True when `path` is covered by a ``merge=union`` rule."""
    p = normalize(path)
    return any(_pattern_to_regex(pattern).match(p) for pattern in patterns)


def is_excluded(path: str, posture: str, patterns: Sequence[str]) -> bool:
    """Whether `path` is ignored by the sibling check under `posture`."""
    if not is_coordination_path(path):
        return False
    if posture == "widened" and is_union_merged(path, patterns):
        # The whole point of `widened`: a union path is a real forge collision.
        return False
    return True


def conflict_files(
    a: Iterable[str], b: Iterable[str], posture: str, patterns: Sequence[str],
) -> set:
    """Files two PRs share that the posture counts as a collision."""
    sa = {normalize(f) for f in a if not is_excluded(f, posture, patterns)}
    sb = {normalize(f) for f in b if not is_excluded(f, posture, patterns)}
    return sa & sb


# ── corpus ──────────────────────────────────────────────────────────────────
def fetch_prs(
    state: str,
    limit: int,
    *,
    runner: Optional[Callable] = None,
    gh_bin: str = "gh",
) -> List[dict]:
    """PRs with the fields the replay needs. Raises on a gh failure."""
    fields = "number,url,createdAt,mergedAt,closedAt,files,headRefName"
    if state == "open":
        # Only meaningful for open PRs, and asking for them on a merged listing
        # makes gh fetch far more than the replay needs.
        fields += ",mergeable,isDraft"
    cmd = [
        gh_bin, "pr", "list", "--state", state, "--limit", str(limit),
        "--json", fields,
    ]
    run = runner or subprocess.run
    proc = run(  # nosec B603 — fixed argv, shell=False
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, shell=False,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(
            f"gh pr list --state {state} failed: "
            f"{(getattr(proc, 'stderr', '') or '').strip()[:200]}"
        )
    data = json.loads(getattr(proc, "stdout", "") or "[]")
    out = []
    for pr in data:
        out.append({
            "number": pr.get("number"),
            "url": pr.get("url") or "",
            "branch": pr.get("headRefName") or "",
            "created": pr.get("createdAt") or "",
            "merged": pr.get("mergedAt") or "",
            "closed": pr.get("closedAt") or "",
            "files": [f.get("path", "") for f in (pr.get("files") or []) if f.get("path")],
            # Absent on a merged listing; only the open snapshot reads them.
            "mergeable": pr.get("mergeable"),
            "draft": bool(pr.get("isDraft")),
        })
    return out


def can_merge(pr: dict) -> bool:
    """Whether this PR could merge at all right now.

    The tie-break keys on PR NUMBER, never on this. That is deliberate — it is
    deterministic and needs no coordination between watcher processes — but it
    means a permanently stuck low-numbered PR holds its whole clique, and the
    hold is re-evaluated every poll, so it never expires.

    Unknown counts as mergeable: over-reporting stuck blockers would overstate
    the case against widening, and this survey has to be honest in the direction
    that is inconvenient for it.
    """
    if pr.get("draft"):
        return False
    m = pr.get("mergeable")
    if m is None:
        return True
    return str(m).upper() != "CONFLICTING"


def _open_at(prs: Sequence[dict], instant: str, exclude_number) -> List[dict]:
    """PRs open at `instant` (ISO-8601 strings compare lexicographically)."""
    out = []
    for pr in prs:
        if pr["number"] == exclude_number or not pr["created"]:
            continue
        if pr["created"] > instant:
            continue
        end = pr["merged"] or pr["closed"] or ""
        if end and end <= instant:
            continue
        out.append(pr)
    return out


# ── the replay ──────────────────────────────────────────────────────────────
def survey(
    prs: Sequence[dict],
    *,
    patterns: Sequence[str],
    postures: Sequence[str] = POSTURES,
) -> dict:
    """Replay each merged PR's merge instant under every posture.

    A PR is HELD when it shares a counted file with an open PR that beats it on
    the tie-break. The tie-break is pr_watcher's: the LOWEST number goes first,
    which is what guarantees a clique always has exactly one member free to
    proceed — so the interesting number is not "can it deadlock" but "how much
    does it serialize".
    """
    merged = sorted(
        (p for p in prs if p["merged"]), key=lambda p: p["merged"]
    )
    report: Dict[str, dict] = {}
    for posture in postures:
        held = 0
        moments = 0
        max_clique = 0
        no_free_moments = 0
        examples: List[dict] = []
        for pr in merged:
            others = _open_at(prs, pr["merged"], pr["number"])
            if not others:
                moments += 1
                continue
            moments += 1
            siblings = []
            for other in others:
                shared = conflict_files(
                    pr["files"], other["files"], posture, patterns)
                if shared:
                    siblings.append((other, shared))
            clique = len(siblings) + 1 if siblings else 1
            max_clique = max(max_clique, clique)
            # pr_watcher's tie-break: a LOWER open PR number goes first.
            blockers = [
                (o, s) for o, s in siblings
                if (o["number"] or 0) < (pr["number"] or 0)
            ]
            if blockers:
                held += 1
                # Nobody free only if every PR in the clique has a blocker —
                # impossible under a lowest-number rule, but assert it rather
                # than assume it.
                lowest = min(
                    [pr["number"]] + [o["number"] for o, _ in siblings]
                )
                if lowest != pr["number"] and not any(
                    o["number"] == lowest for o, _ in siblings
                ):
                    no_free_moments += 1
                if len(examples) < 8:
                    examples.append({
                        "pr": pr["number"],
                        "held_by": [o["number"] for o, _ in blockers][:5],
                        "shared": sorted(
                            {f for _, s in blockers for f in s})[:5],
                    })
        report[posture] = {
            "merge_moments": moments,
            "would_be_held": held,
            "held_pct": round(100.0 * held / moments, 2) if moments else None,
            "max_clique": max_clique,
            "moments_with_nobody_free": no_free_moments,
            "examples": examples,
        }
    return report


def open_snapshot(prs: Sequence[dict], *, patterns: Sequence[str]) -> dict:
    """Same question asked of the PRs open right now — plus the one the
    historical replay structurally cannot answer.

    THE BLIND SPOT. The replay iterates PRs that MERGED and asks whether each was
    held at its own merge moment. A stretch during which nothing could merge
    produces no data points at all, so ``moments_with_nobody_free`` can only ever
    report 0 — it is a tautology, not a reassurance. (The same shape as a gate
    that excludes its maximal case and reports clean.)

    ``held_by_unmergeable`` is the question that actually decides the posture: a
    PR held behind a sibling that CANNOT merge waits forever, because the
    tie-break keys on number and is re-evaluated every poll.
    """
    out: Dict[str, dict] = {}
    for posture in POSTURES:
        held, cliques, stuck = [], [], []
        for pr in prs:
            siblings = [
                o for o in prs
                if o["number"] != pr["number"]
                and conflict_files(pr["files"], o["files"], posture, patterns)
            ]
            cliques.append(len(siblings) + 1)
            blockers = [
                o for o in siblings
                if (o["number"] or 0) < (pr["number"] or 0)
            ]
            if blockers:
                held.append(pr["number"])
                dead = [o["number"] for o in blockers if not can_merge(o)]
                if dead:
                    stuck.append({"pr": pr["number"], "blocked_by": sorted(dead)})
        out[posture] = {
            "open_prs": len(prs),
            "would_be_held": len(held),
            "held_numbers": sorted(held),
            "max_clique": max(cliques) if cliques else 0,
            "free_to_proceed": len(prs) - len(held),
            "held_by_unmergeable": stuck,
        }
    return out


def _render(report: dict) -> str:
    lines: List[str] = []
    pats = report.get("union_patterns") or []
    lines.append(
        f"Sibling-hold survey — {len(pats)} merge=union pattern(s) in .gitattributes"
    )
    for p in pats:
        lines.append(f"    {p}")
    hist = report.get("historical") or {}
    if hist:
        lines.append("")
        lines.append(f"HISTORICAL REPLAY ({report.get('corpus_size', 0)} PRs)")
        for posture in POSTURES:
            r = hist.get(posture)
            if not r:
                continue
            lines.append(
                f"  {posture:9} held at their own merge moment: "
                f"{r['would_be_held']}/{r['merge_moments']} ({r['held_pct']}%)"
                f"   max clique {r['max_clique']}"
                f"   moments with nobody free: {r['moments_with_nobody_free']}"
            )
        for posture in POSTURES:
            for ex in (hist.get(posture) or {}).get("examples", [])[:3]:
                lines.append(
                    f"    [{posture}] #{ex['pr']} held by "
                    f"{', '.join('#%s' % n for n in ex['held_by'])} on "
                    f"{', '.join(ex['shared'])}"
                )
    if hist:
        lines.append("")
        lines.append(
            "  NOTE: 'nobody free' is measured only at moments a merge HAPPENED, "
            "so a stretch\n        where nothing could merge contributes no data "
            "points and it can only report 0.\n        See held_by_unmergeable "
            "below for the question it cannot answer."
        )
    snap = report.get("open_now") or {}
    if snap:
        lines.append("")
        lines.append("OPEN RIGHT NOW")
        for posture in POSTURES:
            r = snap.get(posture)
            if not r:
                continue
            lines.append(
                f"  {posture:9} {r['would_be_held']}/{r['open_prs']} held, "
                f"{r['free_to_proceed']} free, max clique {r['max_clique']}"
                + (f"   held: {r['held_numbers']}" if r["held_numbers"] else "")
            )
            for s in r.get("held_by_unmergeable", []):
                lines.append(
                    f"      !! #{s['pr']} would wait on "
                    f"{', '.join('#%s' % n for n in s['blocked_by'])}, which "
                    f"cannot merge — the tie-break keys on NUMBER, not on "
                    f"mergeability, so this never expires"
                )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fire-rate survey for the sibling-conflict hold")
    ap.add_argument("--limit", type=int, default=120,
                    help="merged PRs to replay (default 120)")
    ap.add_argument("--open-only", action="store_true",
                    help="snapshot the open PRs; skip the historical replay")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    patterns = union_patterns()
    report: Dict[str, object] = {"union_patterns": patterns}
    try:
        open_prs = fetch_prs("open", 200)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        # UNMEASURABLE is not zero. Say so and exit non-zero rather than
        # printing a clean-looking report nobody can act on.
        print(f"sibling-hold survey: corpus unavailable — {exc}", file=sys.stderr)
        return 2
    report["open_now"] = open_snapshot(open_prs, patterns=patterns)

    if not args.open_only:
        try:
            merged = fetch_prs("merged", args.limit)
        except (RuntimeError, OSError, json.JSONDecodeError) as exc:
            print(f"sibling-hold survey: merged corpus unavailable — {exc}",
                  file=sys.stderr)
            return 2
        corpus = merged + open_prs
        report["corpus_size"] = len(corpus)
        report["historical"] = survey(corpus, patterns=patterns)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
