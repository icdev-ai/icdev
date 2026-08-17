#!/usr/bin/env python3
# CUI // SP-CTI
"""Census of raw ``INSERT INTO kanban_tasks`` writers that bypass task_factory (rem-hyg-05).

WHY THIS EXISTS
---------------
``tools/kanban/task_factory.py`` opens with "Canonical task seeder — never use
raw INSERT directly." That sentence has been in the tree the whole time and
nothing has ever checked it.

Measured 2026-08-16 over ``tools/`` and its ``icdev/tools/`` mirror: 231 raw
board INSERT sites across 209 files — 219 of them once the canonical seeder and
the migrations tree are excluded. Twenty-one of
the bypassers are the AUTONOMOUS path — ``tools/genesis/reflexes/*`` (aadc,
academy, aidp_monitor, coherence_to_kanban, cpmp_monitor, e2e_runner, harness,
integrity_monitor, pma_credential_monitor, pma_int_gap_monitor,
pmo_option_tracker, pmo_weekly_report, qa_agent, reflexion_loop, route_perf,
skill_security_monitor) plus ``tools/ace/controller.py``,
``tools/ace/coworker_thread.py``, ``tools/awareness/suggested_card_writer.py``,
``tools/chat/kanban_bridge.py`` and ``tools/chat/requirement_intake_hook.py``.
Roughly seven board writers in ten do not go through the seeder.

That matters because every guarantee ``create_tasks`` provides is skipped with
it: the ``VALID_TASK_TYPES`` check that PostgreSQL would enforce and SQLite would
not, the ``_assert_real_board`` refusal that stops a seed run landing in a
throwaway worktree database, the gate-id and risk-marker validation in
``tools/kanban/gates.py``, and the dedupe that makes a re-run idempotent. A raw
INSERT gets none of them and reports success.

A gate INSIDE ``create_tasks`` therefore cannot be the whole answer: it only ever
sees the 30% that already call it. This is the other half — the part that stops
the bypassing set from growing.

WHAT THIS DOES **NOT** DO
-------------------------
It migrates nothing. Every writer that exists today is grandfathered by name.
Converting them is rem-hyg-06. This task only closes the door.

CENSUS DISCIPLINE (same as args/ci_test_backlog.txt and args/ci_skip_census.txt)
-------------------------------------------------------------------------------
The census ENUMERATES sites by name. It does not count them. A bare count can be
held constant while the set churns — delete one writer, add another, count
unchanged, gate green, and the thing the gate exists to notice has happened
unobserved. That is precisely how the ungated-test gap regrew behind a green
gate, and identity is the only thing that survives it.

``raw_insert_census.raw_insert_max`` in ``args/board_writer_gate.yaml`` is a
ceiling on the registered count and MAY ONLY GO DOWN. Lower it when rem-hyg-06
converts a writer. Never raise it to get a commit through — raising it is the
visible, reviewable act of deciding to take on more unchecked board writes.

PER SITE, NOT PER FILE
----------------------
The key is ``<file>::<qualname>[<ordinal>]``. A per-FILE census would grandfather
``tools/genesis/reflexes/harness.py`` once and then let it grow a second, third
and fourth raw INSERT without a word. Line numbers are deliberately absent from
the key: they churn on every edit above the site, which would make the census a
merge-conflict generator and every unrelated PR a census edit.

WHAT FAILS
----------
  * a raw-INSERT site in scope that is not in the census                  (NEW)
  * a census entry whose reason is missing, too short, or a placeholder
  * a registered count above ``raw_insert_max`` — the census grew
  * a file the TEXT scan flags that the AST scan cannot attribute to a site

WHAT ONLY WARNS
---------------
  * a census entry whose site no longer exists. Deleting a raw INSERT must never
    fail the PR that deleted it — that PR is rem-hyg-06 doing its job. ``--prune``
    clears them, and the ceiling drops.

KNOWN NON-GOAL
--------------
SQL assembled from a variable table name — ``f"INSERT INTO {table} ..."`` — is
not matched, because the literal never contains ``kanban_tasks``. Chasing that
statically is a losing game. What the tool does instead is refuse to be silently
narrower than a plain text search: ``--check`` fails on a file whose text holds
the pattern while the AST scan finds no site, so a writer that hides behind
indirection surfaces as an error rather than as a clean report.

That gap was MEASURED before it was accepted, not assumed away. Of the 53 files
under ``tools/`` and ``icdev/tools/`` that build an INSERT target with an
interpolation, exactly four also mention ``kanban_tasks`` — and all four are the
two copies of ``tools/db/storage.py`` (``translate_sql`` reassembling a statement
for dialect translation) and the two copies of this checker's own caller,
``tools/workflow/coherence_checker.py``. Not one module writes the board through
an interpolated table name today. The blind spot is real and it is currently
empty; if that changes, it changes as a deliberate act by someone who read this
paragraph.

Usage
-----
    python tools/kanban/raw_insert_census.py --json      # full report
    python tools/kanban/raw_insert_census.py --check     # the gate; exit 1 on a defect
    python tools/kanban/raw_insert_census.py --check --changed tools/foo.py
    python tools/kanban/raw_insert_census.py --staged    # only what this commit touches
    python tools/kanban/raw_insert_census.py --prune     # drop entries whose site is gone
    python tools/kanban/raw_insert_census.py --seed      # write the census (adoption only)
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence


# --------------------------------------------------------------------------- #
# Sibling import — deliberately BY PATH, not `from tools.ci import ...`
# --------------------------------------------------------------------------- #
# Only the file-listing utilities are borrowed (`repo_root`, `_tracked_files`,
# `_matches`); this census has nothing to do with the test allowlist and does not
# share its scope. Loading the sibling FILE guarantees we read the copy in THIS
# checkout — a package import resolves through `sys.path`, which in a worktree
# can land on the shared checkout's `tools/` and census a different tree. The
# relative hop is `<pkg>/kanban/ -> <pkg>/ci/`, which resolves identically from
# `tools/` and from the packaged `icdev/tools/` mirror.
def _load_gated_test_list():
    path = Path(__file__).resolve().parents[1] / "ci" / "gated_test_list.py"
    spec = importlib.util.spec_from_file_location("_icdev_gtl_for_raw_insert", path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves `cls.__module__` through
    # sys.modules, so a by-path load that skips this raises AttributeError on the
    # first decorated class in the loaded file.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_gtl = _load_gated_test_list()
repo_root = _gtl.repo_root
_tracked_files = _gtl._tracked_files
_matches = _gtl._matches

#: Where the enumerated census lives. Flat, line-oriented, one site per line —
#: the shape `merge=union` is safe for (see `.gitattributes`).
CENSUS_FILE = Path("args") / "kanban_raw_insert_census.txt"

#: The policy file. Its own, not test_gating_gate.yaml: different subject
#: (board writes, not test coverage), different reviewers, different ratchet.
GATE_CONFIG = Path("args") / "board_writer_gate.yaml"

#: Policy block key inside args/board_writer_gate.yaml.
CONFIG_KEY = "raw_insert_census"

#: Roots scanned when the config does not say otherwise. The `icdev/tools/`
#: mirror is in scope on purpose: CLAUDE.md directs all NEW code at
#: `icdev.tools.*`, so omitting it would leave the growth path wide open.
DEFAULT_SCAN_ROOTS = ("tools", "icdev/tools")

#: A reason shorter than this is not a reason.
DEFAULT_MIN_REASON_CHARS = 12

#: Reasons that clear the length bar while still saying nothing.
PLACEHOLDER_REASONS = {
    "todo", "tbd", "wip", "n/a", "na", "none", "temporary", "temp", "legacy",
    "see above", "see below", "fixme", "unknown", "placeholder", "later",
    "not sure", "no reason", "needs investigation", "investigate", "historical",
}

#: A raw board INSERT, plus the SQLite/PostgreSQL conflict spellings and an
#: optionally quoted table name. `\s+` rather than a single space because the
#: statement is nearly always a triple-quoted block with a newline in it.
#:
#: The trailing alternation is what separates SQL from PROSE ABOUT SQL, and it
#: had to be added: without it this tool flagged its own caller five times, for
#: docstrings and log messages that merely NAME the pattern
#: (`"best-effort INSERT into kanban_tasks failed (non-blocking)"`). A statement
#: always continues into a column list, VALUES, SELECT, DEFAULT VALUES or an
#: alias; a sentence does not. Measured at AST level over both trees: the
#: unqualified form matches 255 literals, this one 239, and every one of the 16
#: it drops is a log line or a docstring. No real write is lost.
RAW_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\"'`\[]?kanban_tasks[\"'`\]]?\s*"
    r"(?:\(|VALUES\b|SELECT\b|DEFAULT\s+VALUES\b|AS\b)",
    re.IGNORECASE,
)

#: The cheap prefilter. Every form of the statement contains this substring, so a
#: file without it cannot hold a site and never needs parsing.
_PREFILTER = "kanban_tasks"

#: `<file>::<qualname>[<ordinal>]`
_KEY_RE = re.compile(r"^(?P<file>[^:]+)::(?P<qual>.+)\[(?P<n>\d+)\]$")


class RawInsertCensusError(RuntimeError):
    """The census could not be resolved."""


@dataclass
class WriterSite:
    """One string literal in scope that writes ``kanban_tasks`` directly."""

    file: str
    qualname: str
    lineno: int
    col: int
    snippet: str = ""
    ordinal: int = 1

    @property
    def key(self) -> str:
        """Stable identity for the census.

        The ordinal is always present, even for a lone site, so that adding a
        SECOND raw INSERT to the same function does not renumber the first — the
        new one is `[2]` and the existing entry keeps its reason.
        """
        return f"{self.file}::{self.qualname}[{self.ordinal}]"

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key, "file": self.file, "qualname": self.qualname,
            "line": self.lineno, "snippet": self.snippet,
        }


# --------------------------------------------------------------------------- #
# Static scan
# --------------------------------------------------------------------------- #
def _snippet(text: str) -> str:
    """The matched statement, collapsed to one readable line."""
    match = RAW_INSERT_RE.search(text)
    if not match:
        return ""
    return " ".join(text[match.start():match.start() + 120].split())


class _SiteVisitor(ast.NodeVisitor):
    """Walk a module, carrying the enclosing def/class chain as we go.

    `ast.walk` would be shorter but discards parentage, and the qualname is the
    part of the key that makes it survive an edit above the site.
    """

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope: List[str] = []
        self.sites: List[WriterSite] = []

    def _qual(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def _record(self, node: ast.AST, text: str) -> None:
        if not RAW_INSERT_RE.search(text):
            return
        self.sites.append(WriterSite(
            file=self.rel, qualname=self._qual(),
            lineno=getattr(node, "lineno", 0), col=getattr(node, "col_offset", 0),
            snippet=_snippet(text),
        ))

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802 - ast API
        # Adjacent string literals are already folded into one Constant by the
        # parser, so a statement split across several quoted lines is one site.
        if isinstance(node.value, str):
            self._record(node, node.value)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:  # noqa: N802 - ast API
        # An f-string whose LITERAL part carries the table name — the common
        # `f"INSERT INTO kanban_tasks ({cols}) VALUES ..."`.
        #
        # Each interpolation becomes a `?` rather than being dropped. Dropping it
        # would splice the surrounding literals together and hide a statement
        # whose COLUMN LIST is built at runtime: `f"...kanban_tasks({cols})..."`
        # would collapse to `...kanban_tasks()...` — still fine here, but
        # `f"...kanban_tasks {cols} VALUES"` would collapse to `...kanban_tasks
        # VALUES` only by luck. A placeholder keeps the statement's SHAPE, which
        # is what RAW_INSERT_RE now matches on.
        #
        # An interpolated TABLE name still reconstructs as `INSERT INTO ?` and
        # does not match. That is the documented non-goal, and it is deliberate:
        # guessing what a variable holds is how a scanner starts lying.
        literal = "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "?"
            for v in node.values
        )
        self._record(node, literal)
        # Deliberately not descending: the constants inside were just folded in,
        # and visiting them again would double-count the same statement.

    def _scoped(self, node) -> None:
        self.scope.append(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    visit_FunctionDef = _scoped          # type: ignore[assignment]
    visit_AsyncFunctionDef = _scoped     # type: ignore[assignment]
    visit_ClassDef = _scoped             # type: ignore[assignment]


def scan_source(rel: str, source: str) -> List[WriterSite]:
    """Every raw-INSERT site in one module, ordinal-numbered and in file order."""
    if _PREFILTER not in source:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A module that does not parse cannot be censused. It also cannot be
        # imported, so it fails loudly on its own path; `text_only_files` below
        # still reports it, so it does not vanish.
        return []
    visitor = _SiteVisitor(rel)
    for node in tree.body:
        visitor.visit(node)

    sites = sorted(visitor.sites, key=lambda s: (s.lineno, s.col))
    counter: Dict[str, int] = defaultdict(int)
    for site in sites:
        counter[site.qualname] += 1
        site.ordinal = counter[site.qualname]
    return sites


def load_config(root: Optional[Path] = None) -> Dict[str, object]:
    """The ``raw_insert_census:`` block of args/board_writer_gate.yaml."""
    root = root or repo_root()
    path = root / GATE_CONFIG
    if not path.is_file():
        raise RawInsertCensusError(
            f"{path} is missing — the raw-INSERT census policy cannot be resolved"
        )
    try:
        import yaml  # noqa: PLC0415 — lazy so a missing pyyaml is a clear error
    except ImportError as exc:  # pragma: no cover - pyyaml is a core dependency
        raise RawInsertCensusError(
            f"pyyaml is required to read {GATE_CONFIG.as_posix()}: {exc}"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RawInsertCensusError(f"{path} did not parse to a mapping")
    block = data.get(CONFIG_KEY) or {}
    if not isinstance(block, dict):
        raise RawInsertCensusError(
            f"`{CONFIG_KEY}:` in {GATE_CONFIG.as_posix()} did not parse to a mapping"
        )
    return block


def exclusions(config: Dict[str, object]) -> List[Dict[str, str]]:
    """Paths the census deliberately does not cover, each with a written reason.

    Distinct from a census entry: an exclusion says "a raw INSERT here is
    CORRECT", where a census entry says "this one is debt". Conflating them would
    make the ceiling meaningless — the canonical seeder would count against the
    number of writers we are trying to drive down.
    """
    raw = config.get("exclude") or []
    if not isinstance(raw, list):
        raise RawInsertCensusError(
            f"`{CONFIG_KEY}.exclude:` in {GATE_CONFIG.as_posix()} must be a list"
        )
    out: List[Dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise RawInsertCensusError(
                f"every `{CONFIG_KEY}.exclude:` entry needs a `path:` — got {entry!r}"
            )
        reason = str(entry.get("reason") or "").strip()
        if len(reason) < DEFAULT_MIN_REASON_CHARS:
            raise RawInsertCensusError(
                f"exclusion {entry['path']!r} carries no written reason. An exclusion "
                "asserts a raw INSERT is correct there; say why"
            )
        out.append({"path": str(entry["path"]), "reason": reason})
    return out


def filter_scope(
    paths: Sequence[str],
    root: Optional[Path] = None,
    config: Optional[Dict[str, object]] = None,
) -> List[str]:
    """The subset of *paths* the census covers: under a scan root, not excluded.

    Deliberately does NOT consult git. The full sweep starts from the tracked
    file list, but a caller who already knows which files it means — the per-task
    coherence gate, the pre-commit hook — is handed exactly those. Requiring a
    file to be tracked before the diff-scoped gate would look at it means a
    brand-new writer sails through until someone remembers to ``git add`` it, and
    "the gate passed before I staged it" is the sort of result that teaches
    people the gate is noise.
    """
    root = root or repo_root()
    config = config if config is not None else load_config(root)
    roots = tuple(config.get("scan_roots") or DEFAULT_SCAN_ROOTS)
    skip = [e["path"] for e in exclusions(config)]
    out: List[str] = []
    for raw in paths:
        rel = str(raw).replace("\\", "/")
        if not rel.endswith(".py"):
            continue
        if not any(rel == r or rel.startswith(r.rstrip("/") + "/") for r in roots):
            continue
        if any(_matches(rel, pattern) for pattern in skip):
            continue
        out.append(rel)
    return sorted(set(out))


def in_scope(root: Optional[Path] = None, config: Optional[Dict[str, object]] = None) -> List[str]:
    """Every tracked ``.py`` file the census covers — the full-sweep scope.

    git-tracked rather than a filesystem walk, for the same reason
    ``gated_test_list`` uses it: an untracked scratch file in a developer's
    worktree must not make the whole-tree sweep report a defect nobody can act
    on, and the census must describe what a CI checkout will contain. The
    diff-scoped path uses :func:`filter_scope` instead and has no such gap.
    """
    root = root or repo_root()
    config = config if config is not None else load_config(root)
    return filter_scope(_tracked_files(root), root, config)


def scan_tree(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
    config: Optional[Dict[str, object]] = None,
) -> tuple:
    """Scan every in-scope file (or just *files*).

    Returns ``(sites, text_only_files)``. The second element is the cross-check:
    files whose raw TEXT holds the pattern while the AST scan attributed no site.
    That is either SQL built from a variable table name or a module that does not
    parse, and either way the census would otherwise be silently narrower than a
    plain ``grep`` — which is the failure mode this whole family of gates exists
    to prevent.
    """
    root = root or repo_root()
    config = config if config is not None else load_config(root)
    targets = list(files) if files is not None else in_scope(root, config)
    sites: List[WriterSite] = []
    text_only: List[str] = []
    for rel in targets:
        path = root / rel
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if _PREFILTER not in source:
            continue
        found = scan_source(rel, source)
        sites.extend(found)
        if not found and RAW_INSERT_RE.search(source):
            text_only.append(rel)
    return sites, text_only


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
    """Read ``<key>  # <reason>`` lines. The text after ``#`` IS the payload."""
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


