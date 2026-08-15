#!/usr/bin/env python3
"""Census of pytest SKIPS inside the CI-gated test allowlist (trust-disc-03).

WHY THIS EXISTS
---------------
`tools/ci/gated_test_list.py --check-coverage` proves every test module under
`tests/` is either gated, excluded with a reason, or in the grandfathered
backlog. It answers "does CI run this file?" and nothing else. A file can be on
the allowlist, be run on every PR, report green, and assert *nothing* — because
it skipped.

The concrete case this was built from is still live in the tree today — verified
by running the gated file on 2026-08-15:

    SKIPPED [1] tests/test_app.py:46: SQLite test DB lacks platform schema for
    overview: no such table: agents

That test is gated. It has been gated the whole time, and it covers
`/api/charts/overview` against the platform schema. Its skip catches whatever
`OperationalError` the route raises first, so the MESSAGE moves as
`MINIMAL_ICDEV_SCHEMA` in `tests/conftest.py` gains pieces — it read `no such
column: classification` when the missing piece was the column the RLS predicate
in `get_connection()` filters on, which turned *every* read of `kanban_tasks`
into a raise; today the first missing piece is the `agents` table. The site is
the same site, and either way the skip presented an unrun route as coverage for
an unknown length of time. Nothing was red. Nothing was measured.

So: a skip is an UNMEASURED test, not a passing one, and the number of them must
be a governed quantity rather than an emergent one.

WHAT IT MEASURES — TWO HALVES, ON PURPOSE
-----------------------------------------
1. STATIC SITE CENSUS (`--check`, the ratchet). Every skip *site* in a gated
   test file — `pytest.skip`, `pytest.importorskip`, `@pytest.mark.skip`,
   `@pytest.mark.skipif`, `unittest.skip*`, `self.skipTest` — is found by AST
   scan and must be registered by name in `args/ci_skip_census.txt` with a
   written reason. It is deterministic, it runs in under a second, it does not
   need a test run, and it is what fails a PR that adds an unregistered skip.

2. RUNTIME SKIP REPORT (`--from-report`, the "count skips in the gated run"
   half). Parses the JUnit XML that the gated pytest run emits and reports what
   ACTUALLY skipped, per file, with reasons. This is not redundant with the
   static half: the static scan reads source text and therefore cannot see a
   skip raised from a `conftest.py` fixture, from a plugin, from a rebound
   alias, or from `pytest.ini`'s collection rules. A gated file that skipped at
   runtime while declaring no skip site in its own source is an *unaccounted*
   skip, and that is a failure too.

Neither half subsumes the other. The static half is the gate you can run before
you commit; the runtime half is the one that cannot be fooled by indirection.

CENSUS DISCIPLINE (same as args/ci_test_backlog.txt, and for the same reason)
-----------------------------------------------------------------------------
The census ENUMERATES skips by name. It does not count them. A bare count can be
held constant while the set churns — delete one skip, add one skip, count
unchanged, gate green, and the thing the gate exists to notice has happened
without the gate noticing. Identity is what gets tracked.

`skip_census.skip_max` in `args/test_gating_gate.yaml` is a ceiling on the
registered count and MAY ONLY GO DOWN. Lower it when you delete a skip. Never
raise it to get a commit through — raising it is the visible, reviewable act of
deciding to take on more unmeasured surface, and it belongs in a PR description.

WHAT FAILS
----------
  * a skip site in a gated file that is not in the census                (NEW)
  * a census entry whose reason is missing, too short, or a placeholder
  * a registered count above `skip_max` — the census grew
  * `--from-report`: a runtime skip in a gated file with no registered site

WHAT ONLY WARNS
---------------
  * a census entry whose site no longer exists (deleting a skip must never fail
    the PR that deleted it). `--prune` clears them, and the ceiling drops.

Usage
-----
    python tools/ci/skip_census.py --json            # full report
    python tools/ci/skip_census.py --check           # the gate; exit 1 on a defect
    python tools/ci/skip_census.py --check --changed tests/test_app.py
    python tools/ci/skip_census.py --from-report junit.xml --check
    python tools/ci/skip_census.py --prune           # drop entries whose site is gone
    python tools/ci/skip_census.py --seed            # write the census (adoption only)
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Sibling import — deliberately BY PATH, not `from tools.ci import ...`
# --------------------------------------------------------------------------- #
# `gated_test_list` defines what "gated" means, and this module must agree with
# it exactly: a census scoped differently from the allowlist would either nag
# about files CI never runs or wave through skips inside files it does. Loading
# the sibling FILE guarantees we read the copy in THIS checkout. A package import
# resolves through `sys.path`, which in a worktree can land on the shared
# checkout's `tools/` and silently census a different tree.
def _load_gated_test_list():
    path = Path(__file__).resolve().with_name("gated_test_list.py")
    spec = importlib.util.spec_from_file_location("_icdev_gated_test_list", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec. `@dataclass` resolves `cls.__module__` through
    # sys.modules, so a by-path load that skips this raises AttributeError on the
    # first decorated class in the loaded file.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_gtl = _load_gated_test_list()
AllowlistError = _gtl.AllowlistError
repo_root = _gtl.repo_root

#: Where the enumerated census lives. Flat, line-oriented, one site per line —
#: the shape `merge=union` is safe for (see `.gitattributes`).
CENSUS_FILE = Path("args") / "ci_skip_census.txt"

#: Policy block key inside args/test_gating_gate.yaml.
CONFIG_KEY = "skip_census"

#: A reason shorter than this is not a reason. "flaky" and "TBD" are the two
#: this exists to refuse; they say a skip happened, not why it is acceptable.
DEFAULT_MIN_REASON_CHARS = 12

#: Reasons that clear the length bar while still saying nothing.
PLACEHOLDER_REASONS = {
    "todo", "tbd", "wip", "n/a", "na", "none", "temporary", "temp", "flaky",
    "see above", "see below", "fixme", "unknown", "placeholder", "later",
    "not sure", "no reason", "needs investigation", "investigate",
}

#: Dotted call/decorator expressions that mean "this test did not run".
#: Matched against the unparsed expression by exact equality or dotted suffix,
#: longest key first, so `pytest.mark.skipif` never falls through to `mark.skip`.
#:
#: DELIBERATE NON-GOAL: an aliased or dynamically-built skip (`s = pytest.skip`)
#: is not matched here. Chasing it statically is a losing game, which is exactly
#: why `--from-report` exists — the runtime half sees the skip whatever spelled
#: it, and fails on a file whose skips are unaccounted for.
SKIP_EXPRESSIONS: Dict[str, str] = {
    "pytest.mark.skipif": "pytest.mark.skipif",
    "pytest.mark.skip": "pytest.mark.skip",
    "pytest.importorskip": "pytest.importorskip",
    "pytest.skip": "pytest.skip",
    "unittest.skipUnless": "unittest.skipUnless",
    "unittest.skipIf": "unittest.skipIf",
    "unittest.skip": "unittest.skip",
    "mark.skipif": "pytest.mark.skipif",
    "mark.skip": "pytest.mark.skip",
    "skipUnless": "unittest.skipUnless",
    "skipIf": "unittest.skipIf",
    "skipTest": "unittest.skipTest",
    "importorskip": "pytest.importorskip",
}

#: Longest-first so a suffix match cannot shadow a more specific one.
_EXPR_ORDER: List[str] = sorted(SKIP_EXPRESSIONS, key=len, reverse=True)

#: `<file>::<qualname>::<kind>[<ordinal>]` — see `SkipSite.key`.
_KEY_RE = re.compile(r"^(?P<file>[^:]+)::(?P<qual>.+)::(?P<kind>[A-Za-z_.]+)\[(?P<n>\d+)\]$")


class SkipCensusError(RuntimeError):
    """The census could not be resolved."""


@dataclass
class SkipSite:
    """One place in a gated test file that can stop a test from asserting."""

    file: str
    qualname: str
    kind: str
    lineno: int
    col: int
    message: str = ""
    ordinal: int = 1

    @property
    def key(self) -> str:
        """Stable identity for the census.

        Line numbers are deliberately NOT part of it: they churn on every edit
        above the site, which would make the census a merge-conflict generator
        and every unrelated PR a census edit. The enclosing qualname plus the
        kind plus an ordinal within that group survives ordinary edits.

        The ordinal is always present, even for a lone site, so that adding a
        SECOND skip to the same function does not renumber the first — the new
        one is `[2]` and the existing entry keeps its reason. Deleting the first
        of several does renumber; that is the one churn case, and it surfaces as
        a stale entry plus a new one rather than as silent drift.
        """
        return f"{self.file}::{self.qualname}::{self.kind}[{self.ordinal}]"

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key, "file": self.file, "qualname": self.qualname,
            "kind": self.kind, "line": self.lineno, "message": self.message,
        }


# --------------------------------------------------------------------------- #
# Static scan
# --------------------------------------------------------------------------- #
def _classify(expr: str) -> Optional[str]:
    """Map an unparsed call/attribute expression to a skip kind, or None."""
    for candidate in _EXPR_ORDER:
        if expr == candidate or expr.endswith("." + candidate):
            return SKIP_EXPRESSIONS[candidate]
    return None


def _literal_message(node: ast.AST) -> str:
    """Best-effort one-line rendering of the skip's own reason argument.

    Reported, never enforced. It is what `--seed` proposes as a starting reason
    and what makes the JSON report readable; the census reason is a human's.
    """
    if not isinstance(node, ast.Call):
        return ""
    parts: List[str] = []
    for arg in list(node.args) + [kw.value for kw in node.keywords if kw.arg == "reason"]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            parts.append(arg.value)
        elif isinstance(arg, ast.JoinedStr):
            parts.append("".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{...}"
                for v in arg.values
            ))
    return " ".join(p.strip() for p in parts if p.strip())[:200]


class _SiteVisitor(ast.NodeVisitor):
    """Walk a module, carrying the enclosing def/class chain as we go.

    `ast.walk` would be shorter but discards parentage, and the qualname is the
    part of the key that makes it survive an edit. Decorators are visited under
    the name of the thing they decorate, which is where a reader looks for them.
    """

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope: List[str] = []
        self.sites: List[SkipSite] = []
        #: Every `Call.func` node, so a bare `@pytest.mark.skip` decorator is
        #: recorded once as an Attribute while `@pytest.mark.skipif(...)` is
        #: recorded once as a Call rather than twice as both.
        self.call_funcs: set = set()

    def _qual(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def _record(self, node: ast.AST, expr: str, message: str = "") -> None:
        kind = _classify(expr)
        if kind is None:
            return
        self.sites.append(SkipSite(
            file=self.rel, qualname=self._qual(), kind=kind,
            lineno=getattr(node, "lineno", 0), col=getattr(node, "col_offset", 0),
            message=message,
        ))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 - ast API
        self.call_funcs.add(id(node.func))
        try:
            expr = ast.unparse(node.func)
        except Exception:  # pragma: no cover - unparse is total on valid trees
            expr = ""
        self._record(node, expr, _literal_message(node))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802 - ast API
        if id(node) not in self.call_funcs:
            try:
                self._record(node, ast.unparse(node))
            except Exception:  # pragma: no cover
                pass
        self.generic_visit(node)

    def _scoped(self, node) -> None:
        # Decorators belong to the decorated name, not to the enclosing scope.
        self.scope.append(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    visit_FunctionDef = _scoped          # type: ignore[assignment]
    visit_AsyncFunctionDef = _scoped     # type: ignore[assignment]
    visit_ClassDef = _scoped             # type: ignore[assignment]


def scan_source(rel: str, source: str) -> List[SkipSite]:
    """Every skip site in one module, ordinal-numbered and in file order."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A test file that does not parse cannot be censused. It also cannot be
        # collected, so pytest fails it loudly on its own; staying silent here
        # is correct rather than convenient.
        return []
    visitor = _SiteVisitor(rel)
    for node in tree.body:
        visitor.visit(node)

    sites = sorted(visitor.sites, key=lambda s: (s.lineno, s.col))
    counter: Dict[Tuple[str, str], int] = defaultdict(int)
    for site in sites:
        group = (site.qualname, site.kind)
        counter[group] += 1
        site.ordinal = counter[group]
    return sites


