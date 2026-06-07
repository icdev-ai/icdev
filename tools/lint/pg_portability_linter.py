"""
pg_portability_linter.py — ICDEV™ PostgreSQL-Portability Lint Gate

PostgreSQL is the primary backend. ``tools/db/storage.py::translate_sql`` is a
*thin SQLite init-fallback only* — it exists so init/seed/migrate paths still
work when PG is unreachable at startup. It is **not** meant to be load-bearing
for runtime data access.

This linter scans *runtime* modules under ``tools/`` for PG-unsafe, SQLite-only
SQL that the translator should never have to handle at runtime:

  * ``json_each(...)``                       — SQLite table-valued function
  * ``json_array_length(json_extract(...))`` — nested form, precedence-fragile
  * ``json_extract(...)`` / ``json_array_length(...)`` (standalone) — SQLite JSON dialect
  * ``PRAGMA ...`` inside an executed SQL string — SQLite-only
  * direct ``sqlite3.connect(...)`` for runtime data access (delegates to the
    sibling ``sqlite3_connect_linter`` exemption rules)

It extends ``tools/lint/sqlite3_connect_linter.py`` (reused for the
``sqlite3.connect`` detection) and uses Python's ``ast`` module so that pattern
mentions inside *comments* and *docstrings* are not flagged — only real string
literals (i.e. SQL handed to a cursor) are scanned.

Severity & gating
-----------------
``high``   — json_each, nested json_array_length(json_extract(...)),
             runtime sqlite3.connect. These FAIL the gate.
``medium`` — standalone json_extract / json_array_length, PRAGMA. Reported as
             warnings; never fail the gate.

The linter exits **non-zero** whenever a ``high`` finding appears that is not
recorded in the baseline allowlist (``--baseline``). Existing technical debt is
snapshotted into the baseline with ``--write-baseline`` so CI only blocks on
*new* high-severity offenders.

Exclusions (never scanned)
--------------------------
  * ``init_db.py`` (any canvas), ``tools/db/storage.py`` (the translator itself)
  * ``tools/db/seeds/*``, ``tools/db/migrations/*``, ``tools/db/migrate_*``
  * ``tools/db/schema/*`` and other DB-bootstrap infra
  * ``tools/lint/*`` (linters describe the patterns in strings)
  * test files (``tests/``, ``test_*.py``, ``*_test.py``)

A single line may opt out with the inline comment ``# pg-ok`` (or
``# pg-portability-ok``) — use this only for a genuinely guarded ``is_pg``
SQLite-fallback branch.

Usage
-----
    python tools/lint/pg_portability_linter.py --json
    python tools/lint/pg_portability_linter.py --write-baseline
    python tools/lint/pg_portability_linter.py --files a.py b.py --json

Exit codes
----------
    0   No high-severity findings outside the baseline
    1   High-severity findings outside the baseline (gate fail)
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Robust import of the sibling sqlite3.connect linter (works as script or module)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:  # pragma: no cover - import shim
    from tools.lint import sqlite3_connect_linter as _s3
except Exception:  # pragma: no cover - degrade gracefully if unavailable
    _s3 = None


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------

DEFAULT_BASELINE = "tools/lint/pg_portability_baseline.json"
EXEMPTION_COMMENTS = ("# pg-ok", "# pg-portability-ok")

# Path-suffix exemptions (translator + DB-bootstrap infra that legitimately
# carries SQLite-dialect SQL behind an init/fallback path).
EXEMPT_SUFFIXES: tuple[str, ...] = (
    "tools/db/storage.py",          # the translator itself
    "tools/db/init_icdev_db.py",
    "tools/db/pg_init.py",
    "tools/db/bootstrap_pg.py",
    "tools/db/migration_runner.py",
    "tools/db/backup_manager.py",
)

# Path-fragment exemptions (any normalized path containing one of these).
EXEMPT_FRAGMENTS: tuple[str, ...] = (
    "/tools/db/seeds/",
    "/tools/db/migrations/",
    "/tools/db/schema/",
    "/tools/lint/",
)

EXEMPT_BASENAMES: frozenset[str] = frozenset({"init_db.py"})


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def _relativize(findings: list[dict], root: Path) -> list[dict]:
    """Rewrite each finding's ``file`` to a forward-slash path relative to
    *root* so baselines stay portable across machines and worktrees."""
    root_norm = _normalise(str(root)).rstrip("/") + "/"
    for f in findings:
        fp = f["file"]
        if fp.startswith(root_norm):
            f["file"] = fp[len(root_norm):]
    return findings


def _is_test_path(fp_norm: str, basename: str) -> bool:
    return (
        "/tests/" in fp_norm
        or "/test/" in fp_norm
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or basename == "conftest.py"
    )


def is_excluded(filepath: Path) -> bool:
    """True when *filepath* is init/seed/migrate/schema/lint/test infra."""
    basename = filepath.name
    fp_norm = "/" + _normalise(str(filepath)).lstrip("/")

    if basename in EXEMPT_BASENAMES:
        return True
    if basename.startswith("migrate_") and "/tools/db/" in fp_norm:
        return True
    if _is_test_path(fp_norm, basename):
        return True
    for frag in EXEMPT_FRAGMENTS:
        if frag in fp_norm:
            return True
    for suffix in EXEMPT_SUFFIXES:
        if fp_norm.endswith(suffix):
            return True
    return False


# ---------------------------------------------------------------------------
# Detection rules (operate on string-literal values, not raw source lines)
# ---------------------------------------------------------------------------

_RE_JSON_EACH = re.compile(r"\bjson_each\s*\(", re.IGNORECASE)
# Nested form: json_array_length( json_extract( ... ) ... ) — capture the full
# extent including both closing parens so standalone rules can be suppressed.
_RE_NESTED = re.compile(
    r"\bjson_array_length\s*\(\s*json_extract\s*\([^()]*\)\s*\)", re.IGNORECASE
)
_RE_JSON_EXTRACT = re.compile(r"\bjson_extract\s*\(", re.IGNORECASE)
_RE_JSON_ARRAY_LEN = re.compile(r"\bjson_array_length\s*\(", re.IGNORECASE)
# PRAGMA only when it begins a SQL statement string (avoids prose).
_RE_PRAGMA = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)

_FIX = {
    "json_each": (
        "json_each() is a SQLite table-valued function; use "
        "jsonb_array_elements() behind an is_pg branch, or expand the array in "
        "Python with json.loads()."
    ),
    "json_array_length(json_extract(...))": (
        "Nested json_array_length(json_extract(...)) relies on translate_sql "
        "precedence handling; compute the count in Python via json.loads(), or "
        "branch on is_pg with jsonb_array_length((col::jsonb)->'key')."
    ),
    "json_extract": (
        "json_extract() is SQLite dialect; compute in Python via json.loads() or "
        "use a guarded is_pg jsonb branch (col::jsonb->>'key')."
    ),
    "json_array_length": (
        "json_array_length() is SQLite dialect; compute the count in Python or "
        "use jsonb_array_length((col::jsonb)) behind an is_pg branch."
    ),
    "pragma": (
        "PRAGMA is SQLite-only; for schema introspection query "
        "information_schema.columns, and drop connection-setup pragmas "
        "(translate_sql no-ops them on PG)."
    ),
    "sqlite3.connect": (
        "Use get_connection() from tools.db.storage for runtime data access; "
        "raw sqlite3.connect() bypasses the PostgreSQL-primary backend."
    ),
}


def _line_has_exemption(lines: list[str], lineno: int) -> bool:
    idx = lineno - 1
    if 0 <= idx < len(lines):
        line = lines[idx]
        return any(tok in line for tok in EXEMPTION_COMMENTS)
    return False


def _norm_snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _match_sql_string(
    filepath: str, lineno: int, value: str, lines: list[str]
) -> list[dict]:
    """Return findings for one SQL string literal *value* at *lineno*."""
    findings: list[dict] = []
    if _line_has_exemption(lines, lineno):
        return findings

    def add(pattern: str, severity: str, snippet: str) -> None:
        findings.append(
            {
                "file": filepath,
                "line": lineno,
                "pattern": pattern,
                "severity": severity,
                "fix": _FIX[pattern],
                "snippet": _norm_snippet(snippet)[:200],
            }
        )

    # 1. json_each — high
    for m in _RE_JSON_EACH.finditer(value):
        add("json_each", "high", value[m.start(): m.start() + 60])

    # 2. nested json_array_length(json_extract(...)) — high
    nested_spans: list[tuple[int, int]] = []
    for m in _RE_NESTED.finditer(value):
        nested_spans.append(m.span())
        add("json_array_length(json_extract(...))", "high", m.group(0))

    def _inside_nested(pos: int) -> bool:
        return any(s <= pos < e for s, e in nested_spans)

    # 3. standalone json_extract — medium (skip parts of a nested match)
    for m in _RE_JSON_EXTRACT.finditer(value):
        if _inside_nested(m.start()):
            continue
        add("json_extract", "medium", value[m.start(): m.start() + 60])

    # 4. standalone json_array_length — medium (skip the nested form)
    for m in _RE_JSON_ARRAY_LEN.finditer(value):
        if _inside_nested(m.start()):
            continue
        add("json_array_length", "medium", value[m.start(): m.start() + 60])

    # 5. PRAGMA — medium
    if _RE_PRAGMA.search(value):
        add("pragma", "medium", value[:60])

    return findings


def _collect_docstring_node_ids(tree: ast.AST) -> set[int]:
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                val = body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    doc_ids.add(id(val))
    return doc_ids


def scan_file(filepath: Path) -> list[dict]:
    """Return all findings for *filepath* (JSON/PRAGMA + sqlite3.connect)."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    fp_norm = _normalise(str(filepath))
    lines = source.splitlines()
    findings: list[dict] = []

    # --- AST-based JSON / PRAGMA scan (ignores comments & docstrings) ---------
    try:
        tree = ast.parse(source)
        doc_ids = _collect_docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in doc_ids
            ):
                findings.extend(
                    _match_sql_string(fp_norm, node.lineno, node.value, lines)
                )
    except SyntaxError:
        # Unparseable file — skip the AST scan rather than crash the gate.
        pass

    # --- sqlite3.connect scan (delegate to the sibling linter) ----------------
    if _s3 is not None:
        # Respect the sibling linter's own exemption list too.
        if not _s3._is_exempt(filepath, _ROOT):
            for v in _s3.scan_file(filepath):
                if _line_has_exemption(lines, v["line"]):
                    continue
                findings.append(
                    {
                        "file": fp_norm,
                        "line": v["line"],
                        "pattern": "sqlite3.connect",
                        "severity": "high",
                        "fix": _FIX["sqlite3.connect"],
                        "snippet": _norm_snippet(v["text"])[:200],
                    }
                )

    return findings


