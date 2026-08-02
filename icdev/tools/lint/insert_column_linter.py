"""
insert_column_linter.py — ICDEV™ INSERT/schema drift scanner (swp-scan-01)

Parses every *static* ``INSERT INTO <table> (<columns>)`` statement in
``tools/`` and checks the named columns against the **live** database schema
(``information_schema.columns`` on PostgreSQL, ``PRAGMA table_info`` on SQLite).

Why the live schema and not the DDL in the source
-------------------------------------------------
``CREATE TABLE IF NOT EXISTS`` never alters an existing table.  A column added
to a canvas ``db/init_db.py`` long after the table was first created is present
in the source and absent from every database that already had the table — and
the resulting ``UndefinedColumn`` is almost always swallowed by an
``except Exception: pass`` around the write.  The source and the database
disagree silently, so only the database can answer the question.

Extraction
----------
AST-based, not line-based.  Python merges implicitly-concatenated string
literals at parse time, so a column list split across adjacent literals::

    "INSERT INTO audit_trail (id, event_type, actor, action, "
    "details, project_id, created_at) VALUES (...)"

is recovered intact.  f-string interpolations are replaced with a sentinel and
any statement whose *column list* contains one is skipped as non-static (the
table name may still be dynamic — those are skipped too).

Finding kinds
-------------
``column_absent``  Table exists; one or more named columns do not.
``table_absent``   The table itself is not in the live schema.

Exclusions
----------
``migrations/``, ``tests/``, ``__pycache__/``.  Migrations are excluded because
they legitimately reference columns that only exist part-way through the
migration sequence.

Usage
-----
    python tools/lint/insert_column_linter.py --json
    python tools/lint/insert_column_linter.py --kind column_absent --json

``--baseline <file.json>`` (a ``{"accepted": [<finding key>, ...]}`` document) and
``--triage <file.yaml>`` are optional and have no default path; without them
every finding is reported and nothing is annotated.

The regression guard does not use a baseline file.  The accepted set is pinned in
``tests/test_insert_column_schema_parity.py::ACCEPTED_COLUMN_ABSENT``, which
asserts it in both directions so it cannot silently rot.  The triage behind those
entries is in ``docs/database/insert-column-schema-parity.md``.

Exit codes
----------
    0   No findings above baseline (or --gate not passed)
    1   Findings above baseline AND --gate was passed
    2   The live schema could not be read (connection failure)
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# A dynamic ``{...}`` slice of an f-string is replaced by this before matching,
# so a column list containing one is recognisably non-static.
_SENTINEL = "\x00"

_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\s+)?INTO\s+"
    r"([A-Za-z_][\w.]*)\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_SKIP_DIR_PARTS = frozenset({"migrations", "tests", "__pycache__", "node_modules"})


# ---------------------------------------------------------------------------
# Static string extraction
# ---------------------------------------------------------------------------


def _render(node: ast.AST) -> str | None:
    """Render a str-valued expression, marking interpolations with a sentinel."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append(_SENTINEL)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _render(node.left)
        right = _render(node.right)
        return None if left is None or right is None else left + right
    return None