def gated_scope(root: Path) -> List[str]:
    """The files this census covers: gated test modules plus their conftests.

    `conftest.py` is not a collectible test module, so `gated_test_list`'s census
    never sees it — but a session-scoped autouse fixture that calls
    `pytest.skip()` there silences an entire directory at once, which is the
    largest and least visible skip in the vocabulary. A conftest is in scope when
    its directory contains at least one gated test file, which keeps the rule
    tied to the allowlist rather than to a second hand-maintained list.
    """
    config = _gtl.load_gate_config(root)
    test_files = _gtl.collect_test_files(root, config)
    gated = _gtl.gated_targets(root, test_files)

    dirs = {f.rsplit("/", 1)[0] for f in gated}
    conftests = [
        rel for rel in _gtl._tracked_files(root)
        if rel.replace("\\", "/").endswith("/conftest.py")
        and rel.replace("\\", "/").rsplit("/", 1)[0] in dirs
    ]
    return sorted(set(gated) | set(conftests))


def scan_tree(root: Optional[Path] = None, files: Optional[Sequence[str]] = None) -> List[SkipSite]:
    """Scan every in-scope file (or just `files`, for the pre-commit fast path)."""
    root = root or repo_root()
    targets = list(files) if files is not None else gated_scope(root)
    sites: List[SkipSite] = []
    for rel in targets:
        path = root / rel
        if not path.is_file():
            continue
        sites.extend(scan_source(rel, path.read_text(encoding="utf-8", errors="replace")))
    return sites


