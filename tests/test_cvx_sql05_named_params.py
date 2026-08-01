# CUI // SP-CTI
"""Pytest suite — canvas named-parameter placeholder sweep (cvx-sql-05).

Follow-up to cvx-sql-01. That sweep converted bare ``?`` placeholders to
PG-native ``%s`` on the migration/security canvas StorageConnection paths, but
its AST scan only looked for ``?`` — it could not see SQLite *named* ``:name``
placeholders, which are an equally broken dialect on the PG-primary path:

  * ``translate_sql`` only rewrites ``?`` → ``%s``; it leaves ``:name`` untouched,
    so psycopg2 receives ``:provider`` verbatim and raises a syntax error.
  * ``StorageCursor.execute`` wraps any non-list/tuple ``params`` (i.e. a dict)
    into a 1-tuple ``(params,)`` before handing it to the driver, so a
    ``%(name)s`` / ``:name`` mapping never reaches psycopg2 or sqlite3 as a
    mapping at all — both backends fail.

The correct authoring for a StorageConnection call site is therefore POSITIONAL
``%s`` placeholders with an ordered sequence of params (translate_sql renders
``%s`` → ``?`` for the SQLite init-fallback). This suite guards that:

  1. No ``execute()`` / ``executemany()`` SQL literal in the migration-canvas
     StorageConnection files still uses a ``:name`` named placeholder.
  2. The rebuilt ``sync_cloud_catalog`` upsert (the cvx-sql-05 finding at
     server_migration.py) executes end-to-end through a real StorageConnection
     on the SQLite backend with an ordered ``%s`` param tuple — no
     "Incorrect number of bindings" / dict-wrapping failure.
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


# Migration-canvas runtime modules that connect through StorageConnection
# (get_canvas_connection). server_migration.py carried the cvx-sql-05 finding.
_STORAGECONN_FILES = [
    "tools/migration_canvas/server_migration.py",
    "tools/migration_canvas/blueprint.py",
    "tools/migration_canvas/sops.py",
]

# A ``:word`` token that is a genuine SQLite named bind param — but NOT a PG cast
# (``::type``), and not the tail of a ``::`` cast. We scan reconstructed SQL
# literals only, so time formats like ``%H:%M`` (which live in strftime calls,
# not execute() SQL) never reach this regex.
_NAMED_PARAM_RE = re.compile(r"(?<!:):[a-zA-Z_]\w*")


def _sql_literal(node: ast.AST) -> str | None:
    """Reconstruct the literal text of a SQL argument (const / a+b / f-string)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("\x00")  # substitution sentinel
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _sql_literal(node.left)
        right = _sql_literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _iter_execute_sql(source: str):
    """Yield (lineno, sql_literal) for every execute()/executemany() call."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("execute", "executemany")
            and node.args
        ):
            sql = _sql_literal(node.args[0])
            if sql is not None:
                yield node.lineno, sql


def _strip_pg_casts(sql: str) -> str:
    """Remove ``::type`` casts so their trailing word isn't mistaken for a bind."""
    return re.sub(r"::[a-zA-Z_]\w*", "", sql)


@pytest.mark.parametrize("rel_path", _STORAGECONN_FILES)
def test_execute_sql_has_no_named_params(rel_path):
    """No StorageConnection execute() SQL literal uses a SQLite :name bind.

    Named params never translate for psycopg2 and get dict-wrapped by
    StorageCursor — they must be positional %s on these paths.
    """
    source = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
    offenders = []
    for lineno, sql in _iter_execute_sql(source):
        hits = _NAMED_PARAM_RE.findall(_strip_pg_casts(sql))
        if hits:
            offenders.append((lineno, sorted(set(hits)), sql))
    assert not offenders, (
        f"{rel_path}: execute() SQL still contains :name named placeholders "
        "(broken on the PG StorageConnection path): "
        + "; ".join(f"line {ln}: {names}" for ln, names, _ in offenders)
    )


def test_server_migration_upsert_is_positional_pct_s():
    """The rebuilt sync_cloud_catalog upsert is positional %s, not :name/%(name)s."""
    source = (_REPO_ROOT / "tools/migration_canvas/server_migration.py").read_text(
        encoding="utf-8"
    )
    assert "INSERT OR REPLACE INTO mc_cloud_instances" in source
    # The VALUES clause uses positional %s (with the single 'api' literal), and
    # no named binds survive anywhere in the upsert block.
    assert "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'api',%s)" in source
    assert ":provider" not in source and ":instance_type" not in source


def test_sync_cloud_catalog_upsert_executes_on_storageconnection(monkeypatch):
    """End-to-end: sync_cloud_catalog upserts through a real StorageConnection
    (SQLite backend) with an ordered %s param tuple — no binding/dict-wrap error.
    """
    # Pin the migration canvas to a throwaway SQLite DB BEFORE importing the
    # module so its module-level backend resolution picks sqlite.
    tmp_db = os.path.join(tempfile.gettempdir(), f"cvxsql05_mc_{uuid.uuid4().hex}.db")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_CANVAS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MC_DB_PATH", tmp_db)

    # Fresh import so _MC_BACKEND / DB path env are read with the pins above.
    #
    # monkeypatch.delitem, not sys.modules.pop: pytest records the evicted module
    # and puts it back at teardown. A bare pop leaves the re-imported replacement
    # installed for the rest of the run, so every later test resolves this module
    # to an object pinned at whatever the env said here — while anything that
    # imported from it earlier still holds the original. The identical pattern in
    # this file's data_canvas siblings failed 11 tests in test_dcpr_product_registry
    # and test_dcpr_governance_engine, all of which passed in isolation.
    for mod in ("tools.migration_canvas.db.init_db", "tools.migration_canvas.server_migration"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    from tools.migration_canvas.db import init_db as mc_init
    from tools.migration_canvas import server_migration as sm

    # Create the mc_cloud_instances schema in the throwaway DB.
    mc_init.init_db()

    # Synthetic fetcher — one row, missing several optional keys on purpose so
    # the ordered-tuple builder must tolerate absent dict keys (row.get()).
    synthetic = [{
        "provider": "aws",
        "instance_type": "m5.large",
        "family": "m5",
        "vcpus": 2,
        "ram_gb": 8.0,
        "storage_type": "ebs",
        "network_gbps": 10.0,
        "premium_disk_opt": 1,
        "cost_tier": "medium",
        "govcloud": 1,
        "il_support": "[4,5]",
        "use_case_tags": "[]",
    }]
    monkeypatch.setattr(sm, "_fetch_aws_instances", lambda: list(synthetic))

    result = sm.sync_cloud_catalog(providers=["aws"], force=True)

    assert result["status"] == "ok", result
    assert result["rows_upserted"] == 1
    assert not result["errors"]

    # Verify the row actually landed via the StorageConnection.
    conn = mc_init.get_connection()
    try:
        row = conn.execute(
            "SELECT provider, instance_type, vcpus, source, eol_status "
            "FROM mc_cloud_instances WHERE provider=%s AND instance_type=%s",
            ("aws", "m5.large"),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["provider"] == "aws"
    assert row["instance_type"] == "m5.large"
    assert row["vcpus"] == 2
    assert row["source"] == "api"
    assert row["eol_status"] == "active"  # setdefault applied
