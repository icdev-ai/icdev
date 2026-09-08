#!/usr/bin/env python3
# CUI // SP-CTI
"""Census of test fixtures that ASSUME a shape ``CREATE TABLE IF NOT EXISTS`` cannot guarantee (tsg-iso-04).

THE DEFECT, shipped as a live CI failure and reproduced deterministically
--------------------------------------------------------------------------
PR #2167 failed CI shard 2 with::

    sqlite3.OperationalError: table dic_documents has no column named status

while the same file passed locally, ALONE, in its own directory, and in file
order. The mechanism::

    an earlier module in the process leaves dic_documents WITHOUT `status`
    -> the fixture's CREATE TABLE IF NOT EXISTS dic_documents (... status ...)
       silently NO-OPS, because IF NOT EXISTS means "if a table by this name
       exists, keep whatever shape it has"
    -> INSERT INTO dic_documents (... status ...) raises

CLAUDE.md already states this rule for PRODUCTION code — "``CREATE TABLE IF NOT
EXISTS`` never alters an existing table, so a table created by an older
migration keeps its old columns while the DDL moves on" — and
``check_insert_schema_parity`` enforces it there. Nothing checked it in TEST
fixtures, where that statement is the standard way to set a table up.

IT IS NOT STABLE DEBT, which is why a census and not a backlog note
-------------------------------------------------------------------
The fixture is correct in isolation and stays correct until some OTHER module
creates the same table first, in the same process, with a narrower shape. So
whether it fails depends on which files share a pytest process — and crx-test-07
bin-packs shards by MEASURED DURATION, so adding two test files anywhere moved
~50 of 442 files between shards. The precondition appears and disappears with
edits that have nothing to do with the test, which is exactly the shape a human
diagnoses as "flaky" and re-runs.

WHAT IS AND IS NOT A FINDING
----------------------------
Not "``CREATE TABLE IF NOT EXISTS`` in a test" — that is the correct way to set
a table up, and banning it would refuse routine work. The finding is the
CONJUNCTION:

    (a) a test DECLARES a table with CREATE TABLE IF NOT EXISTS, naming columns
    (b) the same test INSERTs into that table
    (c) naming at least one column that ONLY that declaration guarantees

(c) is what keeps it high-signal. A test that writes only columns every
definition of the table already has cannot break this way, and is not censused.

A file that reaches the guarantee through ``tests._schema_compat.ensure_table``
is NOT a finding: that helper creates the table and then ALTERs in whatever the
declaration names and the live table lacks — the
``ingest_orchestrator._ensure_schema`` idiom. That check is per FILE, not per
statement, and deliberately coarse: a file that has adopted the helper has
demonstrated it knows the hazard, and tracking which literal reached which call
statically would be a guess dressed as a measurement.

MEASURED BEFORE ARMING (whole tree, 2026-09-07)
-----------------------------------------------
Re-derived by the SHIPPED predicate, never a scratch script: 2,477 test modules
scanned; **273 (file, table) pairs across 194 distinct files — 7.83%** of all
test modules. That is nearly five times the 1.63% this repo already calls
refusing routine work, so a blanket gate is NOT viable and was not built. The
census refuses only a NEW site — which is a commit's own doing, so its fire rate
on routine work is ~0%.

An earlier hand-written survey reported 164 files / 6.62% / 368 pairs. It is
named here because it was wrong in BOTH directions: its regex demanded a
specific quote terminator after the closing paren (missing files), and it
counted one entry per INSERT rather than per (file, table). Quote the number the
gate itself derives.

CENSUS DISCIPLINE (same as args/ci_test_backlog.txt and args/ci_skip_census.txt)
-------------------------------------------------------------------------------
The census ENUMERATES sites by name; it does not count them. A bare count can be
held constant while the set churns — delete one site, add another, count
unchanged, gate green, and the thing the gate exists to notice has happened
unobserved. ``fixture_shape_max`` in ``args/fixture_shape_gate.yaml`` MAY ONLY
GO DOWN.

PER (FILE, TABLE), NOT PER FILE
-------------------------------
The key is ``<file>::<table>``. A per-FILE census would grandfather a module
once and then let it grow the same hazard on a second and third table without a
word. Line numbers are deliberately absent: they churn on every edit above the
site, which would make the census a merge-conflict generator and every unrelated
PR a census edit.

STRINGS, NOT SOURCE TEXT
------------------------
SQL is read from string literals via ``ast``, never by grepping the file. A
commented-out ``# CREATE TABLE IF NOT EXISTS ...`` is not a fixture, and a
census whose first entries were commentary about itself would be discredited on
day one — the same argument ``perfect_score_census`` makes for parsing to an AST.

Gate:  python tools/ci/fixture_shape_census.py --check
       python tools/ci/fixture_shape_census.py --changed <files> --check
       python tools/ci/fixture_shape_census.py --staged --check
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    for parent in [start] + list(start.parents):
        if (parent / "args").is_dir() and (parent / "tools").is_dir():
            return parent
    return start


REPO = _find_repo_root(Path(__file__).resolve().parent)
GATE_FILE = REPO / "args" / "fixture_shape_gate.yaml"

#: A table declaration whose shape IF NOT EXISTS cannot guarantee.
_CREATE_INE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+[\"'`]?(\w+)[\"'`]?\s*\((.*)", re.S | re.I
)
_INSERT = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+[\"'`]?(\w+)[\"'`]?\s*\(([^)]*)\)", re.S | re.I
)

#: Words that open a table CONSTRAINT rather than a column definition.
_CONSTRAINT_WORDS = frozenset(
    {"primary", "foreign", "unique", "check", "constraint", "index", "key"}
)

#: The helper that turns the declaration into a guarantee. A file importing it
#: has demonstrated it knows the hazard.
_GUARANTEE_NAMES = ("ensure_table", "ensure_tables", "_schema_compat")


def _balanced_body(rest: str) -> str:
    """The column body of a CREATE TABLE, up to its matching close paren."""
    depth = 1
    out = []
    for ch in rest:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return "".join(out)


def _split_top_level(body: str) -> list[str]:
    parts, cur, depth = [], "", 0
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def declared_columns(body: str) -> set[str]:
    out: set[str] = set()
    for part in _split_top_level(body):
        part = part.strip()
        if not part:
            continue
        first = part.split()[0].strip("\"`'").lower()
        if first in _CONSTRAINT_WORDS:
            continue
        m = re.match(r"[\"'`]?(\w+)[\"'`]?", part)
        if m:
            out.add(m.group(1).lower())
    return out


def _string_constants(tree: ast.AST) -> list[str]:
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def scan_file(path: Path, repo: Path = REPO) -> list[dict]:
    """Sites in one test module. Never raises on a file it cannot parse."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    if "CREATE TABLE" not in source.upper():
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # An unparseable test is somebody else's finding, not this census's.
        return []

    # A file that reaches the guarantee is not carrying the hazard.
    if any(name in source for name in _GUARANTEE_NAMES):
        return []

    literals = _string_constants(tree)
    declared: dict[str, set[str]] = {}
    for lit in literals:
        for m in _CREATE_INE.finditer(lit):
            table = m.group(1).lower()
            cols = declared_columns(_balanced_body(m.group(2)))
            if cols:
                declared[table] = declared.get(table, set()) | cols
    if not declared:
        return []

    written: dict[str, set[str]] = {}
    for lit in literals:
        for m in _INSERT.finditer(lit):
            table = m.group(1).lower()
            if table not in declared:
                continue
            cols = {
                c.strip().strip("\"`'").lower()
                for c in m.group(2).split(",")
                if c.strip()
            }
            cols = {c for c in cols if re.fullmatch(r"\w+", c)}
            if cols:
                written[table] = written.get(table, set()) | cols

    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        rel = path.name

    sites: list[dict] = []
    for table, wcols in sorted(written.items()):
        risky = sorted(wcols & declared[table])
        if not risky:
            continue
        sites.append(
            {
                "key": f"{rel}::{table}",
                "file": rel,
                "table": table,
                "columns": risky,
            }
        )
    return sites