def census(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    """Classify every raw-INSERT site as registered / UNREGISTERED.

    *files* narrows the scan to a caller-supplied subset (the per-task gate and
    the pre-commit fast path). The ceiling and stale-entry checks are then
    suppressed, because a subset scan cannot tell a deleted site from an
    unscanned one — reporting "230 entries are stale" for a one-file commit would
    train everyone to ignore this tool, and a tool people ignore is a `|| true`
    with extra steps.
    """
    root = root or repo_root()
    config = load_config(root)
    census_rel = str(config.get("census_file") or CENSUS_FILE.as_posix())
    min_reason = int(config.get("min_reason_chars", DEFAULT_MIN_REASON_CHARS))

    census_path = root / census_rel
    if not census_path.is_file():
        raise RawInsertCensusError(
            f"{census_path} is missing — the raw-INSERT census cannot be resolved. "
            "Run `python tools/kanban/raw_insert_census.py --seed` once at adoption"
        )
    registry = parse_census(census_path.read_text(encoding="utf-8"))

    partial = files is not None
    scope = None if partial else in_scope(root, config)
    sites, text_only = scan_tree(root, files if partial else scope, config)
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
    raw_insert_max = int(config.get("raw_insert_max", len(registered)))

    errors: List[str] = []
    if registry.malformed:
        errors.append(
            f"{census_rel} has {len(registry.malformed)} malformed line(s): "
            + "; ".join(registry.malformed[:5])
            + ". Expected `<file>::<qualname>[<n>]  # <written reason>`"
        )
    if registry.duplicates:
        # What a careless union merge leaves behind.
        errors.append(
            f"{census_rel} registers the same site twice: "
            + ", ".join(sorted(set(registry.duplicates))[:10])
        )
    if unregistered:
        shown = ", ".join(unregistered[:10])
        more = f" (+{len(unregistered) - 10} more)" if len(unregistered) > 10 else ""
        errors.append(
            f"{len(unregistered)} raw `INSERT INTO kanban_tasks` site(s) bypass "
            f"tools/kanban/task_factory.py and are registered by nothing: {shown}{more}. "
            "A raw INSERT skips the task-type validation, the "
            "`refusing to write kanban tasks to a SQLite fallback database` guard, "
            "the gate-id/risk-marker checks and the dedupe — and reports success "
            "anyway. Use `from tools.kanban.task_factory import create_tasks`. "
            "If the write genuinely cannot go through the seeder, append a line to "
            f"{census_rel}:\n"
            + "\n".join(
                f"    {k}  # <why this write cannot go through create_tasks>"
                for k in unregistered[:5]
            )
            + f"\nand LOWER `{CONFIG_KEY}.raw_insert_max` only when you remove one — "
            "never raise it"
        )
    if bad_reasons:
        errors.append(
            f"{len(bad_reasons)} census entr(ies) carry no usable reason: "
            + "; ".join(bad_reasons[:5])
        )
    if text_only:
        errors.append(
            f"{len(text_only)} file(s) hold the raw-INSERT pattern in their TEXT while "
            f"the AST scan attributed no site: {', '.join(text_only[:10])}. Either the "
            "table name is interpolated (`f\"INSERT INTO {table}\"`), which hides the "
            "write from this census, or the module does not parse. Name the table "
            "literally, or route the write through create_tasks"
        )
    if not partial and len(registered) > raw_insert_max:
        errors.append(
            f"the raw-INSERT census is {len(registered)} registered site(s), above the "
            f"ceiling of {raw_insert_max} — it grew. Lower "
            f"`{CONFIG_KEY}.raw_insert_max` in {GATE_CONFIG.as_posix()} when a writer "
            "is converted to create_tasks; never raise it to get a commit through"
        )

    return {
        "ran": True,
        "partial": partial,
        "scanned_files": len(files) if partial else len(scope or []),
        "total_sites": len(sites),
        "total_files": len(per_file),
        "registered": len(registered),
        "unregistered": unregistered,
        "raw_insert_max": raw_insert_max,
        "stale": stale,
        "text_only_files": text_only,
        "per_file": dict(sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))),
        "census_file": census_rel,
        "errors": errors,
        "ok": not errors,
    }


