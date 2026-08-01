# CUI // SP-CTI
"""Guards for the SDC lazy pillar-engine table pattern (shx-db-03).

The Security Design Canvas keeps its ~50 ``zig_*`` tables lazily created by
per-module ``_ensure_tables(conn)`` functions. These tests make that decision
auditable:

    a. Inventory freshness -- the checked-in
       ``docs/features/sdc-lazy-table-inventory.md`` matches a fresh in-memory
       regeneration, so the doc cannot silently rot.
    b. Idempotency -- every module's ``_ensure_tables`` runs twice against a
       scratch SQLite connection with no error and an identical table set.
    c. Registry wiring -- the SDC ``completeness.db_migration`` path declared in
       ``args/component_registry.yaml`` exists on disk.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
import yaml

from tools.security_canvas.db import lazy_table_inventory as lti

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "features" / "sdc-lazy-table-inventory.md"
REGISTRY_PATH = REPO_ROOT / "args" / "component_registry.yaml"

# Modules whose _ensure_tables cannot be exercised against a bare SQLite
# connection. Empty today -- all SDC lazy-table modules use plain
# ``CREATE TABLE IF NOT EXISTS`` DDL that SQLite accepts. Add
# ``"<filename>": "<reason>"`` here if that ever changes.
SKIP_MODULES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# a. Inventory freshness
# ---------------------------------------------------------------------------
def test_inventory_doc_is_fresh():
    """Checked-in feature doc must equal a fresh in-memory regeneration."""
    assert DOC_PATH.exists(), (
        f"Missing generated doc {DOC_PATH}. Regenerate with: "
        f"python tools/security_canvas/db/lazy_table_inventory.py --markdown"
    )
    expected = lti.render_markdown(lti.build_inventory())
    actual = DOC_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "sdc-lazy-table-inventory.md is stale. Regenerate with: "
        "python tools/security_canvas/db/lazy_table_inventory.py --markdown"
    )


def test_inventory_nonempty():
    inv = lti.build_inventory()
    assert inv, "Expected at least one SDC module with lazy tables"
    summary = lti.summarize(inv)
    # Sanity floor -- the canvas has ~25 lazy-table modules and ~50 tables.
    assert summary["module_count"] >= 20
    assert summary["distinct_table_count"] >= 40


# ---------------------------------------------------------------------------
# b. Idempotency
# ---------------------------------------------------------------------------
def _module_names() -> list[str]:
    return sorted(lti.build_inventory().keys())


@pytest.mark.parametrize("module_file", _module_names())
def test_ensure_tables_idempotent(module_file):
    """_ensure_tables(conn) runs twice cleanly with an identical table set."""
    if module_file in SKIP_MODULES:
        pytest.skip(f"{module_file}: {SKIP_MODULES[module_file]}")

    mod_name = f"tools.security_canvas.{module_file[:-3]}"
    module = importlib.import_module(mod_name)
    ensure = getattr(module, "_ensure_tables", None)
    assert callable(ensure), f"{mod_name} has no callable _ensure_tables"

    conn = sqlite3.connect(":memory:")
    try:
        ensure(conn)
        first = _table_set(conn)
        # Second call must not raise and must not change the schema.
        ensure(conn)
        second = _table_set(conn)
    finally:
        conn.close()

    assert first == second, f"{module_file}: table set changed on second call"

    expected = set(lti.build_inventory()[module_file])
    created = {t for t in first if not t.startswith("sqlite_")}
    missing = expected - created
    assert not missing, (
        f"{module_file}: inventory lists {sorted(missing)} but "
        f"_ensure_tables created {sorted(created)}"
    )


def _table_set(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# c. Registry wiring
# ---------------------------------------------------------------------------
def test_sdc_db_migration_path_exists():
    """SDC completeness.db_migration path from the registry must exist."""
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    # Top-level is a dict with a `components` list; tolerate a bare list too.
    entries = registry if isinstance(registry, list) else registry.get("components", [])

    sdc = next((c for c in entries if c.get("key") == "sdc"), None)
    assert sdc is not None, "No 'sdc' component in component_registry.yaml"

    migration_path = sdc.get("completeness", {}).get("db_migration")
    assert migration_path, "sdc.completeness.db_migration not set"

    resolved = REPO_ROOT / migration_path
    assert resolved.is_dir(), f"db_migration path does not exist: {resolved}"