def scan_tree(root: Path) -> list[dict]:
    tools_root = root / "tools"
    if not tools_root.is_dir():
        tools_root = root

    all_findings: list[dict] = []
    for py_file in sorted(tools_root.rglob("*.py")):
        if is_excluded(py_file):
            continue
        all_findings.extend(scan_file(py_file))
    return all_findings


# ---------------------------------------------------------------------------
# Baseline allowlist
# ---------------------------------------------------------------------------

def fingerprint(finding: dict) -> str:
    """Line-drift-stable key: file + pattern + normalized snippet."""
    raw = f"{finding['file']}|{finding['pattern']}|{finding['snippet']}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_baseline(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {e["key"] for e in data.get("fingerprints", []) if "key" in e}


def write_baseline(path: Path, findings: list[dict]) -> int:
    """Persist all HIGH findings to the baseline allowlist. Returns count."""
    highs = [f for f in findings if f["severity"] == "high"]
    entries = [
        {
            "key": fingerprint(f),
            "file": f["file"],
            "pattern": f["pattern"],
            "snippet": f["snippet"],
        }
        for f in highs
    ]
    # Stable, de-duplicated ordering for a clean diff.
    seen: set[str] = set()
    uniq: list[dict] = []
    for e in sorted(entries, key=lambda x: (x["file"], x["pattern"], x["snippet"])):
        if e["key"] in seen:
            continue
        seen.add(e["key"])
        uniq.append(e)
    payload = {
        "linter": "pg_portability_linter",
        "version": 1,
        "note": (
            "Snapshot of pre-existing high-severity PG-portability findings. "
            "Only findings NOT in this list fail the gate. Do not add new "
            "entries — fix the offending call site instead."
        ),
        "fingerprints": uniq,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(uniq)


def partition(findings: list[dict], baseline: set[str]) -> tuple[list[dict], list[dict]]:
    """Split findings into (new_high, baselined_high). Mediums excluded."""
    new_high: list[dict] = []
    baselined: list[dict] = []
    for f in findings:
        if f["severity"] != "high":
            continue
        if fingerprint(f) in baseline:
            baselined.append(f)
        else:
            new_high.append(f)
    return new_high, baselined


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Lint runtime tools/ for PG-unsafe SQLite-dialect SQL "
            "(json_each, nested json_array_length(json_extract(...)), PRAGMA, "
            "runtime sqlite3.connect)."
        )
    )
    p.add_argument("--path", default=".", help="Project root to scan (default: cwd).")
    p.add_argument(
        "--files",
        nargs="+",
        metavar="FILE",
        help="Scan only these files (incremental pre-commit/CI mode).",
    )
    p.add_argument(
        "--baseline",
        default=None,
        help=f"Baseline allowlist path (default: {DEFAULT_BASELINE} if present).",
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="Snapshot current HIGH findings to the baseline path and exit 0.",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit machine-readable JSON.",
    )
    p.add_argument(
        "--gate",
        action="store_true",
        help="(Compat) explicit gate flag. High findings above baseline always "
        "exit non-zero regardless.",
    )
    return p


