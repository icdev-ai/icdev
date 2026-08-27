#!/usr/bin/env python3
"""A closed census may LOSE names and must never GAIN one (cef-ci-02).

WHAT THIS GUARDS
----------------
Two files under `args/` are *closed censuses* — an enumerated list of
pre-existing debt that policy says only ever shrinks:

  * ``args/ci_test_backlog.txt``  test modules CI does not run (tsg-policy-01)
  * ``args/ci_skip_census.txt``   skip sites inside gated test files (trust-disc-03)

Both are enumerated rather than counted on purpose, and
``args/test_gating_gate.yaml`` says why in its own words: "A bare count can be
held constant while the set churns: fix one file, add one ungated file, count
unchanged, gate green, gap silently regrown."

THE HOLE THIS CLOSES
--------------------
Identity was tracked but never RATCHETED. Nothing ever compared a census against
its previous self, so the only thing standing between a gated test and the
grandfathered list was the numeric ceiling — and a ceiling is precisely the bare
count the enumeration exists to distrust.

``args/test_gating_gate.yaml`` states the ceiling "equals the count at adoption —
no headroom, deliberately, because headroom is room for the gap to regrow
unobserved". Measured on main at 42f7ea894 that held for ``skip_max`` (81 sites,
ceiling 81) and did NOT hold for ``backlog_max``: 1703 entries against a ceiling
of 1711. Those eight slots were nobody's decision. They are the lag between
deleting a census line — which a PR must do to gate a file — and lowering the
ceiling, which it may simply forget.

Eight slots is enough to matter, demonstrated on this tree before the fix:
delete the ``core.d`` fragments naming eight gated CEF test files (among them
``tests/cortex/test_resolve_facade.py`` and
``tests/cortex/test_resolve_trust_loop.py`` — the ``cortex.resolve()`` facade and
its TRUST loop), append those eight paths to ``args/ci_test_backlog.txt``, and
``gated_test_list.py --check-coverage`` reports

    2289 collectible test modules — 446 gated, 132 excluded,
    1711 grandfathered (ceiling 1711), 0 unlisted.

and exits 0. Eight suites leave CI and every gate stays green.

WHY A SET RULE AND NOT A TIGHTER CEILING
----------------------------------------
"The ceiling must equal the measured count" closes the same hole and must not be
enforced here. Gating a backlogged file LOWERS the measured count, so two
concurrent PRs that each gate one file and each lower the ceiling by one both
merge green and leave main with a ceiling one above the count — main goes red
for a condition neither PR caused. That race is routine rather than theoretical:
over the census's history, line deletions land roughly five times a day.

Set monotonicity has no such interaction. Two PRs deleting different lines each
gained nothing, and neither does their merge. It is also the STRONGER rule: it
refuses a name being added whatever the ceiling happens to be, which is the
manoeuvre above.

SURVEYED BEFORE ARMING
----------------------
Per the CLAUDE.md rule that a check armed without a measured fire rate is
unmeasured rather than proven. Every commit touching ``args/ci_test_backlog.txt``
since it was adopted (ceb10709b, 2026-08-12): **35 commits, every one +0 lines**
— 150 deletions and ZERO additions. This check would have refused nothing in the
census's entire history.

``args/ci_skip_census.txt`` has had no commit since its own adoption, so its rate
is UNMEASURABLE rather than zero, and this module reports it that way rather than
claiming a measured clean sweep it did not measure. It is registered regardless
because its ceiling has no headroom today, so a name added to it already fails
``skip_census.py --check`` — this is a belt beside that brace, and arming it
costs nothing precisely because nothing has ever tripped it.

WHAT IT DELIBERATELY DOES NOT GUARD
-----------------------------------
``args/undeclared_import_census.txt`` and ``args/kanban_raw_insert_census.txt``
are closed censuses under the same discipline and are NOT registered here. They
are unrelated to test gating, neither has been surveyed, and each already has a
``*_max`` ceiling of its own. Adding one is a config entry in ``CENSUSES`` plus
its own survey — not a code change.

EXIT CODES
----------
  0  no registered census gained a name
  1  a census gained a name
  2  the comparison could not be made (no merge base — a shallow checkout)

2 is not 0. A gate that could not run is not a gate that found nothing, so it
stays red; the `test` job checks out with ``fetch-depth: 0`` for exactly this.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

# Sibling modules, imported by path-independent name so this works from the
# `tools/` checkout and the packaged `icdev/tools/` mirror alike. The import
# direction matters: isolation_run already imports gated_test_list, so this
# module importing BOTH keeps the graph a DAG. Never import this from either.
try:  # pragma: no cover - exercised implicitly by both import paths
    from tools.ci.gated_test_list import parse as parse_backlog  # type: ignore
    from tools.ci.gated_test_list import repo_root  # type: ignore
    from tools.ci.isolation_run import ResolutionError, resolve_base  # type: ignore
    from tools.ci.skip_census import parse_census as parse_skip_census  # type: ignore
except ImportError:  # pragma: no cover - direct `python tools/ci/census_growth.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.ci.gated_test_list import parse as parse_backlog  # type: ignore
    from tools.ci.gated_test_list import repo_root  # type: ignore
    from tools.ci.isolation_run import ResolutionError, resolve_base  # type: ignore
    from tools.ci.skip_census import parse_census as parse_skip_census  # type: ignore


def _names_from_backlog(text: str) -> List[str]:
    """One bare path per line; `#` comments and blanks dropped."""
    return list(parse_backlog(text))


def _names_from_skip_census(text: str) -> List[str]:
    """`<site key>  # <reason>` lines. The key is the identity; the reason is not.

    A reason being REWORDED must not read as a new skip, so only the key is
    compared. Making a reason worse is a different defect with a different
    check — `skip_census.py` already refuses a placeholder one.
    """
    return list(parse_skip_census(text).entries)


@dataclass(frozen=True)
class Census:
    """A closed census: an enumerated list that policy says only shrinks."""

    path: str
    #: What the entries ARE, for the failure message.
    unit: str
    #: How a name is recovered from the file's text.
    reader: Callable[[str], List[str]]
    #: The fix a violating PR should make instead of adding a name.
    remedy: str
    #: Measured fire rate over the file's post-adoption history, or None when
    #: the file has no post-adoption history to measure. `None` is reported as
    #: UNMEASURABLE and never rendered as a clean zero.
    surveyed_commits: Optional[int]


#: The registered censuses. Adding one is a config edit plus its own survey.
def _names_from_plain_list(text: str) -> set:
    """One path per line, `#` comments and blanks dropped (wire-req-01).

    The simplest census shape there is. Kept separate from the backlog/skip readers rather
    than reused: those two strip trailing metadata this file does not have, and sharing a
    parser would make a change for one silently reinterpret the others.
    """
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


CENSUSES = (
    Census(
        path="args/kanban_seeder_criteria_census.txt",
        unit="seeder that creates a build/fix card with no acceptance_criteria",
        reader=_names_from_plain_list,
        remedy=(
            "give the module a real acceptance criterion for the cards it seeds -- what "
            "would a reader check to know the card was delivered -- and remove its line. "
            "Draining this file to zero is what lets "
            "KANBAN_REQUIRE_ACCEPTANCE_CRITERIA move from `report` to `enforce`; a new "
            "entry is a new seeder whose cards nothing can judge"
        ),
        surveyed_commits=None,
    ),
    Census(
        path="args/ci_test_backlog.txt",
        unit="ungated test module",
        reader=_names_from_backlog,
        remedy=(
            "make the test pass and gate it in args/ci_test_files/core.d/<task-id>.txt "
            "— that is the only sanctioned way to widen the allowlist. If it genuinely "
            "should not be gated, add an exclusion WITH A REASON to "
            "args/test_gating_gate.yaml"
        ),
        surveyed_commits=35,
    ),
    Census(
        path="args/ci_skip_census.txt",
        unit="skip site in a gated test file",
        reader=_names_from_skip_census,
        remedy=(
            "delete the skip and make the test run. A gated test that skips is "
            "UNMEASURED, not passing, and registering one is a debt you have "
            "written down"
        ),
        surveyed_commits=None,
    ),
)


@dataclass
class CensusResult:
    path: str
    unit: str
    #: Names present now and absent at the base. The finding.
    added: List[str] = field(default_factory=list)
    #: Names dropped since the base. Reported because shrinking is the point.
    removed: List[str] = field(default_factory=list)
    now: int = 0
    base: int = 0
    #: True when the file does not exist at the base — a census being
    #: INTRODUCED has gained nothing, whatever set arithmetic says.
    introduced: bool = False
    #: None when the file has no surveyed post-adoption history.
    surveyed_commits: Optional[int] = None
    remedy: str = ""

    @property
    def ok(self) -> bool:
        return not self.added

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "unit": self.unit,
            "added": self.added,
            "removed": self.removed,
            "now": self.now,
            "base": self.base,
            "introduced": self.introduced,
            "fire_rate": (
                "unmeasurable"
                if self.surveyed_commits is None
                else f"0/{self.surveyed_commits} commits"
            ),
            "ok": self.ok,
        }


def _read_at(root: Path, ref: str, relpath: str) -> Optional[str]:
    """The file's content at `ref`, or None when it did not exist there.

    `git show` is used rather than reading a worktree: the base is a commit, not
    a checkout, and materialising one to read a text file would cost a worktree
    per run.
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{relpath}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def compare(root: Path, base_ref: str, census: Census) -> CensusResult:
    """Set-compare one census against `base_ref`."""
    result = CensusResult(
        path=census.path,
        unit=census.unit,
        surveyed_commits=census.surveyed_commits,
        remedy=census.remedy,
    )

    current_path = root / census.path
    current_text = current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
    now = set(census.reader(current_text))
    result.now = len(now)

    base_text = _read_at(root, base_ref, census.path)
    if base_text is None:
        # Absent at the base. Introducing a census is not growing one.
        result.introduced = True
        result.base = 0
        return result

    before = set(census.reader(base_text))
    result.base = len(before)
    result.added = sorted(now - before)
    result.removed = sorted(before - now)
    return result


