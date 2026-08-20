#!/usr/bin/env python3
"""Is `E2E (Playwright)` reliable enough to be a REQUIRED check? (crx-test-06)

The promotion is a branch-protection change, and a flaky required check is how
gates get disabled — the failure mode this codebase documents repeatedly. So it
is surveyed first, exactly like the PreToolUse checks and `landed_check` were.

THE MEASUREMENT THAT LOOKS FINE AND IS NOT. E2E has run 25/25 green. That number
cannot support a promotion, because until crx-test-05 the job declared
`needs: [test]`: it only ever ran on branches whose unit suite had ALREADY
passed, and on every other branch GitHub SKIPPED it. So the sample was drawn
from the healthiest branches in the population and the unhealthy ones are not
absent from the numerator — they are absent from the DENOMINATOR, which is the
one place an absence is invisible. Counting a skipped run as "did not fail"
turns selection bias into evidence of reliability.

This tool therefore refuses to produce one number. It splits the population
STRUCTURALLY — a post-unblock run carries a job named `E2E Shard k of N`, which
is a fact about the run itself and cannot drift the way a hardcoded cutoff date
would — and it never merges these five outcomes:

    success       the job ran and passed
    failure       the job ran and failed          <- the only flake signal
    cancelled     an infrastructure event, NOT a verdict about the suite
    skipped       a dependency failed, so the job NEVER RAN. This is the bucket
                  that made 25/25 look like a reliability measurement.
    in_progress   no verdict yet

`flake_rate` is None — never 0.0 — when nothing was exercised. A survey of three
runs is not a 0% flake rate, and a promotion decision must be able to tell
"measured clean" from "never measured", because those two justify opposite
actions.

REPORT ONLY, and deliberately no --gate. This repo already learned (kpr-fix-03)
that a survey shipped with a gate earns itself a `|| true` inside a week. The
verdict is a field in the output that a human reads before touching branch
protection, not a step that blocks a merge.

Usage:
    python tools/ci/e2e_flake_survey.py --json
    python tools/ci/e2e_flake_survey.py --limit 100
    python tools/ci/e2e_flake_survey.py --from-json runs.json   # offline replay
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - pyyaml is a hard dependency of ICDEV
    yaml = None

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "args" / "e2e_promotion.yaml"

# The aggregator that carries the required check name.
DEFAULT_CHECK_NAME = "E2E (Playwright)"
# A post-unblock run is identified by the PRESENCE of a sharded E2E job. This is
# structural: it is read off the run being classified, so it stays correct
# without anybody remembering to update a cutoff date or a commit sha.
DEFAULT_SHARD_PATTERN = r"^E2E Shard \d+ of \d+$"
DEFAULT_MIN_RUNS = 20
DEFAULT_LIMIT = 100
DEFAULT_WORKFLOW = "icdev-ci.yml"

# Outcome buckets. `skipped` is kept apart from every other zero-failure state
# on purpose — see the module docstring.
EXERCISED = ("success", "failure")
NOT_EXERCISED = ("skipped", "cancelled", "in_progress", "absent")


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Claims live in YAML so a rename does not need a code change."""
    cfg: Dict[str, Any] = {
        "check_name": DEFAULT_CHECK_NAME,
        "shard_job_pattern": DEFAULT_SHARD_PATTERN,
        "min_runs": DEFAULT_MIN_RUNS,
        "workflow": DEFAULT_WORKFLOW,
    }
    path = path or CONFIG_PATH
    if yaml is not None and path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                cfg.update({k: v for k, v in loaded.items() if v is not None})
        except Exception:
            # A malformed config must not fabricate a verdict; the defaults are
            # the same claims the docstring states.
            pass
    return cfg


