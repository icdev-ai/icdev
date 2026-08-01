# CUI // SP-CTI
"""pip-install DB provisioning — the two defects that reached 1.2.42.

Neither was visible from a source checkout, which is why both shipped:

  1. ``provision_db.init_schema`` launched ``python -m tools.db.init_icdev_db``.
     The ``tools`` → ``icdev.tools`` alias is a ``sys.modules`` entry installed
     by ``icdev/__init__.py``, so a CHILD process never sees it. A source
     checkout has a real top-level ``tools/`` shim and works; a pip install
     raises ``ModuleNotFoundError: No module named 'tools'``.

  2. The wizard's SQLite branch — the default zero-install path — called
     ``check_sqlite`` and stopped. It printed ``database ready : False
     (missing: schema)`` and created nothing: a diagnosis rendered as an action.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from tools.cli import provision_db
from tools.compat.subprocess_utils import runnable_module


# --------------------------------------------------------------------------- #
# runnable_module
# --------------------------------------------------------------------------- #

def test_non_tools_module_is_returned_unchanged():
    assert runnable_module("icdev.tools.db.storage") == "icdev.tools.db.storage"
    assert runnable_module("json.tool") == "json.tool"


def test_source_checkout_keeps_the_real_shim():
    """A genuine top-level ``tools`` package must not be redirected.

    Running the ``icdev.tools`` mirror instead would silently execute a
    possibly-stale copy of whatever the caller asked for.
    """
    assert runnable_module("tools.db.init_icdev_db") == "tools.db.init_icdev_db"


def test_aliased_tools_resolves_to_the_packaged_name(monkeypatch):
    """With only the alias present, the child needs the ``icdev.`` prefix.

    Simulates the installed wheel, where ``icdev/__init__.py`` has bound
    ``sys.modules["tools"]`` to the ``icdev.tools`` module — whose ``__name__``
    is the tell that no real top-level package exists for a child to find.
    Verified against a real 1.2.42 wheel in a clean venv.
    """
    import icdev.tools

    monkeypatch.setitem(sys.modules, "tools", icdev.tools)
    assert runnable_module("tools.db.init_icdev_db") == "icdev.tools.db.init_icdev_db"


def test_spec_is_not_consulted_for_an_imported_shim(monkeypatch):
    """The repo shim sets ``__spec__ = None``.

    ``find_spec`` raises ValueError for an imported module with no spec, so a
    spec-based check reported a real source checkout as an installed wheel and
    redirected to the mirror. Guards that regression: find_spec must not be
    reached when ``tools`` is already imported.
    """
    import importlib.util

    import tools as real_tools

    assert real_tools.__spec__ is None, "shim no longer sets __spec__ = None"

    def _explode(name):
        raise AssertionError("find_spec consulted for an already-imported tools")

    monkeypatch.setattr(importlib.util, "find_spec", _explode)
    assert runnable_module("tools.db.init_icdev_db") == "tools.db.init_icdev_db"


def test_unimportable_tools_resolves_to_the_packaged_name(monkeypatch):
    """find_spec returning None (or raising) must not leave a bare `tools.`."""
    import importlib.util

    monkeypatch.delitem(sys.modules, "tools", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert runnable_module("tools.x") == "icdev.tools.x"

    def _boom(name):
        raise ValueError("odd sys.path entry")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert runnable_module("tools.x") == "icdev.tools.x"


# --------------------------------------------------------------------------- #
# init_schema
# --------------------------------------------------------------------------- #

def test_init_schema_launches_a_resolvable_module(monkeypatch):
    """The -m target must be one the child can import, and carry --db-path.

    Without an explicit path the child does not read the project's .env and
    falls back to its own default, creating the schema where nothing looks.
    """
    seen: dict = {}

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Proc()

    monkeypatch.setattr(provision_db.subprocess, "run", _fake_run)
    res = provision_db.init_schema(db_path=Path("data/icdev.db"))

    assert res.ok, res.error
    cmd = seen["cmd"]
    assert cmd[0] == sys.executable
    assert cmd[1] == "-m"
    # The exact name depends on the environment; what must hold is that it is
    # importable HERE, which is the property the child needs.
    target = cmd[2]
    assert target.endswith("tools.db.init_icdev_db")
    import importlib.util

    assert importlib.util.find_spec(target) is not None, (
        f"init_schema would launch `python -m {target}`, which does not resolve"
    )
    assert "--db-path" in cmd and str(Path("data/icdev.db")) in cmd


def test_init_schema_dry_run_names_the_command_it_would_run(monkeypatch):
    def _explode(*a, **k):  # dry run must not spawn anything
        raise AssertionError("dry_run spawned a subprocess")

    monkeypatch.setattr(provision_db.subprocess, "run", _explode)
    res = provision_db.init_schema(dry_run=True)
    assert res.ok
    assert "tools.db.init_icdev_db" in str(res.to_dict())


# --------------------------------------------------------------------------- #
# provision_sqlite
# --------------------------------------------------------------------------- #

def test_provision_sqlite_creates_the_schema(tmp_path, monkeypatch):
    """The regression that matters: it must CREATE, not merely report."""
    db = tmp_path / "data" / "icdev.db"

    def _fake_init(*, dry_run=False, db_path=None):
        # Stand in for the real 520-table initialiser.
        db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db_path))
        con.execute("CREATE TABLE rag_chunks (id TEXT)")
        con.commit()
        con.close()
        return provision_db.ProvisionResult()

    monkeypatch.setattr(provision_db, "init_schema", _fake_init)
    res = provision_db.provision_sqlite(db)

    assert db.is_file(), "provision_sqlite returned without creating the database"
    assert res["ok"]
    assert res["status"]["schema_present"]


def test_provision_sqlite_reports_failure_instead_of_claiming_ready(tmp_path, monkeypatch):
    """A failed initialiser must not come back ok — that is the original bug."""
    db = tmp_path / "data" / "icdev.db"

    def _failing_init(*, dry_run=False, db_path=None):
        res = provision_db.ProvisionResult()
        res.ok = False
        res.error = "boom"
        return res

    monkeypatch.setattr(provision_db, "init_schema", _failing_init)
    res = provision_db.provision_sqlite(db)

    assert res["ok"] is False
    assert not db.exists()
    assert res.get("hint")


def test_provision_sqlite_is_idempotent(tmp_path, monkeypatch):
    """An already-provisioned database must not be re-initialised."""
    db = tmp_path / "data" / "icdev.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE rag_chunks (id TEXT)")
    con.commit()
    con.close()

    def _explode(*a, **k):
        raise AssertionError("re-initialised an already-provisioned database")

    monkeypatch.setattr(provision_db, "init_schema", _explode)
    res = provision_db.provision_sqlite(db)
    assert res["ok"]
    assert res["steps"] == []


def test_provision_sqlite_dry_run_writes_nothing(tmp_path, monkeypatch):
    db = tmp_path / "data" / "icdev.db"
    monkeypatch.setattr(provision_db.subprocess, "run",
                        lambda *a, **k: pytest.fail("dry run spawned a subprocess"))
    res = provision_db.provision_sqlite(db, dry_run=True)
    assert not db.exists()
    assert res["ok"]


# --------------------------------------------------------------------------- #
# wizard wiring
# --------------------------------------------------------------------------- #

def test_wizard_prompts_to_provision_but_leaves_automation_explicit():
    """Interactive runs offer provisioning (default yes); scripted runs do not.

    "Run the setup wizard" has to end with a working database, so the common
    path asks instead of requiring a flag nobody knows about. --non-interactive
    is deliberately excluded: automation should not acquire a side effect it
    never requested.
    """
    source = Path(provision_db.__file__).parent.joinpath("setup_wizard.py").read_text(
        encoding="utf-8")
    assert "provision_db_now" in source
    assert "if not provision_db_now and not args.non_interactive" in source, (
        "the provisioning prompt no longer excludes --non-interactive"
    )


def test_wizard_sqlite_branch_provisions_rather_than_only_checking():
    """Pin the wiring, not just the helper.

    ``provision_sqlite`` existing is worth nothing if the wizard still calls
    ``check_sqlite`` and stops — which is exactly how this shipped.
    """
    source = Path(provision_db.__file__).parent.joinpath("setup_wizard.py").read_text(
        encoding="utf-8")
    assert "provision_sqlite" in source, (
        "setup_wizard no longer calls provision_sqlite — the SQLite branch is "
        "back to reporting without creating"
    )
