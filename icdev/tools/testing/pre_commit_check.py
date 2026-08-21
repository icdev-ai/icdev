#!/usr/bin/env python3
# CUI // SP-CTI
"""Pre-commit gate — blueprint imports, route smoke, and the test-gating census.

Called by .githooks/pre-commit. Blocks the commit if:
  1. This commit adds or renames a test file that no allowlist and no exclusion
     covers (tsg-policy-02 — the same census the CI `test` job runs)
  2. This commit adds an unregistered `pytest.skip` to a CI-gated test file
     (trust-disc-03 — a skipped test is UNMEASURED, not passing)
  3. Any blueprint.py fails to import (would cause 500 on all its routes)
  4. Any nav route returns non-200 or contains error text (catches runtime failures
     that CodeLens + Coherence cannot detect)

Exit 0 = all checks pass (commit proceeds).
Exit 1 = a check failed (commit blocked with error message).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Files that trigger the route smoke gate when changed
DASHBOARD_PATTERNS = (
    "tools/dashboard/",
    "tools/dashboard/templates/",
    "icdev/tools/dashboard/",
    "/blueprint.py",
    "/app.py",
)

#: The census CLI. The pre-commit gate shells out to exactly what CI runs so the
#: message an author reads here is the message CI would have printed, by
#: construction rather than by convention.
CENSUS_TOOL = Path("tools") / "ci" / "gated_test_list.py"

#: The skip census CLI (trust-disc-03). Same shell-out discipline, same reason:
#: the author reads the message CI would have printed.
SKIP_CENSUS_TOOL = Path("tools") / "ci" / "skip_census.py"


def _get_staged_name_status(root: Path = BASE_DIR) -> list[tuple[str, str]]:
    """(status, path) for every staged change — ONE git call for both consumers.

    `--name-status` rather than `--name-only` because the test-gating gate needs
    to distinguish an ADD/RENAME (a file that has to be registered) from a plain
    modification (already registered, or already someone else's debt). For a
    rename git emits `R100<TAB>old<TAB>new`, and the destination is the path that
    matters, so the LAST field is the one taken.
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-status"],
        capture_output=True, text=True, cwd=str(root),
        encoding="utf-8", errors="replace",
    )
    out: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        parts = [p for p in line.split("\t") if p.strip()]
        if len(parts) < 2:
            continue
        out.append((parts[0].strip(), parts[-1].strip().replace("\\", "/")))
    return out


def _get_staged_files(root: Path = BASE_DIR) -> list[str]:
    return [path for _status, path in _get_staged_name_status(root)]


def _added_or_renamed(name_status: list[tuple[str, str]]) -> list[str]:
    """Paths this commit introduces under a name that was not there before."""
    return [path for status, path in name_status if status and status[0] in ("A", "R")]


def _is_dashboard_change(files: list[str]) -> bool:
    for f in files:
        # Exclude generated build artifacts — they are never live routes
        if f.replace("\\", "/").startswith("build/"):
            continue
        for pat in DASHBOARD_PATTERNS:
            if pat in f.replace("\\", "/"):
                return True
    return False


# --------------------------------------------------------------------------- #
# Test-gating census (tsg-policy-02)
# --------------------------------------------------------------------------- #
# WHY AT COMMIT TIME. `--check-coverage` already runs in the CI `test` job and it
# works — it caught two unregistered test files within two hours of existing. But
# it caught them in the wrong place: each turned main RED, which blocks every open
# PR, and each cost a follow-up branch + PR + full CI cycle to add one line the
# author could have added in one second. The check is a filesystem walk plus two
# config reads — 0.17s measured over three runs on this repo, no database, no
# network, no LLM — so it is cheap enough to run where the author can act on it.
#
# WHY CI KEEPS IT ANYWAY. A hook is skippable with --no-verify and is simply
# absent for anything that does not arrive through a local commit. CI stays the
# backstop; this is the fast path.
#
# WHY IT DOES NOT AUTO-FIX. The census message deliberately says "make each one
# pass and append it to core.txt". A hook that appended the line itself would gate
# a test nobody has run — which is the exact failure tsg-policy-01 exists to
# close, reintroduced as a convenience feature.


