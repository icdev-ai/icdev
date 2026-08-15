#!/usr/bin/env python3
"""Run every test file a PR changed ALONE, in its own pytest process (trust-disc-02).

WHY THIS EXISTS
---------------
There is no test-order randomisation and no isolation run anywhere in CI --
verified against `.github/workflows/icdev-ci.yml`, `pytest.ini`, `setup.cfg` and
`pyproject.toml`. Every gated test module is run exactly once, in one process, in
one fixed order (the order of `args/ci_test_files/core.txt`). An order-dependent
pass is therefore invisible: the file is green because of what ran before it, and
it stays green until an unrelated allowlist edit reshuffles the run -- at which
point it presents as a failure in whatever PR happened to move the list.

Four defects in a single session had exactly this shape:

  * tests/cortex/test_chat_routing.py            passed in-directory, failed ALONE
  * tests/cortex/test_chat_turn_connections.py   passed alone, failed IN-SUITE
  * tests/cortex/test_blueprint_routes.py        same class
  * tests/test_cnr_mission_canvas.py             same class

All four registered a blueprint onto the shared `tools.dashboard.app` singleton
behind an ``if "x" not in app.blueprints`` guard. That guard skips only when the
blueprint is ALREADY present -- i.e. never in the case that fails. When an earlier
module in the run has already driven the shared app, Flask's setup phase is
locked and the registration raises::

    AssertionError: The setup method 'register_blueprint' can no longer be called
    on the application. It has already handled its first request.

BOTH HALVES
-----------
"Alone" and "in-suite" catch DIFFERENT defects, and the four above split across
both, so a PR needs both signals:

  * IN-SUITE is the existing gated run (`Run core unit tests`). A file that only
    passes alone -- because nothing else had touched the singleton -- fails there.
  * ALONE is this module. A file that only passes with company -- because a
    neighbour imported its dependency, seeded its table, or registered its
    blueprint first -- fails here.

The marginal cost is small by construction: only files the PR actually TOUCHED
are re-run, they were already being run once, and each is a single process.

GATED vs UNGATED, AND WHY ONLY ONE OF THEM IS FATAL
---------------------------------------------------
A changed file that is in an allowlist is a file CI already runs and that is
already green in-suite. Running it alone is a strict addition, so a standalone
failure there is real news -- order dependence or a genuine break -- and it exits
non-zero.

A changed file that is NOT in any allowlist has never gated a merge (1,826 of
them, grandfathered by name in `args/ci_test_backlog.txt`). An unknown number are
already red for reasons that predate the PR touching them, so failing on those
would red-light PRs for pre-existing debt and the step would get a `|| true`
bolted onto it inside a week. Those are run and REPORTED, with a warning naming
the real fix -- make it pass and append it to `args/ci_test_files/core.txt` --
but they do not fail the job. They also do not get an in-suite run at all, which
is the same warning said a second way.

Usage
-----
    python tools/ci/isolation_run.py --list                 # which files would run
    python tools/ci/isolation_run.py --json                 # resolution, no pytest
    python tools/ci/isolation_run.py --run                  # run them, gate on gated
    python tools/ci/isolation_run.py --run --json
    python tools/ci/isolation_run.py --run --base origin/main
    python tools/ci/isolation_run.py --run --timeout 1200

Exit codes are load-bearing and must not be swallowed:

    0  nothing to run, or every gated changed file passed alone
    1  a GATED changed test file failed or timed out when run alone
    2  the changed set could not be resolved (no base ref, or a shallow clone
       with no merge base) -- the step ran nothing and must not read as green
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# Sibling module. Imported by path-independent name so this works from the
# `tools/` checkout and the packaged `icdev/tools/` mirror alike.
try:  # pragma: no cover - exercised implicitly by both import paths
    from tools.ci.gated_test_list import (  # type: ignore
        AllowlistError,
        collect_test_files,
        gated_targets,
        in_scope,
        load_gate_config,
        repo_root,
    )
except ImportError:  # pragma: no cover - direct `python tools/ci/isolation_run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.ci.gated_test_list import (  # type: ignore
        AllowlistError,
        collect_test_files,
        gated_targets,
        in_scope,
        load_gate_config,
        repo_root,
    )

#: Per-file wall-clock ceiling for one standalone pytest process, in seconds.
#: A file that hangs alone is exactly the defect this looks for, so the timeout
#: must be a FAILURE and never a skip.
DEFAULT_TIMEOUT = 900

#: Candidate base refs, tried in order, when neither --base nor the environment
#: names one. `origin/*` first: on a runner the local `main` is whatever the
#: checkout left behind, while `origin/main` is what the PR will merge into.
FALLBACK_BASES = ("origin/main", "origin/master", "main", "master")


class ResolutionError(RuntimeError):
    """The set of changed test files could not be determined."""


# --------------------------------------------------------------------------- #
# Resolving what changed
# --------------------------------------------------------------------------- #
def _git(root: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        # Explicit, because `text=True` decodes with `locale.getencoding()` --
        # cp1252 on a Windows dev box, where a non-ASCII path byte raises
        # UnicodeDecodeError. git emits UTF-8.
        encoding="utf-8",
        errors="replace",
    )


def _rev_exists(root: Path, ref: str) -> bool:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0


def resolve_base(root: Path, explicit: Optional[str] = None) -> str:
    """Return the ref this branch diverged from, or raise.

    Order: an explicit `--base` or `ICDEV_ISOLATION_BASE`, then GitHub's
    `GITHUB_BASE_REF` (set on pull_request events and naming the TARGET branch),
    then the usual default branches.

    A base the CALLER named is authoritative: if it does not resolve, this raises
    instead of quietly trying the next candidate. Falling back there would answer
    a question nobody asked -- "no files changed vs main" reads identically to
    "your --base was a typo", and the second is the one that leaves a real
    regression unrun. The auto-detected candidates below it may fall through,
    because none of them was asserted by a human.

    Raises rather than falling back to `HEAD~1` at the end, for the same reason:
    a silent wrong base produces a silently wrong -- usually empty -- file set,
    and a step that ran nothing must not be indistinguishable from a step that
    ran everything and passed.
    """

    def _usable(ref: str) -> bool:
        return (
            _rev_exists(root, ref)
            and _git(root, "merge-base", ref, "HEAD").returncode == 0
        )

    named = explicit or os.environ.get("ICDEV_ISOLATION_BASE")
    if named:
        if _usable(named):
            return named
        raise ResolutionError(
            f"base ref {named!r} does not resolve to a commit sharing history with "
            "HEAD. It was named explicitly, so it is not guessed around"
        )

    candidates: List[str] = []
    gh_base = os.environ.get("GITHUB_BASE_REF")
    if gh_base:
        candidates.extend([f"origin/{gh_base}", gh_base])
    candidates.extend(FALLBACK_BASES)

    for ref in candidates:
        if _usable(ref):
            return ref
    raise ResolutionError(
        "no usable base ref -- tried " + ", ".join(candidates) + ". On a CI runner "
        "this almost always means a shallow checkout: set `fetch-depth: 0` on the "
        "actions/checkout step, or pass --base explicitly. Refusing to guess, "
        "because a wrong base silently resolves to zero changed files"
    )


def changed_paths(root: Path, base: str) -> List[str]:
    """Repo-relative paths this branch ADDS, MODIFIES or RENAMES-TO vs `base`.

    Two diffs, unioned:

      * `<merge-base>..HEAD` -- what the PR contains. Diffing against the merge
        base rather than the branch tip means commits that landed on main after
        this branch forked are not counted as "changed by this PR".
      * `HEAD..worktree` -- staged and unstaged edits. Zero-width on a runner
        (the checkout is clean) and the whole point locally, where the file you
        just edited has not been committed yet.

    Plus untracked-but-not-ignored files, which `git diff` does not report at
    all. A brand-new test file is untracked until it is `git add`ed, and that is
    exactly when running it alone is most useful; without this the tool would
    print "this branch changes no test file" to someone who had just written
    one -- the silent-zero failure the base-ref handling above refuses to make.
    """
    merge_base = _git(root, "merge-base", base, "HEAD")
    if merge_base.returncode != 0:
        raise ResolutionError(
            f"`git merge-base {base} HEAD` failed: {merge_base.stderr.strip()} -- "
            "a shallow clone has no common ancestor to diff against"
        )
    fork_point = merge_base.stdout.strip()

    out: List[str] = []
    for diff_args in (
        ("diff", "--name-only", "--diff-filter=ACMR", fork_point, "HEAD"),
        ("diff", "--name-only", "--diff-filter=ACMR", "HEAD"),
    ):
        proc = _git(root, *diff_args)
        if proc.returncode != 0:
            raise ResolutionError(
                f"`git {' '.join(diff_args)}` failed: {proc.stderr.strip()}"
            )
        out.extend(
            line.strip().replace("\\", "/")
            for line in proc.stdout.splitlines()
            if line.strip()
        )

    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if untracked.returncode == 0:
        out.extend(
            line.strip().replace("\\", "/")
            for line in untracked.stdout.splitlines()
            if line.strip()
        )
    return sorted(set(out))


def resolve(root: Optional[Path] = None, base: Optional[str] = None) -> Dict[str, object]:
    """Which test files this PR changed, and whether CI runs each one in-suite.

    Only files that still EXIST are returned. `--diff-filter=ACMR` excludes
    deletions and the range diff nets an add-then-delete out to nothing, but the
    two diffs are unioned across different endpoints: a file the branch COMMITTED
    and the working tree has since deleted is reported as added by the first and
    not reported at all by the second. pytest cannot run a path that is gone, so
    those land in `vanished` rather than in the run.
    """
    root = root or repo_root()
    resolved_base = resolve_base(root, base)
    config = load_gate_config(root)

    candidates = [p for p in changed_paths(root, resolved_base) if in_scope(p, config)]
    files = [p for p in candidates if (root / p).is_file()]
    vanished = [p for p in candidates if p not in files]

    # The changed files are folded into the census `gated_targets` intersects
    # against. That census is `git ls-files`, so a test file that has been
    # written and added to core.txt but not yet `git add`ed would otherwise come
    # back UNGATED and be reported as one CI never runs -- which is both wrong
    # and the opposite of what its author just did.
    known = sorted(set(collect_test_files(root, config)) | set(files))
    gated = gated_targets(root, known)

    return {
        "base": resolved_base,
        "root": str(root),
        "files": files,
        "gated": sorted(f for f in files if f in gated),
        "ungated": sorted(f for f in files if f not in gated),
        "vanished": vanished,
    }


# --------------------------------------------------------------------------- #
# Running them, one process each
# --------------------------------------------------------------------------- #
def run_one(
    root: Path, rel: str, timeout: int = DEFAULT_TIMEOUT, extra: Sequence[str] = ()
) -> Dict[str, object]:
    """One test file, one fresh interpreter. That process boundary IS the isolation.

    `-p no:cacheprovider` keeps N runs from fighting over `.pytest_cache`, which
    is shared state of exactly the kind being tested for.
    """
    env = dict(os.environ)
    # conftest.py inserts the repo root itself, but a file that fails at COLLECT
    # time never reaches conftest; make the import path explicit either way.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    cmd = [
        sys.executable, "-m", "pytest", rel,
        "-q", "--tb=short", "-p", "no:cacheprovider", *extra,
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), env=env, capture_output=True, text=True,
            timeout=timeout, check=False, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {
            "file": rel,
            "status": "timeout",
            "returncode": None,
            "duration_s": round(time.monotonic() - started, 1),
            "output": f"timed out after {timeout}s when run alone",
        }
    # pytest exit 5 is "no tests collected". Alone, that is a real finding: the
    # file contributes nothing on its own, which is indistinguishable from a
    # collection error for the purpose this check serves.
    status = "passed" if proc.returncode == 0 else "failed"
    return {
        "file": rel,
        "status": status,
        "returncode": proc.returncode,
        "duration_s": round(time.monotonic() - started, 1),
        "output": (proc.stdout or "") + (proc.stderr or ""),
    }


def run(
    root: Optional[Path] = None,
    base: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    extra: Sequence[str] = (),
    emit=None,
) -> Dict[str, object]:
    """Resolve, run each changed test file alone, and classify the outcome.

    `emit` receives progress lines as they happen; pass `print` for a CI log that
    is not silent for ten minutes. Returns the report; the caller decides the
    exit code from `ok`.
    """
    root = root or repo_root()
    say = emit or (lambda _msg: None)
    resolution = resolve(root, base)
    files: List[str] = resolution["files"]  # type: ignore[assignment]
    gated = set(resolution["gated"])  # type: ignore[arg-type]

    results: List[Dict[str, object]] = []
    for i, rel in enumerate(files, 1):
        say(f"[{i}/{len(files)}] pytest {rel}  (alone)")
        result = run_one(root, rel, timeout=timeout, extra=extra)
        result["gated"] = rel in gated
        results.append(result)
        say(f"    -> {result['status']} in {result['duration_s']}s")

    failures = [r for r in results if r["status"] != "passed"]
    fatal = [r for r in failures if r["gated"]]
    advisory = [r for r in failures if not r["gated"]]

    return {
        **resolution,
        "results": results,
        "failures": [r["file"] for r in failures],
        "fatal": [r["file"] for r in fatal],
        "advisory": [r["file"] for r in advisory],
        "ok": not fatal,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_failure(report_entry: Dict[str, object]) -> None:
    rel = report_entry["file"]
    print(f"\n{'=' * 78}\n{rel} -- {report_entry['status'].upper()} when run ALONE\n{'=' * 78}")  # type: ignore[union-attr]
    print(report_entry["output"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: derived from __file__)")
    parser.add_argument("--base", help="ref this branch forked from (default: auto-detected)")
    parser.add_argument("--list", dest="do_list", action="store_true",
                        help="print the changed test files, one per line, and stop")
    parser.add_argument("--run", action="store_true", help="run each changed test file alone")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"per-file wall-clock ceiling in seconds (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("pytest_args", nargs="*",
                        help="extra args forwarded to each pytest invocation (after --)")
    args = parser.parse_args(argv)

    try:
        root = args.root.resolve() if args.root else repo_root()
        report = (
            run(root, args.base, timeout=args.timeout, extra=args.pytest_args,
                emit=None if args.json else print)
            if args.run
            else resolve(root, args.base)
        )
    except (ResolutionError, AllowlistError) as exc:
        # Exit 2, never 0. A step that could not work out what to run has not
        # proved anything, and reading that as green is the failure mode this
        # whole module exists to close.
        print(f"::error::isolation run: {exc}", file=sys.stderr)
        return 2

    if args.do_list:
        print("\n".join(report["files"]))  # type: ignore[arg-type]
        return 0

    if args.json:
        print(json.dumps(report, indent=2))

    files: List[str] = report["files"]  # type: ignore[assignment]
    if not files:
        if not args.json:
            print(
                f"Isolation run: this branch changes no test file vs {report['base']} "
                "-- nothing to run alone."
            )
        return 0

    if not args.run:
        if not args.json:
            print(
                f"Isolation run: {len(files)} changed test file(s) vs {report['base']} "
                f"({len(report['gated'])} gated, {len(report['ungated'])} ungated). "  # type: ignore[arg-type]
                "Pass --run to execute them."
            )
        return 0

    if not args.json:
        for entry in report["results"]:  # type: ignore[union-attr]
            if entry["status"] != "passed":
                _print_failure(entry)  # type: ignore[arg-type]

        for rel in report["advisory"]:  # type: ignore[union-attr]
            print(
                f"::warning::{rel} fails when run ALONE. It is in no allowlist, so CI "
                "never runs it in-suite either and this is not blocking -- but the file "
                "is order-dependent. Fix it and append it to args/ci_test_files/core.txt."
            )
        for rel in report["ungated"]:  # type: ignore[union-attr]
            if rel not in report["advisory"]:  # type: ignore[operator]
                print(
                    f"::warning::{rel} passes alone but is in no allowlist, so the "
                    "in-suite half of this check never runs for it. Append it to "
                    "args/ci_test_files/core.txt."
                )
        for rel in report["fatal"]:  # type: ignore[union-attr]
            print(
                f"::error::{rel} passes in-suite but FAILS when run alone. That is an "
                "order-dependent pass: it is green only because of what ran before it, "
                "and it will break in whatever unrelated PR next reshuffles "
                "args/ci_test_files/core.txt. Make the test self-sufficient -- do not "
                "register onto the shared tools.dashboard.app singleton."
            )

        passed = len(files) - len(report["failures"])  # type: ignore[arg-type]
        print(
            f"\nIsolation run: {passed}/{len(files)} changed test file(s) pass alone "
            f"({len(report['fatal'])} gated failure(s), "  # type: ignore[arg-type]
            f"{len(report['advisory'])} advisory)."  # type: ignore[arg-type]
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
