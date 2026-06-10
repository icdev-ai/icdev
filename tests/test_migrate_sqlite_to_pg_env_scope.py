# CUI // SP-CTI
"""SIPA env_secret false-positive guard for tools.network.db.migrate_sqlite_to_pg.

SIPA's env_secret sweep has previously mis-flagged the ``NC_STORAGE_BACKEND``
read in ``migrate_sqlite_to_pg.py:96`` as an unauthorized credential access
(it's an operational toggle, not a credential). Lock in the exact allowlist
so the scope stays auditable and any future addition breaks this test until
a scoping note + companion docstring update are added together.

Mirrors the established pattern: e8a7daa40 (cli_bridge/activate.py),
b1a6f6215 (cli_bridge/capability.py), 42521c2da (subprocess_backend.py).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.network.db import migrate_sqlite_to_pg  # noqa: E402


# ── env-var scope (SIPA env_secret false-positive guard) ───────────────────
#
# This allowlist must match the "Env-var scope (auditable)" block in
# tools/network/db/migrate_sqlite_to_pg.py. Update both together.
MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS = frozenset(
    {
        # Operational toggle the operator sets to authorize the migrator
        # to run. NOT a credential. See module docstring.
        "NC_STORAGE_BACKEND",
        # pytest fixtures / monkeypatch internals — not real ICDEV config.
        "PYTEST_CURRENT_TEST",
        "PYTEST_VERSION",
    }
)


def _collect_env_reads(source: str) -> set[str]:
    """AST-walk a module and return the set of env-var names it reads.

    Captures three forms: ``os.environ.get("X")``, ``os.getenv("X")``,
    and ``os.environ["X"]``. Other os.environ mutations (pop/setdefault/
    assignment) are not allowed in this module at all.
    """
    tree = ast.parse(source)
    reads: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # os.environ.get("X") / os.getenv("X")
        target_name = None
        if isinstance(func, ast.Attribute) and func.attr in ("get", "getenv"):
            if isinstance(func.value, ast.Attribute) and func.value.attr == "environ":
                if isinstance(func.value.value, ast.Name) and func.value.value.id == "os":
                    target_name = "os.environ.get"
            elif isinstance(func.value, ast.Name) and func.value.id == "os":
                target_name = "os.getenv"

        if target_name and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                reads.add(first.value)

    # Also catch os.environ["X"] subscript form
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
                if isinstance(node.value.value, ast.Name) and node.value.value.id == "os":
                    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                        reads.add(sl.value)

    return reads


def test_no_unauthorized_env_secret_reads():
    """migrate_sqlite_to_pg reads only documented env vars.

    Any ``os.environ.get`` / ``os.getenv`` / ``os.environ[...]`` of a var
    not in MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS is treated as an
    unauthorized credential read. Update the allowlist + the module
    docstring "Env-var scope (auditable)" block together when adding a
    new var.
    """
    src = Path(migrate_sqlite_to_pg.__file__).read_text(encoding="utf-8")
    reads = _collect_env_reads(src)

    unauthorized = reads - MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS
    assert not unauthorized, (
        f"Unauthorized env reads in migrate_sqlite_to_pg.py: {sorted(unauthorized)}. "
        f"Update MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS + the module docstring "
        f"'Env-var scope (auditable)' block together."
    )


def test_allowlist_is_nonempty():
    """Sanity: the allowlist itself is not accidentally emptied."""
    assert MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS, (
        "MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS must list at least NC_STORAGE_BACKEND."
    )
    assert "NC_STORAGE_BACKEND" in MIGRATE_SQLITE_TO_PG_ALLOWED_ENV_VARS, (
        "NC_STORAGE_BACKEND is the documented guard; removing it re-opens "
        "the SIPA env_secret finding and removes the safety guard."
    )


def test_migrate_refuses_without_pg_backend(monkeypatch):
    """Behavioral: the migrate() safety guard still fires.

    The scope-clarify comments do NOT change runtime behavior. If
    NC_STORAGE_BACKEND is unset (or not 'postgresql'), migrate() must
    SystemExit to avoid silently writing back into a SQLite file the
    dashboard no longer reads.
    """
    monkeypatch.delenv("NC_STORAGE_BACKEND", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        migrate_sqlite_to_pg.migrate(verbose=False)
    assert "NC_STORAGE_BACKEND" in str(exc_info.value)


def test_migrate_refuses_on_sqlite_backend(monkeypatch):
    """Behavioral: 'sqlite' (the legacy value) is also refused."""
    monkeypatch.setenv("NC_STORAGE_BACKEND", "sqlite")
    with pytest.raises(SystemExit):
        migrate_sqlite_to_pg.migrate(verbose=False)
