# CUI // SP-CTI
"""Promote an ungated test module into the gate — but only if it is green BOTH WAYS.

WHY THIS EXISTS. `ungated_test_census.py` measures which of the ungated backlog
modules pass today (1,691 of 1,792 at the last snapshot) and deliberately stops
there: it changes no allowlist and promotes nothing. Nothing consumed that
measurement, so the census aged — the committed snapshot was three days stale
when this was written — and the backlog only ever shrank when a human hand-moved
one file. A measurement nobody acts on is the same defect as a capability nobody
calls.

THE SAFEGUARD IS THE WHOLE POINT, AND IT IS NOT OPTIONAL.

The census runs each module ALONE, in its own process. Green-alone is NOT
green-in-suite, and this repo has the scar: `tests/cortex/test_chat_routing.py`
passed in-directory and failed alone, while three others passed alone and failed
IN-SUITE — all four registering a blueprint onto a shared app singleton behind a
guard that only skips when the blueprint is already there. On 2026-08-19
`kpr-watch-03` hit the same shape in CI: a test that passed alone and failed in
the full run, which read as flake and was not.

So a module is promoted only when it passes:

  1. ALONE, in its own process (what the census already measured), AND
  2. IN-SUITE, appended to the gated set and run in ONE process with it.

Promoting on (1) alone is how `main` goes red, the gate gets disabled, and the
debt comes back worse — which CLAUDE.md already calls strictly worse than the
debt itself.

FAIL-CLOSED ON THE BATCH. If the in-suite run fails, NOTHING from that batch is
promoted. The tool does not guess which module was responsible: the failure may
be an interaction between two of them, and a bisect that promotes "the innocent
ones" would ship exactly the pair that interact. Re-run with a smaller
``--limit`` to isolate.

REPORT ONLY BY DEFAULT. ``--apply`` is required to write, and even then the tool
only ever RATCHETS: modules move out of the backlog census into a `core.d`
fragment and `backlog_max` goes DOWN. It never adds to the census and never
raises a ceiling.

    python -m tools.ci.gate_promoter --limit 10
    python -m tools.ci.gate_promoter --limit 10 --apply
    python -m tools.ci.gate_promoter --census docs/testing/ungated_test_census.json
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — fixed argv, no shell
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CENSUS = REPO_ROOT / "docs" / "testing" / "ungated_test_census.json"
BACKLOG = REPO_ROOT / "args" / "ci_test_backlog.txt"
GATE_CONFIG = REPO_ROOT / "args" / "test_gating_gate.yaml"
FRAGMENT_DIR = REPO_ROOT / "args" / "ci_test_files" / "core.d"

#: How many modules one run may promote. Small on purpose: the in-suite run is
#: one process over the whole gated set, so a failure costs the batch, and a
#: smaller batch isolates the culprit faster.
DEFAULT_LIMIT = 10

#: A module the census timed out on, or that collected no tests, is NOT a
#: candidate whatever its return code — "no tests ran" is not "the tests pass".
PROMOTABLE_STATUS = "passed"


def load_census(path: Optional[Path] = None) -> dict:
    """The census snapshot, or ``{}`` when it cannot be read."""
    p = Path(path or DEFAULT_CENSUS)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing census is reported, not raised
        return {}


def backlog_modules(path: Optional[Path] = None) -> List[str]:
    """Modules still enumerated in the ungated census file."""
    p = Path(path or BACKLOG)
    try:
        raw = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in raw if ln.strip() and not ln.strip().startswith("#")]


def candidates(census: dict, backlog: Sequence[str], limit: int) -> List[str]:
    """Modules the census found GREEN ALONE that are still ungated.

    Ordered by the census's own measured duration, cheapest first: a promoted
    module is added to every future CI run, so the cheap ones buy the most
    coverage per second of build time.
    """
    still_ungated = set(backlog)
    rows = [
        r for r in (census.get("results") or [])
        if r.get("status") == PROMOTABLE_STATUS and r.get("file") in still_ungated
    ]
    rows.sort(key=lambda r: (float(r.get("duration_s") or 0.0), str(r.get("file"))))
    return [str(r["file"]) for r in rows[:limit]]


def _pytest(files: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 — fixed argv, no shell
        [sys.executable, "-m", "pytest", *files, "-q", "--no-header", "-p",
         "no:cacheprovider"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def verify_alone(module: str, timeout: int = 600) -> dict:
    """Phase 1: the module in its OWN process. Re-run rather than trusted.

    The census measured this, possibly days ago, on a different tree. Trusting a
    stale measurement to change an allowlist is how the allowlist stops
    describing reality.
    """
    try:
        proc = _pytest([module], timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"timed out after {timeout}s alone"}
    return {"ok": proc.returncode == 0,
            "reason": "" if proc.returncode == 0 else _tail(proc)}


def verify_in_suite(gated: Sequence[str], batch: Sequence[str],
                    timeout: int = 5400) -> dict:
    """Phase 2: the batch appended to the GATED set, in ONE process.

    This is the check the census cannot make and the reason this tool exists. A
    module that registers onto a shared singleton, mutates module state, or
    depends on import order passes alone and fails here — and if it were
    promoted on phase 1 alone, it would fail on `main` instead.
    """
    if not gated:
        return {"ok": False, "reason": "the gated list is empty — cannot verify in-suite"}
    try:
        proc = _pytest([*gated, *batch], timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"in-suite run timed out after {timeout}s"}
    return {"ok": proc.returncode == 0,
            "reason": "" if proc.returncode == 0 else _tail(proc)}


def _tail(proc: subprocess.CompletedProcess, n: int = 12) -> str:
    out = (proc.stdout or "") + (proc.stderr or "")
    return "\n".join(out.strip().splitlines()[-n:])[:2000]


def plan(limit: int = DEFAULT_LIMIT, census_path: Optional[Path] = None) -> dict:
    """What this run WOULD consider, without running anything."""
    census = load_census(census_path)
    if not census:
        return {"measurable": False,
                "reason": f"census not readable at {census_path or DEFAULT_CENSUS}"}
    backlog = backlog_modules()
    if not backlog:
        return {"measurable": False, "reason": "ungated census file is empty or unreadable"}
    picked = candidates(census, backlog, limit)
    return {
        "measurable": True,
        "backlog_size": len(backlog),
        "census_green": sum(1 for r in (census.get("results") or [])
                            if r.get("status") == PROMOTABLE_STATUS),
        "candidates": picked,
    }


def promote(limit: int = DEFAULT_LIMIT, apply: bool = False,
            census_path: Optional[Path] = None,
            alone_timeout: int = 600, suite_timeout: int = 5400) -> dict:
    """Verify candidates both ways and, with ``apply``, ratchet them in."""
    report = plan(limit=limit, census_path=census_path)
    if not report.get("measurable"):
        return report
    picked = report["candidates"]
    report.update({"applied": False, "promoted": [], "rejected": [],
                   "in_suite": None})
    if not picked:
        report["reason"] = "no ungated module is currently measured green"
        return report

    # Phase 1 — each alone. A module that fails here never reaches the
    # expensive suite run.
    survivors: List[str] = []
    for module in picked:
        verdict = verify_alone(module, timeout=alone_timeout)
        if verdict["ok"]:
            survivors.append(module)
        else:
            report["rejected"].append(
                {"file": module, "phase": "alone", "reason": verdict["reason"]})
    if not survivors:
        report["reason"] = "every candidate failed when run alone"
        return report

    # Phase 2 — the survivors WITH the gated set, one process.
    from tools.ci.gated_test_list import resolve

    gated = resolve("core", root=REPO_ROOT)
    suite = verify_in_suite(gated, survivors, timeout=suite_timeout)
    report["in_suite"] = suite
    if not suite["ok"]:
        # FAIL-CLOSED ON THE BATCH: the failure may be an interaction between
        # two survivors, so "promote the innocent ones" could ship exactly the
        # interacting pair. Re-run with a smaller --limit to isolate.
        report["rejected"].extend(
            {"file": m, "phase": "in_suite", "reason": "batch failed in-suite"}
            for m in survivors)
        report["reason"] = "the in-suite run failed — nothing promoted from this batch"
        return report

    report["promoted"] = survivors
    if apply:
        report["applied"] = True
        report["fragment"] = _write_fragment(survivors)
        report["backlog_size_after"] = _drop_from_backlog(survivors)
        report["backlog_max"] = _ratchet_ceiling(report["backlog_size_after"])
    return report


def _write_fragment(modules: Sequence[str]) -> str:
    """One `core.d` fragment per run, named for the run.

    A per-run file rather than an append to `core.txt`: tsg-policy-03 measured
    that shared file as the largest merge-collision surface in the repository,
    and two runs writing two files cannot collide at all.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = FRAGMENT_DIR / f"auto-promote-{stamp}.txt"
    header = (
        "# Promoted by tools/ci/gate_promoter.py — each module verified GREEN\n"
        "# BOTH alone (own process) AND in-suite (appended to the gated set and\n"
        "# run in one process with it). Green-alone alone is not enough: a module\n"
        "# that registers onto a shared singleton passes alone and fails in-suite,\n"
        "# and promoting it on the isolated result turns `main` red.\n"
        f"# Run: {stamp}\n"
    )
    path.write_text(header + "\n".join(modules) + "\n", encoding="utf-8", newline="\n")
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        # A relocated FRAGMENT_DIR (a test, or a caller pointing elsewhere)
        # is not an error worth raising from a function whose only job is to
        # write a file it has already written.
        return str(path)