def _load_census_module() -> ModuleType | None:
    """Load tools/ci/gated_test_list.py BY PATH, not as `tools.ci.gated_test_list`.

    The package import costs 136ms here because `tools/__init__.py` is the shim
    that redirects to `icdev.tools`; loading the file directly costs ~5ms because
    the module imports nothing but stdlib. Every commit would pay the former.

    Loaded from BASE_DIR — the census is CODE, and it ships in the same checkout
    as this hook. Which tree it then INSPECTS is a separate argument (`root`), so
    a test can point a real census at a throwaway repository.

    Returns None when the file is absent or will not load — a fast path that
    cannot resolve its own policy must not block a commit CI would have passed.
    """
    path = BASE_DIR / CENSUS_TOOL
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("icdev_gated_test_list", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _staged_new_test_files(name_status: list[tuple[str, str]], root: Path = BASE_DIR) -> list[str]:
    """Which files this commit adds/renames that the census would collect.

    Returns [] — costing nothing beyond the git call `main` already made — when
    the commit adds no files at all, which is the common case.
    """
    added = _added_or_renamed(name_status)
    if not added:
        return []
    module = _load_census_module()
    if module is None:
        return []
    try:
        return list(module.staged_new_test_files(root=root, files=added))
    except Exception as exc:
        # A missing/unparseable policy config, or missing pyyaml. Say so and let
        # the commit through: CI runs the same census and will not be so kind.
        print(f"[pre-commit] Test gating census: SKIPPED — policy unreadable ({exc})")
        return []


def _run_test_gating_census(new_tests: list[str], root: Path = BASE_DIR) -> bool:
    """Run the CI census and report its verdict. Never writes to any allowlist.

    Refuses the commit only for the files THIS commit introduces. The census
    describes the whole tree, and the tree can already be non-compliant through
    no fault of the author — `main` was red on two other people's files while
    this was being written. Blocking on those would be a false refusal for
    something the author cannot fix without stepping on the PR that already
    owns it, and a hook that refuses commits you cannot fix gets `--no-verify`d
    permanently. Pre-existing offenders are reported and left to CI.

    `--json` gives the classified file list on stdout AND the human-readable
    message on stderr, so the message an author reads here stays identical to
    the one CI prints.
    """
    print(f"[pre-commit] Test gating census ({len(new_tests)} new test file(s))...")
    try:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / CENSUS_TOOL),
             "--check-coverage", "--json", "--root", str(root)],
            capture_output=True, text=True, cwd=str(root), timeout=120,
            # encoding EXPLICIT, not text=True's default. The census message
            # contains em dashes and curly quotes; the child writes them as UTF-8
            # while `locale.getencoding()` on a Windows dev box is cp1252, in
            # which 0x9d is undefined — so the decode raises UnicodeDecodeError
            # and the hook dies in a traceback instead of printing the message.
            # Measured on this repo 2026-08-13 building the end-to-end proof.
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[pre-commit] Test gating census: SKIPPED — could not run ({exc})")
        return True

    if result.returncode == 0:
        print("[pre-commit] Test gating census: OK")
        return True

    # Which of the offending files did THIS commit introduce? An unparseable
    # report cannot answer that, so it blocks: the census said no, and the commit
    # that provoked it adds a test file.
    import json  # noqa: PLC0415 — only on the failure path, never on a clean commit

    over_ceiling = True
    try:
        report = json.loads(result.stdout)
        unlisted = set(report.get("unlisted") or [])
        mine = [f for f in new_tests if f in unlisted]
        theirs = sorted(unlisted - set(new_tests))
        # The census's OTHER failure mode: the grandfathered census grew past its
        # ceiling. `unlisted` is empty in that case — appending a new test file to
        # args/ci_test_backlog.txt is precisely how you make it empty — so
        # attributing on `unlisted` alone would wave through the one move the
        # census message explicitly forbids.
        over_ceiling = int(report.get("backlog", 0)) > int(report.get("backlog_max", 0))
        attributed = True
    except (ValueError, AttributeError, TypeError):
        mine, theirs, attributed = list(new_tests), [], False

    if theirs:
        print(
            f"[pre-commit] NOTE: {len(theirs)} test file(s) already in the tree are gated "
            "by nothing — CI is red on them independently of this commit: "
            + ", ".join(theirs)
        )
    if attributed and not mine and not over_ceiling:
        print("[pre-commit] Test gating census: OK — every test file this commit adds is gated")
        return True

    if mine:
        print("[pre-commit] BLOCKED: this commit adds a test file that CI would never run:")
        for path in mine:
            print(f"  {path}")
    else:
        print("[pre-commit] BLOCKED: the grandfathered test backlog grew past its ceiling.")
    # The census message names every offending file AND the file to append it to;
    # printed verbatim so a local failure reads identically to the CI one.
    if result.stderr and result.stderr.strip():
        print(result.stderr.strip())
    print(
        "[pre-commit] Append the file to args/ci_test_files/core.txt in THIS commit "
        "(after making it pass), or add a documented exclusion to "
        "args/test_gating_gate.yaml. Never add it to args/ci_test_backlog.txt."
    )
    return False


