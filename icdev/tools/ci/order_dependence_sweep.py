#!/usr/bin/env python3
"""Sweep a set of gated test files for ORDER DEPENDENCE, and tell it apart from FLAKE.

WHY THIS EXISTS (crx-test-09)
-----------------------------
`isolation_run.py` answers one question — does a CHANGED file still pass alone — and
`crx-test-07` records the cost of the bin-packed shards: adding one line to
`args/ci_test_files/core.d/` re-partitions all four shards, so a file moves, and a
latent order dependence surfaces as a failure in whatever PR happened to move the
list. Neither tool sweeps a SUBSYSTEM to find the next one before it lands on an
innocent branch.

THE DISTINCTION THIS TOOL EXISTS TO MAKE
----------------------------------------
"It failed in the suite and passes alone" is NOT evidence of order dependence. It is
equally the signature of a self-flake — a test racing a background thread, a clock or
a filesystem — which merely got unlucky in the suite run. The two need different fixes
and a sweep that conflates them sends you to the wrong one. crx-test-09 is exactly
that case: `tests/document_intelligence/test_original_retention.py` failed on CI shard
2, passed alone, and the carrier was not shared state at all — the test read a
database row that the ingest thread writes AFTER the result the test waited for.

So every file is run alone REPEATEDLY first. A file that fails even one of its own
solo repeats is `flaky_alone`, and no amount of shuffling can implicate ordering. Only
a file that is green in every solo repeat and red under some permutation is reported
`order_dependent`, and that verdict is CONFIRMED by re-running the same permutation —
a one-off red under one shuffle is a flake that happened in company.

Usage
-----
    python -m tools.ci.order_dependence_sweep --match document_intelligence --json
    python -m tools.ci.order_dependence_sweep --files tests/a.py tests/b.py --permutations 5
    python -m tools.ci.order_dependence_sweep --match cortex --bisect   # name the predecessor

Exit codes: 0 = swept (findings are reported, not fatal), 1 = a finding with --gate,
2 = the sweep could not run (nothing matched). A sweep that could not run is not a
sweep that found nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree

from icdev.core.paths import repo_root

# Resolved from THIS file, never from os.getcwd(): a sweep is routinely driven from a
# worktree whose cwd is not the canonical root.
REPO_ROOT = repo_root(__file__)

DEFAULT_PERMUTATIONS = 3
DEFAULT_REPEATS = 2
DEFAULT_TIMEOUT = 1800
# How much of a failing run's output a report keeps. A verdict without the message
# under it is not evidence (see solo_baseline).
OUTPUT_KEPT = 3000

# Verdicts, most-actionable first.
ORDER_DEPENDENT = "order_dependent"      # green in every solo repeat, red under some order
SUSPECT = "order_suspect"                # red under one order, green when that order re-ran
FLAKY_ALONE = "flaky_alone"              # red in some solo repeat: a self-flake, not ordering
ALONE_RED = "alone_red"                  # red in every solo repeat: broken, not ordering
STABLE = "stable"

VERDICTS = (ORDER_DEPENDENT, SUSPECT, FLAKY_ALONE, ALONE_RED, STABLE)


# -- selecting the files ------------------------------------------------------

def gated_files(root: Path, match: Optional[str] = None) -> List[str]:
    """The gated list (`core.txt` plus every `core.d/` fragment), optionally filtered
    to the paths containing ``match``. Only files that EXIST are returned."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.ci.gated_test_list import resolve

    out = []
    for rel in resolve("core", root=root):
        if match and match not in rel:
            continue
        if (root / rel).is_file():
            out.append(rel)
    return sorted(set(out))


# -- running pytest -----------------------------------------------------------

def _run_pytest(root: Path, targets: Sequence[str], timeout: int) -> Tuple[int, Dict[str, str], str]:
    """Run ``targets`` in ONE pytest process. Returns (exit code, {file: outcome}, tail
    of the output). The outcome is per FILE, not per test."""
    xml = Path(tempfile.mkdtemp(prefix="ods_")) / "report.xml"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env.setdefault("PYTHONHASHSEED", "0")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
           "--junitxml=" + str(xml), *targets]
    try:
        proc = subprocess.run(cmd, cwd=str(root), env=env, timeout=timeout,
                              capture_output=True, text=True, errors="replace")
        code = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = "TIMEOUT after %ss\n%s" % (timeout, exc.stdout or "")
    return code, _outcomes(xml, targets), out[-4000:]


