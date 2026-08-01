# CUI // SP-CTI
"""Migration 261 (conformance columns) — well-formed, idempotent, PG-conditional."""
import importlib.util
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UP = _REPO_ROOT / "tools" / "db" / "migrations" / "261_kanban_verifications_conformance" / "up.py"


def _load():
    spec = importlib.util.spec_from_file_location("migration_261_up", str(_UP))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Cur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row or []


class _FakeConn:
    """PG stub: table exists, reports which columns already exist, records DDL."""
    def __init__(self, existing_cols):
        self._backend = "postgresql"
        self._cols = set(existing_cols)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        low = sql.lower()
        if "information_schema.tables" in low:
            return _Cur({"1": 1})
        if "information_schema.columns" in low:
            col = params[1] if params else None
            return _Cur({"1": 1} if col in self._cols else None)
        return _Cur(None)

    def commit(self):
        pass


def test_migration_columns_defined():
    up = _load()
    cols = [c for c, _ in up._COLUMNS]
    assert cols == ["review_passed", "review_findings", "pytest_ran"]


def test_migration_adds_missing_columns():
    up = _load()
    conn = _FakeConn(existing_cols=[])   # none exist yet
    res = up.up(conn)
    assert res["status"] == "applied"
    ddl = " ".join(conn.executed).lower()
    for col in ("review_passed", "review_findings", "pytest_ran"):
        assert f"add column {col}" in ddl


def test_migration_idempotent_when_present():
    up = _load()
    conn = _FakeConn(existing_cols=["review_passed", "review_findings", "pytest_ran"])
    res = up.up(conn)
    assert res["status"] == "applied"
    assert not any("ADD COLUMN" in s for s in conn.executed)


def test_migration_skips_when_table_absent():
    up = _load()

    class _NoTable(_FakeConn):
        def execute(self, sql, params=None):
            self.executed.append(sql)
            if "information_schema.tables" in sql.lower():
                return _Cur(None)   # table missing
            return _Cur(None)

    res = up.up(_NoTable([]))
    assert res["status"] == "skipped"
