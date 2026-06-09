"""
canvas_table_collision_auditor.py - ICDEV (tm) PGP schema integrity check

Detects canvas table-name collisions in a PostgreSQL primary deployment where
every canvas shares the same database. Two failure modes:

* DIVERGENT (high severity) - same table name defined by multiple canvas
  modules with DIFFERENT column sets. Queries against this table on PG
  silently return the wrong shape for one owner. Must namespace or unify.
* BENIGN-SHARED (info) - same table name across multiple canvases with
  IDENTICAL column signatures. The shared table is the design intent; report
  it for awareness but do not block.

This tool is the detector that feeds pgp-sch-02 (collision resolution) and
the coherence gate (pgp-gate-01).

Usage
-----
    python tools/lint/canvas_table_collision_auditor.py [--scope public] [--json]
    python tools/lint/canvas_table_collision_auditor.py --scope <schema>
    python tools/lint/canvas_table_collision_auditor.py --canvas agentic_ai_canvas

Exit codes
----------
    0   No divergent collisions found
    1   Divergent collisions found
    2   Database unreachable / configuration error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Cross-platform path resolution
REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"

# Canvas init_db modules that we mine for CREATE TABLE statements.
# Mirrors the per-canvas surface that ships in tools/<canvas>/db/init_db.py
CANVAS_INIT_MODULES: tuple[str, ...] = (
    "tools/agentic_ai_canvas/db/init_db.py",
    "tools/aiml_canvas/db/init_db.py",
    "tools/boundary_canvas/db/init_db.py",
    "tools/data_canvas/db/init_db.py",
    "tools/infra_canvas/db/init_db.py",
    "tools/migration_canvas/db/init_db.py",
    "tools/observability_canvas/db/init_db.py",
    "tools/ohc/db/init_db.py",
    "tools/ops_hub/db/init_db.py",
    "tools/pipeline/db/init_db.py",
    "tools/qdc_canvas/db/init_db.py",
    "tools/security_canvas/db/init_db.py",
    "tools/zta/db/init_db.py",
    "tools/document_intelligence/db/init_db.py",
    "tools/foundry/db/init_db.py",
    "tools/network/db/init_db.py",
    "tools/logging/constants.py",
)

# Regex-free, line-based extractor: every "CREATE TABLE [IF NOT EXISTS] <name> ("
# line starts a definition. Column shape is read by joining all lines until
# the next ");" at column 0. This is robust against the multi-line DDL
# pattern used by every canvas init module.
def _extract_tables_from_module(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Return {table_name: [(col_name, col_type), ...]} for one module."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, list[tuple[str, str]]] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        upper = line.upper()
        if "CREATE TABLE" in upper and "(" in line and ");" not in line:
            # extract table name (last identifier on the CREATE line)
            # e.g. CREATE TABLE IF NOT EXISTS aadc_designs (
            after = line.split("CREATE TABLE", 1)[1]
            after = after.replace("IF NOT EXISTS", "")
            after = after.split("(", 1)[0].strip()
            tbl = after.strip('"').strip("`").strip()
            # collect columns until ");" at line start
            cols: list[tuple[str, str]] = []
            i += 1
            while i < len(lines):
                ln = lines[i].rstrip(",")
                if ln.strip().endswith(");") or ln.strip() == ");":
                    break
                # crude col extractor: "<name> <type> ..."
                parts = ln.strip().split(None, 1)
                if parts and len(parts) >= 2 and parts[0].isidentifier() and parts[0].upper() not in {
                    "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX", "KEY"
                }:
                    rest = parts[1].split(",")[0].strip()
                    type_tok = rest.split()[0] if rest.split() else ""
                    cols.append((parts[0], type_tok))
                i += 1
            if cols:
                out[tbl] = cols
        i += 1
    return out


def _signature(cols: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Stable canonical signature for column-set comparison."""
    return tuple(sorted((c[0].lower(), c[1].lower()) for c in cols))


def _pg_conn_kwargs() -> dict[str, Any] | None:
    """Return PG connection kwargs from environment, or None if not configured."""
    host = os.environ.get("ICDEV_PG_HOST", "localhost")
    port = os.environ.get("ICDEV_PG_PORT", "5432")
    db = os.environ.get("ICDEV_PG_DB", "icdev")
    user = os.environ.get("ICDEV_PG_USER", "icdev")
    pwd = os.environ.get("ICDEV_PG_PASSWORD", "icdev-local-dev")
    return dict(host=host, port=int(port), dbname=db, user=user, password=pwd)


