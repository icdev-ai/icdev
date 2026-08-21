#!/usr/bin/env python3
# CUI // SP-CTI
"""Census of the PERFECT-SCORE-FOR-NO-DATA fallback (rem-hyg-13).

WHY THIS EXISTS
---------------
Three of the four defects fixed on 2026-08-20 were one defect wearing different
clothes: a surface rendering a CONFIDENT NUMBER over an ABSENCE.

  * rem-hyg-08  a project card computed from rows no epic claimed
  * cch-obs-03 / ctx-obs-03  a cache and a governance card reporting a rate
    nobody had measured
  * rem-hyg-09  Compliance Posture scoring canvases nobody had ever assessed

The most dangerous concrete form is a HARDCODED PERFECT SCORE returned when the
denominator is empty::

    pct = round(within / total_relevant * 100, 1) if total_relevant > 0 else 100.0

Nothing was scanned, nothing was assessed, nothing was measured -- and the page
draws a full green bar at 100%. It is strictly worse than a missing number,
because a missing number prompts somebody to go and measure; a perfect one
closes the question.

WHAT THE DEFECT ACTUALLY IS
---------------------------
NOT "the literal 100.0 appears in a fallback". The finding is the CONJUNCTION:

    a conditional expression whose FALLBACK arm is the constant 100.0,
    guarding a body that computes a RATIO (a division).

Both halves are load-bearing, and requiring the ratio is what keeps this gate
high-signal. Measured over the tree, ``else 100.0`` appears at 15 sites and TWO
of them are not scores at all:

  * tools/trading/data/fixture_provider.py -- a synthetic bar price for a symbol
    with no fixture. 100.0 is a made-up dollar figure, not a percentage.
  * tools/trading/data/macro_data.py -- the US Dollar Index, whose BASE IS 100
    by definition. A DXY of 100.0 is the neutral reading, not a perfect one.

Neither body divides, so neither is a finding, and neither needs a written
excuse. Encoding that STRUCTURALLY is stronger than exempting it by hand: an
exemption list is a claim a reviewer has to check, and a predicate is one the
scanner re-derives on every run.

The same property disposes of a third false positive for free. A grep for
``else 100.0`` matches tools/canvas_compliance/posture.py:260 -- which is a
COMMENT, inside the rem-hyg-09 fix, explaining the very defect that was removed
there. This scanner parses to an AST and so cannot see a comment at all. A
census whose first entry was the previous fix's own explanation of itself would
have discredited the gate on the day it shipped.

WHY THE CONSTANT IS ``100.0`` AND NEVER ``100``
-----------------------------------------------
Deliberately narrow, and measured rather than assumed. Widening to the bare int
adds ZERO true positives over this tree and adds one legitimate site that would
then need an excuse: tools/trading/dashboard/app.py computes RSI as
``100 - (100 / (1 + rs)) if avg_loss > 0 else 100``, and an RSI of 100 with no
down moves is the DEFINITION of the indicator, not a fabrication. A gate that
must be argued with on its first run is a gate people learn to bypass. The card
measured ``else 100.0``; that is what this enforces.

WHY NOT THE BROADER ``if X else 0`` SHAPE
------------------------------------------
Because it is 1,167 occurrences across 566 files and most of them are ordinary
counters and indices. Refusing those refuses routine work -- which is exactly
the defect the PreToolUse fire-rate survey found (CLAUDE.md: 1.63% of calls is
already grounds for standing a check down). ``else 100.0`` over a ratio is 12
sites: enumerable, high-signal, and gateable.

THE CORRECT FIX
---------------
The convention is already in the tree -- tools/quality/component_scorer.py's
:data:`NOT_ASSESSED`. Return ``None``, never a number, and let the renderer say
"not assessed". ``None`` and ``0.0`` are different claims and templates in this
repo already tell them apart (see tools/dashboard/templates/network/compare.html
and network/enterprise.html, which render an em dash and "No audits").

CENSUS DISCIPLINE
-----------------
Same as args/kanban_raw_insert_census.txt and args/undeclared_import_census.txt.
The census ENUMERATES sites by name. It does not count them. A bare count can
be held constant while the set churns -- delete one site, add another, count
unchanged, gate green, and the thing the gate exists to notice has happened
unobserved. Identity is the only thing that survives that.

``perfect_score_census.perfect_score_max`` in ``args/perfect_score_gate.yaml``
is a ceiling on the REGISTERED count and MAY ONLY GO DOWN.

PER SITE, NOT PER FILE
----------------------
The key is ``<file>::<qualname>#<n>``. A per-FILE census would grandfather a
module once and then let it grow a second and third fabricated score without a
word. Line numbers are deliberately absent from the key: they churn on every
edit above the site, which would make the census a merge-conflict generator and
every unrelated PR a census edit. ``#<n>`` is the ordinal WITHIN the enclosing
function, so two sites in one function stay distinguishable without pinning a
line.

USAGE
-----
    python tools/ci/perfect_score_census.py --check        # the gate
    python tools/ci/perfect_score_census.py --json
    python tools/ci/perfect_score_census.py --changed tools/foo.py --check
    python tools/ci/perfect_score_census.py --staged
    python tools/ci/perfect_score_census.py --prune
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    """Walk up to the checkout root, rather than counting parents.

    This module is mirrored to ``icdev/tools/ci/``, where a fixed
    ``parents[2]`` resolves to ``<repo>/icdev`` and every path below it is
    wrong. Resolved from ``__file__`` and never from ``os.getcwd()``, which is
    the worktree root under a git worktree (see CLAUDE.md).
    """
    for candidate in (start, *start.parents):
        if (candidate / "requirements.txt").exists() and (candidate / "args").is_dir():
            return candidate
    return start.parents[2]


REPO = _find_repo_root(Path(__file__).resolve().parent)
GATE_FILE = REPO / "args" / "perfect_score_gate.yaml"

#: The fabricated value. A FLOAT literal only -- see the module docstring for
#: why the bare int is deliberately out of scope.
PERFECT = 100.0


# -- the two predicates -----------------------------------------------------
def is_perfect_constant(node: ast.AST) -> bool:
    """True for the float literal 100.0, and never for the int 100 or True.

    ``isinstance(True, int)`` is True in Python and ``100.0 == 100`` is also
    True, so an ``== PERFECT`` test alone would accept both. The type is
    checked first and exactly.
    """
    return (
        isinstance(node, ast.Constant)
        and type(node.value) is float  # noqa: E721 -- exact, not isinstance
        and node.value == PERFECT
    )


def computes_ratio(node: ast.AST) -> bool:
    """True when this expression divides -- i.e. it has a DENOMINATOR.

    This is the half that separates a fabricated SCORE from a fallback that
    merely happens to be the number 100.0. A percentage, a coverage, a
    compliance rate and a pass rate all divide by the thing that was counted;
    a synthetic price and an index base do not.

    ``ast.walk`` is used on purpose so the division may be nested arbitrarily
    deep -- the common spelling in this tree wraps it in ``round(...)``.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.BinOp) and isinstance(child.op, (ast.Div, ast.FloorDiv)):
            return True
    return False


