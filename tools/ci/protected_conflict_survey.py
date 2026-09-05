#!/usr/bin/env python3
"""Would refusing a protected-path PR BEFORE the conflict arm divert real work?

mfx-mrg-03. `pr_watcher` asks the protected-path question on the MERGEABLE arm
of its ladder (`_refuse_protected`, immediately before `_mark_ready` /
`_auto_merge`) and nowhere else. A PR classified `MERGE_CONFLICT` takes a
DIFFERENT arm -- `_maybe_rebase`, then the resume ladder, then `escalate` -- and
never reaches the rung that would have refused it. PR #2064 changed
`tools/ci/pr_watcher.py`, the FIRST entry in `protected_paths`, and spent 63
rebases, 5 resumes and an escalation without one of its 165 audit rows ever
mentioning `protected`.

The fix asks the question before the conflict arm. Per the standing rule in
CLAUDE.md -- never arm a refusal without measuring its fire rate first -- this
replays every recorded conflict-ladder episode against the SHIPPED predicate
(`merge_readiness.protected_hits`, never a second copy) and reports:

  * how many episodes the divert would have caught, and what each one SPENT;
  * the false-positive question the card names. A pushed `pr_watcher.rebase` is
    the ONLY thing the conflict arm produces that a divert would take away: it
    repaired the branch. A rebase that FAILED, a resume and an escalation all
    leave the PR exactly where it was, so they are savings, not losses.
  * `unmeasurable` -- a PR whose file list the forge would not return. NEVER
    folded into either side: the predicate is fail-closed in production, and a
    survey that quietly read an unreadable PR as clean would understate the fire
    rate of the very control it is measuring.

Report only, deliberately no `--gate` (kpr-fix-03: a survey with a --gate earns
itself a `|| true`). Exit 2 = the survey could not be produced, which is never
the same as a clean survey.
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 -- `gh`, an argv list, never a shell string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from icdev.core.paths import repo_root

BASE_DIR = repo_root(__file__)

#: The ladder rows an episode is made of. `wait` is deliberately absent -- a
#: wait spends nothing, and counting it would make a PR the watcher merely
#: LOOKED at indistinguishable from one it worked on.
LADDER_ACTIONS = (
    "pr_watcher.rebase",
    "pr_watcher.rebase_failed",
    "pr_watcher.resume",
    "pr_watcher.escalate",
)


def config_protected_paths() -> List[str]:
    """The DEPLOYED list, read from the config the watcher itself reads."""
    import yaml

    path = BASE_DIR / "args" / "pr_watcher_config.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return [str(x).strip() for x in (cfg.get("protected_paths") or [])
            if str(x or "").strip()]


def collect_episodes(
    window_days: Optional[int] = None,
    get_connection=None,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(task_id, pr_url) -> what the conflict ladder spent on it.

    Read from `audit_trail`, the watcher's own ledger: no new writer, and no
    second opinion about what happened.
    """
    if get_connection is None:
        from tools.db.storage import get_connection as _gc

        get_connection = _gc
    conn = get_connection()
    try:
        pg = getattr(conn, "_backend", "sqlite") == "postgresql"
        details = "details::text" if pg else "details"
        placeholders = ", ".join(["%s"] * len(LADDER_ACTIONS))
        params: List[Any] = list(LADDER_ACTIONS)
        sql = (
            f"SELECT action, {details} AS d, created_at "  # nosec B608
            f"FROM audit_trail WHERE action IN ({placeholders})"
        )
        if window_days:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=int(window_days))).isoformat()
            sql += " AND created_at >= %s"
            params.append(cutoff)
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw) if not isinstance(raw, dict) else raw
        try:
            payload = json.loads(row.get("d") or "")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        task_id = payload.get("task_id") or ""
        pr_url = payload.get("pr_url") or ""
        if not task_id or not pr_url:
            continue
        act = str(row.get("action") or "").split(".", 1)[-1]
        # ONLY the conflict arm. A resume spent on CI_FAILED or
        # CHANGES_REQUESTED is on a path this change does not touch, and
        # counting it would credit the divert with savings it cannot make.
        if (act in ("resume", "escalate")
                and str(payload.get("classification") or "") != "merge_conflict"):
            continue
        ep = out.setdefault((task_id, pr_url), {
            "task_id": task_id, "pr_url": pr_url,
            "rebase": 0, "rebase_failed": 0, "resume": 0, "escalate": 0,
            "first_seen": None, "last_seen": None,
        })
        ep[act] = ep.get(act, 0) + 1
        when = str(row.get("created_at") or "")
        if when:
            if ep["first_seen"] is None or when < ep["first_seen"]:
                ep["first_seen"] = when
            if ep["last_seen"] is None or when > ep["last_seen"]:
                ep["last_seen"] = when
    # An episode with no rebase attempt and no conflict-classified resume never
    # entered the arm this change touches.
    return {k: v for k, v in out.items()
            if any(v[a] for a in ("rebase", "rebase_failed", "resume", "escalate"))}


