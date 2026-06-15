"""PGP (pgp-res-02): canvas backend resolution is PG-primary, no sqlite defaults.

Verifies the two halves of the fix:

1. No ``tools/<canvas>/db/init_db.py`` hard-codes a ``"sqlite"`` default for any
   ``*_STORAGE_BACKEND`` resolution — canvases inherit the platform backend.
2. ``storage.resolve_canvas_backend()`` returns ``"postgresql"`` when every
   backend env var is unset, and ``get_connection()`` no longer forces SQLite for
   a ``.db`` path when the process backend is PostgreSQL (the db_path('.db')
   ambiguity that caused the shared-DB collisions).

conftest forces ``ICDEV_STORAGE_BACKEND=sqlite`` for the rest of the suite, so the
resolution tests explicitly clear the backend env vars first.
"""

import importlib
import re
from pathlib import Path

import pytest

# Patch/import via the canonical `tools.db.storage` module object the canvas
# init files import from (shim-aware: tools.* and icdev.tools.* differ for
# from-imports).
storage = importlib.import_module("tools.db.storage")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANVAS_INIT_FILES = sorted(_REPO_ROOT.glob("tools/*/db/init_db.py"))

# Matches a backend resolution that hard-codes a sqlite default, e.g.
#   os.environ.get("FOO_STORAGE_BACKEND", "sqlite")
#   os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "sqlite")
_HARD_SQLITE_DEFAULT = re.compile(r'STORAGE_BACKEND"\s*,\s*"sqlite"')

_BACKEND_ENV_VARS = (
    "ICDEV_STORAGE_BACKEND",
    "ICDEV_CANVAS_STORAGE_BACKEND",
)


def test_canvas_init_files_discovered():
    """Sanity: the glob actually finds the canvas init modules."""
    assert len(_CANVAS_INIT_FILES) >= 12, _CANVAS_INIT_FILES


@pytest.mark.parametrize(
    "init_file", _CANVAS_INIT_FILES, ids=lambda p: p.parent.parent.name
)
def test_no_hard_sqlite_default(init_file):
    """No canvas init_db.py may default a *_STORAGE_BACKEND to sqlite."""
    src = init_file.read_text(encoding="utf-8")
    offenders = _HARD_SQLITE_DEFAULT.findall(src)
    assert not offenders, (
        f"{init_file} hard-codes a sqlite backend default "
        f"({offenders}); canvases must inherit the platform backend "
        f"(default postgresql)."
    )


def test_resolve_canvas_backend_defaults_to_postgresql(monkeypatch):
    """With every backend env var unset, resolution is postgresql (PG-primary)."""
    for var in _BACKEND_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert storage.resolve_canvas_backend() == "postgresql"
    # A canvas-specific override var that is also unset still resolves to PG.
    monkeypatch.delenv("NC_STORAGE_BACKEND", raising=False)
    assert storage.resolve_canvas_backend("NC_STORAGE_BACKEND") == "postgresql"


def test_resolve_canvas_backend_respects_overrides(monkeypatch):
    """Resolution order: canvas var > ICDEV_CANVAS_* > ICDEV_STORAGE_BACKEND."""
    for var in _BACKEND_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NC_STORAGE_BACKEND", raising=False)

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    assert storage.resolve_canvas_backend() == "sqlite"

    monkeypatch.setenv("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql")
    assert storage.resolve_canvas_backend() == "postgresql"

    monkeypatch.setenv("NC_STORAGE_BACKEND", "sqlite")
    assert storage.resolve_canvas_backend("NC_STORAGE_BACKEND") == "sqlite"


def test_get_connection_does_not_force_sqlite_for_db_path_on_pg(monkeypatch):
    """A '.db' db_path is ignored on a PG-primary stack (no silent SQLite)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    monkeypatch.delenv("ICDEV_PG_NO_FALLBACK", raising=False)

    calls = {}

    class _FakeRaw:
        pass

    def _fake_pg(url=None):
        calls["pg"] = True
        return _FakeRaw()

    def _fake_sqlite(path=None):
        calls["sqlite"] = path
        return _FakeRaw()

    monkeypatch.setattr(storage, "_get_pg_connection", _fake_pg)
    monkeypatch.setattr(storage, "_get_sqlite_connection", _fake_sqlite)

    storage.get_connection(db_path="data/network_canvas.db")

    assert calls.get("pg") is True, "PG connection was not used"
    assert "sqlite" not in calls, "a '.db' path wrongly forced SQLite on PG"


def test_get_connection_uses_db_path_when_pinned_to_sqlite(monkeypatch):
    """When the backend IS sqlite, a canvas '.db' path still selects that file."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    calls = {}

    class _FakeRaw:
        pass

    def _fake_sqlite(path=None):
        calls["sqlite"] = path
        return _FakeRaw()

    monkeypatch.setattr(storage, "_get_sqlite_connection", _fake_sqlite)

    storage.get_connection(db_path="data/network_canvas.db")

    assert calls.get("sqlite") == "data/network_canvas.db"


def test_get_canvas_connection_disables_rls(monkeypatch):
    """get_canvas_connection always returns an RLS-disabled connection."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    monkeypatch.delenv("ICDEV_PG_NO_FALLBACK", raising=False)

    class _FakeRaw:
        pass

    monkeypatch.setattr(storage, "_get_pg_connection", lambda url=None: _FakeRaw())

    conn = storage.get_canvas_connection()
    # security_context None == RLS predicate not injected.
    assert getattr(conn, "_security_context", "unset") is None