# --------------------------------------------------------------------------- #
# The enumerated census file
# --------------------------------------------------------------------------- #
@dataclass
class CensusEntry:
    key: str
    reason: str
    lineno: int = 0


@dataclass
class CensusFile:
    entries: Dict[str, CensusEntry] = field(default_factory=dict)
    duplicates: List[str] = field(default_factory=list)
    malformed: List[str] = field(default_factory=list)


def parse_census(text: str) -> CensusFile:
    """Read `<key>  # <reason>` lines, keeping the reason (unlike `parse()`).

    `gated_test_list.parse` strips an inline `#` comment because a backlog line
    is a bare path. Here the text after `#` IS the payload — the written reason
    the gate demands — so it needs its own parser rather than a shared one.
    """
    out = CensusFile()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, reason = line.partition("#")
        key = key.strip()
        reason = reason.strip() if sep else ""
        if not key:
            continue
        if not _KEY_RE.match(key):
            out.malformed.append(f"line {lineno}: {line}")
            continue
        if key in out.entries:
            out.duplicates.append(key)
            continue
        out.entries[key] = CensusEntry(key=key, reason=reason, lineno=lineno)
    return out


def load_config(root: Path) -> Dict[str, object]:
    """The `skip_census:` block of args/test_gating_gate.yaml, with defaults.

    It shares that file with the test-gating ratchet on purpose: same policy
    family, same reviewers, one place to look. A separate YAML would be a second
    file to keep in step, and a second file to forget.
    """
    data = _gtl.load_gate_config(root)
    block = data.get(CONFIG_KEY) or {}
    if not isinstance(block, dict):
        raise SkipCensusError(
            f"`{CONFIG_KEY}:` in args/test_gating_gate.yaml did not parse to a mapping"
        )
    return block