def _gh_json(args: List[str], runner=None) -> Optional[Any]:
    run = runner or subprocess.run
    try:
        proc = run(["gh", *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=60)
    except Exception:  # noqa: BLE001
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        return json.loads(proc.stdout or "null")
    except (ValueError, TypeError):
        return None


def fetch_pr_facts(pr_url: str, runner=None) -> Dict[str, Any]:
    """`{files, state, merged}` for one PR; `files: None` when unreadable.

    REST, never GraphQL: the measured outage that produced this card refused
    every `gh pr view` (GraphQL) while `gh api repos/.../pulls/N` answered
    normally, so a survey built on the GraphQL door cannot be run on the day it
    is most needed.
    """
    from tools.ci.pr_watcher import repo_of

    repo = repo_of(pr_url)
    number = str(pr_url).rstrip("/").rsplit("/", 1)[-1]
    if not repo or not number.isdigit():
        return {"files": None, "state": None, "merged": None,
                "reason": "unparseable pr url"}
    files = _gh_json(
        ["api", f"repos/{repo}/pulls/{number}/files", "--paginate"], runner)
    pr = _gh_json(["api", f"repos/{repo}/pulls/{number}"], runner)
    paths = None
    if isinstance(files, list):
        paths = [f.get("filename") for f in files
                 if isinstance(f, dict) and f.get("filename")]
    return {
        "files": paths,
        "state": (pr or {}).get("state") if isinstance(pr, dict) else None,
        "merged": bool(pr.get("merged")) if isinstance(pr, dict) else None,
        "reason": "" if paths is not None else "forge did not return a file list",
    }


def survey(
    window_days: Optional[int] = None,
    limit: Optional[int] = None,
    facts: Optional[Dict[str, Dict[str, Any]]] = None,
    get_connection=None,
    runner=None,
    use_forge: bool = True,
) -> Dict[str, Any]:
    """Replay every conflict-ladder episode through the shipped predicate."""
    from tools.ci.merge_readiness import protected_hits

    protected = config_protected_paths()
    episodes = collect_episodes(window_days, get_connection=get_connection)
    ordered = sorted(
        episodes.values(),
        key=lambda e: -(e["rebase"] + e["rebase_failed"] + e["resume"]),
    )
    if limit:
        ordered = ordered[: int(limit)]

    known = dict(facts or {})
    diverted: List[Dict[str, Any]] = []
    untouched: List[Dict[str, Any]] = []
    unmeasurable: List[Dict[str, Any]] = []

    for ep in ordered:
        pr_url = ep["pr_url"]
        if pr_url not in known:
            known[pr_url] = (
                fetch_pr_facts(pr_url, runner=runner) if use_forge
                else {"files": None, "state": None, "merged": None,
                      "reason": "forge not consulted (--no-forge)"})
        fact = known[pr_url]
        row = dict(ep)
        row["pr_state"] = fact.get("state")
        row["merged"] = fact.get("merged")
        if fact.get("files") is None:
            row["reason"] = fact.get("reason") or "file list unavailable"
            unmeasurable.append(row)
            continue
        hits = protected_hits(fact["files"], protected) or []
        row["protected_hits"] = hits
        if hits:
            row["repaired_by_rebase"] = ep["rebase"] > 0
            diverted.append(row)
        else:
            untouched.append(row)

    measured = len(diverted) + len(untouched)
    spent = {k: sum(r[k] for r in diverted)
             for k in ("rebase", "rebase_failed", "resume", "escalate")}
    false_positives = [r for r in diverted if r["repaired_by_rebase"]]
    # THE TWO SHAPES, and the difference between them is what the survey is FOR.
    #
    #   before_rebase  the card's SUGGESTED shape: hold the PR ahead of
    #                  `_maybe_rebase` so it never enters the ladder at all.
    #                  Costs every rebase the ladder would have pushed.
    #   shipped        ASK and AUDIT the refusal ahead of `_maybe_rebase`, let
    #                  the bounded rebase run, then hold before the resume
    #                  ladder. Loses no rebase by construction, which is why
    #                  `false_positives` is 0 rather than merely small.
    #
    # `sole_action_rebases` is the sharpest number here: an episode where a
    # single pushed rebase was the ONLY thing the ladder did before the PR
    # merged. Those are the ones the suggested shape would have converted into
    # manual work for no gain.
    sole_action = [r for r in diverted
                   if r["rebase"] > 0 and not r["rebase_failed"]
                   and not r["resume"] and not r["escalate"]]
    shapes = {
        "before_rebase": {
            "diverted": len(diverted),
            "false_positives": len(false_positives),
            "false_positive_pct_of_population": (
                round(100.0 * len(false_positives) / measured, 2)
                if measured else None),
            "sole_action_rebases": len(sole_action),
            "rebases_lost": spent["rebase"],
        },
        "shipped": {
            "held": len(diverted),
            "false_positives": 0,
            "rebases_preserved": spent["rebase"],
            "resumes_saved": spent["resume"],
            # Historic `escalate` rows repeat per poll for PRs that predate the
            # escalate-once fix, so this is rows saved, NOT distinct escalations.
            "escalate_rows_saved": spent["escalate"],
            "escalated_episodes_saved": sum(1 for r in diverted if r["escalate"]),
        },
    }
    return {
        "shapes": shapes,
        "sole_action_rebase_episodes": sole_action,
        "protected_paths": protected,
        "window_days": window_days,
        "episodes_total": len(episodes),
        "episodes_examined": len(ordered),
        "measured": measured,
        "unmeasurable": len(unmeasurable),
        # None, never 0.0, over an empty denominator (args/perfect_score_gate.yaml).
        "divert_rate_pct": (round(100.0 * len(diverted) / measured, 2)
                            if measured else None),
        "diverted": len(diverted),
        "untouched": len(untouched),
        "spent_on_diverted": spent,
        "false_positives": len(false_positives),
        "false_positive_rate_pct": (
            round(100.0 * len(false_positives) / measured, 2) if measured else None),
        "diverted_episodes": diverted,
        "false_positive_episodes": false_positives,
        "unmeasurable_episodes": unmeasurable,
        "forge_facts": known,
        "state": ("unmeasurable" if not measured
                  else "findings" if diverted else "clean"),
    }


def render(result: Dict[str, Any]) -> str:
    lines = [
        "Protected-path divert survey (mfx-mrg-03)",
        "=" * 72,
        "protected paths        : %s" % (", ".join(result["protected_paths"])
                                         or "(none)"),
        "conflict episodes      : %d (examined %d)"
        % (result["episodes_total"], result["episodes_examined"]),
        "measured / unmeasurable: %d / %d"
        % (result["measured"], result["unmeasurable"]),
        ("would divert           : %d (%s%%)"
         % (result["diverted"], result["divert_rate_pct"])
         if result["divert_rate_pct"] is not None
         else "would divert           : unmeasurable"),
        "unchanged              : %d" % result["untouched"],
    ]
    s = result["spent_on_diverted"]
    lines.append(
        "spent on diverted      : %d rebase, %d rebase_failed, %d resume, "
        "%d escalate" % (s["rebase"], s["rebase_failed"], s["resume"],
                         s["escalate"]))
    b, s = result["shapes"]["before_rebase"], result["shapes"]["shipped"]
    lines += [
        "",
        "Shape A — hold BEFORE _maybe_rebase (the card's suggested shape):",
        "  false positives      : %d (%s%% of the population); "
        "%d were a single pushed rebase and nothing else"
        % (b["false_positives"], b["false_positive_pct_of_population"],
           b["sole_action_rebases"]),
        "  rebases lost         : %d" % b["rebases_lost"],
        "Shape B — SHIPPED: refuse+audit before _maybe_rebase, keep the bounded",
        "          rebase, hold before the resume ladder:",
        "  false positives      : %d (by construction)" % s["false_positives"],
        "  rebases preserved    : %d" % s["rebases_preserved"],
        "  resumes saved        : %d" % s["resumes_saved"],
        "  escalated episodes   : %d (%d audit rows; historic rows repeat per "
        "poll)" % (s["escalated_episodes_saved"], s["escalate_rows_saved"]),
    ]
    if result["diverted_episodes"]:
        lines += ["", "Diverted episodes:"]
        for r in result["diverted_episodes"]:
            lines.append(
                "  %-22s %-50s rb=%d rbf=%d res=%d esc=%d %-8s hits=%s"
                % (r["task_id"], r["pr_url"], r["rebase"], r["rebase_failed"],
                   r["resume"], r["escalate"],
                   "merged" if r.get("merged") else (r.get("pr_state") or "?"),
                   ",".join(r["protected_hits"])))
    if result["unmeasurable_episodes"]:
        lines += ["", "Unmeasurable (NOT a clean bill of health):"]
        for r in result["unmeasurable_episodes"][:20]:
            lines.append("  %-22s %-50s %s"
                         % (r["task_id"], r["pr_url"], r.get("reason", "")))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--window-days", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-forge", action="store_true",
                    help="do not call gh; every episode reports unmeasurable")
    ap.add_argument("--facts-cache", type=Path, default=None,
                    help="read/write the per-PR forge facts, for offline replay")
    args = ap.parse_args(argv)

    facts: Dict[str, Dict[str, Any]] = {}
    if args.facts_cache and args.facts_cache.exists():
        try:
            facts = json.loads(args.facts_cache.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print("could not read facts cache: %s" % exc, file=sys.stderr)
            return 2
    try:
        result = survey(window_days=args.window_days, limit=args.limit,
                        facts=facts, use_forge=not args.no_forge)
    except Exception as exc:  # noqa: BLE001
        print("survey could not be produced: %s" % exc, file=sys.stderr)
        return 2
    gathered = result.pop("forge_facts", {})
    if args.facts_cache:
        args.facts_cache.write_text(
            json.dumps(gathered, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str) if args.json
          else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
