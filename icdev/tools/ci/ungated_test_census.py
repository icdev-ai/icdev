#!/usr/bin/env python3
"""Measure every module in the ungated backlog: which are GREEN today? (rem-tst-01)

WHY THIS EXISTS
---------------
`args/ci_test_backlog.txt` enumerates the test modules pytest collects that CI
has NEVER run -- 1,794 of them at the time of writing. An unknown fraction are
RED (one isolated sweep found 531 failure lines across 87 files), which is
precisely why they cannot be bulk-added to `args/ci_test_files/core.txt`: adding
a red file turns `main` red, the gate gets disabled, and that is strictly worse
than the debt.

The only way out is one file at a time, and the only way to plan that is to know
which files are already green. This tool MEASURES. It changes no allowlist, it
promotes nothing, and it is not a gate -- it exits 0 whatever it finds. The
follow-up promotion batches consume its JSON.

WHAT IT MEASURES, AND WHAT IT DOES NOT
--------------------------------------
Each backlog module is run ALONE in its own pytest process, reusing
`isolation_run.run_one` -- the same execution path the changed-test isolation
run uses, so the two cannot drift apart on what "alone" means. A green verdict
here therefore means "green ALONE, in this checkout, today". It does NOT mean
"safe to append to core.txt": that also requires the file to pass IN-SUITE,
after the ~260 modules already on the list have run in the same process. Both
halves catch different defects (see `isolation_run.__doc__`), and this tool only
measures the first. A promotion batch must run the in-suite half itself.

CONCURRENCY, AND WHY EACH CHILD GETS ITS OWN DATABASE
-----------------------------------------------------
1,794 processes at ~8s each is ~4 hours serially. The files are run across a
bounded worker pool instead. That is a deviation from "one at a time" and it has
exactly one dangerous consequence: concurrent pytest processes writing the one
`data/icdev.db` SQLite file produce `database is locked` failures that are
artifacts of this tool, not properties of the test. So every child gets its own
`ICDEV_DB_PATH` under a scratch root outside the repo. That is recorded in the
report header (`db_isolation`) rather than left implicit, because it is a
measurement condition a reader has to be able to reproduce.

Interference this does NOT eliminate: anything the tests write by a path other
than `ICDEV_DB_PATH` -- fixed filenames under `data/`, `.tmp/`, or the OS temp
dir. Files whose verdict looks concurrency-shaped (`database is locked`,
`PermissionError`, `FileExistsError`) are counted separately in the summary so a
reader can discount them rather than being told a clean number that is not.

PARTIAL IS FINE; PARTIAL THAT LOOKS COMPLETE IS THE DEFECT
-----------------------------------------------------------
`--deadline-s` caps total wall clock. When it expires the tool stops launching
and records every unstarted module with status `not-reached`. The invariant the
report asserts on itself, and that `--verify` re-checks, is::

    len(results) + not_reached == backlog size

so a census can never be read as covering more than it measured.

Usage
-----
    python tools/ci/ungated_test_census.py --run --out docs/testing/ungated_test_census.json
    python tools/ci/ungated_test_census.py --run --limit 20 --workers 4
    python tools/ci/ungated_test_census.py --run --deadline-s 3600 --timeout 240
    python tools/ci/ungated_test_census.py --summarize docs/testing/ungated_test_census.json
    python tools/ci/ungated_test_census.py --verify docs/testing/ungated_test_census.json

Always exits 0 on a completed measurement (1 only on a usage/IO error). A census
that found 900 red files has done its job; making it a gate would guarantee it
gets switched off before anyone reads it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: This checkout, derived from `__file__` and never from `os.getcwd()` or from
#: whatever `import tools` happens to resolve to.
_ROOT = Path(__file__).resolve().parents[2]

# Put THIS checkout ahead of everything else before importing its siblings.
#
# A `try: import tools.ci.x / except ImportError: sys.path.insert(...)` is the
# obvious shape here and it is WRONG, because the failure mode is not an
# ImportError. Run as `python tools/ci/ungated_test_census.py`, `sys.path[0]` is
# `tools/ci/` -- the repo root is not on the path at all -- and `import
# tools.ci.isolation_run` then resolves through a `.pth`/installed `icdev`
# package to the SHARED checkout at `C:\AI\ICDev`. That import SUCCEEDS, so the
# except branch never runs. Measured here: `run_one` came from the shared
# checkout (no `env_extra`, hence a TypeError) and, far worse, `repo_root()`
# returned the shared checkout too -- a census that reported on this branch
# while actually measuring a tree other sessions are editing underneath it.
# Prepending unconditionally is what makes the sibling import mean this branch.
if str(_ROOT) in sys.path:
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from tools.ci.isolation_run import run_one  # noqa: E402  # type: ignore
from tools.ci.gated_test_list import repo_root  # noqa: E402  # type: ignore


def _assert_siblings_are_local() -> None:
    """Fail loudly if `tools.*` still resolved somewhere other than this checkout.

    The sys.path insert above is necessary but not sufficient: an already-imported
    `tools.ci.isolation_run` in `sys.modules` (a parent process, a test harness, a
    `.pth` that imports eagerly) wins over any path change. Silently measuring the
    wrong tree is the one outcome this census must never have, so the mismatch is
    an error rather than a warning.
    """
    for module in (sys.modules.get("tools.ci.isolation_run"),
                   sys.modules.get("tools.ci.gated_test_list")):
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        resolved = Path(origin).resolve()
        try:
            resolved.relative_to(_ROOT)
        except ValueError:
            raise CensusError(
                f"{resolved} was imported instead of the copy under {_ROOT}. "
                "The census would report on this branch while measuring another "
                "checkout; refusing to run."
            ) from None

#: The census under measurement. Read-only here -- this tool never edits it.
BACKLOG = Path("args") / "ci_test_backlog.txt"

#: Per-file wall-clock ceiling. A backlog module that needs longer than this
#: ALONE is itself a finding (it would add that much to every future CI run), so
#: the timeout is recorded as its own status and never silently retried.
DEFAULT_TIMEOUT = 300

#: Bounded by default rather than `cpu_count()`: these children each build a
#: Flask app and a SQLite schema, and oversubscribing turns real verdicts into
#: timeouts.
DEFAULT_WORKERS = 8

#: Total wall-clock budget. 0 means "no deadline".
DEFAULT_DEADLINE = 0

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_COLLECTION_ERROR = "collection-error"
STATUS_NO_TESTS = "no-tests"
STATUS_TIMEOUT = "timeout"
STATUS_MISSING = "missing"
STATUS_NOT_REACHED = "not-reached"

#: Every status a result row may carry, in report order.
STATUSES = (
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_COLLECTION_ERROR,
    STATUS_NO_TESTS,
    STATUS_TIMEOUT,
    STATUS_MISSING,
    STATUS_NOT_REACHED,
)

#: Substrings that mark a failure as plausibly caused by running many pytest
#: processes at once rather than by the test. Counted and reported separately;
#: never silently reclassified as a pass, because "probably concurrency" is a
#: hypothesis and the row is the evidence for it.
CONCURRENCY_SHAPED = (
    "database is locked",
    "database table is locked",
    "PermissionError",
    "FileExistsError",
    "WinError 32",
    "Address already in use",
)

#: pytest's own counts line, e.g. "3 failed, 5 passed, 1 warning in 2.10s".
_COUNTS_RE = re.compile(
    r"^=*\s*(?:\x1b\[[0-9;]*m)?[\d]+ (?:passed|failed|error|skipped|xfailed|xpassed|deselected)"
)
_COUNT_PAIR_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|warnings?)")


class CensusError(RuntimeError):
    """The census could not be set up or its inputs could not be read."""


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def read_backlog(root: Path) -> List[str]:
    """The backlog modules, in file order, comments and blanks dropped.

    Order is preserved rather than sorted: the file is already sorted, and
    preserving it means a `--limit` sample is a stable prefix that a later run
    can extend instead of re-measuring a different subset.
    """
    path = root / BACKLOG
    if not path.is_file():
        raise CensusError(f"backlog census not found at {path}")
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped.replace("\\", "/"))
    if not out:
        raise CensusError(f"{path} lists no modules -- refusing to report an empty census")
    return out


# --------------------------------------------------------------------------- #
# Classifying one pytest run
# --------------------------------------------------------------------------- #
def parse_counts(output: str) -> Dict[str, int]:
    """pytest's own per-test tallies, from its counts line.

    Reported alongside the per-FILE verdict because they are different
    quantities: one red assertion in a 40-test module is a very different
    promotion job from a module where all 40 fail, and a census that only says
    "failed" cannot tell a batch which is which.
    """
    counts: Dict[str, int] = {}
    for line in reversed(output.splitlines()):
        if not _COUNTS_RE.match(line.strip()):
            continue
        for number, label in _COUNT_PAIR_RE.findall(line):
            key = label.rstrip("s") if label in ("errors", "warnings") else label
            counts[key] = int(number)
        break
    return counts


def first_failure_line(output: str) -> str:
    """The single most useful line explaining a non-pass, or "".

    Preference order: pytest's short-summary line WITH its reason attached
    (`FAILED path::test - AssertionError: ...`), then the first `E   ` assertion
    line, then the first line that looks like an exception, and only then a bare
    summary line. The ordering matters for the `collection-error` bucket: pytest
    prints an `ERROR collecting tests/foo.py` banner ABOVE the reason, and taking
    the first `ERROR ` line verbatim yields a row that repeats the filename the
    row already carries and names no cause -- useless to a batch deciding whether
    twenty reds share one import fix. Truncated, because 1,794 of these have to
    fit in one committed artifact.
    """
    lines = [ln.rstrip() for ln in output.splitlines()]
    for line in lines:
        if line.startswith(("FAILED ", "ERROR ")) and " - " in line:
            return line[:400]
    for line in lines:
        if line.startswith("E   ") and line[4:].strip():
            return line.strip()[:400]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("ImportError", "ModuleNotFoundError", "AttributeError",
                                "SyntaxError", "INTERNALERROR", "collection failure")):
            return stripped[:400]
    for line in lines:
        if line.startswith(("FAILED ", "ERROR ")):
            return line[:400]
    for line in reversed(lines):
        if line.strip():
            return line.strip()[:400]
    return ""


def classify(returncode: Optional[int], output: str) -> str:
    """Map one pytest process onto a census status.

    The distinctions are the ones a promotion batch actually acts on, and none
    of them may be merged:

      * `no-tests` (exit 5) is NOT a pass. The module contributes no assertion,
        so promoting it would widen the allowlist without widening coverage --
        the same error as counting a skip as coverage.
      * `collection-error` is NOT a plain failure. It means the module does not
        import at all, so the fix is an import/dependency one and it can be
        batched with its siblings; a failing assertion cannot.
    """
    if returncode == 0:
        return STATUS_PASSED
    if returncode == 5:
        return STATUS_NO_TESTS
    if "ERROR collecting" in output or "errors during collection" in output:
        return STATUS_COLLECTION_ERROR
    if returncode in (2, 3, 4):
        # 2 interrupted / 3 internal error / 4 usage error. With a single file
        # argument these are overwhelmingly "it could not even be loaded".
        return STATUS_COLLECTION_ERROR
    return STATUS_FAILED


def concurrency_shaped(output: str) -> bool:
    return any(marker in output for marker in CONCURRENCY_SHAPED)


def measure_one(
    root: Path, rel: str, scratch: Path, timeout: int
) -> Dict[str, object]:
    """Run one backlog module alone and reduce it to a census row."""
    target = root / rel
    if not target.is_file():
        # The backlog is only supposed to shrink by deletion-with-promotion, so a
        # line naming a file that is gone is a real finding about the census
        # itself, not a test result. `--prune-backlog` in gated_test_list.py is
        # the fix; this tool only reports it.
        return {
            "file": rel,
            "status": STATUS_MISSING,
            "returncode": None,
            "duration_s": 0.0,
            "counts": {},
            "first_failure": "backlog names a path that does not exist in this checkout",
            "concurrency_shaped": False,
        }

    db_dir = scratch / rel.replace("/", "__")
    db_dir.mkdir(parents=True, exist_ok=True)
    result = run_one(
        root,
        rel,
        timeout=timeout,
        extra=("-rfE",),
        env_extra={
            "ICDEV_DB_PATH": str(db_dir / "icdev.db"),
            # pytest's own tmp roots, so concurrent children do not collide on
            # `pytest-of-<user>/pytest-current` either.
            "PYTEST_DEBUG_TEMPROOT": str(db_dir),
            # REPLACES the inherited PYTHONPATH rather than prepending to it.
            # These sessions run with PYTHONPATH pointing at the SHARED checkout,
            # so `import tools.x` in a child resolves there -- a census of this
            # branch would silently be measuring another worktree's source, which
            # other sessions are editing while this runs. Root-only is the whole
            # import path a fresh CI checkout has.
            "PYTHONPATH": str(root),
        },
    )
    output = str(result.get("output") or "")
    if result["status"] == "timeout":
        return {
            "file": rel,
            "status": STATUS_TIMEOUT,
            "returncode": None,
            "duration_s": result["duration_s"],
            "counts": {},
            "first_failure": f"no verdict: exceeded the {timeout}s per-file ceiling when run alone",
            "concurrency_shaped": False,
        }

    rc = result.get("returncode")
    status = classify(rc if isinstance(rc, int) else None, output)
    return {
        "file": rel,
        "status": status,
        "returncode": rc,
        "duration_s": result["duration_s"],
        "counts": parse_counts(output),
        "first_failure": "" if status == STATUS_PASSED else first_failure_line(output),
        "concurrency_shaped": status != STATUS_PASSED and concurrency_shaped(output),
    }


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #
def census(
    root: Optional[Path] = None,
    limit: Optional[int] = None,
    workers: int = DEFAULT_WORKERS,
    timeout: int = DEFAULT_TIMEOUT,
    deadline_s: int = DEFAULT_DEADLINE,
    emit=None,
    checkpoint: Optional[Path] = None,
) -> Dict[str, object]:
    """Measure the backlog and return the report.

    Workers pull from a shared index rather than being handed a pre-sliced
    partition, so one slow file does not leave a worker's whole slice unmeasured
    while the others idle -- and so the deadline can stop the sweep between files
    instead of mid-partition.
    """
    _assert_siblings_are_local()
    root = root or repo_root()
    say = emit or (lambda _msg: None)
    backlog = read_backlog(root)
    targets = backlog[:limit] if limit else list(backlog)

    scratch = Path(tempfile.mkdtemp(prefix="icdev-census-"))
    started_wall = time.time()
    deadline_at = started_wall + deadline_s if deadline_s else None

    results: List[Dict[str, object]] = []
    cursor = {"i": 0}
    lock = threading.Lock()
    stop = threading.Event()

    def worker() -> None:
        while not stop.is_set():
            with lock:
                idx = cursor["i"]
                if idx >= len(targets):
                    return
                cursor["i"] = idx + 1
            if deadline_at and time.time() >= deadline_at:
                stop.set()
                return
            rel = targets[idx]
            row = measure_one(root, rel, scratch, timeout)
            with lock:
                results.append(row)
                done = len(results)
            say(
                f"[{done}/{len(targets)}] {rel} -> {row['status']} "
                f"({row['duration_s']}s)"
            )
            if checkpoint and done % 50 == 0:
                with lock:
                    _write_checkpoint(checkpoint, results, len(targets))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    shutil.rmtree(scratch, ignore_errors=True)

    measured = {row["file"] for row in results}
    not_reached = [rel for rel in targets if rel not in measured]
    # Anything the --limit excluded was never in scope for this run, but it IS in
    # the backlog, and the acceptance invariant is stated against the BACKLOG.
    # Naming both keeps a sampled run from reading like a full one.
    out_of_scope = backlog[limit:] if limit else []

    results.sort(key=lambda r: r["file"])  # type: ignore[arg-type,return-value]

    by_status = {status: 0 for status in STATUSES}
    for row in results:
        by_status[str(row["status"])] += 1
    by_status[STATUS_NOT_REACHED] = len(not_reached)

    elapsed = round(time.time() - started_wall, 1)
    return {
        "task": "rem-tst-01",
        "generated_by": "tools/ci/ungated_test_census.py",
        "backlog": str(BACKLOG).replace("\\", "/"),
        "backlog_size": len(backlog),
        "targeted": len(targets),
        "measured": len(results),
        "not_reached_count": len(not_reached),
        "not_reached": sorted(not_reached),
        "out_of_scope_count": len(out_of_scope),
        "out_of_scope": sorted(out_of_scope),
        "conditions": {
            "runner": "tools/ci/isolation_run.py::run_one (one pytest process per file)",
            "pytest_args": ["-q", "--tb=short", "-p", "no:cacheprovider", "-rfE"],
            "workers": max(1, workers),
            "per_file_timeout_s": timeout,
            "deadline_s": deadline_s,
            "db_isolation": "per-file ICDEV_DB_PATH + PYTEST_DEBUG_TEMPROOT under a scratch root",
            "measures": "green ALONE only -- NOT the in-suite half, which a promotion batch must run itself",
            "elapsed_s": elapsed,
        },
        "counts": by_status,
        "concurrency_shaped_count": sum(1 for r in results if r["concurrency_shaped"]),
        "accounted_for": len(results) + len(not_reached) + len(out_of_scope) == len(backlog),
        "results": results,
    }


def _write_checkpoint(path: Path, results: List[Dict[str, object]], targeted: int) -> None:
    """Flush partial results so a crash or a kill does not lose hours of work."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"partial": True, "targeted": targeted, "results": results}, indent=1),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def verify(report: Dict[str, object]) -> List[str]:
    """Re-derive the report's self-claims. Returns the problems, empty if clean."""
    problems: List[str] = []
    results = report.get("results") or []
    backlog_size = int(report.get("backlog_size") or 0)
    measured = len(results)
    # `or -1` here would be a bug: a census whose deadline expired before ANY
    # file finished legitimately reports measured=0, and `0 or -1` is -1, so the
    # most honest possible partial report would be the one flagged inconsistent.
    claimed = report.get("measured")
    if measured != (int(claimed) if claimed is not None else -1):
        problems.append(f"measured={claimed} but results holds {measured} rows")
    total = measured + int(report.get("not_reached_count") or 0) + int(
        report.get("out_of_scope_count") or 0
    )
    if total != backlog_size:
        problems.append(
            f"measured({measured}) + not_reached({report.get('not_reached_count')}) + "
            f"out_of_scope({report.get('out_of_scope_count')}) = {total}, "
            f"but the backlog holds {backlog_size}"
        )
    files = [r["file"] for r in results]  # type: ignore[index]
    if len(set(files)) != len(files):
        problems.append("results contains duplicate file entries")
    tallied = sum(int(v) for k, v in (report.get("counts") or {}).items() if k != STATUS_NOT_REACHED)
    if tallied != measured:
        problems.append(f"status counts sum to {tallied} but {measured} files were measured")
    return problems