# --------------------------------------------------------------------------- #
# Skip census (trust-disc-03)
# --------------------------------------------------------------------------- #
# The gate above asks whether CI RUNS a file. This one asks whether the file
# ASSERTS anything, and a `pytest.skip` makes those different questions. It fires
# on MODIFIED files too, not just added ones — adding a skip to an existing gated
# test is the whole failure mode, and it never adds a file, so the `--diff-filter=AR`
# trigger the census above uses would miss every single instance of it.
#
# Scoped with `--changed` to the gated files THIS commit touches, so the author is
# never blocked by a skip somebody else left unregistered elsewhere in the tree.


def _run_skip_census(root: Path = BASE_DIR) -> bool:
    """Refuse a commit that adds an unregistered skip to a gated test file.

    Returns True (allow) whenever the census cannot be resolved. A fast path that
    cannot read its own policy must not block a commit — CI runs the same check
    and will not be so forgiving.
    """
    tool = BASE_DIR / SKIP_CENSUS_TOOL
    if not tool.is_file():
        return True
    try:
        result = subprocess.run(
            [sys.executable, str(tool), "--check", "--staged", "--root", str(root)],
            capture_output=True, text=True, cwd=str(root), timeout=120,
            # EXPLICIT, not text=True's default: the census message carries em
            # dashes, and `locale.getencoding()` on a Windows dev box is cp1252.
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[pre-commit] Skip census: SKIPPED — could not run ({exc})")
        return True

    if result.returncode == 0:
        out = (result.stdout or "").strip()
        if "touches no gated test file" not in out:
            print("[pre-commit] Skip census: OK")
        return True

    print("[pre-commit] BLOCKED: this commit adds a skip to a CI-gated test file.")
    print("[pre-commit] A skipped test satisfies the coverage claim while asserting nothing.")
    if result.stderr and result.stderr.strip():
        print(result.stderr.strip())
    print(
        "[pre-commit] Delete the skip and make the test run, or register it in "
        "args/ci_skip_census.txt with a written reason AND lower "
        "skip_census.skip_max only when you remove one."
    )
    return False


# ---------------------------------------------------------------------------
# Domain leak gate (xit-leak-01)
# ---------------------------------------------------------------------------
DOMAIN_LEAK_TOOL = Path("tools") / "ci" / "domain_leak_gate.py"


def _run_domain_leak_gate(root: Path = BASE_DIR) -> bool:
    """Refuse a commit that stages a broker credential, an ad_* dump, or (once
    armed) a file under a removed trading path. This repository is PUBLIC.

    Returns True (allow) whenever the gate cannot be resolved -- CI runs the
    same check and will not be so forgiving.
    """
    tool = BASE_DIR / DOMAIN_LEAK_TOOL
    if not tool.is_file():
        return True
    try:
        result = subprocess.run(
            [sys.executable, str(tool), "--check", "--staged", "--root", str(root)],
            capture_output=True, text=True, cwd=str(root), timeout=120,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[pre-commit] Domain leak gate: SKIPPED -- could not run ({exc})")
        return True
    if result.returncode == 0:
        print("[pre-commit] Domain leak gate: OK")
        return True
    print("[pre-commit] BLOCKED: this commit would publish trading-domain material or a broker credential.")
    if result.stdout and result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr and result.stderr.strip():
        print(result.stderr.strip())
    return False


def _run_blueprint_import_check() -> bool:
    """Run the coherence blueprint_imports check."""
    print("[pre-commit] Checking blueprint imports...")
    result = subprocess.run(
        [sys.executable, "tools/workflow/coherence_checker.py", "--check", "blueprint_imports", "--json"],
        capture_output=True, text=True, cwd=str(BASE_DIR), timeout=300,
    )
    if result.returncode != 0:
        print("[pre-commit] BLOCKED: Blueprint import check failed:")
        print(result.stdout or result.stderr)
        return False
    import json
    try:
        data = json.loads(result.stdout)
        checks = data.get("checks", [])
        for c in checks:
            if c.get("status") == "fail":
                print("[pre-commit] BLOCKED: Blueprint import failures:")
                for m in c.get("missing", []):
                    print(f"  {m}")
                return False
    except Exception:
        pass
    print("[pre-commit] Blueprint imports: OK")
    return True


#: How long the inline route smoke may take, and how many routes it may check.
#:
#: SURVEYED BEFORE CHANGING, 2026-08-15 (fli-smk-01), because arming or re-arming
#: a check without measuring its fire rate is how a gate earns itself a bypass.
#: What the measurement found is that this gate has never run to completion:
#:
#:   * _routes_for_changed_files is all-or-nothing. Any blueprint.py, app.py or
#:     templates/ path returns the FULL nav list; anything else usually returns
#:     zero and the gate skips early. There is no middle.
#:   * The full list is 79 routes and takes ~212s against a warm dashboard —
#:     2-3s per page, not the ~0.3s the fast pages suggest.
#:   * The timeout was 120s. So every run that actually reached the subprocess
#:     died on the timeout, and the timeout branch returned True.
#:
#: Route smoke therefore gated nothing, while printing as though it might. Same
#: shape as the `|| true` that neutered the PreToolUse hook: nominally
#: enforcing, actually inert, nothing red.
#:
#: The bound is what makes it able to finish. 20 routes x ~2.7s is ~55s, inside
#: the budget with headroom for a cold page. It is a REAL reduction in coverage
#: and is reported as such — route_smoke names every route it skipped, because a
#: cap you cannot see reads as "covered everything".
#:
#: Full coverage still has no home: route_smoke appears in no workflow under
#: .github/workflows, so this hook is the only place it runs at all. Fixing that
#: needs a live dashboard in CI (the E2E job has one) and is deliberately NOT
#: bundled here — but it must not be described as covered when it is not.
ROUTE_SMOKE_TIMEOUT_SECONDS = int(os.environ.get("ICDEV_ROUTE_SMOKE_TIMEOUT", "120"))
ROUTE_SMOKE_MAX_ROUTES = int(os.environ.get("ICDEV_ROUTE_SMOKE_MAX_ROUTES", "20"))


def _run_route_smoke(changed_files: list[str]) -> bool:
    """Run route smoke against running server for changed routes.

    Uses --changed mode so only affected routes are tested (fast).
    Falls back to skip gracefully when no routes are affected or when
    the dashboard is not running.
    """
    print("[pre-commit] Running route smoke test...")
    # Only pass dashboard-relevant files to avoid Windows cmd-line length limit.
    # Exclude build/ artifacts — they are generated output, not live routes.
    dashboard_files = [
        f for f in changed_files
        if not f.replace("\\", "/").startswith("build/")
        and any(pat in f.replace("\\", "/") for pat in DASHBOARD_PATTERNS)
    ]
    changed_arg = ",".join(dashboard_files)

    # Pre-check: ask route_smoke which routes it would test so we can skip
    # early when no routes are affected (e.g. only tool/test files changed).
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from tools.testing.route_smoke import _routes_for_changed_files, _server_up
        affected = _routes_for_changed_files(changed_files)
        if not affected:
            print("[pre-commit] Route smoke: no dashboard routes affected — skipped")
            return True
        # 127.0.0.1, never "localhost": on Windows localhost resolves to ::1
        # first, the dashboard binds IPv4, and the probe reports a live
        # server dead — which here means silently SKIPPING the gate. The
        # socket guard below already used 127.0.0.1; these two disagreeing
        # is what made the skip look like a considered decision.
        if not _server_up("http://127.0.0.1:5050", timeout=2.0):
            print("[pre-commit] Route smoke: dashboard not running — skipped")
            return True
    except Exception:
        pass  # fall through to subprocess approach

    # Guard: if import-based check failed, verify server is reachable via socket
    # before launching subprocess — avoids a 60-second timeout when server is down.
    import socket as _socket
    try:
        with _socket.create_connection(("127.0.0.1", 5050), timeout=2.0):
            pass
    except OSError:
        print("[pre-commit] Route smoke: dashboard not running (port 5050 closed) — skipped")
        return True

    try:
        result = subprocess.run(
            [sys.executable, "tools/testing/route_smoke.py", "--changed", changed_arg,
             "--max-routes", str(ROUTE_SMOKE_MAX_ROUTES)],
            capture_output=True, text=True, cwd=str(BASE_DIR),
            timeout=ROUTE_SMOKE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # NOT "OK", and not silent. A gate that cannot run is not a gate that
        # found nothing — and this one timed out on EVERY run that reached it
        # (see ROUTE_SMOKE_MAX_ROUTES), so the old `return True` meant route
        # smoke had never gated a commit while printing as though it might.
        print(f"[pre-commit] Route smoke DID NOT RUN: exceeded "
              f"{ROUTE_SMOKE_TIMEOUT_SECONDS}s even bounded to "
              f"{ROUTE_SMOKE_MAX_ROUTES} route(s). Nothing was verified.")
        print("[pre-commit] Commit allowed (route smoke needs a live dashboard and "
              "runs nowhere else), but these routes are UNCHECKED — run: "
              "python tools/testing/route_smoke.py --all")
        return True

    if result.returncode == 0:
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                if "[FAIL]" in line or line.startswith("FAIL"):
                    print(f"  {line}")
        print("[pre-commit] Route smoke: OK")
        return True

    print("[pre-commit] BLOCKED: Route smoke failed:")
    print(result.stdout or result.stderr)
    return False


def main() -> int:
    name_status = _get_staged_name_status()
    staged = [path for _status, path in name_status]
    if not staged:
        return 0

    failed = False

    # Test gating census — only when this commit introduces a test file. A commit
    # that adds nothing returns from _staged_new_test_files without reading the
    # policy config, so it pays no measurable time.
    new_tests = _staged_new_test_files(name_status)
    if new_tests and not _run_test_gating_census(new_tests):
        failed = True

    # Skip census — fires on any staged change to a GATED test file, added or
    # modified. Its own --staged mode returns immediately when none of the staged
    # paths is in scope, so a commit touching no gated test pays one subprocess
    # and no scan.
    if any(f.endswith(".py") for f in staged) and not _run_skip_census():
        failed = True

    # Domain leak gate -- every staged file, any suffix. Its --staged mode is
    # one subprocess and a regex pass over the staged paths only.
    if not _run_domain_leak_gate():
        failed = True

    # Always run blueprint import check when Python files change
    py_changes = [f for f in staged if f.endswith(".py")]
    if py_changes:
        if not _run_blueprint_import_check():
            failed = True

    # Run route smoke when dashboard files change (requires running server)
    if not failed and _is_dashboard_change(staged):
        if not _run_route_smoke(staged):
            failed = True

    if failed:
        print("\n[pre-commit] Commit BLOCKED. Fix the issues above and retry.")
        return 1

    print("[pre-commit] All pre-commit checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
