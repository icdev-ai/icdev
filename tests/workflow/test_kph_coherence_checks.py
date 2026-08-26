# CUI // SP-CTI
"""Unit tests for the kanban-pipeline-hardening coherence checks (kph-B/C/D).

Each check follows the full-repo=WARN / changed-files=FAIL invariant. The tests
monkeypatch coherence_checker.PROJECT_ROOT to a temp tree so they are hermetic
(no dependence on the real repo's debt).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.workflow import coherence_checker as cc


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A throwaway PROJECT_ROOT the checks scan."""
    monkeypatch.setattr(cc, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# check_test_db_isolation (kph-B)
# ---------------------------------------------------------------------------
class TestTestDbIsolation:
    def test_factory_hijack_to_raw_sqlite_fails(self, repo):
        f = _write(
            repo / "tests" / "test_bad_factory.py",
            "import sqlite3\n"
            "def _fake(db_path=None):\n"
            "    return sqlite3.connect(db_path)\n"
            "def test_x(monkeypatch):\n"
            "    import tools.dashboard.api.admin as m\n"
            "    monkeypatch.setattr(m, 'get_connection', _fake)\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "fail", r.message
        assert any("get_connection" in v or "connection factory" in v for v in r.extra)

    def test_raw_conn_passed_with_pct_s_fails(self, repo):
        f = _write(
            repo / "tests" / "test_bad_conn.py",
            "import sqlite3\n"
            "def test_x():\n"
            "    conn = sqlite3.connect(':memory:')\n"
            "    from tools.example.db import get_rows\n"
            "    get_signals(conn=conn)  # runtime uses '%s'\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "fail", r.message

    def test_local_bound_storage_connection_is_clean(self, repo):
        """A local bound straight to StorageConnection(...) IS the remedy.

        The propagation in _safe_connection_names only reached names referencing
        an already-safe FACTORY FUNCTION, so patching with a variable assigned
        directly from StorageConnection(raw, "sqlite") -- the exact fix this
        check's own failure message prescribes -- still read as a violation, with
        no way to write it that would pass.
        """
        f = _write(
            repo / "tests" / "test_ok_direct_wrap.py",
            "import sqlite3\n"
            "from unittest.mock import patch\n"
            "from tools.db.storage import StorageConnection\n"
            "def test_x():\n"
            "    raw = sqlite3.connect(':memory:')\n"
            "    wrapped = StorageConnection(raw, 'sqlite')\n"
            "    import tools.observability_canvas.mitre_coverage_db as m\n"
            "    with patch.object(m, 'get_connection', return_value=wrapped):\n"
            "        m.list_coverage('proj-1')  # runtime SQL uses '%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "pass", r.message

    def test_direct_wrap_recognition_does_not_clear_an_unwrapped_raw(self, repo):
        """...but wrapping ONE connection must not launder a different raw one.

        Guards the fix above from over-reaching: a file holding both a correctly
        wrapped connection and a factory patched to a bare sqlite3 handle is
        still a violation.
        """
        f = _write(
            repo / "tests" / "test_mixed_wrap.py",
            "import sqlite3\n"
            "from unittest.mock import patch\n"
            "from tools.db.storage import StorageConnection\n"
            "def test_x():\n"
            "    wrapped = StorageConnection(sqlite3.connect(':memory:'), 'sqlite')\n"
            "    assert wrapped is not None\n"
            "    naked = sqlite3.connect(':memory:')\n"
            "    import tools.dashboard.api.admin as m\n"
            "    with patch.object(m, 'get_connection', return_value=naked):\n"
            "        m.load('%s')\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "fail", r.message

    def test_raw_sqlite_only_qmark_is_clean(self, repo):
        # conftest-style seed: raw sqlite3 but only ? placeholders, no factory patch.
        f = _write(
            repo / "tests" / "test_ok_seed.py",
            "import sqlite3\n"
            "def test_x():\n"
            "    conn = sqlite3.connect(':memory:')\n"
            "    conn.execute('INSERT INTO t (id) VALUES (?)', ('a',))\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "pass", r.message

    def test_full_repo_is_warn_not_fail(self, repo):
        _write(
            repo / "tests" / "test_bad_factory.py",
            "import sqlite3\n"
            "def _fake(db_path=None):\n    return sqlite3.connect(db_path)\n"
            "def test_x(monkeypatch):\n"
            "    import m\n    monkeypatch.setattr(m, '_get_db', _fake)\n",
        )
        r = cc.check_test_db_isolation(None)
        assert r.status == "warn", r.message

    def test_raw_conn_in_another_function_does_not_taint(self, repo):
        """`conn` bound raw in one test must not flag a safe `conn` in another.

        `conn` is the obvious name for a connection, so a read-only assertion
        connection in one test and a translating fixture in the next is normal.
        Resolving the name file-wide made the first taint the second and flagged
        the fixture that IS the remedy (tests/unit/test_audit_trail.py).
        """
        f = _write(
            repo / "tests" / "test_mixed_conn.py",
            "import sqlite3\n"
            "from _sql_compat import connect as _tconnect\n"
            "def _factory():\n"
            "    return _tconnect('db')\n"
            "def test_reads_raw():\n"
            "    conn = sqlite3.connect(':memory:')\n"
            "    conn.execute('SELECT 1')\n"
            "def test_passes_safe_conn():\n"
            "    conn = _factory()\n"
            "    from tools.audit.audit_logger import log_event\n"
            "    log_event(conn=conn)\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "pass", r.message

    def test_raw_conn_in_same_function_still_fails(self, repo):
        """Guard the fix: scoping must not blind the check to a real violation."""
        f = _write(
            repo / "tests" / "test_same_scope.py",
            "import sqlite3\n"
            "from _sql_compat import connect as _tconnect\n"
            "def _factory():\n"
            "    return _tconnect('db')\n"
            "def test_safe_elsewhere():\n"
            "    conn = _factory()\n"
            "def test_bad():\n"
            "    conn = sqlite3.connect(':memory:')\n"
            "    from tools.audit.audit_logger import log_event\n"
            "    log_event(conn=conn)\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "fail", r.message

    def test_storage_connection_factory_is_the_prescribed_remedy(self, repo):
        """The failure message says "Wrap in StorageConnection(conn, 'sqlite')".

        A fixture that does exactly that — including via a module alias, which
        is how a shim-aware test reaches tools.db.storage — must pass, or the
        gate rejects its own remedy.
        """
        f = _write(
            repo / "tests" / "test_storage_conn_factory.py",
            "import importlib, sqlite3\n"
            "_storage = importlib.import_module('tools.db.storage')\n"
            "def _connect(path):\n"
            "    raw = sqlite3.connect(str(path))\n"
            "    return _storage.StorageConnection(raw, 'sqlite')\n"
            "def _mount(path, monkeypatch):\n"
            "    def _fake(*a, **kw):\n"
            "        return _connect(path)\n"
            "    monkeypatch.setattr(_storage, 'get_connection', _fake)\n"
            "def test_reads():\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "pass", r.message

    def test_bare_sqlite_factory_still_fails_alongside_a_storage_one(self, repo):
        """Guard the fix: recognising StorageConnection must not clear a raw one."""
        f = _write(
            repo / "tests" / "test_storage_conn_mixed.py",
            "import sqlite3\n"
            "from tools.db.storage import StorageConnection\n"
            "def _safe(path):\n"
            "    return StorageConnection(sqlite3.connect(path), 'sqlite')\n"
            "def _raw(path):\n"
            "    return sqlite3.connect(path)\n"
            "def test_patches_raw(monkeypatch):\n"
            "    monkeypatch.setattr('tools.db.storage.get_connection', _raw)\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "fail", r.message

    def test_raise_only_factory_is_not_a_raw_connection(self, repo):
        """A replacement that only raises hands runtime code no connection.

        It is how a test drives the caller's 'database unavailable' branch —
        the one path that must never reach a sqlite3 handle. Flagging it made
        the gate reject the negative test it wants written (hgx-doc-01).
        """
        f = _write(
            repo / "tests" / "test_no_connection.py",
            "import sqlite3\n"
            "def _boom():\n"
            "    raise RuntimeError('no database')\n"
            "def test_survives_no_db(monkeypatch):\n"
            "    monkeypatch.setattr('tools.db.storage.get_connection', _boom)\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "pass", r.message

    def test_raise_then_raw_connect_still_fails(self, repo):
        """Guard the fix: only an unconditionally-raising factory is cleared."""
        f = _write(
            repo / "tests" / "test_raise_then_connect.py",
            "import sqlite3\n"
            "def _maybe(fail=False):\n"
            "    if fail:\n"
            "        raise RuntimeError('no database')\n"
            "    return sqlite3.connect(':memory:')\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr('tools.db.storage.get_connection', _maybe)\n"
            "    _ = 'SELECT * FROM t WHERE a=%s'\n",
        )
        r = cc.check_test_db_isolation([f])
        assert r.status == "fail", r.message


# ---------------------------------------------------------------------------
# check_migration_numbering (kph-C)
# ---------------------------------------------------------------------------
class TestMigrationNumbering:
    def _seed(self, repo: Path):
        mig = repo / "tools" / "db" / "migrations"
        mig.mkdir(parents=True, exist_ok=True)
        (mig / "260_a.sql").write_text("-- a\n", encoding="utf-8")
        (mig / "261_b.sql").write_text("-- b\n", encoding="utf-8")
        return mig

    def test_new_collision_fails(self, repo):
        mig = self._seed(repo)
        dup = mig / "261_c.sql"  # collides with 261_b
        dup.write_text("-- c\n", encoding="utf-8")
        r = cc.check_migration_numbering([dup])
        assert r.status == "fail", r.message
        assert "262" in r.message  # suggests next free

    def test_fresh_number_passes(self, repo):
        mig = self._seed(repo)
        fresh = mig / "262_c.sql"
        fresh.write_text("-- c\n", encoding="utf-8")
        r = cc.check_migration_numbering([fresh])
        assert r.status == "pass", r.message

    def test_existing_dups_full_repo_warn(self, repo):
        mig = self._seed(repo)
        (mig / "261_c.sql").write_text("-- c\n", encoding="utf-8")
        r = cc.check_migration_numbering(None)
        assert r.status == "warn", r.message

    def test_dir_based_migrations_counted(self, repo):
        mig = self._seed(repo)
        (mig / "262_thing").mkdir()
        dup_dir = mig / "262_other"  # collides with 262_thing (both dir-based)
        dup_dir.mkdir()
        r = cc.check_migration_numbering([dup_dir])
        assert r.status == "fail", r.message


# ---------------------------------------------------------------------------
# check_icdev_mirror_parity (kph-D)
# ---------------------------------------------------------------------------
class TestIcdevMirrorParity:
    def test_missing_twin_fails(self, repo):
        f = _write(repo / "tools" / "cortex" / "widget.py", "x = 1\n")
        r = cc.check_icdev_mirror_parity([f])
        assert r.status == "fail", r.message
        assert any("widget.py" in m for m in r.missing)

    def test_present_twin_passes(self, repo):
        f = _write(repo / "tools" / "cortex" / "widget.py", "x = 1\n")
        _write(repo / "icdev" / "tools" / "cortex" / "widget.py", "x = 1\n")
        r = cc.check_icdev_mirror_parity([f])
        assert r.status == "pass", r.message

    def test_non_mirrored_root_ignored(self, repo):
        # tools/random is not a mirrored root -> not flagged.
        f = _write(repo / "tools" / "random" / "thing.py", "x = 1\n")
        r = cc.check_icdev_mirror_parity([f])
        assert r.status == "pass", r.message

    def test_full_repo_drift_warn(self, repo):
        _write(repo / "tools" / "cortex" / "widget.py", "x = 1\n")  # no twin
        r = cc.check_icdev_mirror_parity(None)
        assert r.status == "warn", r.message