def census(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    """Classify every skip site as registered / UNREGISTERED, and check reasons.

    `files` narrows the scan to a caller-supplied subset (the pre-commit fast
    path). The ceiling and stale-entry checks are then skipped, because a subset
    scan cannot tell a deleted site from an unscanned one — reporting "82 entries
    are stale" for a one-file commit would train everyone to ignore this tool.
    """
    root = root or repo_root()

    # No tests/ tree means an installed wheel: args/ is mirrored but the suite is
    # not shipped. Report NOT RUN rather than flagging every census entry as
    # stale — a check that cries wolf gets a `|| true` bolted onto it. Same
    # reasoning, and the same ordering, as gated_test_list.census.
    if not (root / "tests").is_dir():
        return {
            "ran": False,
            "reason": f"no tests/ tree at {root} — skip census NOT RUN",
            "errors": [], "ok": True,
        }

    config = load_config(root)
    census_rel = str(config.get("census_file") or CENSUS_FILE.as_posix())
    min_reason = int(config.get("min_reason_chars", DEFAULT_MIN_REASON_CHARS))

    census_path = root / census_rel
    if not census_path.is_file():
        raise SkipCensusError(
            f"{census_path} is missing — the skip census cannot be resolved. "
            "Run `python tools/ci/skip_census.py --seed` once at adoption"
        )
    registry = parse_census(census_path.read_text(encoding="utf-8"))

    partial = files is not None
    scope = None if partial else gated_scope(root)
    sites = scan_tree(root, files if partial else scope)
    by_key = {s.key: s for s in sites}

    unregistered = sorted(k for k in by_key if k not in registry.entries)
    per_file: Dict[str, int] = defaultdict(int)
    for site in sites:
        per_file[site.file] += 1

    bad_reasons: List[str] = []
    for key in sorted(by_key):
        entry = registry.entries.get(key)
        if entry is None:
            continue
        reason = entry.reason.strip()
        if len(reason) < min_reason:
            bad_reasons.append(
                f"{key} — reason is {len(reason)} chars, needs at least {min_reason}"
            )
        elif reason.lower().rstrip(".!") in PLACEHOLDER_REASONS:
            bad_reasons.append(f"{key} — {reason!r} is a placeholder, not a reason")

    stale = [] if partial else sorted(set(registry.entries) - set(by_key))
    registered = sorted(set(registry.entries) & set(by_key))
    skip_max = int(config.get("skip_max", len(registered)))

    errors: List[str] = []
    if registry.malformed:
        errors.append(
            f"{census_rel} has {len(registry.malformed)} malformed line(s): "
            + "; ".join(registry.malformed[:5])
            + ". Expected `<file>::<qualname>::<kind>[<n>]  # <written reason>`"
        )
    if registry.duplicates:
        # What a careless union merge leaves behind, exactly as in --check.
        errors.append(
            f"{census_rel} registers the same site twice: "
            + ", ".join(sorted(set(registry.duplicates))[:10])
        )
    if unregistered:
        shown = ", ".join(unregistered[:10])
        more = f" (+{len(unregistered) - 10} more)" if len(unregistered) > 10 else ""
        errors.append(
            f"{len(unregistered)} skip(s) in CI-gated test file(s) are registered by "
            f"nothing: {shown}{more}. A skipped test satisfies the coverage claim "
            f"while asserting nothing, so each one needs a written reason. Append a "
            f"line to {census_rel}:\n"
            + "\n".join(
                f"    {k}  # <why this test is allowed not to run>" for k in unregistered[:5]
            )
            + "\nBetter: delete the skip and make the test run. If you register one, "
            "LOWER skip_max only when you remove one — never raise it"
        )
    if bad_reasons:
        errors.append(
            f"{len(bad_reasons)} census entr(ies) carry no usable reason: "
            + "; ".join(bad_reasons[:5])
        )
    if not partial and len(registered) > skip_max:
        errors.append(
            f"the skip census is {len(registered)} registered site(s), above the "
            f"ceiling of {skip_max} — it grew. Lower `skip_census.skip_max` in "
            "args/test_gating_gate.yaml when you delete a skip; never raise it to "
            "get a commit through"
        )

    return {
        "ran": True,
        "partial": partial,
        "scanned_files": len(files) if partial else len(scope or []),
        "total_sites": len(sites),
        "registered": len(registered),
        "unregistered": unregistered,
        "skip_max": skip_max,
        "stale": stale,
        "by_kind": dict(sorted(
            ((k, sum(1 for s in sites if s.kind == k)) for k in {s.kind for s in sites}),
            key=lambda kv: (-kv[1], kv[0]),
        )),
        "per_file": dict(sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))),
        "census_file": census_rel,
        "errors": errors,
        "ok": not errors,
    }