def render_markdown(report: Dict[str, object]) -> str:
    counts: Dict[str, int] = report.get("counts") or {}  # type: ignore[assignment]
    conditions: Dict[str, object] = report.get("conditions") or {}  # type: ignore[assignment]
    backlog_size = int(report.get("backlog_size") or 0)
    measured = int(report.get("measured") or 0)
    not_reached = int(report.get("not_reached_count") or 0)
    out_of_scope = int(report.get("out_of_scope_count") or 0)
    results: List[Dict[str, object]] = report.get("results") or []  # type: ignore[assignment]

    green = counts.get(STATUS_PASSED, 0)
    pct = (100.0 * green / measured) if measured else 0.0

    lines: List[str] = []
    lines.append("# Ungated test census — which backlog modules are green TODAY?")
    lines.append("")
    lines.append(
        f"`args/ci_test_backlog.txt` enumerates **{backlog_size}** test modules that CI has "
        "never run, so none of them has ever gated a merge. They cannot be bulk-added to "
        "`args/ci_test_files/core.txt` — an unknown fraction are red, adding a red file turns "
        "`main` red, and a red `main` gets the gate disabled, which is strictly worse than the "
        "debt. This is the measurement that has to come first."
    )
    lines.append("")
    lines.append(
        f"**{measured} measured, {not_reached} not reached"
        + (f", {out_of_scope} outside this run's sample" if out_of_scope else "")
        + f". {measured} + {not_reached}"
        + (f" + {out_of_scope}" if out_of_scope else "")
        + f" = {backlog_size}.**"
    )
    lines.append("")
    lines.append(
        "Machine-readable companion: `docs/testing/ungated_test_census.json` — that is the "
        "artifact the promotion batches consume; this page is the summary."
    )
    lines.append("")
    lines.append("## Outcome by status")
    lines.append("")
    lines.append("| Status | Files | Share of measured | What it means |")
    lines.append("|---|---:|---:|---|")
    meanings = {
        STATUS_PASSED: "Green alone. **Candidate** for promotion — still needs the in-suite half.",
        STATUS_FAILED: "Ran, asserted, and at least one test failed.",
        STATUS_COLLECTION_ERROR: "Did not import/collect at all. Import or dependency fix, batchable.",
        STATUS_NO_TESTS: "Collected nothing. **Not a pass** — promoting it widens the allowlist without widening coverage.",
        STATUS_TIMEOUT: f"No verdict inside the {conditions.get('per_file_timeout_s')}s per-file ceiling.",
        STATUS_MISSING: "The backlog names a path that is not in the checkout — a defect in the census file itself.",
    }
    for status in STATUSES:
        if status == STATUS_NOT_REACHED:
            continue
        n = counts.get(status, 0)
        share = (100.0 * n / measured) if measured else 0.0
        lines.append(f"| `{status}` | {n} | {share:.1f}% | {meanings[status]} |")
    lines.append(f"| `not-reached` | {not_reached} | — | Never launched (deadline or sample bound). No verdict is claimed. |")
    lines.append("")
    lines.append(
        f"**{green} of {measured} measured modules ({pct:.1f}%) pass when run alone.**"
    )
    lines.append("")
    lines.append("## What this does NOT say")
    lines.append("")
    lines.append(
        "- A `passed` row means **green ALONE**. It does not mean safe to append to "
        "`core.txt`: the in-suite half — running after the ~260 modules already on the "
        "list, in one process — catches a different defect class and is not measured here. "
        "A promotion batch runs it itself."
    )
    lines.append(
        "- `no-tests` is counted separately from `passed` on purpose. A module that "
        "collects nothing exits 0 and would look green to any check that only reads the "
        "exit code; promoting it is the same error as counting a skip as coverage."
    )
    conc = int(report.get("concurrency_shaped_count") or 0)
    lines.append(
        f"- {conc} non-passing rows carry a concurrency-shaped error (`database is locked`, "
        "`PermissionError`, …). This sweep ran "
        f"{conditions.get('workers')} pytest processes at a time, each with its own "
        "`ICDEV_DB_PATH`, but tests that write fixed paths under `data/` or the OS temp dir "
        "can still collide. Those rows carry `concurrency_shaped: true` in the JSON and are "
        "worth re-running singly before being treated as real."
    )
    lines.append("")
    lines.append("## Measurement conditions")
    lines.append("")
    lines.append("| Condition | Value |")
    lines.append("|---|---|")
    for key in ("runner", "workers", "per_file_timeout_s", "deadline_s", "db_isolation",
                "elapsed_s"):
        lines.append(f"| `{key}` | {conditions.get(key)} |")
    lines.append(f"| `pytest_args` | `{' '.join(conditions.get('pytest_args') or [])}` |")
    lines.append("")

    green = [r for r in results if r["status"] == STATUS_PASSED]
    if green:
        lines.append("## Where the green modules are")
        lines.append("")
        green_secs = sum(float(r.get("duration_s") or 0) for r in green)
        lines.append(
            f"Grouped by directory, because promotion happens in batches and a batch that "
            f"shares a directory shares its fixtures. Promoting all {len(green)} of them would "
            f"add roughly **{green_secs / 60:.0f} minutes** of standalone pytest time — less "
            f"in-suite, where the interpreter and imports are paid once, but not nothing, and "
            f"that is the number a batch plan has to argue with."
        )
        lines.append("")
        dirs: Dict[str, int] = {}
        for row in green:
            parent = str(Path(str(row["file"])).parent).replace("\\", "/")
            dirs[parent] = dirs.get(parent, 0) + 1
        lines.append("| Green modules | Directory |")
        lines.append("|---:|---|")
        for name, n in sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))[:25]:
            lines.append(f"| {n} | `{name}/` |")
        if len(dirs) > 25:
            lines.append(f"| … | {len(dirs) - 25} further directories — see the JSON |")
        lines.append("")

    failing = [r for r in results if r["status"] in (STATUS_FAILED, STATUS_COLLECTION_ERROR)]
    if failing:
        lines.append("## Most common failure signatures")
        lines.append("")
        lines.append(
            "Grouped on a normalised first-failure line, so a promotion batch can see which "
            "reds are one shared cause rather than N separate jobs."
        )
        lines.append("")
        buckets: Dict[str, int] = {}
        for row in failing:
            key = _signature(str(row.get("first_failure") or ""))
            buckets[key] = buckets.get(key, 0) + 1
        lines.append("| Files | Signature |")
        lines.append("|---:|---|")
        for sig, n in sorted(buckets.items(), key=lambda kv: -kv[1])[:20]:
            lines.append(f"| {n} | `{sig[:160]}` |")
        lines.append("")

    lines.append("## Reproducing this")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/ci/ungated_test_census.py --run \\")
    lines.append(f"  --workers {conditions.get('workers')} "
                 f"--timeout {conditions.get('per_file_timeout_s')} \\")
    lines.append("  --out docs/testing/ungated_test_census.json \\")
    lines.append("  --md docs/testing/ungated_test_census.md")
    lines.append("python tools/ci/ungated_test_census.py --verify docs/testing/ungated_test_census.json")
    lines.append("```")
    lines.append("")
    lines.append(
        "This task MEASURES only. `args/ci_test_files/core.txt` and "
        "`args/ci_test_backlog.txt` are untouched by it, and the tool exits 0 whatever it "
        "finds — a census that becomes a gate gets switched off before anyone reads it."
    )
    lines.append("")
    return "\n".join(lines)