def is_finding(node: ast.AST) -> bool:
    """The conjunction: a 100.0 fallback arm guarding a ratio."""
    return (
        isinstance(node, ast.IfExp)
        and is_perfect_constant(node.orelse)
        and computes_ratio(node.body)
    )


# -- scanning ---------------------------------------------------------------
def _qualname_index(tree: ast.AST) -> dict[int, str]:
    """Map every node's lineno to its enclosing def/class qualname."""
    index: dict[int, str] = {}

    def walk(node, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                for line in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    index[line] = qual
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(tree, "")
    return index


def _unparse(node: ast.AST) -> str:
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover -- unparse is total on parsed trees
        return "<unparseable>"
    return text if len(text) <= 160 else text[:157] + "..."


def scan_source(source: str, rel: str) -> list[dict]:
    """Every perfect-score-for-no-data site in one module's source.

    Separated from :func:`scan_file` so a test can hand it a string, which is
    what lets the "a comment is never a finding" property be asserted directly
    rather than inferred from a whole-tree scan.
    """
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []

    quals = _qualname_index(tree)
    found: list[dict] = []
    for node in ast.walk(tree):
        if not is_finding(node):
            continue
        found.append({
            "file": rel,
            "line": node.lineno,
            "qualname": quals.get(node.lineno, "<module>"),
            "expression": _unparse(node),
        })

    # Ordinal WITHIN the enclosing function, so a second site in the same
    # function is a distinct entry without the key carrying a line number.
    found.sort(key=lambda s: s["line"])
    seen: dict[str, int] = {}
    for site in found:
        qual = site["qualname"]
        ordinal = seen.get(qual, 0)
        seen[qual] = ordinal + 1
        site["key"] = "{}::{}#{}".format(rel, qual, ordinal)
    return found


def scan_file(path: Path, repo: Path = REPO) -> list[dict]:
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        rel = path.as_posix()
    return scan_source(source, rel)


# -- config -----------------------------------------------------------------
def load_gate(path: Path = GATE_FILE) -> dict:
    try:
        import yaml  # noqa: PLC0415 -- pyyaml IS declared
    except ImportError:  # pragma: no cover
        raise SystemExit("perfect_score_census: pyyaml is required and declared")
    if not path.exists():
        raise SystemExit("perfect_score_census: missing gate config {}".format(path))
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded.get("perfect_score_census", {})


def load_census(repo: Path, cfg: dict) -> set[str]:
    census_path = repo / cfg.get("census_file", "args/perfect_score_census.txt")
    if not census_path.exists():
        return set()
    entries = set()
    for line in census_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A key contains '#<ordinal>', so a trailing comment is only recognised
        # when it is set off by whitespace. Splitting on a bare '#' would
        # truncate every key at its ordinal.
        entries.add(stripped.split("  #")[0].strip())
    return entries


def _excluded(rel: str, cfg: dict) -> bool:
    for entry in cfg.get("exclude", []) or []:
        if fnmatch(rel, entry.get("path", "")):
            return True
    return False


def collect(repo: Path, cfg: dict, only: list[str] | None = None) -> list[dict]:
    roots = cfg.get("scan_roots", ["tools", "icdev/tools"])
    targets: list[Path] = []
    if only is not None:
        targets = [repo / f for f in only if f.endswith(".py")]
    else:
        for root in roots:
            base = repo / root
            if base.is_dir():
                targets += sorted(base.rglob("*.py"))

    sites: list[dict] = []
    for path in targets:
        if not path.exists():
            continue
        try:
            rel = path.relative_to(repo).as_posix()
        except ValueError:
            continue
        if not any(rel == r or rel.startswith(r + "/") for r in roots):
            continue
        if _excluded(rel, cfg):
            continue
        sites += scan_file(path, repo)
    return sorted(sites, key=lambda s: (s["file"], s["line"]))


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".py")]