# --------------------------------------------------------------------------- #
# Runtime half — what the gated run ACTUALLY skipped
# --------------------------------------------------------------------------- #
def _rel_from_body(text: str, root: Path) -> str:
    """Pull the test file out of a JUnit `<skipped>` body.

    pytest writes `<abs path>:<line>: <reason>` there. The absolute path is the
    reliable identifier; `classname` is a dotted module path that collapses a
    class-based test into `tests.test_foo.TestBar` and cannot be inverted without
    guessing where the module ends and the class begins.
    """
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    # Windows paths carry a drive letter, so split on the LAST `:` pair that is
    # followed by digits rather than on the first colon.
    match = re.match(r"^(?P<path>.+?\.py):(?P<line>\d+):", first)
    if not match:
        return ""
    candidate = Path(match.group("path"))
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return candidate.as_posix()


def _rel_from_classname(classname: str, root: Path) -> str:
    """Fallback: walk `a.b.C` back to the longest prefix that is a real file."""
    parts = [p for p in (classname or "").split(".") if p]
    while parts:
        candidate = "/".join(parts) + ".py"
        if (root / candidate).is_file():
            return candidate
        parts.pop()
    return ""


def runtime_report(
    xml_paths: Sequence[Path],
    root: Optional[Path] = None,
) -> Dict[str, object]:
    """Count and attribute the skips in a gated pytest run's JUnit XML.

    This is the half the static scan cannot fake its way past. A skip raised from
    a conftest fixture, a plugin, or an alias never appears in the target file's
    source — but it appears here, and a gated file that skipped while declaring
    no site of its own is reported as UNACCOUNTED.

    Attribution is per FILE, not per site, and deliberately so: one site can skip
    many parametrized cases and one case can pass several sites, so a 1:1 map
    between XML entries and AST nodes would be fiction. "This file skipped and
    owns no registered skip" is a claim the data actually supports.
    """
    root = root or repo_root()
    scope = set(gated_scope(root))
    sites_by_file: Dict[str, int] = defaultdict(int)
    for site in scan_tree(root):
        sites_by_file[site.file] += 1

    parsed: List[str] = []
    unreadable: List[str] = []
    per_file: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    total_tests = 0
    total_skipped = 0
    unattributed: List[Dict[str, str]] = []

    for path in xml_paths:
        try:
            tree = ET.parse(path)
        except (OSError, ET.ParseError) as exc:
            unreadable.append(f"{path}: {exc}")
            continue
        parsed.append(str(path))
        for case in tree.iter("testcase"):
            total_tests += 1
            skipped = case.find("skipped")
            if skipped is None:
                continue
            total_skipped += 1
            rel = _rel_from_body(skipped.text or "", root) or _rel_from_classname(
                case.get("classname", ""), root
            )
            record = {
                "test": case.get("name", ""),
                "classname": case.get("classname", ""),
                "type": skipped.get("type", ""),
                "message": (skipped.get("message") or "").strip()[:300],
            }
            if rel:
                per_file[rel].append(record)
            else:
                unattributed.append(record)

    # A file the run skipped in, that is gated, and that declares no skip site of
    # its own. Either something outside the file silenced it, or the static scan
    # could not see how it was spelled. Both are exactly what this half is for.
    unaccounted = sorted(
        rel for rel in per_file
        if rel in scope and sites_by_file.get(rel, 0) == 0
    )

    errors: List[str] = []
    if not parsed:
        errors.append(
            "no JUnit XML could be read ("
            + "; ".join(unreadable[:3])
            + ") — the gated run's skip count is UNKNOWN, which is not the same as zero"
        )
    elif total_tests == 0:
        errors.append(
            f"the JUnit XML at {', '.join(parsed)} holds zero testcases — the run "
            "collected nothing, so a skip count of 0 proves nothing"
        )
    if unaccounted:
        errors.append(
            f"{len(unaccounted)} gated file(s) SKIPPED at runtime while declaring no "
            f"skip site in their own source: {', '.join(unaccounted[:10])}. Something "
            "outside the file silenced them — a conftest fixture, a plugin, or an "
            "alias the static scan cannot see. Find it and register it, or remove it"
        )

    return {
        "ran": bool(parsed),
        "sources": parsed,
        "unreadable": unreadable,
        "total_tests": total_tests,
        "total_skipped": total_skipped,
        "skip_rate": round(total_skipped / total_tests, 4) if total_tests else 0.0,
        "per_file": {
            rel: {"skipped": len(recs), "registered_sites": sites_by_file.get(rel, 0),
                  "gated": rel in scope, "cases": recs}
            for rel, recs in sorted(per_file.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        },
        "unattributed": unattributed,
        "unaccounted": unaccounted,
        "errors": errors,
        "ok": not errors,
    }


# --------------------------------------------------------------------------- #
# Maintenance
# --------------------------------------------------------------------------- #
_SEED_HEADER = """\
# CUI // SP-CTI
# CI SKIP CENSUS (trust-disc-03) — enumerated, never counted
#
# A skipped test satisfies a coverage claim while asserting nothing. Every line
# below is one place inside a CI-GATED test file where a test can decline to run,
# together with the written reason that is acceptable.
#
# FORMAT
#   <file>::<qualname>::<kind>[<ordinal>]  # <written reason>
#
# THE RULES
#   * A skip site that is not on this list FAILS the `test` job by name.
#   * A reason must be a reason. "flaky" and "TBD" are refused.
#   * `skip_census.skip_max` in args/test_gating_gate.yaml is a ceiling on the
#     number of lines here and MAY ONLY GO DOWN. Lower it when you delete a
#     skip; never raise it to get a commit through.
#   * The right fix is almost always to DELETE the skip and make the test run.
#     Registering one is the fallback, and it is a debt you have written down.
#
# Enumerated rather than counted on purpose: a bare count can be held constant
# while the set churns — delete one skip, add another, count unchanged, gate
# green, and the thing the gate exists to notice has happened unobserved.
#
# Gate:   python tools/ci/skip_census.py --check
# Policy: docs/ci/test-gating-policy.md
"""


def _seed_reason(site: SkipSite) -> str:
    """The adoption reason for an existing site, derived from its own message.

    Honest for a bootstrap and no more: the message at the site IS what its
    author wrote down about it. It is not a substitute for a considered reason,
    and the header says so. Every seeded line is a candidate for deletion.
    """
    msg = " ".join((site.message or "").split())
    if len(msg) >= DEFAULT_MIN_REASON_CHARS and msg.lower().rstrip(".!") not in PLACEHOLDER_REASONS:
        return f"seeded at adoption: {msg}"
    return f"seeded at adoption: {site.kind} with no literal reason at the site — review"


def seed(root: Optional[Path] = None, force: bool = False) -> Dict[str, object]:
    """Write the census for the sites that exist today. Adoption only.

    Refuses to overwrite an existing census unless `--force`, so it cannot be
    used to launder a new skip past the gate. The ceiling is the real interlock
    anyway: re-seeding a tree that gained a skip produces a census above
    `skip_max`, and `--check` still fails.
    """
    root = root or repo_root()
    config = load_config(root)
    census_rel = str(config.get("census_file") or CENSUS_FILE.as_posix())
    path = root / census_rel
    if path.exists() and not force:
        raise SkipCensusError(
            f"{path} already exists — seeding again would overwrite written reasons. "
            "Add the one missing line by hand, or pass --force if you truly mean to "
            "rebuild the census from scratch"
        )
    sites = scan_tree(root)
    lines = [f"{s.key}  # {_seed_reason(s)}" for s in sorted(sites, key=lambda s: s.key)]
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" explicitly: the default translates to CRLF on Windows and a
    # trailing CR makes every consumer report a key it cannot match.
    path.write_text(_SEED_HEADER + "\n" + "\n".join(lines) + "\n",
                    encoding="utf-8", newline="\n")
    return {"census_file": census_rel, "seeded": len(lines)}


def prune(root: Optional[Path] = None) -> Dict[str, object]:
    """Delete census lines whose site no longer exists. Only removes lines."""
    root = root or repo_root()
    report = census(root)
    if not report.get("ran"):
        return {"pruned": [], "report": report}
    stale = set(report["stale"])  # type: ignore[arg-type]
    path = root / str(report["census_file"])
    kept = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().partition("#")[0].strip() not in stale
    ]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    return {"pruned": sorted(stale), "report": report}