def _drop_from_backlog(modules: Sequence[str]) -> int:
    """Remove promoted modules from the ungated census. Returns the new size."""
    drop = set(modules)
    lines = BACKLOG.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines if ln.strip() not in drop]
    BACKLOG.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    return len([ln for ln in kept if ln.strip() and not ln.strip().startswith("#")])


def _ratchet_ceiling(new_size: int) -> Optional[int]:
    """Lower ``backlog_max`` to the new size. NEVER raises it.

    The ceiling is a ratchet in CLAUDE.md's words — it may only go down — so a
    tool that could raise it would be the one thing this file must not do.
    """
    try:
        text = GATE_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return None
    out, changed = [], None
    for line in text.splitlines():
        if line.startswith("backlog_max:"):
            try:
                current = int(line.split(":", 1)[1].strip())
            except ValueError:
                out.append(line)
                continue
            if new_size < current:
                changed = new_size
                out.append(f"backlog_max: {new_size}")
                continue
        out.append(line)
    if changed is not None:
        GATE_CONFIG.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return changed


def render(report: dict) -> str:
    if not report.get("measurable"):
        return (f"UNMEASURABLE — {report['reason']}.\n"
                "No module is promoted when the measurement cannot be read; an "
                "unreadable census is not an empty one.")
    lines = [
        f"ungated backlog        : {report['backlog_size']}",
        f"census says green alone: {report['census_green']}",
        f"candidates this run    : {len(report['candidates'])}",
    ]
    if report.get("in_suite") is not None:
        lines.append(f"in-suite verification  : "
                     f"{'PASSED' if report['in_suite']['ok'] else 'FAILED'}")
    if report.get("promoted"):
        lines += ["", "PROMOTED (green alone AND in-suite):"]
        lines += [f"   {m}" for m in report["promoted"]]
    if report.get("rejected"):
        lines += ["", "not promoted:"]
        for r in report["rejected"][:10]:
            lines.append(f"   [{r['phase']}] {r['file']}")
    if report.get("applied"):
        lines += ["", f"wrote {report['fragment']}",
                  f"backlog {report['backlog_size']} -> {report['backlog_size_after']}"]
        if report.get("backlog_max"):
            lines.append(f"backlog_max ratcheted to {report['backlog_max']}")
    elif report.get("promoted"):
        lines += ["", "DRY RUN — pass --apply to write the fragment and ratchet."]
    if report.get("reason"):
        lines += ["", report["reason"]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--apply", action="store_true",
                        help="write the fragment and ratchet the ceiling")
    parser.add_argument("--plan", action="store_true",
                        help="list candidates without running anything")
    parser.add_argument("--census", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--alone-timeout", type=int, default=600)
    parser.add_argument("--suite-timeout", type=int, default=5400)
    args = parser.parse_args(argv)

    census_path = Path(args.census) if args.census else None
    if args.plan:
        report = plan(limit=args.limit, census_path=census_path)
    else:
        report = promote(limit=args.limit, apply=args.apply,
                         census_path=census_path,
                         alone_timeout=args.alone_timeout,
                         suite_timeout=args.suite_timeout)
    print(json.dumps(report, indent=2) if args.json else render(report))
    #: Never a gate. Promoting is an improvement, and failing a build because an
    #: improvement was unavailable would make the improvement unwelcome.
    return 0


if __name__ == "__main__":
    sys.exit(main())