# --------------------------------------------------------------------------- #
# Maintenance
# --------------------------------------------------------------------------- #
_SEED_HEADER = """\
# CUI // SP-CTI
# RAW `INSERT INTO kanban_tasks` CENSUS (rem-hyg-05) — enumerated, never counted
#
# tools/kanban/task_factory.py says "Canonical task seeder — never use raw INSERT
# directly". Every line below is a place that does it anyway. A raw INSERT skips
# the task-type validation, the SQLite-fallback refusal, the gate-id/risk-marker
# checks and the dedupe — and still reports success.
#
# FORMAT
#   <file>::<qualname>[<ordinal>]  # <written reason>
#
# THE RULES
#   * A raw-INSERT site that is not on this list FAILS the coherence gate by name.
#   * A reason must be a reason. "legacy" and "TBD" are refused.
#   * `raw_insert_census.raw_insert_max` in args/board_writer_gate.yaml is a
#     ceiling on the number of lines here and MAY ONLY GO DOWN. Lower it when a
#     writer is converted; never raise it to get a commit through.
#   * The right fix is `from tools.kanban.task_factory import create_tasks`.
#     Registering a site is the fallback, and it is a debt you have written down.
#
# Per SITE, not per file: a per-file census would grandfather a module once and
# then let it grow a second and third raw INSERT without a word.
#
# The `shape:` word in each reason is the migration hint for rem-hyg-06 —
# `reflex` is the autonomous path and the highest-value cohort, `seeder` is a
# one-shot script, `mixed` already calls create_tasks elsewhere in the same file
# (so the import is there and only the call site needs moving), `runtime` is
# everything else.
#
# Gate:  python tools/kanban/raw_insert_census.py --check
#        python tools/workflow/coherence_checker.py --check board_writer_census --gate
"""


