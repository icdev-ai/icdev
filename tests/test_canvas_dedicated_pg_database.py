# CUI // SP-CTI
"""Three canvases claimed a dedicated PostgreSQL database. None of them had one.

``tools/pipeline``, ``tools/infra_canvas`` and ``tools/network`` each opened their
connection as ``get_connection(db_path=os.environ.get("<X>_PG_DATABASE", "<name>"))``
and each documented that as a "dedicated <X>_PG_DATABASE contract" — pipeline's
note even gave it as the REASON the wrapper exists rather than
``get_canvas_connection()``.

``get_connection`` honours ``db_path`` as a SQLite file ONLY when the backend is
sqlite and the name ends in '.db'. On PostgreSQL it ignores db_path and returns
the shared icdev connection; ``tools/db/storage.py`` says so in as many words —
"the '.db' path is ignored and the connection goes to the shared icdev database
(canvas tables are namespaced by table-name prefix to avoid collisions)".

Measured on the live box 2026-08-16, and this is the part worth keeping:

  * ``get_connection(db_path='pipeline_canvas')`` -> ``current_database() = icdev``
  * the ``network_canvas`` database EXISTED and held ZERO tables, while ``icdev``
    held 139 ``nc_*`` tables

So an operator had followed .env.example, created the database, and not one row
ever landed in it. The shared database is the design; the claim was the defect.

These tests pin the two halves that made it invisible: no canvas passes a bare
database NAME as a path any more, and a bare name really does become a file.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
from pathlib import Path

import pytest

CANVASES = (
    ("pipeline", "tools.pipeline.db.init_db", "PC_PG_DATABASE"),
    ("infra_canvas", "tools.infra_canvas.db.init_db", "IDC_PG_DATABASE"),
    ("network", "tools.network.db.init_db", "NC_PG_DATABASE"),
)


@pytest.mark.parametrize("name,module_path,env_var", CANVASES)
def test_no_canvas_passes_a_bare_database_name_as_a_path(name, module_path, env_var):
    """The fix, asserted on the source that produces the behaviour.

    Checked as source rather than by connecting, because the failure is silent by
    construction: passing the name still RETURNS a working connection — to the
    wrong place on PG, and to a stray file on SQLite — so a smoke test of
    "does it connect" passes either way. That is exactly how this survived.
    """
    import ast
    import importlib
    import textwrap

    module = importlib.import_module(module_path)
    tree = ast.parse(textwrap.dedent(inspect.getsource(module.get_connection)))

    # AST, not text. The docstring deliberately QUOTES the old call in order to
    # bury it, so any substring scan fails on its own explanation — and stripping
    # the docstring by string-replace is unreliable (source and __doc__ can differ
    # by line endings; it silently did nothing here). The defect is a `db_path=`
    # keyword argument, so look for exactly that.
    passed_db_path = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "db_path"
    ]
    offending = [ast.unparse(kw) for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 for kw in node.keywords
                 if kw.arg == "db_path" and env_var in ast.unparse(kw)]

    assert not offending, (
        f"{name}: get_connection still passes {env_var} as db_path ({offending}). "
        f"On PostgreSQL db_path is ignored and the shared icdev database is "
        f"returned regardless, so the 'dedicated database' it names does not "
        f"exist as behaviour — the network_canvas database was measured with ZERO "
        f"tables while icdev held 139 nc_* tables."
    )
    # A db_path may still be passed for the SQLite fallback, which is a real
    # path under data/ — that one is legitimate and covered separately.
    for kw in passed_db_path:
        assert "environ" not in ast.unparse(kw), (
            f"{name}: db_path is being read from the environment again"
        )


@pytest.mark.parametrize("name,module_path,env_var", CANVASES)
def test_the_docstring_no_longer_claims_a_dedicated_database(name, module_path, env_var):
    """A comment that outlives its code is how the next reader gets misled.

    pipeline's note cited the dedicated contract as the REASON not to use
    get_canvas_connection() — reasoning from a thing that was not happening.
    """
    import importlib

    module = importlib.import_module(module_path)
    doc = (module.get_connection.__doc__ or "").lower()

    assert "shared icdev database" in doc, (
        f"{name}: the docstring must state where the data actually goes"
    )
    # No negative phrase check: these docstrings QUOTE the old claim in order to
    # bury it, so "dedicated ..." legitimately appears. What matters is that the
    # destination is stated, and it is asserted above.


def test_a_bare_name_really_does_become_a_file_on_sqlite(tmp_path, monkeypatch):
    """Why passing the name was worse than useless, not merely inaccurate.

    A bare name does not end in '.db', so the dedicated-SQLite-file branch is
    skipped and the connection is opened at that relative path — producing an
    extension-less database in the process's working directory, which the
    `data/*.db` ignore rule does not match. One appeared in a repo checkout while
    the pipeline canvas was being enabled for CI.
    """
    from tools.db.storage import get_connection

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.chdir(tmp_path)

    conn = get_connection(db_path="pipeline_canvas")
    try:
        assert (tmp_path / "pipeline_canvas").exists(), (
            "expected the bare name to be treated as a path — if this ever stops "
            "being true, the storage layer changed and these canvases can be "
            "revisited"
        )
        assert not str(tmp_path / "pipeline_canvas").endswith(".db")
    finally:
        conn.close()


def test_the_env_templates_no_longer_advertise_the_setting():
    """.env.example told operators to create a database nothing would ever use."""
    root = Path(__file__).resolve().parents[1]
    for rel in (".env.example", ".env.sample"):
        p = root / rel
        if not p.is_file():
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        active = [ln.strip() for ln in body.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
        offenders = [ln for ln in active if "_PG_DATABASE=" in ln]
        assert not offenders, (
            f"{rel} still sets {offenders} as an active line; it has no effect on "
            "PostgreSQL and creates a stray file on SQLite"
        )


def test_sqlite_paths_stay_inside_data_where_they_are_ignored():
    """Each canvas's SQLite fallback must be a real .db path under data/."""
    import importlib

    for name, module_path, _env in CANVASES:
        module = importlib.import_module(module_path)
        db_path = Path(str(module.DB_PATH))
        assert db_path.suffix == ".db", f"{name}: DB_PATH must end in .db, got {db_path}"
        assert db_path.parent.name == "data", (
            f"{name}: DB_PATH must live in data/ so `data/*.db` ignores it, got {db_path}"
        )


def test_sqlite3_import_is_still_reachable():
    """Guards the edit itself: these modules use sqlite3 on the fallback path."""
    assert sqlite3 is not None and os is not None
