# CUI // SP-CTI
"""Lazy pillar-engine table inventory for the Security Design Canvas (SDC).

Roughly 50 ``zig_*`` (and a few shared) tables are created lazily at first use
by per-module ``_ensure_tables(conn)`` functions across
``tools/security_canvas/*.py`` (pillar engines, managers, orchestrators). The
architecture decision (shx-db-03) is to KEEP the lazy pattern but make it
auditable: this generator scans the canvas source tree and emits a
module -> tables mapping so the set of lazily-created tables is documented and
cannot silently drift.

It powers ``docs/features/sdc-lazy-table-inventory.md`` (regenerate with
``python tools/security_canvas/db/lazy_table_inventory.py --markdown``) and is
consumed by ``tests/test_sdc_lazy_tables.py`` for a freshness assertion.

Usage:
    python tools/security_canvas/db/lazy_table_inventory.py --json
    python tools/security_canvas/db/lazy_table_inventory.py --markdown

Pure, deterministic, offline — no LLM, no DB connection, no I/O beyond reading
the canvas source files.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Directory holding the security_canvas modules (this file lives in db/).
CANVAS_DIR = Path(__file__).resolve().parent.parent

# A module "declares lazy tables" iff it defines an ``_ensure_tables`` function.
_ENSURE_RE = re.compile(r"^def _ensure_tables\b", re.MULTILINE)

# Matches ``CREATE TABLE IF NOT EXISTS <name>`` (case-insensitive), capturing the
# table name. Quoted identifiers are tolerated.
_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+['\"`]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

REGEN_CMD = "python tools/security_canvas/db/lazy_table_inventory.py --markdown"


def build_inventory(canvas_dir: Path | None = None) -> dict[str, list[str]]:
    """Scan canvas modules and return a {module_filename: [sorted table names]} map.

    Only modules that define ``_ensure_tables`` are included. Table names are
    de-duplicated and sorted for stable output. The mapping is ordered by
    module filename so serialized output is deterministic.
    """
    root = canvas_dir or CANVAS_DIR
    inventory: dict[str, list[str]] = {}
    for path in sorted(root.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _ENSURE_RE.search(source):
            continue
        tables = sorted({m.group(1) for m in _CREATE_RE.finditer(source)})
        if tables:
            inventory[path.name] = tables
    return inventory


def summarize(inventory: dict[str, list[str]]) -> dict[str, int]:
    """Return {module_count, table_count (distinct), table_declarations}."""
    distinct: set[str] = set()
    declarations = 0
    for tables in inventory.values():
        distinct.update(tables)
        declarations += len(tables)
    return {
        "module_count": len(inventory),
        "distinct_table_count": len(distinct),
        "table_declarations": declarations,
    }


def render_json(inventory: dict[str, list[str]]) -> str:
    payload = {
        "generated_by": "tools/security_canvas/db/lazy_table_inventory.py",
        "summary": summarize(inventory),
        "modules": inventory,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_markdown(inventory: dict[str, list[str]]) -> str:
    """Render the checked-in feature doc. Deterministic — used for freshness test."""
    summary = summarize(inventory)
    lines: list[str] = []
    lines.append("<!-- CUI // SP-CTI -->")
    lines.append("# SDC Lazy Pillar-Engine Table Inventory")
    lines.append("")
    lines.append(
        "> **GENERATED FILE -- do not edit by hand.** "
        f"Regenerate with `{REGEN_CMD}`."
    )
    lines.append("")
    lines.append(
        "The Security Design Canvas (SDC) ZIG pillar engines, managers, and "
        "orchestrators create their backing tables lazily via per-module "
        "`_ensure_tables(conn)` functions (idempotent "
        "`CREATE TABLE IF NOT EXISTS`). Decision (shx-db-03): keep the lazy "
        "pattern but make it auditable. This inventory is the audit surface -- "
        "`tests/test_sdc_lazy_tables.py` fails if it drifts from source, and "
        "asserts each `_ensure_tables` is idempotent."
    )
    lines.append("")
    lines.append(
        f"- **Modules with lazy tables:** {summary['module_count']}"
    )
    lines.append(
        f"- **Distinct tables:** {summary['distinct_table_count']}"
    )
    lines.append(
        f"- **Table declarations (incl. duplicates across modules):** "
        f"{summary['table_declarations']}"
    )
    lines.append("")
    lines.append("| Module | Tables |")
    lines.append("|--------|--------|")
    for module, tables in inventory.items():
        rendered = ", ".join(f"`{t}`" for t in tables)
        lines.append(f"| `{module}` | {rendered} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="Emit JSON (default)")
    group.add_argument("--markdown", action="store_true", help="Emit the feature doc markdown")
    args = parser.parse_args()

    inventory = build_inventory()
    if args.markdown:
        print(render_markdown(inventory))
    else:
        print(render_json(inventory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