def _shape(rel: str, source: str) -> str:
    """Migration hint for rem-hyg-06, derived from the file itself."""
    if "create_tasks" in source:
        return "mixed"
    if "/genesis/reflexes/" in rel:
        return "reflex"
    name = rel.rsplit("/", 1)[-1]
    if name.startswith("seed_") or name.startswith("schedule_") or "/db/seeds/" in rel:
        return "seeder"
    return "runtime"


def _seed_reason(site: WriterSite, shape: str) -> str:
    return (
        f"grandfathered at adoption (rem-hyg-05); shape: {shape}; "
        "convert to task_factory.create_tasks in rem-hyg-06"
    )


def seed(root: Optional[Path] = None, force: bool = False) -> Dict[str, object]:
    """Write the census for the sites that exist today. Adoption only.

    Refuses to overwrite an existing census unless ``--force``, so it cannot be
    used to launder a new writer past the gate. The ceiling is the real interlock
    anyway: re-seeding a tree that gained a writer produces a census above
    ``raw_insert_max``, and ``--check`` still fails.
    """
    root = root or repo_root()
    config = load_config(root)
    census_rel = str(config.get("census_file") or CENSUS_FILE.as_posix())
    path = root / census_rel
    if path.exists() and not force:
        raise RawInsertCensusError(
            f"{path} already exists — seeding again would overwrite written reasons. "
            "Add the one missing line by hand, or pass --force if you truly mean to "
            "rebuild the census from scratch"
        )
    sites, _ = scan_tree(root, config=config)
    shapes: Dict[str, str] = {}
    lines: List[str] = []
    for site in sorted(sites, key=lambda s: s.key):
        if site.file not in shapes:
            shapes[site.file] = _shape(
                site.file, (root / site.file).read_text(encoding="utf-8", errors="replace")
            )
        lines.append(f"{site.key}  # {_seed_reason(site, shapes[site.file])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" explicitly: the default translates to CRLF on Windows and a
    # trailing CR makes every consumer report a key it plainly holds as missing.
    path.write_text(_SEED_HEADER + "\n" + "\n".join(lines) + "\n",
                    encoding="utf-8", newline="\n")
    return {"census_file": census_rel, "seeded": len(lines)}


def prune(root: Optional[Path] = None) -> Dict[str, object]:
    """Delete census lines whose site no longer exists. Only removes lines."""
    root = root or repo_root()
    report = census(root)
    stale = set(report["stale"])  # type: ignore[arg-type]
    path = root / str(report["census_file"])
    kept = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().partition("#")[0].strip() not in stale
    ]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    return {"pruned": sorted(stale), "report": report}