def _gather(args, root: Path) -> list[dict]:
    if args.files:
        findings: list[dict] = []
        for f in args.files:
            fp = Path(f)
            if not fp.is_absolute():
                fp = root / fp
            if fp.suffix != ".py":
                continue
            if is_excluded(fp):
                continue
            if "tools/" not in _normalise(str(fp)) + "/":
                continue
            findings.extend(scan_file(fp))
        return findings
    return scan_tree(root)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.path).resolve()

    baseline_path = Path(args.baseline) if args.baseline else (root / DEFAULT_BASELINE)

    findings = _relativize(_gather(args, root), root)

    if args.write_baseline:
        n = write_baseline(baseline_path, findings)
        msg = f"pg_portability_linter: wrote {n} high-severity finding(s) to {_normalise(str(baseline_path))}"
        if args.json_output:
            print(json.dumps({"status": "BASELINE_WRITTEN", "count": n,
                              "baseline": _normalise(str(baseline_path))}, indent=2))
        else:
            print(msg)
        return 0

    baseline = load_baseline(baseline_path)
    new_high, baselined = partition(findings, baseline)
    mediums = [f for f in findings if f["severity"] == "medium"]

    status = "FAIL" if new_high else "PASS"

    if args.json_output:
        result = {
            "linter": "pg_portability_linter",
            "root": _normalise(str(root)),
            "baseline": _normalise(str(baseline_path)),
            "status": status,
            "total_findings": len(findings),
            "new_high_count": len(new_high),
            "baselined_high_count": len(baselined),
            "medium_count": len(mediums),
            "new_high": new_high,
            "medium": mediums,
        }
        print(json.dumps(result, indent=2))
    else:
        if new_high:
            print(
                f"pg_portability_linter: {len(new_high)} NEW high-severity "
                f"finding(s) above baseline ({len(baselined)} baselined, "
                f"{len(mediums)} medium warnings).\n"
            )
            for f in new_high:
                print(f"  [HIGH] {f['file']}:{f['line']}  {f['pattern']}")
                print(f"         {f['snippet']}")
                print(f"         fix: {f['fix']}")
            print()
        else:
            print(
                f"pg_portability_linter: PASS — no new high-severity findings "
                f"({len(baselined)} baselined, {len(mediums)} medium warnings)."
            )

    return 1 if new_high else 0


if __name__ == "__main__":
    sys.exit(main())
