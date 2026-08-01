# CUI // SP-CTI
"""cvx-sql-04 — Network canvas CHECK constraints derive from Python constants.

Guards the drift class that broke ACE live (a live CHECK diverged from code and
every write failed). Asserts the rendered SQL schema's allowed-value sets match
the enums in ``tools/network/db/constants.py`` exactly, that the schema still
builds and enforces, and that the tools/ and icdev/ copies stay identical.
"""
import re
import sqlite3

import pytest

from tools.network.db import constants as C
from tools.network.db import init_db as m


def _parse_string_checks(schema):
    """Return {column: [frozenset(values), ...]} for every string CHECK in schema."""
    out = {}
    for col, body in re.findall(r"CHECK\((\w+) IN \(([^)]*)\)\)", schema):
        vals = re.findall(r"'([^']*)'", body)
        if vals:  # skip boolean IN (0,1)
            out.setdefault(col, []).append(frozenset(vals))
    return out


class TestValueSetParity:
    def test_no_markers_remain(self):
        assert "@@CK" not in m.SCHEMA

    def test_every_site_clause_is_rendered(self):
        for col, values in m._CHECK_SITES:
            clause = C._check(col, values)
            assert clause in m.SCHEMA, f"missing rendered clause for {col}: {values}"

    def test_rendered_value_sets_match_constants(self):
        """The multiset of value-sets parsed from the SQL equals the one the
        constants declare — per column, order-independent."""
        rendered = _parse_string_checks(m.SCHEMA)

        expected = {}
        for col, values in m._CHECK_SITES:
            expected.setdefault(col, []).append(frozenset(values))

        assert set(rendered) == set(expected), (
            f"columns differ: rendered={set(rendered)} expected={set(expected)}"
        )
        for col in expected:
            assert sorted(map(sorted, rendered[col])) == sorted(
                map(sorted, expected[col])
            ), f"value-set mismatch for column {col}"

    def test_site_count(self):
        # 43 string-enum CHECK constraints extracted into constants.
        assert len(m._CHECK_SITES) == 43

    def test_check_helper_shape(self):
        assert C._check("x", ("a", "b")) == "CHECK(x IN ('a', 'b'))"


class TestNetworkInitSmoke:
    def _init(self, tmp_path, monkeypatch):
        db = tmp_path / "nc_test.db"
        monkeypatch.setattr(m, "DB_PATH", db)
        monkeypatch.setattr(m, "_NC_BACKEND", "sqlite")
        m.init_db()
        return db

    def test_schema_builds_and_creates_tables(self, tmp_path, monkeypatch, capsys):
        db = self._init(tmp_path, monkeypatch)
        conn = sqlite3.connect(str(db))
        try:
            n = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            assert n > 100
        finally:
            conn.close()

    def test_derived_check_is_enforced(self, tmp_path, monkeypatch, capsys):
        db = self._init(tmp_path, monkeypatch)
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND sql LIKE '%partner_type IN%'"
            ).fetchone()
            assert row is not None
            table = row[0]
            # valid value accepted (name is NOT NULL — supply it)
            conn.execute(
                f"INSERT INTO {table} (id, name, partner_type) VALUES ('ok', 'n', 'isp')"
            )
            # value outside the constant rejected by the derived CHECK
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    f"INSERT INTO {table} (id, name, partner_type) "
                    "VALUES ('bad', 'n', 'BOGUS')"
                )
        finally:
            conn.close()


class TestConstraintRepair:
    def test_iter_string_checks_covers_all_sites(self):
        found = {(t, c) for t, c, _ in m._iter_string_checks(m.SCHEMA)}
        # every column in _CHECK_SITES appears in the parsed (table, col) set
        cols = {c for c, _ in m._CHECK_SITES}
        assert cols <= {c for _, c in found}

    def test_repair_is_noop_on_sqlite(self, tmp_path, monkeypatch):
        db = tmp_path / "nc_repair.db"
        monkeypatch.setattr(m, "DB_PATH", db)
        monkeypatch.setattr(m, "_NC_BACKEND", "sqlite")
        m.init_db()
        conn = m.get_connection()
        try:
            result = m.repair_check_constraints(conn)
            assert result == {"_backend": "skipped:sqlite"}
        finally:
            conn.close()


class TestMirrorParity:
    def test_tools_and_icdev_copies_identical(self):
        import importlib

        icdev_m = importlib.import_module("icdev.tools.network.db.init_db")
        icdev_c = importlib.import_module("icdev.tools.network.db.constants")
        assert icdev_m.SCHEMA == m.SCHEMA
        assert icdev_c.PARTNER_TYPES == C.PARTNER_TYPES
        assert icdev_m._CHECK_SITES == m._CHECK_SITES