# ── config ─────────────────────────────────────────────────────────────────
def load_gate(path: Path = GATE_FILE) -> dict:
    try:
        import yaml  # noqa: PLC0415 — pyyaml IS declared
    except ImportError:  # pragma: no cover
        raise SystemExit("fixture_shape_census: pyyaml is required and declared")
    if not path.exists():
        raise SystemExit(f"fixture_shape_census: missing gate config {path}")
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
        "fixture_shape_census", {}
    )


def load_census(repo: Path, cfg: dict) -> set[str]:
    census_path = repo / cfg.get("census_file", "args/fixture_shape_census.txt")
    if not census_path.exists():
        return set()
    entries = set()
    for line in census_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            entries.add(line)
    return entries


def _excluded(rel: str, cfg: dict) -> bool:
    from fnmatch import fnmatch

    for entry in cfg.get("exclude", []) or []:
        if fnmatch(rel, entry.get("path", "")):
            return True
    return False


def collect(repo: Path, cfg: dict, only: list[str] | None = None) -> list[dict]:
    roots = cfg.get("scan_roots", ["tests"])
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
    return sites


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip().endswith(".py")]


# ── report ─────────────────────────────────────────────────────────────────
def build_report(repo: Path = REPO, only: list[str] | None = None) -> dict:
    cfg = load_gate()
    census = load_census(repo, cfg)
    sites = collect(repo, cfg, only)
    keys = [s["key"] for s in sites]

    unregistered = [s for s in sites if s["key"] not in census]
    ceiling = int(cfg.get("fixture_shape_max", 0))

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
    census_path = repo / cfg.get("census_file", "args/fixture_shape_census.txt")
    live = {s["key"] for s in collect(repo, cfg)}
    kept, dropped = [], 0
    for line in census_path.read_text(encoding="utf-8").splitlines():
        bare = line.split("#")[0].strip()
        if bare and bare not in live:
            dropped += 1
            continue
        kept.append(line)
    census_path.write_text(
        "\n".join(kept).rstrip("\n") + "\n", encoding="utf-8", newline="\n"
    )
    return dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on a NEW site")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--changed", nargs="*", help="limit the scan to these files")
    parser.add_argument("--staged", action="store_true", help="scan only staged files")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument(
        "--write-census",
        action="store_true",
        help="ADOPTION ONLY: write today's sites into the census file",
    )
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    repo = Path(args.root).resolve() if args.root else REPO

    if args.prune:
        dropped = prune(repo)
        print(f"Fixture-shape census: pruned {dropped} stale entr(ies).")
        return 0

    if args.write_census:
        cfg = load_gate()
        sites = collect(repo, cfg)
        path = repo / cfg.get("census_file", "args/fixture_shape_census.txt")
        header = path.read_text(encoding="utf-8").split("\n\n")[0] if path.exists() else ""
        body = "\n".join(sorted({s["key"] for s in sites}))
        path.write_text(
            (header + "\n\n" if header else "") + body + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"Fixture-shape census: wrote {len(set(s['key'] for s in sites))} entr(ies).")
        return 0

    only = None
    if args.staged:
        only = _staged_files(repo)
    elif args.changed is not None:
        only = list(args.changed)

    report = build_report(repo, only)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Fixture-shape census ({report['scope']}): {report['sites_seen']} site(s) seen, "
            f"{report['registered']} registered, {len(report['unregistered'])} unregistered "
            f"| census {report['census_size']} (ceiling {report['ceiling']})"
        )
        for site in report["unregistered"][:40]:
            print(
                f"  NEW  {site['file']}  table {site['table']!r} "
                f"writes {', '.join(site['columns'][:5])}"
                + (" ..." if len(site["columns"]) > 5 else "")
            )
        if report.get("over_ceiling"):
            print(
                f"  CEILING BREACHED: census {report['census_size']} > {report['ceiling']}. "
                "fixture_shape_max may only go DOWN."
            )

    if args.check and not report["ok"]:
        print(
            "\nA fixture that declares a table with CREATE TABLE IF NOT EXISTS and then\n"
            "writes a column only that declaration guarantees is relying on a shape it\n"
            "does not control: if any other module in the same pytest process created\n"
            "that table first with a narrower shape, the declaration NO-OPS and the\n"
            "INSERT raises. It passes alone and fails on one shard.\n\n"
            "Fix it with the helper that makes the declaration a guarantee:\n\n"
            "    from tests._schema_compat import ensure_table\n"
            "    ensure_table(conn, '''CREATE TABLE IF NOT EXISTS ...''')\n\n"
            "ensure_table creates the table and then ALTERs in whatever the\n"
            "declaration names and the live table lacks - the\n"
            "ingest_orchestrator._ensure_schema idiom, additive and safe for a table\n"
            "another module is sharing.\n\n"
            "Registering it in args/fixture_shape_census.txt is a debt you have\n"
            "written down, and it breaches the ceiling.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