def _signature(line: str) -> str:
    """Collapse a failure line to what it has in common with its siblings."""
    if not line:
        return "(no failure line captured)"
    # Drop the leading `FAILED <path>::<test> - ` so the exception is the key.
    if line.startswith(("FAILED ", "ERROR ")) and " - " in line:
        line = line.split(" - ", 1)[1]
    elif line.startswith(("FAILED ", "ERROR ")):
        line = line.split(" ", 1)[1].split("::")[0]
    line = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", line)
    line = re.sub(r"\b\d+\b", "N", line)
    line = re.sub(r"[A-Za-z]:[\\/][^\s'\"]+", "<path>", line)
    return line.strip()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: derived from __file__)")
    parser.add_argument("--run", action="store_true", help="measure the backlog")
    parser.add_argument("--limit", type=int, help="measure only the first N backlog modules")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"concurrent pytest processes (default {DEFAULT_WORKERS})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"per-file wall-clock ceiling in seconds (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--deadline-s", type=int, default=DEFAULT_DEADLINE,
                        help="total wall-clock budget; unstarted modules become not-reached")
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument("--md", type=Path, help="write the markdown summary here")
    parser.add_argument("--checkpoint", type=Path, help="flush partial results here every 50 files")
    parser.add_argument("--quiet", action="store_true", help="suppress per-file progress")
    parser.add_argument("--summarize", type=Path, help="render markdown from an existing report")
    parser.add_argument("--verify", type=Path, help="re-check an existing report's arithmetic")
    args = parser.parse_args(argv)

    try:
        _assert_siblings_are_local()
        root = args.root.resolve() if args.root else repo_root()

        if args.verify:
            report = json.loads(args.verify.read_text(encoding="utf-8"))
            problems = verify(report)
            if problems:
                for problem in problems:
                    print(f"::error::census report inconsistent: {problem}", file=sys.stderr)
                return 1
            print(
                f"Census OK: {report.get('measured')} measured + "
                f"{report.get('not_reached_count')} not reached + "
                f"{report.get('out_of_scope_count')} out of scope = "
                f"{report.get('backlog_size')} backlog modules."
            )
            return 0

        if args.summarize:
            report = json.loads(args.summarize.read_text(encoding="utf-8"))
            text = render_markdown(report)
            if args.md:
                args.md.parent.mkdir(parents=True, exist_ok=True)
                args.md.write_text(text, encoding="utf-8", newline="\n")
                print(f"wrote {args.md}")
            else:
                print(text)
            return 0

        if not args.run:
            backlog = read_backlog(root)
            print(
                f"{len(backlog)} ungated modules in {BACKLOG}. Pass --run to measure them "
                f"(~{len(backlog) * 8 // max(1, args.workers) // 60} min at "
                f"{args.workers} workers)."
            )
            return 0

        report = census(
            root=root,
            limit=args.limit,
            workers=args.workers,
            timeout=args.timeout,
            deadline_s=args.deadline_s,
            emit=None if args.quiet else print,
            checkpoint=args.checkpoint,
        )
    except CensusError as exc:
        print(f"::error::ungated test census: {exc}", file=sys.stderr)
        return 1

    problems = verify(report)
    for problem in problems:
        print(f"::error::census report inconsistent: {problem}", file=sys.stderr)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1), encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_markdown(report), encoding="utf-8", newline="\n")
        print(f"wrote {args.md}")

    counts: Dict[str, int] = report["counts"]  # type: ignore[assignment]
    print(
        "\nCensus: "
        + ", ".join(f"{counts.get(s, 0)} {s}" for s in STATUSES)
        + f" (of {report['backlog_size']} backlog modules)."
    )
    # Always 0 on a completed measurement. This is a census, not a gate.
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
