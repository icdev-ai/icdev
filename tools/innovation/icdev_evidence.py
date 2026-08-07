#!/usr/bin/env python3
# CUI // SP-CTI
"""What ICDEV itself holds for a given subsystem tag (xbm-cmp-01).

The scout reports external findings tagged with a ``subsystem``. A finding
reported in isolation — "SigNoz shipped X" — is not actionable; the same finding
next to ICDEV's own observability inventory is. This module is the ICDEV half of
that comparison: given a subsystem tag, how many modules back it, how many rows
its tables hold, and whether it is live or merely built.

It is the scripted form of docs/research/canvas-engine-sweep.md, whose closing
note asks for exactly this ("the sweep is a script, not a manual audit, so it can
be re-run as the scorecard's evidence collector"), and it reproduces the module
counts quoted in docs/research/external-benchmark-map.md §1–§10.

The subsystem -> modules/tables map lives in args/xbm_subsystem_inventory.yaml.
Re-pointing a subsystem is a config change; this file is not edited for it.

Two measurement rules carried over from the sweep, both load-bearing:

1. **An unmeasured subsystem is not an empty one.** If the database cannot be
   reached, status is ``unmeasured``, never ``inert``. The sweep's own stated
   limitation was that a table-discovery miss looked identical to a component
   with no data; reporting zero rows for a DB that was never opened repeats it.

2. **Tables are matched against the catalog, then counted.** The catalog is read
   once and patterns are matched in Python. Probing each candidate table with a
   COUNT and catching the error is the shape that, on PostgreSQL, makes every
   table after the first failure look absent — one aborted transaction poisons
   the rest of the loop.

Usage:
    python tools/innovation/icdev_evidence.py --subsystem observability --json
    python tools/innovation/icdev_evidence.py --all --json
    python tools/innovation/icdev_evidence.py --audit      # unmatched patterns
"""

import argparse
import fnmatch
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg

# Checkout layout first, packaged layout second — same convention as
# seed_external_repos.py, whose icdev/ copy sits beside icdev/data/args/.
INVENTORY_CANDIDATES = (
    BASE_DIR / "args" / "xbm_subsystem_inventory.yaml",
    BASE_DIR / "data" / "args" / "xbm_subsystem_inventory.yaml",
)

# Liveness verdicts. `unmeasured` is not a failure state — it is the honest
# answer when the DB was unreachable, and it is what keeps a cold worktree from
# reporting the whole platform inert.
STATUS_LIVE = "live"              # tables located, rows > 0
STATUS_INERT = "inert"            # tables located, all empty — built but unfed
STATUS_CODE_ONLY = "code_only"    # modules exist, no tables of its own
STATUS_ABSENT = "absent"          # no modules and no tables
STATUS_UNMEASURED = "unmeasured"  # DB unreachable; liveness not determined

# Table names are read from the DB catalog, so they are already trusted; this is
# belt-and-braces before they go into a quoted identifier.
_SAFE_TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InventoryError(RuntimeError):
    """The subsystem inventory could not be read.

    Raised rather than returning ``{}``: an evidence collector that silently
    finds nothing reports the entire platform absent and looks like a result.
    """


def inventory_path(candidates=INVENTORY_CANDIDATES) -> Path:
    for path in candidates:
        if path.exists():
            return path
    raise InventoryError(
        "no subsystem inventory found; looked for: " + ", ".join(str(p) for p in candidates)
    )