# --------------------------------------------------------------------------- #
# Pre-commit fast path (mirrors tsg-policy-02)
# --------------------------------------------------------------------------- #
def staged_gated_test_files(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> List[str]:
    """Of the files this commit touches, which are in the skip census's scope.

    Unlike the test-gating fast path this looks at MODIFIED files too, not just
    added ones: adding a `pytest.skip` to an existing gated test is the whole
    failure mode, and it never adds a file.
    """
    root = root or repo_root()
    if files is None:
        staged = _staged_paths(root)
    else:
        staged = [f.replace("\\", "/") for f in files]
    if not staged:
        return []
    scope = set(gated_scope(root))
    return sorted(f for f in staged if f in scope)


def _staged_paths(root: Path) -> List[str]:
    import subprocess  # noqa: PLC0415 - only needed on the git path
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--name-only",
             "--diff-filter=ACMR"],
            capture_output=True, text=True, timeout=60, check=False,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_static(report: Dict[str, object]) -> None:
    if not report.get("ran"):
        print(f"Skip census: {report['reason']}")
        return
    scope = "changed files" if report.get("partial") else "gated test files"
    print(
        f"Skip census: {report['total_sites']} skip site(s) across "
        f"{report['scanned_files']} {scope} — {report['registered']} registered "
        f"(ceiling {report['skip_max']}), {len(report['unregistered'])} unregistered."
    )
    by_kind = report.get("by_kind") or {}
    if by_kind:
        print("  by kind: " + ", ".join(f"{k}={v}" for k, v in by_kind.items()))  # type: ignore[union-attr]
    per_file = report.get("per_file") or {}
    for rel, count in list(per_file.items())[:10]:  # type: ignore[union-attr]
        print(f"    {count:>3}  {rel}")
    if len(per_file) > 10:  # type: ignore[arg-type]
        print(f"    ... {len(per_file) - 10} more file(s)")  # type: ignore[arg-type]
    if report.get("stale"):
        print(
            f"::warning::{len(report['stale'])} census entr(ies) name a site that no "  # type: ignore[arg-type]
            "longer exists — run `python tools/ci/skip_census.py --prune` and lower "
            "skip_census.skip_max"
        )


