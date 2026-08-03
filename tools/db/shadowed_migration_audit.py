#!/usr/bin/env python3
"""Shadowed-migration audit — is a grandfathered collision actually harmless?

CUI // SP-CTI

``args/migration_duplicate_versions.yaml`` freezes the duplicate migration
versions that already existed, so the collision gate is actionable instead of
perpetually red. Freezing them is not the same as clearing them: a shadowed
migration declares schema that may or may not exist by some other route, and
"grandfathered" was never a statement about which.

PR #1199 triaged the set once and found ten real gaps out of 71 — but its own
result table accounts for 57 (38 benign + 9 no-table + 10 gaps). This module is
the re-runnable version of that triage, so the answer is derived by a tool
rather than by a person reading 60 files.

Three things it does that a hand pass does not:

1. **Uses the runner's own view of the world.** ``migration_versions.py`` lists
   every ``NNN_`` entry on disk, which is right for POLICING collisions but
   wrong for predicting them: ``MigrationRunner.discover_migrations`` only sees
   ``.sql`` files and directories holding ``up.sql``/``up.py``. When the entry
   that sorts first is a bare ``NNN_name.py``, the runner never sees it, so the
   "shadowed" sibling is in fact the one that runs. Three of the 60 are this
   case.

2. **Separates "exists" from "exists on a fresh database".** A table present in
   a long-lived database may have been created by a canvas ``init_db.py``, by a
   test fixture, or by hand years ago. The question the audit has to answer is
   whether a database built today would get it, so every object is also traced
   back to a declaring source in the tree.

3. **Checks columns, not just tables.** The nine migrations previously recorded
   as "create no tables — not assessed" are exactly the ALTER-only ones, and an
   ALTER that never ran is a missing column, which fails at runtime the same way
   a missing table does.

CLI::

    python tools/db/shadowed_migration_audit.py --json
    python tools/db/shadowed_migration_audit.py            # human summary
    python tools/db/shadowed_migration_audit.py --gaps     # only the real gaps
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

def _repo_root() -> Path:
    """Repo root, whether this file is read from ``tools/`` or ``icdev/tools/``.

    ``parents[2]`` resolves to ``icdev/`` for the mirrored copy, which silently
    retargets everything at ``icdev/tools/db/migrations`` — a SEPARATE directory
    that has diverged from the real one. ``migration_runner.py`` computes its
    root the same naive way, which is why the mirror is the set of migrations
    an ``import``-based caller actually runs.
    """
    root = Path(__file__).resolve().parents[2]
    if root.name == "icdev" and (root.parent / "tools" / "db" / "migrations").is_dir():
        return root.parent
    return root


_REPO_ROOT = _repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

MIGRATIONS_DIR = _REPO_ROOT / "tools" / "db" / "migrations"
MIRROR_MIGRATIONS_DIR = _REPO_ROOT / "icdev" / "tools" / "db" / "migrations"

#: Where else a table can legitimately come from on a fresh database. Scanned
#: for CREATE TABLE so "the table exists anyway" can be attributed to a source
#: instead of assumed from the presence of a row in information_schema.
_SCHEMA_SOURCE_GLOBS = (
    "tools/**/*.py",
    "tools/**/*.sql",
    "icdev/tools/**/*.py",
    "icdev/tools/**/*.sql",
)

# --------------------------------------------------------------------------- #
# DDL extraction
# --------------------------------------------------------------------------- #
# Deliberately regex over raw text rather than a SQL parser. The corpus mixes
# .sql files, up.py modules building DDL with f-strings, and engine-directive
# blocks; a parser would need the f-strings evaluated. What matters here is the
# NAME of each object, which survives interpolation intact.
#
# The one thing that must NOT be fed to the regexes is prose. Half these
# migrations open with a docstring like "Idempotent: uses CREATE TABLE IF NOT
# EXISTS and CREATE INDEX IF NOT EXISTS", which yields a table named `and`. So
# comments and docstrings are stripped first, and a keyword denylist catches
# whatever survives.

_RE_CREATE_TABLE = re.compile(
    r"\bCREATE\s+(?:TEMP\s+|TEMPORARY\s+|VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"'`\[]?(?:public\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
#: ``CHECK(col IN ('a','b'))`` — the enum-widening migrations change nothing a
#: table/column diff can see, which is why they landed in PR #1199's
#: "create no tables — not assessed" bucket. The allowed-value SET is the
#: schema they carry, so that is what gets compared.
_RE_CHECK_IN = re.compile(
    r"\bCHECK\s*\(\s*[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s+IN\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_RE_QUOTED = re.compile(r"'([^']*)'")
_RE_ALTER_ADD = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"'`\[]?(?:public\.)?([A-Za-z_][A-Za-z0-9_]*)"
    r"[\"'`\]]?\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_RE_CREATE_INDEX = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_RE_CREATE_VIEW = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"'`\[]?(?:public\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

_RE_RENAME_TO = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"'`\[]?(?:public\.)?([A-Za-z_][A-Za-z0-9_]*)"
    r"[\"'`\]]?\s+RENAME\s+TO\s+[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

#: Words that are never an object name. Two sources: SQL keywords picked up when
#: a fragment reads like DDL but is prose, and single-letter loop variables left
#: behind by f-string interpolation.
_NOT_A_NAME = frozenset({
    "if", "not", "exists", "and", "or", "in", "is", "as", "to", "on", "the",
    "table", "column", "constraint", "index", "view", "select", "insert",
    "update", "delete", "with", "using", "statement", "statements", "directly",
    "each", "all", "any", "this", "that", "these", "those", "it", "its",
    "t", "s", "x", "n", "c", "name", "tbl", "col",
    # SQL keywords that land in the column slot when an ALTER is split across
    # two string literals, or when a log line ("added_<table>.<col>") sits next
    # to real DDL in the same module and the regex bridges the gap.
    "alter", "add", "drop", "rename", "check", "default", "null", "primary",
    "foreign", "key", "unique", "references", "set", "type", "values",
    # Placeholders _python_sql_text substitutes for an f-string hole and for
    # the seam between two adjacent string literals.
    "__expr__", "__stmt_break__",
})

_RE_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")
_RE_SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_sql_comments(sql: str) -> str:
    return _RE_SQL_LINE_COMMENT.sub(" ", _RE_SQL_BLOCK_COMMENT.sub(" ", sql))


def _python_sql_text(source: str) -> str:
    """String literals from a .py migration, minus its docstrings.

    A docstring is a bare string expression statement, so ``ast`` distinguishes
    it from an actual SQL literal without heuristics. f-strings come back with
    their interpolations replaced by a placeholder — object NAMES survive
    interpolation intact in this corpus, which is all the audit reads.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _strip_sql_comments(source)

    docstrings = set()
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant) \
                    and isinstance(child.value.value, str):
                docstrings.add(id(child.value))

    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                parts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                else:
                    parts.append(" __expr__ ")
    # Joined with a non-whitespace sentinel rather than a newline. Two adjacent
    # literals are unrelated statements, but every regex here bridges runs of
    # whitespace, so `conn.execute("ALTER TABLE kanban_tasks ADD COLUMN {c}")`
    # followed by `actions.append("added_kanban_tasks.{c}")` reads as one ALTER
    # adding a column literally named `added_kanban_tasks`. The sentinel is not
    # whitespace, so no pattern can span the seam.
    return _strip_sql_comments("\n__stmt_break__\n".join(parts))