# -- report -----------------------------------------------------------------
def build_report(repo: Path = REPO, only: list[str] | None = None) -> dict:
    cfg = load_gate()
    census = load_census(repo, cfg)
    sites = collect(repo, cfg, only)
    keys = [s["key"] for s in sites]

    unregistered = [s for s in sites if s["key"] not in census]
    ceiling = int(cfg.get("perfect_score_max", 0))

    report = {
        "scope": "changed" if only is not None else "tree",
        "sites_seen": len(sites),
        "registered": len([k for k in keys if k in census]),
        "unregistered": unregistered,
        "census_size": len(census),
        "ceiling": ceiling,
        "over_ceiling": len(census) > ceiling,
        "ok": not unregistered and len(census) <= ceiling,
    }
    if only is None:
        report["stale_entries"] = sorted(census - set(keys))
    return report


def prune(repo: Path = REPO) -> int:
    """Drop census entries whose site no longer exists. Only ever SHRINKS."""
    cfg = load_gate()
    census_path = repo / cfg.get("census_file", "args/perfect_score_census.txt")
    live = {s["key"] for s in collect(repo, cfg)}
    kept, dropped = [], 0
    for line in census_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if stripped.split("  #")[0].strip() not in live:
                dropped += 1
                continue
        kept.append(line)
    census_path.write_text(
        "\n".join(kept).rstrip("\n") + "\n", encoding="utf-8", newline="\n"
    )
    return dropped


ADVICE = """
A perfect score computed over an empty denominator is a claim nobody measured.
Return None and let the renderer say "not assessed" -- the convention is already
in the tree as tools/quality/component_scorer.py::NOT_ASSESSED, and templates
here already tell None from 0.0 (network/compare.html, network/enterprise.html).

    score = round(passed / total * 100, 1) if total else None      # not 100.0

Registering it in args/perfect_score_census.txt is a debt you have written
down, and it breaches the ceiling -- perfect_score_max may only go DOWN.
""".strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on a NEW site")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--changed", nargs="*", help="limit the scan to these files")
    parser.add_argument("--staged", action="store_true", help="scan only staged files")
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args(argv)

    if args.prune:
        dropped = prune()
        print("Perfect-score census: pruned {} stale entr(ies).".format(dropped))
        return 0

    only = None
    if args.staged:
        only = _staged_files(REPO)
    elif args.changed is not None:
        only = list(args.changed)

    report = build_report(REPO, only)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            "Perfect-score census ({}): {} site(s) seen, {} registered, "
            "{} unregistered | census {} (ceiling {})".format(
                report["scope"], report["sites_seen"], report["registered"],
                len(report["unregistered"]), report["census_size"], report["ceiling"],
            )
        )
        for site in report["unregistered"][:40]:
            print("  NEW  {}:{}  in {}  ->  {}".format(
                site["file"], site["line"], site["qualname"], site["expression"]))
        if report.get("over_ceiling"):
            print(
                "  CEILING BREACHED: census {} > {}. perfect_score_max may only "
                "go DOWN.".format(report["census_size"], report["ceiling"])
            )
        for stale in report.get("stale_entries", []):
            print("  stale census entry (site gone; run --prune): {}".format(stale))

    if args.check and not report["ok"]:
        print("\n" + ADVICE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