def load_inventory(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """The subsystem -> {modules, tables} map, keyed by subsystem tag."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a hard dependency
        raise InventoryError(f"pyyaml is required to read the inventory: {exc}") from exc

    path = path or inventory_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InventoryError(f"{path} is not valid YAML: {exc}") from exc

    subsystems = data.get("subsystems")
    if not isinstance(subsystems, dict) or not subsystems:
        raise InventoryError(f"{path} declares no subsystems")
    return subsystems


def count_modules(patterns: List[str], base_dir: Path = BASE_DIR) -> Tuple[int, Dict[str, int]]:
    """Files matching each glob, repo-root relative.

    Counted per pattern as well as in total so a directory that has been moved
    shows up as one zero rather than a quietly smaller total.
    """
    per_pattern: Dict[str, int] = {}
    seen: set = set()
    for pattern in patterns or []:
        matched = [p for p in base_dir.glob(pattern) if p.is_file()]
        per_pattern[pattern] = len(matched)
        seen.update(p.resolve() for p in matched)
    return len(seen), per_pattern


def catalog_tables(conn) -> List[str]:
    """Every table name visible to this connection, read in one query."""
    if is_pg(conn):
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    else:
        sql = "SELECT name AS table_name FROM sqlite_master WHERE type = 'table'"
    return [dict(row)["table_name"] for row in conn.execute(sql).fetchall()]


def match_tables(patterns: List[str], catalog: List[str]) -> List[str]:
    """Catalog names matching any glob, de-duplicated and ordered.

    Patterns are allowed to overlap — `llm_*` and `llm_judge_evaluations` both
    matching the same table counts its rows once.
    """
    matched = {name for pattern in patterns or [] for name in fnmatch.filter(catalog, pattern)}
    return sorted(matched)


def count_rows(conn, tables: List[str]) -> Tuple[int, Dict[str, int]]:
    """COUNT(*) per table. Tables come from the catalog, so all of them exist."""
    per_table: Dict[str, int] = {}
    for table in tables:
        if not _SAFE_TABLE.match(table):  # pragma: no cover - catalog names are identifiers
            continue
        row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()
        per_table[table] = int(dict(row)["n"])
    return sum(per_table.values()), per_table


def _verdict(*, module_count: int, declares_tables: bool, tables_found: int,
             row_count: int, db_measured: bool) -> str:
    if not declares_tables:
        # No tables declared, so the DB cannot change the answer — this stays
        # decidable on a machine with no database at all.
        return STATUS_CODE_ONLY if module_count else STATUS_ABSENT
    if not db_measured:
        return STATUS_UNMEASURED
    if row_count > 0:
        return STATUS_LIVE
    if tables_found:
        return STATUS_INERT
    return STATUS_CODE_ONLY if module_count else STATUS_ABSENT


def gather_subsystem_evidence(
    subsystem: str,
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
    catalog: Optional[List[str]] = None,
    db_error: Optional[str] = None,
) -> Dict[str, Any]:
    """ICDEV's own evidence for one subsystem tag.

    Args:
        subsystem: A tag from args/xbm_subsystem_inventory.yaml — the same
            vocabulary context/genesis/competitors.yaml tags its targets with.
        inventory: Pre-loaded inventory. Defaults to the shipped file.
        base_dir: Repo root the module globs resolve against.
        conn: An open connection to measure. Defaults to opening (and closing)
            one via get_connection(). Pass one to batch a whole-platform sweep
            onto a single connection.
        catalog: Pre-read table list, to avoid re-reading it per subsystem.
        db_error: The database is already known to be unreachable. Set by
            gather_all so a sweep whose connection failed reports unmeasured
            rather than having each subsystem quietly open a connection of its
            own — which measures a different database than the caller asked for.

    Returns:
        module_count, table_row_count and status, plus the per-pattern and
        per-table breakdown behind them and, when the DB could not be opened,
        a db_error saying so.

    Raises:
        KeyError: the tag is not in the inventory. A finding tagged with a
            subsystem nobody declared must not silently compare against zero.
    """
    inventory = load_inventory() if inventory is None else inventory
    if subsystem not in inventory:
        raise KeyError(
            f"subsystem {subsystem!r} is not in the inventory; "
            f"known: {', '.join(sorted(inventory))}"
        )

    spec = inventory[subsystem] or {}
    module_patterns = spec.get("modules") or []
    table_patterns = spec.get("tables") or []

    module_count, modules = count_modules(module_patterns, base_dir)

    row_count = 0
    tables: Dict[str, int] = {}
    db_measured = False
    owns_conn = False

    if table_patterns and not db_error:
        try:
            if conn is None:
                conn = get_connection()
                owns_conn = True
            if catalog is None:
                catalog = catalog_tables(conn)
            row_count, tables = count_rows(conn, match_tables(table_patterns, catalog))
            db_measured = True
        except Exception as exc:
            db_error = str(exc)
        finally:
            if owns_conn and conn is not None:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - close-time failures are noise
                    pass

    result: Dict[str, Any] = {
        "subsystem": subsystem,
        "title": spec.get("title", subsystem),
        "benchmark_section": spec.get("section"),
        "module_count": module_count,
        "modules": modules,
        "table_count": len(tables),
        "table_row_count": row_count,
        "tables": tables,
        "status": _verdict(
            module_count=module_count,
            declares_tables=bool(table_patterns),
            tables_found=len(tables),
            row_count=row_count,
            db_measured=db_measured,
        ),
        "db_measured": db_measured,
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    if spec.get("note"):
        result["note"] = spec["note"]
    if db_error:
        result["db_error"] = db_error
    return result


def gather_all(
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
) -> Dict[str, Any]:
    """Every declared subsystem, over a single connection and one catalog read."""
    inventory = load_inventory() if inventory is None else inventory

    catalog = None
    db_error = None
    owns_conn = False
    try:
        if conn is None:
            conn = get_connection()
            owns_conn = True
        catalog = catalog_tables(conn)
    except Exception as exc:
        db_error = str(exc)

    try:
        subsystems = {
            tag: gather_subsystem_evidence(
                tag, inventory=inventory, base_dir=base_dir, conn=conn,
                catalog=catalog, db_error=db_error,
            )
            for tag in sorted(inventory)
        }
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass

    counts: Dict[str, int] = {}
    for ev in subsystems.values():
        counts[ev["status"]] = counts.get(ev["status"], 0) + 1

    result = {
        "subsystems": subsystems,
        "total_subsystems": len(subsystems),
        "status_counts": counts,
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    if db_error:
        result["db_error"] = db_error
    return result


def audit_inventory(
    *,
    inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    base_dir: Path = BASE_DIR,
    conn=None,
) -> Dict[str, Any]:
    """Patterns that match nothing — the inventory's own rot detector.

    A module glob pointing at a moved directory, or a table glob pointing at a
    renamed table, silently shrinks a subsystem's evidence to zero and reads as
    a real finding. This lists both so the map can be repaired deliberately.
    """
    inventory = load_inventory() if inventory is None else inventory

    catalog: List[str] = []
    db_error = None
    owns_conn = False
    try:
        if conn is None:
            conn = get_connection()
            owns_conn = True
        catalog = catalog_tables(conn)
    except Exception as exc:
        db_error = str(exc)
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass

    unmatched_modules: Dict[str, List[str]] = {}
    unmatched_tables: Dict[str, List[str]] = {}
    for tag, spec in sorted(inventory.items()):
        spec = spec or {}
        _, per_pattern = count_modules(spec.get("modules") or [], base_dir)
        dead = [pattern for pattern, n in per_pattern.items() if n == 0]
        if dead:
            unmatched_modules[tag] = dead
        if catalog:
            dead_tables = [p for p in (spec.get("tables") or []) if not fnmatch.filter(catalog, p)]
            if dead_tables:
                unmatched_tables[tag] = dead_tables

    result = {
        "unmatched_module_patterns": unmatched_modules,
        "unmatched_table_patterns": unmatched_tables,
        "tables_in_catalog": len(catalog),
        "inventory": str(inventory_path()),
    }
    if db_error:
        result["db_error"] = db_error
        result["table_audit_skipped"] = True
    return result


def _render(result: Dict[str, Any]) -> None:
    if "subsystems" in result:
        for ev in result["subsystems"].values():
            print(
                f"  [{ev['status']:<10}] {ev['subsystem']:<22}"
                f" modules={ev['module_count']:<4} tables={ev['table_count']:<4}"
                f" rows={ev['table_row_count']}"
            )
        print(f"\n{result['total_subsystems']} subsystems: " +
              ", ".join(f"{k}={v}" for k, v in sorted(result["status_counts"].items())))
    elif "unmatched_module_patterns" in result:
        for tag, patterns in result["unmatched_module_patterns"].items():
            print(f"  modules {tag}: {', '.join(patterns)}")
        for tag, patterns in result.get("unmatched_table_patterns", {}).items():
            print(f"  tables  {tag}: {', '.join(patterns)}")
        if not result["unmatched_module_patterns"] and not result.get("unmatched_table_patterns"):
            print("  every pattern matches something")
    else:
        print(f"  [{result['status']}] {result['subsystem']} — {result['title']}")
        print(f"    modules: {result['module_count']}")
        print(f"    tables:  {result['table_count']} holding {result['table_row_count']} rows")
    if result.get("db_error"):
        print(f"\n  DB not measured: {result['db_error']}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ICDEV's own evidence, per subsystem tag")
    parser.add_argument("--subsystem", help="Subsystem tag to gather evidence for")
    parser.add_argument("--all", action="store_true", help="Every declared subsystem")
    parser.add_argument("--audit", action="store_true", help="List patterns matching nothing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    try:
        if args.audit:
            result = audit_inventory()
        elif args.all:
            result = gather_all()
        elif args.subsystem:
            result = gather_subsystem_evidence(args.subsystem)
        else:
            parser.error("use --subsystem <tag>, --all, or --audit")
    except (InventoryError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(json.dumps({"error": message}) if args.json else f"ERROR: {message}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _render(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