# --------------------------------------------------------------------------- #
# Pre-commit / per-task fast path
# --------------------------------------------------------------------------- #
def staged_scope_files(
    root: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> List[str]:
    """Of the files this commit touches, which are in the census's scope.

    MODIFIED files count, not just added ones: adding a raw INSERT to an existing
    module is the whole failure mode, and it never adds a file.
    """
    root = root or repo_root()
    staged = _staged_paths(root) if files is None else [f.replace("\\", "/") for f in files]
    if not staged:
        return []
    return filter_scope(staged, root)


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
def _print(report: Dict[str, object]) -> None:
    scope = "changed files" if report.get("partial") else "in-scope files"
    print(
        f"Raw-INSERT census: {report['total_sites']} site(s) in "
        f"{report['total_files']} file(s) across {report['scanned_files']} {scope} — "
        f"{report['registered']} registered (ceiling {report['raw_insert_max']}), "
        f"{len(report['unregistered'])} unregistered."
    )
    per_file = report.get("per_file") or {}
    for rel, count in list(per_file.items())[:10]:  # type: ignore[union-attr]
        print(f"    {count:>3}  {rel}")
    if len(per_file) > 10:  # type: ignore[arg-type]
        print(f"    ... {len(per_file) - 10} more file(s)")  # type: ignore[arg-type]
    if report.get("stale"):
        print(
            f"::warning::{len(report['stale'])} census entr(ies) name a site that no "  # type: ignore[arg-type]
            "longer exists — run `python tools/kanban/raw_insert_census.py --prune` "
            f"and LOWER {CONFIG_KEY}.raw_insert_max"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: from __file__)")
    parser.add_argument("--check", action="store_true", help="exit 1 on any defect")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--changed", nargs="+", metavar="PATH",
                        help="scan only these files (the per-task fast path)")
    parser.add_argument("--staged", action="store_true",
                        help="scan only the in-scope files this commit touches")
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
                  else f"Seeded {result['seeded']} raw-INSERT site(s) into {result['census_file']}.")
            return 0

        if args.prune:
            result = prune(root)
            print(json.dumps(result, indent=2) if args.json
                  else f"Pruned {len(result['pruned'])} stale census entries.")
            return 0

        files: Optional[List[str]] = None
        if args.staged:
            files = staged_scope_files(root)
            if not files:
                if not args.json:
                    print("Raw-INSERT census: this commit touches no in-scope file.")
                return 0
        elif args.changed is not None:
            files = staged_scope_files(root, files=args.changed)
            if not files:
                if not args.json:
                    print("Raw-INSERT census: none of the named files is in scope.")
                return 0

        report = census(root, files)
    except RawInsertCensusError as exc:
        print(f"::error::raw-insert census: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print(report)

    if not report["ok"]:
        for err in report["errors"]:  # type: ignore[union-attr]
            print(f"::error::raw-insert census: {err}", file=sys.stderr)
    return 1 if (args.check and not report["ok"]) else 0


if __name__ == "__main__":
    sys.exit(main())