def check(root: Optional[Path] = None, base: Optional[str] = None) -> Dict[str, object]:
    """Compare every registered census against the merge base.

    Raises `ResolutionError` when no base can be resolved. That is exit 2 at the
    CLI and never a clean report: a comparison that could not be made has not
    found the censuses unchanged, and saying so is the point.
    """
    root = root or repo_root()
    base_ref = resolve_base(root, base)

    results = [compare(root, base_ref, c) for c in CENSUSES]
    return {
        "base": base_ref,
        "censuses": [r.to_dict() for r in results],
        "grew": [r.path for r in results if not r.ok],
        "ok": all(r.ok for r in results),
        "_results": results,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: derived from __file__)")
    parser.add_argument("--base", help="ref this branch forked from (default: auto-detected)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when a census gained a name, 2 when it could not be compared")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")  # type: ignore[union-attr]

    root = args.root.resolve() if args.root else repo_root()

    try:
        report = check(root, args.base)
    except ResolutionError as exc:
        # Exit 2, not 1 and emphatically not 0. "Could not compare" is a third
        # state, and collapsing it into either of the other two is how a gate
        # that never ran reads as a gate that found nothing.
        print(f"::error::census growth: {exc}", file=sys.stderr)
        return 2

    results: List[CensusResult] = report.pop("_results")  # type: ignore[assignment]

    if args.json:
        print(json.dumps(report, indent=2))

    if not args.json:
        print(f"Closed-census growth vs {report['base']}:")
        for r in results:
            rate = "unmeasurable" if r.surveyed_commits is None else f"0/{r.surveyed_commits}"
            state = "INTRODUCED" if r.introduced else (
                f"+{len(r.added)} / -{len(r.removed)}"
            )
            print(
                f"  {r.path}: {r.base} -> {r.now} {r.unit}(s), {state} "
                f"[surveyed fire rate {rate}]"
            )

    for r in results:
        if r.ok:
            continue
        shown = ", ".join(r.added[:20]) + (f" (+{len(r.added) - 20} more)" if len(r.added) > 20 else "")
        print(
            f"::error::census growth: {r.path} gained {len(r.added)} "
            f"{r.unit}(s): {shown}. That census is CLOSED and only shrinks — a name "
            f"added to it is surface leaving CI, and the ceiling cannot see it because "
            f"a count is exactly what an enumerated census exists to distrust. "
            f"Instead: {r.remedy}.",
            file=sys.stderr,
        )

    if args.check and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