def _sql_strings(tree: ast.AST):
    """Yield ``(lineno, sql)`` for every string literal mentioning INSERT."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            rendered = _render(node)
            if rendered and "INSERT" in rendered.upper():
                yield node.lineno, rendered


def _static_columns(raw: str) -> list[str] | None:
    """Parse a column list, or return None when it is not statically known."""
    if _SENTINEL in raw:
        return None
    columns = []
    for token in raw.split(","):
        token = token.strip().strip('"').strip()
        if not _IDENT_RE.fullmatch(token or ""):
            return None
        columns.append(token.lower())
    return columns or None


# ---------------------------------------------------------------------------
# Live schema
# ---------------------------------------------------------------------------


def load_live_schema() -> dict[str, set[str]]:
    """Return ``{table: {column, ...}}`` for the live database."""
    from tools.db.storage import get_connection

    conn = get_connection()
    # This reads information_schema/sqlite_master only — catalog metadata, never tenant
    # rows — and the scan is worthless unless it sees every table. The RLS predicate
    # references classification/tenant_id columns that system catalogs do not have, so
    # leaving the context attached raises UndefinedColumn on the catalog read.
    # The annotation must sit on the call line itself: check_security_context_wiring
    # skips comment-only lines, so a preceding block does not register.
    conn.set_security_context(None)  # rls-bypass: catalog-metadata read only — swp-scan-01
    cursor = conn.cursor()
    schema: dict[str, set[str]] = {}
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
    if backend.startswith("postgres"):
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        for row in cursor.fetchall():
            record = dict(row)
            table = record["table_name"].lower()
            schema.setdefault(table, set()).add(record["column_name"].lower())
    else:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = [dict(r).get("name") or r[0] for r in cursor.fetchall()]
        for table in tables:
            cursor.execute(f'PRAGMA table_info("{table}")')
            cols = set()
            for row in cursor.fetchall():
                record = dict(row) if not isinstance(row, tuple) else {}
                cols.add(str(record.get("name", row[1] if isinstance(row, tuple) else "")).lower())
            schema[table.lower()] = cols
    return schema


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def iter_source_files(root: Path):
    for path in sorted((root / "tools").rglob("*.py")):
        if set(path.parts) & _SKIP_DIR_PARTS:
            continue
        yield path


def scan(root: Path, schema: dict[str, set[str]]) -> dict:
    findings: list[dict] = []
    seen: set[tuple] = set()
    stats = {"files": 0, "statements": 0, "static": 0, "clean": 0}

    for path in iter_source_files(root):
        stats["files"] += 1
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "INSERT" not in source.upper():
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")

        for lineno, sql in _sql_strings(tree):
            for match in _INSERT_RE.finditer(sql):
                stats["statements"] += 1
                table = match.group(1).lower().split(".")[-1]
                columns = _static_columns(match.group(2))
                if columns is None:
                    continue
                stats["static"] += 1
                line = lineno + sql[: match.start()].count("\n")
                key = (rel, line, table, tuple(columns))
                if key in seen:
                    continue
                seen.add(key)

                if table not in schema:
                    findings.append(
                        {
                            "file": rel,
                            "line": line,
                            "table": table,
                            "kind": "table_absent",
                            "columns": columns,
                            "missing": columns,
                        }
                    )
                    continue
                missing = sorted(set(columns) - schema[table])
                if missing:
                    findings.append(
                        {
                            "file": rel,
                            "line": line,
                            "table": table,
                            "kind": "column_absent",
                            "columns": columns,
                            "missing": missing,
                            "live_columns": sorted(schema[table]),
                        }
                    )
                else:
                    stats["clean"] += 1

    findings.sort(key=lambda f: (f["table"], f["file"], f["line"]))
    return {"stats": stats, "findings": findings}


def finding_key(finding: dict) -> str:
    return "{file}::{table}::{missing}".format(
        file=finding["file"],
        table=finding["table"],
        missing=",".join(finding["missing"]),
    )


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


def apply_triage(findings: list[dict], verdicts_path: Path) -> list[dict]:
    """Join findings against a curated verdicts file.

    ``file_overrides`` win over ``tables`` so that one site writing to a
    different database can be excused without excusing the table everywhere.
    A finding with no verdict is reported as ``unclassified`` rather than
    silently dropped — an unreviewed finding must never read as an accepted one.
    """
    import yaml

    doc = yaml.safe_load(verdicts_path.read_text(encoding="utf-8")) or {}
    overrides = {
        (o["file"], o["table"]): o for o in doc.get("file_overrides") or []
    }
    tables = doc.get("tables") or {}

    out = []
    for finding in findings:
        override = overrides.get((finding["file"], finding["table"]))
        if override:
            verdict, remedy, note = override["verdict"], None, override["reason"]
        else:
            entry = tables.get(finding["table"])
            if entry:
                verdict = entry.get("verdict")
                remedy = entry.get("remedy")
                note = entry.get("note")
            else:
                verdict, remedy, note = "unclassified", None, None
        out.append({**finding, "verdict": verdict, "remedy": remedy, "note": note})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan static INSERTs against the live schema")
    parser.add_argument("--path", default=str(REPO_ROOT), help="Repository root to scan")
    parser.add_argument(
        "--kind",
        choices=["column_absent", "table_absent", "all"],
        default="all",
        help="Restrict output to one finding kind",
    )
    parser.add_argument("--baseline", help="JSON baseline of accepted findings")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 on findings above baseline")
    parser.add_argument(
        "--triage",
        help="Curated verdicts YAML; annotates each finding with verdict/remedy",
    )
    args = parser.parse_args(argv)

    try:
        schema = load_live_schema()
    except Exception as exc:  # noqa: BLE001 - the caller needs the reason
        message = f"Cannot read the live schema: {exc}"
        print(json.dumps({"error": message}) if args.json else message, file=sys.stderr)
        return 2

    result = scan(Path(args.path).resolve(), schema)
    findings = result["findings"]
    if args.kind != "all":
        findings = [f for f in findings if f["kind"] == args.kind]

    accepted: set[str] = set()
    if args.baseline and Path(args.baseline).exists():
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        accepted = set(baseline.get("accepted", []))
    new_findings = [f for f in findings if finding_key(f) not in accepted]

    if args.triage:
        new_findings = apply_triage(new_findings, Path(args.triage))

    if args.json:
        print(
            json.dumps(
                {
                    "stats": result["stats"],
                    "total": len(findings),
                    "above_baseline": len(new_findings),
                    "findings": new_findings,
                },
                indent=2,
            )
        )
    else:
        for finding in new_findings:
            print(
                "{file}:{line}  {table}  {kind}  missing={missing}".format(
                    file=finding["file"],
                    line=finding["line"],
                    table=finding["table"],
                    kind=finding["kind"],
                    missing=",".join(finding["missing"]),
                )
            )
        print(
            "\n{n} finding(s) above baseline "
            "({total} total, {static} static INSERT statements scanned)".format(
                n=len(new_findings),
                total=len(findings),
                static=result["stats"]["static"],
            )
        )

    return 1 if args.gate and new_findings else 0


if __name__ == "__main__":
    sys.exit(main())