def _outcomes(xml: Path, targets: Sequence[str]) -> Dict[str, str]:
    """Per-FILE outcome from a junit report. A file with any failure or error is
    'failed'; a file the report never mentions stays 'absent' — which is NOT a pass, so
    a collection error that kills the run cannot read as green."""
    result: Dict[str, str] = {str(t).replace("\\", "/"): "absent" for t in targets}
    if not xml.exists():
        return result
    try:
        # `xml` is the --junitxml report THIS module's own pytest subprocess just
        # wrote into a private mkdtemp directory (see _run_pytest): first-party
        # output, never a user-supplied or network-sourced document.
        tree = ElementTree.parse(xml)  # nosec B314
    except ElementTree.ParseError:
        return result
    for case in tree.iter("testcase"):
        rel = (case.get("file") or "").replace("\\", "/")
        if rel not in result:
            # pytest omits `file` on some collection errors; fall back to classname,
            # which is the dotted module path.
            dotted = (case.get("classname") or "").replace("\\", "/")
            rel = next((k for k in result
                        if dotted.startswith(k[:-3].replace("/", "."))), "")
            if not rel:
                continue
        if any(case.find(tag) is not None for tag in ("failure", "error")):
            result[rel] = "failed"
        elif result[rel] == "absent":
            result[rel] = "passed"
    return result


# -- the sweep ----------------------------------------------------------------