def _print_runtime(report: Dict[str, object]) -> None:
    if not report.get("ran"):
        print("Runtime skip report: NOT RUN — no JUnit XML could be read")
        return
    print(
        f"Runtime skips in the gated run: {report['total_skipped']} of "
        f"{report['total_tests']} tests ({report['skip_rate']:.2%}) skipped — "
        f"i.e. unmeasured, not passing."
    )
    for rel, info in list((report.get("per_file") or {}).items())[:15]:  # type: ignore[union-attr]
        flag = "" if info["registered_sites"] else "  <-- no registered skip site"
        print(f"    {info['skipped']:>3}  {rel}{flag}")
    if report.get("unattributed"):
        print(
            f"::warning::{len(report['unattributed'])} skipped case(s) could not be "  # type: ignore[arg-type]
            "attributed to a file"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: from __file__)")
    parser.add_argument("--check", action="store_true", help="exit 1 on any defect")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--changed", nargs="+", metavar="PATH",
                        help="scan only these files (the pre-commit fast path)")
    parser.add_argument("--staged", action="store_true",
                        help="scan only the in-scope files this commit touches")
    parser.add_argument("--from-report", nargs="+", type=Path, metavar="XML",
                        help="also report the skips a gated run actually produced")
    parser.add_argument("--seed", action="store_true",
                        help="write the census from the sites present today (adoption only)")
    parser.add_argument("--force", action="store_true", help="with --seed, overwrite")
    parser.add_argument("--prune", action="store_true",
                        help="delete census lines whose site no longer exists")
    args = parser.parse_args(argv)

    # LF on every platform: `print()` emits CRLF on Windows and a stray CR makes
    # a consumer report a key it plainly holds as missing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")  # type: ignore[union-attr]

    try:
        root = args.root.resolve() if args.root else repo_root()

        if args.seed:
            result = seed(root, force=args.force)
            print(json.dumps(result, indent=2) if args.json
                  else f"Seeded {result['seeded']} skip site(s) into {result['census_file']}.")
            return 0

        if args.prune:
            result = prune(root)
            print(json.dumps(result, indent=2) if args.json
                  else f"Pruned {len(result['pruned'])} stale census entries.")
            return 0

        files: Optional[List[str]] = None
        if args.staged:
            files = staged_gated_test_files(root)
            if not files:
                if not args.json:
                    print("Skip census: this commit touches no gated test file.")
                return 0
        elif args.changed is not None:
            files = staged_gated_test_files(root, files=args.changed)
            if not files:
                if not args.json:
                    print("Skip census: none of the named files is a gated test file.")
                return 0

        static = census(root, files)
        runtime = runtime_report(args.from_report, root) if args.from_report else None
    except (SkipCensusError, AllowlistError) as exc:
        print(f"::error::skip census: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"static": static, "runtime": runtime}, indent=2, default=str))
    else:
        _print_static(static)
        if runtime is not None:
            _print_runtime(runtime)

    failed = not static["ok"] or (runtime is not None and not runtime["ok"])
    if failed:
        for err in list(static["errors"]) + list(runtime["errors"] if runtime else []):  # type: ignore[arg-type]
            print(f"::error::skip census: {err}", file=sys.stderr)
    return 1 if (args.check and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