def classify_run(run: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """One run -> (population, outcome). Never raises on a partial payload."""
    jobs = run.get("jobs") or []
    shard_re = re.compile(cfg["shard_job_pattern"])
    check_name = cfg["check_name"]

    shard_jobs = [j for j in jobs if shard_re.match(str(j.get("name", "")))]
    agg_jobs = [j for j in jobs if str(j.get("name", "")) == check_name]

    # STRUCTURAL, not chronological. A run that fanned E2E across shards is a
    # post-unblock run by construction.
    population = "post_unblock" if shard_jobs else "pre_unblock"

    # The aggregator is the verdict when present; before crx-test-05 it was also
    # the only E2E job, so the same lookup serves both eras.
    considered = agg_jobs or shard_jobs
    if not considered:
        outcome = "absent"
    else:
        conclusions = [str(j.get("conclusion") or "").lower() for j in considered]
        if any(c == "failure" for c in conclusions):
            outcome = "failure"
        elif any(c == "cancelled" for c in conclusions):
            # NOT a failure and NOT a success. A cancelled suite never finished,
            # so it says nothing about flakiness in either direction.
            outcome = "cancelled"
        elif any(c == "" for c in conclusions):
            outcome = "in_progress"
        elif all(c == "skipped" for c in conclusions):
            outcome = "skipped"
        elif any(c == "success" for c in conclusions):
            outcome = "success"
        else:
            outcome = "cancelled"

    return {
        "run_id": run.get("databaseId"),
        "branch": run.get("headBranch"),
        "event": run.get("event"),
        "created_at": run.get("createdAt"),
        "population": population,
        "outcome": outcome,
        "shard_count": len(shard_jobs),
    }


def summarise(classified: List[Dict[str, Any]], population: str,
              min_runs: int) -> Dict[str, Any]:
    rows = [r for r in classified if r["population"] == population]
    counts = {k: 0 for k in ("success", "failure", "cancelled",
                             "skipped", "in_progress", "absent")}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1

    exercised = counts["success"] + counts["failure"]
    # None, NEVER 0.0. "Nothing ran" and "everything that ran passed" are
    # different findings that justify opposite decisions.
    flake_rate = (counts["failure"] / exercised) if exercised else None

    if exercised == 0:
        state = "unmeasurable"
    elif counts["failure"] > 0:
        state = "blocked"
    elif exercised < min_runs:
        state = "insufficient"
    else:
        state = "supported"

    return {
        "population": population,
        "runs_seen": len(rows),
        "exercised": exercised,
        "counts": counts,
        "flake_rate": flake_rate,
        "flake_rate_pct": round(flake_rate * 100, 2) if flake_rate is not None else None,
        "min_runs": min_runs,
        "state": state,
    }


def fetch_runs(limit: int, workflow: str) -> List[Dict[str, Any]]:
    """Pull runs + their jobs from the forge. Fail loudly, never to an empty list."""
    listing = subprocess.run(
        ["gh", "run", "list", "--workflow", workflow, "--limit", str(limit),
         "--json", "databaseId,headSha,conclusion,event,createdAt,headBranch"],
        capture_output=True, text=True,
    )
    if listing.returncode != 0:
        raise RuntimeError(
            "gh run list failed — the survey could not be produced, which is "
            "NOT the same as a clean survey: " + (listing.stderr or "").strip()
        )
    runs = json.loads(listing.stdout or "[]")
    for run in runs:
        detail = subprocess.run(
            ["gh", "run", "view", str(run["databaseId"]), "--json", "jobs"],
            capture_output=True, text=True,
        )
        run["jobs"] = (
            json.loads(detail.stdout or "{}").get("jobs", [])
            if detail.returncode == 0 else []
        )
    return runs


def survey(runs: List[Dict[str, Any]],
           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    classified = [classify_run(r, cfg) for r in runs]
    post = summarise(classified, "post_unblock", int(cfg["min_runs"]))
    pre = summarise(classified, "pre_unblock", int(cfg["min_runs"]))
    # The pre-unblock population is INELIGIBLE by construction, so it must never
    # print a state a reader could quote as a verdict. Left as computed it reads
    # `supported` — the precise misreading this whole tool exists to prevent.
    pre["state"] = "biased_ineligible"

    return {
        "check_name": cfg["check_name"],
        "runs_examined": len(runs),
        # THE decision. Only the post-unblock population can support it.
        "promotion_supported": post["state"] == "supported",
        "verdict_state": post["state"],
        "post_unblock": post,
        # Reported so a reader can see the bias rather than be protected from
        # it, and never used in the verdict.
        "pre_unblock_BIASED": pre,
        "bias_note": (
            "pre_unblock runs declared `needs: [test]`, so E2E only ran on "
            "branches whose unit suite had already passed and was SKIPPED on "
            "every other. Its {} skipped run(s) are absences from the "
            "denominator, not passes. This population cannot support a "
            "promotion.".format(pre["counts"]["skipped"])
        ),
        "runs": classified,
    }


def render(report: Dict[str, Any]) -> str:
    out: List[str] = []
    post = report["post_unblock"]
    pre = report["pre_unblock_BIASED"]
    out.append(f"E2E promotion survey — {report['check_name']}")
    out.append(f"  runs examined: {report['runs_examined']}")
    out.append("")

    for label, block in (("POST-unblock (the only population that counts)", post),
                         ("PRE-unblock  (BIASED — reported, never counted)", pre)):
        c = block["counts"]
        rate = ("n/a — nothing was exercised"
                if block["flake_rate_pct"] is None
                else f"{block['flake_rate_pct']}%")
        out.append(f"  {label}")
        out.append(f"    runs        : {block['runs_seen']}")
        out.append(f"    exercised   : {block['exercised']}  "
                   f"(success {c['success']}, failure {c['failure']})")
        out.append(f"    not exercised: skipped {c['skipped']}, "
                   f"cancelled {c['cancelled']}, in progress {c['in_progress']}, "
                   f"absent {c['absent']}")
        out.append(f"    flake rate  : {rate}")
        out.append(f"    state       : {block['state']}")
        out.append("")

    reasons = {
        "unmeasurable": (
            "no post-unblock run has EXERCISED the check yet. This is not a 0% "
            "flake rate — it is no measurement at all."
        ),
        "insufficient": (
            f"only {post['exercised']} of the required {post['min_runs']} "
            "post-unblock runs have been exercised."
        ),
        "blocked": (
            f"{post['counts']['failure']} post-unblock failure(s). Promoting a "
            "flaky required check is how gates get disabled — fix the flake, do "
            "not raise the threshold."
        ),
        "supported": (
            f"{post['exercised']} post-unblock runs exercised, 0 failures. The "
            "promotion is supported by evidence."
        ),
    }
    out.append(f"  VERDICT: promotion_supported={report['promotion_supported']} "
               f"({post['state']})")
    out.append(f"    {reasons.get(post['state'], '')}")
    out.append("")
    out.append("  " + report["bias_note"])
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"runs to examine (default {DEFAULT_LIMIT})")
    parser.add_argument("--from-json", type=Path, metavar="FILE",
                        help="replay a saved `gh run list` payload instead of "
                             "calling the forge (each run needs a `jobs` list)")
    parser.add_argument("--config", type=Path, help="override args/e2e_promotion.yaml")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    try:
        if args.from_json:
            runs = json.loads(args.from_json.read_text(encoding="utf-8"))
        else:
            runs = fetch_runs(args.limit, str(cfg["workflow"]))
    except Exception as exc:
        # Exit 2: the survey could not be produced. A survey that could not run
        # is not a survey that found nothing.
        print(f"e2e_flake_survey: {exc}", file=sys.stderr)
        return 2

    report = survey(runs, cfg)
    print(json.dumps(report, indent=2) if args.json else render(report))
    # Report only, always 0 — see the module docstring.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