def solo_baseline(root: Path, files: Sequence[str], repeats: int,
                  timeout: int, log=print,
                  evidence: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
    """Run every file ALONE, ``repeats`` times, each in its own process.

    ``evidence``, when given, collects the pytest tail of each file's FIRST red run.
    That output is the whole point of a solo red: `flaky_alone` names a verdict, and
    without the message underneath it the next reader has to reproduce the race from
    scratch to learn which assertion lost. Measured on this card — a sweep reported
    this file `flaky_alone`, the tail was discarded, and 20 subsequent solo runs
    (quiet, cold-cache and 4x loaded) all passed, so the one red could not be named.
    """
    baseline: Dict[str, List[str]] = {f: [] for f in files}
    for rel in files:
        for n in range(repeats):
            t0 = time.time()
            _, outcomes, tail = _run_pytest(root, [rel], timeout)
            verdict = outcomes.get(str(rel).replace("\\", "/"), "absent")
            baseline[rel].append(verdict)
            if verdict != "passed" and evidence is not None and rel not in evidence:
                evidence[rel] = tail
            log("  alone %d/%d %s: %s (%.1fs)" % (n + 1, repeats, rel, verdict, time.time() - t0))
    return baseline


def permutations(files: Sequence[str], count: int, seed: int) -> List[List[str]]:
    rng = random.Random(seed)
    orders = []
    for _ in range(count):
        order = list(files)
        rng.shuffle(order)
        orders.append(order)
    return orders


def bisect_predecessor(root: Path, order: Sequence[str], victim: str,
                       timeout: int, log=print) -> Optional[str]:
    """Shrink the prefix preceding ``victim`` to the smallest one that still reproduces
    its failure and return that prefix's LAST file — the neighbour to name in a report.
    ``None`` when no prefix reproduces it (the failure needed the whole run)."""
    prefix = list(order[: list(order).index(victim)])
    if not prefix:
        return None
    key = str(victim).replace("\\", "/")

    def fails_with(head: Sequence[str]) -> bool:
        _, outcomes, _ = _run_pytest(root, [*head, victim], timeout)
        return outcomes.get(key) == "failed"

    if not fails_with(prefix):
        return None
    while len(prefix) > 1:
        half = len(prefix) // 2
        if fails_with(prefix[half:]):
            prefix = list(prefix[half:])
        elif fails_with(prefix[:half]):
            prefix = list(prefix[:half])
        else:
            break  # neither half alone reproduces it: the cause is not one neighbour
        log("    bisect: %d file(s) still reproduce %s" % (len(prefix), victim))
    return prefix[-1]


def sweep(root: Path, files: Sequence[str], *, permutation_count: int = DEFAULT_PERMUTATIONS,
          repeats: int = DEFAULT_REPEATS, seed: int = 0, timeout: int = DEFAULT_TIMEOUT,
          bisect: bool = False, log=print) -> Dict[str, object]:
    started = time.time()
    log("solo baseline: %d file(s) x %d repeat(s)" % (len(files), repeats))
    solo_evidence: Dict[str, str] = {}
    baseline = solo_baseline(root, files, repeats, timeout, log=log, evidence=solo_evidence)

    orders = permutations(files, permutation_count, seed)
    per_order: List[Dict[str, str]] = []
    order_output: List[str] = []
    for i, order in enumerate(orders, 1):
        log("permutation %d/%d (seed %d)" % (i, len(orders), seed))
        t0 = time.time()
        _, outcomes, tail = _run_pytest(root, order, timeout)
        red = [f for f, v in outcomes.items() if v != "passed"]
        log("  %d not-green (%.1fs): %s" % (len(red), time.time() - t0, ", ".join(red) or "-"))
        per_order.append(outcomes)
        order_output.append(tail)

    findings: Dict[str, dict] = {}
    for rel in files:
        key = str(rel).replace("\\", "/")
        solo = baseline[rel]
        entry: dict = {"file": rel, "solo": solo,
                       "in_suite": [o.get(key, "absent") for o in per_order]}
        if rel in solo_evidence:
            entry["solo_red_output"] = solo_evidence[rel][-OUTPUT_KEPT:]
        if solo and all(v == "failed" for v in solo):
            entry["verdict"] = ALONE_RED
        elif any(v != "passed" for v in solo):
            entry["verdict"] = FLAKY_ALONE
        elif all(v == "passed" for v in entry["in_suite"]):
            entry["verdict"] = STABLE
        else:
            # Green alone every time, red under some order: CONFIRM by re-running that
            # exact order. A red that does not reproduce is a flake that happened in
            # company, and calling it order dependence sends the next reader to the
            # wrong fix.
            idx = next(i for i, v in enumerate(entry["in_suite"]) if v != "passed")
            entry["in_suite_red_output"] = order_output[idx][-OUTPUT_KEPT:]
            _, again, _ = _run_pytest(root, orders[idx], timeout)
            reproduced = again.get(key, "absent") != "passed"
            entry["verdict"] = ORDER_DEPENDENT if reproduced else SUSPECT
            entry["confirm_run"] = again.get(key, "absent")
            if reproduced and bisect:
                entry["predecessor"] = bisect_predecessor(root, orders[idx], rel, timeout, log=log)
        findings[rel] = entry

    counts: Dict[str, int] = {v: 0 for v in VERDICTS}
    for e in findings.values():
        counts[e["verdict"]] = counts.get(e["verdict"], 0) + 1
    return {
        "root": str(root),
        "files": len(files),
        "permutations": permutation_count,
        "repeats": repeats,
        "seed": seed,
        "counts": counts,
        "order_dependent": sorted(f for f, e in findings.items() if e["verdict"] == ORDER_DEPENDENT),
        "results": [findings[f] for f in files],
        "elapsed_seconds": round(time.time() - started, 1),
    }


def _human(report: Dict[str, object]) -> str:
    counts: Dict[str, int] = report["counts"]  # type: ignore[assignment]
    lines = ["order-dependence sweep: %s file(s), %s permutation(s), %s solo repeat(s), "
             "seed %s, %ss" % (report["files"], report["permutations"], report["repeats"],
                               report["seed"], report["elapsed_seconds"])]
    for verdict in VERDICTS:
        lines.append("  %s: %d" % (verdict, counts.get(verdict, 0)))
    for e in report["results"]:  # type: ignore[union-attr]
        if e["verdict"] == STABLE:
            continue
        extra = (" after " + e["predecessor"]) if e.get("predecessor") else ""
        lines.append("    %s: %s%s (solo %s; in-suite %s)"
                     % (e["verdict"], e["file"], extra,
                        "/".join(e["solo"]), "/".join(e["in_suite"])))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--files", nargs="*", help="explicit test files (default: the gated list)")
    ap.add_argument("--match", help="substring filter over the gated list, e.g. document_intelligence")
    ap.add_argument("--permutations", type=int, default=DEFAULT_PERMUTATIONS)
    ap.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                    help="solo runs per file; more than one is what separates a flake from ordering")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--bisect", action="store_true",
                    help="shrink the failing order to name the predecessor")
    ap.add_argument("--gate", action="store_true", help="exit 1 when a file is order_dependent")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    files = list(args.files or gated_files(root, args.match))
    if not files:
        print("order_dependence_sweep: no files matched - a sweep that ran nothing is "
              "not a sweep that found nothing", file=sys.stderr)
        return 2

    def _quiet(*_a, **_k):
        return None

    log = _quiet if args.json else print
    report = sweep(root, files, permutation_count=args.permutations, repeats=args.repeats,
                   seed=args.seed, timeout=args.timeout, bisect=args.bisect, log=log)
    print(json.dumps(report, indent=2) if args.json else _human(report))
    if args.gate and report["counts"].get(ORDER_DEPENDENT):  # type: ignore[union-attr]
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