def _dynamic_column_names(source: str) -> set[str]:
    """Column names a ``.py`` migration ALTERs in through an f-string loop.

    ``f"ALTER TABLE t ADD COLUMN {col} {defn}"`` driven by
    ``_NEW_COLUMNS = [("is_read", "INTEGER"), ...]`` is the dominant shape in
    this corpus (055, 057, 113 all use it). The name never appears next to the
    ALTER, so a text scan reports the column as missing and the migration as a
    gap — a false positive, and exactly the kind that makes an audit worth less
    than no audit. Harvest the first element of every 2-tuple of strings: it is
    over-inclusive by design, and over-inclusion here only ever moves an entry
    from "gap" to "benign" when the column genuinely exists.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) == 2:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                name = first.value.strip()
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    out.add(name.lower())
        elif isinstance(node, ast.Assign):
            # _COLS = ["a", "b"] — the list-of-plain-strings variant.
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any("COL" in t.upper() for t in targets) and isinstance(
                node.value, (ast.List, ast.Tuple)
            ):
                for e in node.value.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", e.value):
                            out.add(e.value.lower())
    return out


#: ``ALTER TABLE t ADD CONSTRAINT t_col_check`` — the constraint name carries
#: the column, which is what makes the PG rebuild shape readable at all.
_RE_ADD_NAMED_CHECK = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"'`\[]?(?:public\.)?"
    r"([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s+ADD\s+CONSTRAINT\s+"
    r"[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
#: ``CHECK (col = ANY (ARRAY[...]))`` — PostgreSQL's rendering of an enum.
_RE_CHECK_ANY_ARRAY = re.compile(
    r"\bCHECK\s*\(\s*[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s*=\s*ANY\s*\(\s*ARRAY\s*\[",
    re.IGNORECASE,
)


def _dynamic_check_values(source: str, text: str) -> dict[str, dict[str, list[str]]]:
    """Enum values a ``.py`` migration installs from a module-level constant.

    ``247_dashboard_users_role_check`` is the shape this exists for, and it is
    the single most consequential entry in the whole set. It widens
    ``dashboard_users.role`` by interpolating a Python tuple::

        _ROLES = ("admin", ..., "migration_engineer", "ciso")
        role_list = ", ".join(f"'{r}'::text" for r in _ROLES)
        conn.execute(f"... CHECK (role = ANY (ARRAY[{role_list}]))")

    Nothing survives into the SQL text but a placeholder, so the value-level
    regexes see an empty constraint and the entry grades ``needs_review`` —
    an entry that reads as "a human should look" rather than "four roles the
    RBAC matrix hands out cannot be stored". The repo rule that CHECK
    constraints be derived from Python constants rather than hardcoded makes
    this the normal shape, not an exception, so the audit has to read it.

    Attribution is by constraint NAME: ``<table>_<column>_check`` is the
    convention PostgreSQL itself generates and this corpus follows.
    """
    import ast

    if "ADD CONSTRAINT" not in text.upper():
        return {}

    targets: list[tuple[str, str]] = []
    for m in _RE_ADD_NAMED_CHECK.finditer(text):
        table, cname = m.group(1).lower(), m.group(2).lower()
        if not _keep(table):
            continue
        col = None
        if cname.startswith(f"{table}_") and cname.endswith("_check"):
            col = cname[len(table) + 1: -len("_check")]
        if not col:
            am = _RE_CHECK_ANY_ARRAY.search(text[m.end():])
            if am:
                col = am.group(1).lower()
        if col and _keep(col):
            targets.append((table, col))
    if not targets:
        return {}

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    # Only module-level constants: a tuple built inside a function is far more
    # likely to be unrelated data than the vocabulary being installed.
    values: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        # An UPPER_CASE name, with any leading underscores ignored: _ROLES.
        if not any(
            isinstance(t, ast.Name) and t.id.lstrip("_").isupper()
            for t in node.targets
        ):
            continue
        if isinstance(node.value, (ast.Tuple, ast.List)):
            elts = [
                e.value for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if len(elts) >= 2 and all(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", e) for e in elts
            ):
                values.update(v.lower() for v in elts)
    if not values:
        return {}
    return {t: {c: sorted(values)} for t, c in targets}


def _balanced_body(text: str, open_at: int) -> str:
    """Text between the paren at ``open_at`` and its match."""
    depth, i = 0, open_at
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
        i += 1
    return text[open_at + 1:]


def _widening_checks(text: str) -> dict[str, dict[str, list[str]]]:
    """table -> column -> allowed values, for constraints on an EXISTING table.

    Only enum WIDENING counts. A ``CHECK`` inside a ``CREATE TABLE`` for a table
    the migration itself creates says nothing about a gap — if the table is
    missing that is already reported as a missing table, and if it exists then
    whatever created it chose its own vocabulary. What matters is the migration
    whose entire purpose is to enlarge an allowed-value set on a table that is
    already there, because it changes no table and no column and so leaves
    nothing for a schema diff to notice. Two shapes:

      * ``ALTER TABLE t ADD CONSTRAINT ... CHECK (col IN (...))`` — PostgreSQL.
      * the SQLite rebuild: ``CREATE TABLE t_new (... CHECK ...)``, copy, drop,
        ``ALTER TABLE t_new RENAME TO t``. The constraint is written against the
        scratch name but lands on ``t``.
    """
    renames = {m.group(1).lower(): m.group(2).lower() for m in _RE_RENAME_TO.finditer(text)}
    out: dict[str, dict[str, set[str]]] = {}

    def record(table: str, body: str) -> None:
        for m in _RE_CHECK_IN.finditer(body):
            col = m.group(1).lower()
            if not _keep(col):
                continue
            vals = {v for v in _RE_QUOTED.findall(m.group(2)) if v}
            if vals:
                out.setdefault(table, {}).setdefault(col, set()).update(vals)

    # Rebuild idiom: only the scratch table that gets renamed onto a real one.
    for m in _RE_CREATE_TABLE.finditer(text):
        name = m.group(1).lower()
        if name not in renames:
            continue
        paren = text.find("(", m.end())
        if paren != -1:
            record(renames[name], _balanced_body(text, paren))

    # ALTER TABLE t ADD CONSTRAINT ... CHECK (...)
    for m in re.finditer(
        r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"'`\[]?(?:public\.)?"
        r"([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?\s+ADD\s+CONSTRAINT\b",
        text,
        re.IGNORECASE,
    ):
        table = m.group(1).lower()
        if not _keep(table):
            continue
        # The CHECK follows within this statement; bound the scan at the next
        # statement terminator so a later ALTER's values are not attributed here.
        tail = text[m.end():]
        stop = tail.find(";")
        record(table, tail[:stop if stop != -1 else len(tail)])

    return {t: {c: sorted(v) for c, v in cols.items()} for t, cols in out.items()}


def _keep(name: str) -> bool:
    return name.lower() not in _NOT_A_NAME


def extract_objects(text: str, py_source: str = "") -> dict[str, Any]:
    """Pull declared schema objects out of already-decommented migration text."""
    renamed_from = {m.group(1).lower() for m in _RE_RENAME_TO.finditer(text)}
    renamed_to = {m.group(2).lower() for m in _RE_RENAME_TO.finditer(text)}
    tables = sorted({
        m.group(1).lower() for m in _RE_CREATE_TABLE.finditer(text)
        if _keep(m.group(1))
    } - renamed_from)

    dynamic = _dynamic_column_names(py_source) if py_source else set()
    checks = _widening_checks(text)
    for table, cols in (_dynamic_check_values(py_source, text) if py_source else {}).items():
        for col, values in cols.items():
            merged = set(checks.get(table, {}).get(col, [])) | set(values)
            checks.setdefault(table, {})[col] = sorted(merged)
    columns_raw: set[tuple[str, str]] = set()
    unresolved: set[str] = set()
    for m in _RE_ALTER_ADD.finditer(text):
        table, col = m.group(1).lower(), m.group(2).lower()
        if not _keep(table):
            continue
        if _keep(col):
            columns_raw.add((table, col))
        else:
            # An f-string hole: the migration ALTERs this table but the column
            # name is computed. Asserting the harvested candidates as required
            # columns would manufacture gaps out of index names and loop
            # variables — the failure mode that makes an audit worse than none.
            # Record the table as unresolved and let a human close it, with the
            # candidates offered as a starting point.
            unresolved.add(table)
    columns = sorted(columns_raw)
    indexes = sorted({
        m.group(1).lower() for m in _RE_CREATE_INDEX.finditer(text) if _keep(m.group(1))
    })
    views = sorted({
        m.group(1).lower() for m in _RE_CREATE_VIEW.finditer(text) if _keep(m.group(1))
    })
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "views": views,
        # SQLite's table-rebuild idiom (create <t>_new, copy, drop <t>, rename)
        # produces a CREATE TABLE for a name that is not supposed to survive.
        # Recorded so the exclusion above is visible rather than silent.
        "transient_tables": sorted(renamed_from),
        "renamed_to": sorted(renamed_to),
        "widening_checks": checks,
        "unresolved_alter_tables": sorted(unresolved),
        "dynamic_candidates": sorted(dynamic),
    }


def _read(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _python_sql_text(text) if path.suffix == ".py" else _strip_sql_comments(text)


def entry_source_text(path: Path) -> tuple[str, str]:
    """(decommented DDL text, raw python source) for a migration entry."""
    if path.is_file():
        raw = path.read_text(encoding="utf-8", errors="replace")
        return _read(path), (raw if path.suffix == ".py" else "")
    ddl, py = [], []
    for name in ("up.sql", "up.py", "migration.py"):
        f = path / name
        if f.is_file():
            ddl.append(_read(f))
            if f.suffix == ".py":
                py.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(ddl), "\n".join(py)


# --------------------------------------------------------------------------- #
# Which migrations the RUNNER actually shadows
# --------------------------------------------------------------------------- #


def _discover(migrations_dir: Path) -> list[dict[str, Any]]:
    """``MigrationRunner.discover_migrations`` pinned to an explicit directory.

    Pinned because the runner's own default is ``parents[2]`` of wherever its
    module was loaded from, and the ``tools.* -> icdev.tools.*`` shim means an
    ``import`` lands on the mirrored copy — whose migrations directory is not
    the one the gate and the allowlist describe.
    """
    from tools.db.migration_runner import MigrationRunner

    return MigrationRunner(migrations_dir=migrations_dir).discover_migrations()


def _runner_visible_names(migrations_dir: Path | None = None) -> list[str]:
    """Entry names ``MigrationRunner.discover_migrations`` returns, in its order."""
    return [
        (m.get("dir") or m.get("flat_sql")).name
        for m in _discover(migrations_dir or MIGRATIONS_DIR)
    ]


def runner_shadowed(migrations_dir: Path | None = None) -> list[dict[str, str]]:
    """Shadowing as the RUNNER sees it, which is what actually happens.

    ``migration_versions.shadowed_migrations`` walks every ``NNN_`` name on
    disk. That is the right population to police — a colliding name is a
    collision whatever its shape — but it mispredicts the outcome whenever the
    first-sorting entry is one the runner skips (a bare ``NNN_name.py``, or a
    directory with no ``up.sql``/``up.py``). Then the runner's winner is a
    different file, and the entry the gate calls shadowed is the one that runs.
    """
    order: dict[str, list[str]] = {}
    for m in _discover(migrations_dir or MIGRATIONS_DIR):
        version = str(m["version"]).lstrip("0") or "0"
        name = (m.get("dir") or m.get("flat_sql")).name
        order.setdefault(version, []).append(name)

    out = []
    for version, names in sorted(order.items(), key=lambda kv: (len(kv[0]), kv[0])):
        winner, *losers = names
        for loser in losers:
            out.append({"version": version, "applied": winner, "shadowed": loser})
    return out


def runner_invisible(migrations_dir: Path | None = None) -> list[str]:
    """Entries that look like migrations but the runner never discovers."""
    d = migrations_dir or MIGRATIONS_DIR
    visible = set(_runner_visible_names(d))
    return [
        p.name for p in sorted(d.iterdir())
        if re.match(r"^\d+_", p.name) and p.name not in visible
    ]


def mirror_divergence() -> dict[str, Any]:
    """Entries present in one migrations directory but not the other.

    There are two migration trees: ``tools/db/migrations`` (what the gate, the
    allowlist and this audit describe) and ``icdev/tools/db/migrations``. The
    ``tools.* -> icdev.tools.*`` shim sends every ``import``-based caller to the
    second one, and ``MigrationRunner`` derives its directory from its own
    ``__file__`` — so the tree a caller runs depends on how the runner was
    loaded. A migration that exists in only one of them is applied or shadowed
    accordingly, which makes any fix landed in one tree alone a half-fix.
    """
    if not MIRROR_MIGRATIONS_DIR.is_dir():
        return {"mirror_present": False}
    canonical = {p.name for p in MIGRATIONS_DIR.iterdir() if re.match(r"^\d+_", p.name)}
    mirror = {p.name for p in MIRROR_MIGRATIONS_DIR.iterdir() if re.match(r"^\d+_", p.name)}
    return {
        "mirror_present": True,
        "canonical_only": sorted(canonical - mirror),
        "mirror_only": sorted(mirror - canonical),
    }


# --------------------------------------------------------------------------- #
# Where else the schema could come from
# --------------------------------------------------------------------------- #


def dead_entry_prefixes() -> set[str]:
    """Repo-relative path prefixes of migration entries that never execute.

    The reason this exists is the failure mode that makes a source-attribution
    oracle lie. "Some other file declares this table, so the shadowed migration
    is harmless" is only true when that other file RUNS. Two shadowed migrations
    at different versions routinely declare the same table — 189_genesis_phase_log
    and 188_genesis_phase_log.sql are exactly this — and attributing each to the
    other clears both while the table is created by neither.

    So every entry the runner will not execute is disqualified as a source:
    both the shadowed losers and the entries ``discover_migrations`` never sees
    at all. Computed per tree, because the canonical and mirrored migration
    directories shadow different files.
    """
    prefixes: set[str] = set()
    for d, rel in (
        (MIGRATIONS_DIR, "tools/db/migrations"),
        (MIRROR_MIGRATIONS_DIR, "icdev/tools/db/migrations"),
    ):
        if not d.is_dir():
            continue
        dead = {r["shadowed"] for r in runner_shadowed(d)} | set(runner_invisible(d))
        prefixes |= {f"{rel}/{name}" for name in dead}
    return prefixes


def declaring_sources(dead_prefixes: set[str] | None = None) -> dict[str, set[str]]:
    """table name -> repo-relative paths that declare CREATE TABLE for it.

    This is the "fresh database" oracle. A table's presence in a long-lived
    database proves only that something once created it; to say a fresh build
    gets it too, some file in the tree has to still declare it AND that file has
    to be one that runs. ``dead_prefixes`` removes the ones that do not.
    """
    dead = dead_prefixes if dead_prefixes is not None else dead_entry_prefixes()
    out: dict[str, set[str]] = {}
    seen: set[Path] = set()
    for pattern in _SCHEMA_SOURCE_GLOBS:
        for p in _REPO_ROOT.glob(pattern):
            if not p.is_file() or p in seen:
                continue
            seen.add(p)
            rel = p.relative_to(_REPO_ROOT).as_posix()
            # A migration that never runs cannot vouch for anyone else's schema.
            if any(rel.startswith(prefix) for prefix in dead):
                continue
            try:
                if "CREATE TABLE" not in p.read_text(
                    encoding="utf-8", errors="replace"
                ).upper():
                    continue
                text = _read(p)
            except OSError:
                continue
            for m in _RE_CREATE_TABLE.finditer(text):
                name = m.group(1).lower()
                if not _keep(name):
                    continue
                out.setdefault(name, set()).add(rel)
    return out


# --------------------------------------------------------------------------- #
# Live database
# --------------------------------------------------------------------------- #


def fresh_schema(db_path: Path) -> dict[str, Any]:
    """Tables/columns/CHECKs of a database built from scratch, read directly.

    Deliberately a raw ``sqlite3`` connection rather than ``get_connection()``:
    the point of this snapshot is to describe ONE specific file — the database
    the caller just built by running ``init_icdev_db.py`` and then the migration
    chain — and ``get_connection()`` would resolve the ambient environment
    instead, which is how the first run of this audit ended up interrogating an
    empty file it had itself created and calling 39 migrations gaps.

    Build the input with::

        ICDEV_STORAGE_BACKEND=sqlite ICDEV_DB_PATH=<path> \\
            python tools/db/init_icdev_db.py
        python tools/db/migrate.py --up --converge --db-path <path>
    """
    import sqlite3

    if not db_path.is_file():
        return {"available": False, "reason": f"no such database: {db_path}"}
    conn = sqlite3.connect(str(db_path))
    try:
        tables: dict[str, set[str]] = {}
        checks: dict[str, str] = {}
        for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ).fetchall():
            key = str(name).lower()
            checks[key] = str(sql or "")
            tables[key] = {
                str(c[1]).lower()
                for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            }
        return {"available": True, "tables": tables, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    finally:
        conn.close()


def pg_schema(dsn: str) -> dict[str, Any]:
    """Tables/columns/CHECKs of ONE PostgreSQL database, addressed by DSN.

    A DSN rather than the ambient environment, and raw ``psycopg2`` rather than
    ``get_connection()``, for the same reason ``fresh_schema`` takes a path: the
    caller is describing a specific database it just built, not "the" database.

    The ambient route is actively unsafe here. ``ICDEV_DATABASE_URL`` is checked
    BEFORE ``ICDEV_PG_DATABASE`` in ``storage._get_pg_pool``, so exporting
    ``ICDEV_PG_DATABASE=<scratch>`` in an environment that already exports a URL
    silently keeps the old target — the connection succeeds, reports healthy,
    and addresses the wrong database. Building this audit's fresh PostgreSQL
    oracle that way ran three pending migrations against the long-lived database
    instead of the scratch one.

    This is the oracle that matters most. PostgreSQL is the primary backend and
    roughly a third of the chain is PostgreSQL-only DDL, so a SQLite build alone
    cannot distinguish "this migration's schema is missing" from "SQLite could
    never have applied it".
    """
    import psycopg2

    try:
        conn = psycopg2.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        tables: dict[str, set[str]] = {}
        for t, c in cur.fetchall():
            tables.setdefault(str(t).lower(), set()).add(str(c).lower())
        checks: dict[str, str] = {}
        cur.execute(
            "SELECT t.relname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE c.contype = 'c'"
        )
        for tname, cdef in cur.fetchall():
            key = str(tname).lower()
            checks[key] = checks.get(key, "") + " " + str(cdef)
        return {"available": True, "tables": tables, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    finally:
        conn.close()


def live_schema() -> dict[str, Any]:
    """Tables and columns present in the configured backend, or ``None``."""
    try:
        from tools.db.storage import get_connection, is_pg
    except Exception:  # noqa: BLE001
        return {"available": False, "reason": "storage import failed"}

    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    try:
        if is_pg():
            rows = conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            ).fetchall()
        else:
            # pg-portability: sqlite-only path — only reached when the backend
            # is SQLite, which has no information_schema.
            names = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            rows = []
            for n in names:
                for c in conn.execute(f'PRAGMA table_info("{n}")').fetchall():
                    rows.append((n, c[1]))
        tables: dict[str, set[str]] = {}
        for t, c in rows:
            tables.setdefault(str(t).lower(), set()).add(str(c).lower())

        # CHECK definitions, as raw text. The enum-widening migrations change
        # nothing else, so without this they read as "creates no tables" and
        # get waved through — which is how the four GovLift roles stayed
        # un-grantable while two separate migrations to add them sat shadowed.
        checks: dict[str, str] = {}
        try:
            if is_pg():
                for tname, cdef in conn.execute(
                    "SELECT t.relname, pg_get_constraintdef(c.oid) "
                    "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "WHERE c.contype = 'c'"
                ).fetchall():
                    checks[str(tname).lower()] = (
                        checks.get(str(tname).lower(), "") + " " + str(cdef)
                    )
            else:
                # pg-portability: sqlite-only path — CHECK text lives in the
                # stored CREATE TABLE statement, there being no pg_constraint.
                for tname, sql in conn.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='table'"
                ).fetchall():
                    checks[str(tname).lower()] = str(sql or "")
        except Exception:  # noqa: BLE001 — checks are an enrichment, not the core
            checks = {}
        return {"available": True, "tables": tables, "checks": checks}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

#: Verdicts, worst first.
GAP = "gap"                       # declares schema nothing else provides
GAP_LIVE_ONLY = "gap_fresh_only"  # present live, but no source declares it
NEEDS_REVIEW = "needs_review"     # ALTERs a computed column name — read it
BENIGN = "benign"                 # every object provided by another source
NO_SCHEMA = "no_schema"           # data-only / no DDL to lose
NOT_SHADOWED = "not_shadowed"     # gate says shadowed, runner disagrees


def audit(
    fresh_db: Path | None = None,
    fresh_pg_dsn: str | None = None,
) -> dict[str, Any]:
    """Classify every shadowed migration against four independent oracles.

    The question is "would a database built TODAY get this schema", and no
    single oracle answers it:

    * **A freshly built PostgreSQL database** is the strongest, because
      PostgreSQL is the primary backend and the migration chain is its ONLY
      source of schema (``init_icdev_db.py`` refuses to run on PostgreSQL). If
      a fresh PostgreSQL build lacks the object, no new deployment gets it.
    * **A freshly built SQLite database** covers the other supported backend.
      Its absences are weak evidence on their own — roughly a third of the
      chain is PostgreSQL-only DDL that no SQLite build can apply — but its
      PRESENCES are conclusive, and it is the only oracle that sees the
      ~527 tables ``init_icdev_db.py`` creates outside the migration chain.
    * **A declaring source in the tree** catches what neither build does: a
      table created lazily by a canvas ``init_db.py`` at app startup rather
      than by a migration. Restricted to files that actually run — see
      ``dead_entry_prefixes``.
    * **The long-lived database** is the weakest and is never allowed to clear
      an entry on its own: it proves only that something, once, created a table
      — possibly a migration since deleted, a test fixture, or a hand-run DDL.
      It appears in the output as ``live_only``, which is a finding, not a pass.
    """
    from tools.db.migration_versions import shadowed_migrations

    # Every call is pinned to MIGRATIONS_DIR: imported through the shim these
    # helpers default to the icdev/ mirror, which holds a different set of files.
    gate_rows = shadowed_migrations(MIGRATIONS_DIR)
    runner_rows = {(r["version"], r["shadowed"]) for r in runner_shadowed(MIGRATIONS_DIR)}
    invisible = set(runner_invisible(MIGRATIONS_DIR))
    dead_prefixes = dead_entry_prefixes()
    sources = declaring_sources(dead_prefixes)

    sqlite_fresh = (
        fresh_schema(fresh_db) if fresh_db else {"available": False, "reason": "not built"}
    )
    pg_fresh = (
        pg_schema(fresh_pg_dsn) if fresh_pg_dsn
        else {"available": False, "reason": "not built"}
    )

    sqlite_tables: dict[str, set[str]] = (
        sqlite_fresh.get("tables", {}) if sqlite_fresh.get("available") else {}
    )
    fresh_checks: dict[str, str] = (
        sqlite_fresh.get("checks", {}) if sqlite_fresh.get("available") else {}
    )
    pg_tables: dict[str, set[str]] = (
        pg_fresh.get("tables", {}) if pg_fresh.get("available") else {}
    )
    pg_checks: dict[str, str] = (
        pg_fresh.get("checks", {}) if pg_fresh.get("available") else {}
    )

    # "A fresh build gets it" = EITHER backend's fresh build got it. A table is
    # only a gap when no supported backend produces it, so the union is the
    # correct combinator; intersecting would report every PostgreSQL-only table
    # as missing from SQLite and vice versa.
    fresh_tables: dict[str, set[str]] = {t: set(c) for t, c in sqlite_tables.items()}
    for t, cols in pg_tables.items():
        fresh_tables.setdefault(t, set()).update(cols)

    live = live_schema()
    live_tables: dict[str, set[str]] = live.get("tables", {}) if live.get("available") else {}
    live_checks: dict[str, str] = live.get("checks", {}) if live.get("available") else {}

    findings = []
    for row in gate_rows:
        name = row["shadowed"]
        path = MIGRATIONS_DIR / name
        ddl_text, py_source = entry_source_text(path)
        objs = extract_objects(ddl_text, py_source)

        # Does the runner agree this entry is shadowed?
        really_shadowed = (row["version"], name) in runner_rows
        runner_note = None
        if not really_shadowed:
            if name in invisible:
                runner_note = (
                    "runner never discovers this entry at all (bare .py file or "
                    "directory without up.sql/up.py) — dead for a different reason"
                )
            else:
                runner_note = (
                    f"the runner's winner for v{row['version']} differs: the entry "
                    "that sorts first on disk is one the runner skips, so THIS "
                    "entry is the one that runs"
                )

        # Which declared objects would a fresh build actually get?
        def provided(table: str) -> set[str]:
            return sources.get(table, set()) - {_rel(path)}

        missing_tables = [
            t for t in objs["tables"]
            if not provided(t) and t not in fresh_tables
        ]
        # Present in the long-lived database, but nothing that runs declares it
        # and a fresh build does not produce it. The table exists only because
        # of history, so a new deployment is missing it just the same.
        live_only_tables = [
            t for t in objs["tables"]
            if not provided(t) and t not in fresh_tables and t in live_tables
        ]
        missing_tables = [t for t in missing_tables if t not in live_only_tables]

        # A column is accounted for if the fresh build has it, or some live
        # source ALTERs/declares it. Checked against the fresh build first.
        missing_columns = [
            f"{t}.{c}" for t, c in objs["columns"]
            if t in fresh_tables and c not in fresh_tables[t]
            and not (t in live_tables and c in live_tables.get(t, set()))
        ]
        # An ALTER whose base table does not exist anywhere is the base table's
        # problem, not this migration's — record it separately.
        orphan_columns = [
            f"{t}.{c}" for t, c in objs["columns"]
            if t not in fresh_tables and t not in live_tables
        ]

        # Enum values the migration would have permitted that the constraint
        # still rejects. Each backend is asked separately and a value missing
        # from EITHER is a gap on that backend.
        #
        # Taking the first oracle that answers would erase the single most
        # important finding in this audit: 247_dashboard_users_role_check is
        # PostgreSQL-only by construction — SQLite gets the widened vocabulary
        # straight from init_icdev_db.py's CREATE TABLE — so a fresh SQLite
        # build reports it clean while PostgreSQL, the primary backend, still
        # rejects four roles the RBAC matrix hands out.
        missing_check_values: list[str] = []
        for target, cols in objs["widening_checks"].items():
            for backend, table_checks in (
                ("pg", pg_checks or live_checks),
                ("sqlite", fresh_checks),
            ):
                ref = table_checks.get(target)
                if ref is None:
                    continue
                for col, values in cols.items():
                    for v in values:
                        if f"'{v}'" not in ref:
                            missing_check_values.append(
                                f"{target}.{col} = '{v}' [{backend}]"
                            )

        if not really_shadowed:
            verdict = NOT_SHADOWED
        elif missing_tables or missing_columns or missing_check_values:
            verdict = GAP
        elif live_only_tables:
            verdict = GAP_LIVE_ONLY
        elif objs["unresolved_alter_tables"]:
            verdict = NEEDS_REVIEW
        elif not (objs["tables"] or objs["columns"] or objs["widening_checks"]):
            verdict = NO_SCHEMA
        else:
            verdict = BENIGN

        findings.append({
            "version": row["version"],
            "shadowed": name,
            "applied": row["applied"],
            "verdict": verdict,
            "runner_note": runner_note,
            "declares_tables": objs["tables"],
            "declares_columns": [f"{t}.{c}" for t, c in objs["columns"]],
            "declares_check_values": objs["widening_checks"],
            "unresolved_alter_tables": objs["unresolved_alter_tables"],
            "dynamic_candidates": objs["dynamic_candidates"],
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "missing_check_values": sorted(set(missing_check_values)),
            "orphan_columns": orphan_columns,
            "live_only_tables": live_only_tables,
            "in_fresh_build": sorted(t for t in objs["tables"] if t in fresh_tables),
            "provided_by": {
                t: sorted(provided(t))[:3] for t in objs["tables"] if provided(t)
            },
        })

    order = [GAP, GAP_LIVE_ONLY, NEEDS_REVIEW, NOT_SHADOWED, NO_SCHEMA, BENIGN]
    findings.sort(key=lambda f: (order.index(f["verdict"]), int(f["version"])))

    counts: dict[str, int] = {}
    for f in findings:
        counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1

    return {
        "live_db": {"available": live.get("available"), "reason": live.get("reason")},
        "fresh_pg": {
            "available": pg_fresh.get("available"),
            "reason": pg_fresh.get("reason"),
            "tables": len(pg_tables),
        },
        "fresh_sqlite": {
            "available": sqlite_fresh.get("available"),
            "reason": sqlite_fresh.get("reason"),
            "tables": len(sqlite_tables),
        },
        "fresh_db": {
            "available": bool(fresh_tables),
            "reason": None if fresh_tables else "neither backend built",
            "tables": len(fresh_tables),
        },
        "gate_shadowed_count": len(gate_rows),
        "runner_shadowed_count": len(runner_rows),
        "mirror": mirror_divergence(),
        "counts": counts,
        "findings": findings,
    }


def _rel(p: Path) -> str:
    try:
        return p.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--gaps", action="store_true", help="only entries needing action")
    ap.add_argument(
        "--fresh-db",
        type=Path,
        help="path to a SQLite database built from init_icdev_db.py + "
             "migrate.py --up --converge; without it the fresh-build oracle is "
             "skipped and classification rests on source attribution alone",
    )
    ap.add_argument(
        "--fresh-pg-dsn",
        help="DSN of a PostgreSQL database built by migrate.py --up --converge "
             "into an EMPTY database. The strongest oracle: PostgreSQL is the "
             "primary backend and the migration chain is its only schema "
             "source. Pass the DSN explicitly — exporting ICDEV_PG_DATABASE is "
             "silently ignored when ICDEV_DATABASE_URL is set.",
    )
    args = ap.parse_args(argv)

    result = audit(fresh_db=args.fresh_db, fresh_pg_dsn=args.fresh_pg_dsn)
    if args.gaps:
        result["findings"] = [
            f for f in result["findings"]
            if f["verdict"] in (GAP, GAP_LIVE_ONLY, NEEDS_REVIEW, NOT_SHADOWED)
        ]

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    live = result["live_db"]

    def _oracle(label: str, o: dict[str, Any]) -> str:
        if o.get("available"):
            return f"{label}: {o['tables']} tables"
        return f"{label}: NOT BUILT — {o.get('reason')}"

    print(f"live database : {'reachable' if live['available'] else 'UNREACHABLE — ' + str(live['reason'])}")
    print(_oracle("fresh pg      ", result["fresh_pg"]))
    print(_oracle("fresh sqlite  ", result["fresh_sqlite"]))
    print(f"gate says shadowed   : {result['gate_shadowed_count']}")
    print(f"runner truly shadows : {result['runner_shadowed_count']}")
    mirror = result["mirror"]
    if mirror.get("mirror_present"):
        print(f"icdev/ mirror drift  : {len(mirror['canonical_only'])} missing from "
              f"mirror, {len(mirror['mirror_only'])} stale in mirror")
    print()
    for verdict, n in sorted(result["counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:16s} {n}")
    print()
    for f in result["findings"]:
        if f["verdict"] in (BENIGN, NO_SCHEMA) and not args.gaps:
            continue
        print(f"v{f['version']:>3} {f['shadowed']}  [{f['verdict']}]")
        if f["runner_note"]:
            print(f"      note: {f['runner_note']}")
        if f["missing_tables"]:
            print(f"      MISSING TABLES : {', '.join(f['missing_tables'])}")
        if f["missing_columns"]:
            print(f"      MISSING COLUMNS: {', '.join(f['missing_columns'])}")
        if f["missing_check_values"]:
            print(f"      CHECK REJECTS  : {', '.join(f['missing_check_values'])}")
        if f["live_only_tables"]:
            print(f"      LIVE-ONLY      : {', '.join(f['live_only_tables'])}")
        if f["unresolved_alter_tables"]:
            print(f"      COMPUTED ALTER : {', '.join(f['unresolved_alter_tables'])}"
                  f"  candidates={', '.join(f['dynamic_candidates'][:8])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