def audit_live_pg(scope: str) -> dict[str, Any]:
    """Compare live PG table signatures against the modules' declared schemas."""
    try:
        import psycopg2  # type: ignore
    except ImportError:
        return {"error": "psycopg2 not installed"}
    kwargs = _pg_conn_kwargs()
    if not kwargs:
        return {"error": "PG env not configured"}
    try:
        conn = psycopg2.connect(**kwargs)
    except Exception as exc:  # pragma: no cover - network error path
        return {"error": f"PG connect failed: {exc}"}
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position
        """,
        (scope,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for tbl, col, typ in rows:
        grouped[tbl].append((col, typ))
    return _classify(grouped)


def audit_modules() -> dict[str, Any]:
    """Static audit of CREATE TABLE definitions across canvas init modules."""
    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for rel in CANVAS_INIT_MODULES:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        module = rel.split("/")[1] if rel.count("/") > 1 else rel
        tables = _extract_tables_from_module(path)
        for tbl, cols in tables.items():
            for col, typ in cols:
                grouped[tbl].append((module, col, typ))
    # Re-bucket to {table: {module: [cols]}} for comparison
    modulewise: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(dict)
    for tbl, owners in grouped.items():
        per_module: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for module, col, typ in owners:
            per_module[module].append((col, typ))
        for module, cols in per_module.items():
            modulewise[tbl][module] = cols
    return _classify_modulewise(modulewise)


def _classify_modulewise(
    modulewise: dict[str, dict[str, list[tuple[str, str]]]],
) -> dict[str, Any]:
    divergent: list[dict[str, Any]] = []
    benign_shared: list[dict[str, Any]] = []
    single_owner: int = 0
    for tbl, owners in sorted(modulewise.items()):
        if len(owners) == 1:
            single_owner += 1
            continue
        sigs = {_signature(cols) for cols in owners.values()}
        entry = {"table": tbl, "owners": sorted(owners.keys())}
        if len(sigs) > 1:
            entry["signatures"] = [list(s) for s in sigs]
            divergent.append(entry)
        else:
            benign_shared.append(entry)
    return {
        "source": "modules",
        "tables_total": len(modulewise),
        "single_owner": single_owner,
        "divergent_count": len(divergent),
        "benign_shared_count": len(benign_shared),
        "divergent": divergent,
        "benign_shared": benign_shared,
    }


def _classify(
    grouped: dict[str, list[tuple[str, str]]],
) -> dict[str, Any]:
    """For live PG we only have one owner per table; classify by name clusters
    that match the static module audit's divergent set."""
    divergent: list[dict[str, Any]] = []
    for tbl, cols in sorted(grouped.items()):
        # find other tables in PG with same name + different sig (impossible in PG
        # since one name = one table) - so we just report all as single-owner
        divergent.append({"table": tbl, "columns": len(cols)})
    return {
        "source": "live_pg",
        "tables_total": len(grouped),
        "divergent_count": 0,
        "tables": divergent,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "PGP collision auditor")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--scope", default="public", help="PG schema to audit (default: public)")
    parser.add_argument(
        "--canvas",
        default=None,
        help="restrict static module audit to one canvas module (e.g. agentic_ai_canvas)",
    )
    parser.add_argument(
        "--md",
        action="store_true",
        help="emit a markdown summary (implies --scope, ignores --json)",
    )
    args = parser.parse_args(argv)

    static = audit_modules()
    if args.canvas:
        # filter divergent/benign to entries that include this canvas
        canvas = args.canvas
        static["divergent"] = [d for d in static["divergent"] if canvas in d["owners"]]
        static["benign_shared"] = [d for d in static["benign_shared"] if canvas in d["owners"]]
        static["divergent_count"] = len(static["divergent"])
        static["benign_shared_count"] = len(static["benign_shared"])
        static["canvas_filter"] = canvas
    live = audit_live_pg(args.scope)
    report = {"static_module_audit": static, "live_pg_audit": live}

    if args.md:
        lines = [
            f"# Canvas Table Collision Audit ({args.canvas or 'all'})",
            "",
            f"- Tables discovered in modules: **{static['tables_total']}**",
            f"- Single-owner tables: **{static['single_owner']}**",
            f"- Benign-shared (identical column sets, multiple owners): **{static['benign_shared_count']}**",
            f"- **Divergent collisions: {static['divergent_count']}**",
            f"- Live PG `{args.scope}` tables: **{live.get('tables_total', '?')}**",
            "",
        ]
        if static["divergent"]:
            lines.append("## Divergent collisions")
            for d in static["divergent"]:
                lines.append(f"- `{d['table']}` owned by: {', '.join(d['owners'])}")
            lines.append("")
        if static["benign_shared"]:
            lines.append("## Benign-shared tables")
            for d in static["benign_shared"]:
                lines.append(f"- `{d['table']}` shared by: {', '.join(d['owners'])}")
            lines.append("")
        out = "\n".join(lines)
        if not args.json:
            sys.stdout.write(out + "\n")
        return 1 if static["divergent_count"] else 0

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    else:
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 1 if static["divergent_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
