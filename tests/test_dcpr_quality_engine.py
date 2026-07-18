# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/quality_engine.py (dcpr-qa-01).

Exercises rule validation and rule execution against a *real* seeded table,
plus the batch runner that persists results into ``dd_quality_runs``.

The DDC storage is pinned to an isolated tmp SQLite file so writes never
contend with the shared data/icdev.db a live dashboard may hold open. The
"data source" being quality-checked is a plain table seeded (via the DDC
connection helper) into that same tmp DB and read back through the engine's
own ``conn_params`` connection path.
"""

import importlib

import pytest

from tools.data_canvas import quality_engine as qe


@pytest.fixture(autouse=True)
def ddc_db(tmp_path, monkeypatch):
    """Pin DDC to a tmp SQLite DB, build the schema, and seed a source table."""
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    db_file = tmp_path / "ddc_quality.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(db_file))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    init_db.init_db()

    # Seed a source table with a known completeness/uniqueness/range profile.
    conn = init_db.get_connection()
    conn.execute(
        "CREATE TABLE qsrc (id INTEGER, email TEXT, score INTEGER)"
    )
    rows = [
        (1, "a@x.com", 10),
        (2, "b@x.com", 20),
        (3, None, 30),          # null email → completeness 75%
        (4, "a@x.com", 40),     # duplicate email → uniqueness 75% (3 distinct/4)
    ]
    conn.executemany("INSERT INTO qsrc (id, email, score) VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()

    return str(db_file)


def _conn_params(ddc_db):
    return {"db_type": "sqlite", "path": ddc_db}


# ── validate_rule ─────────────────────────────────────────────────────────────

def test_validate_rule_accepts_valid():
    rule = {"check_type": "completeness", "table_name": "qsrc",
            "column_name": "email", "threshold": 90}
    out = qe.validate_rule(rule)
    assert out["valid"] is True
    assert out["error"] is None


def test_validate_rule_rejects_bad_check_type():
    out = qe.validate_rule({"check_type": "bogus", "table_name": "t",
                            "column_name": "c", "threshold": 1})
    assert out["valid"] is False


def test_validate_rule_requires_threshold():
    out = qe.validate_rule({"check_type": "completeness", "table_name": "t",
                            "column_name": "c"})
    assert out["valid"] is False
    assert "threshold" in out["error"]


# ── run_rule against a real table ─────────────────────────────────────────────

def test_run_rule_completeness(ddc_db):
    rule = {"check_type": "completeness", "table_name": "qsrc",
            "column_name": "email", "threshold": 90}
    result = qe.run_rule(rule, _conn_params(ddc_db))
    assert "error" not in result or result.get("error") is None
    assert result["actual_value"] == 75.0   # 3 of 4 non-null
    assert result["passed"] is False        # 75 < 90
    assert result["threshold"] == 90.0


def test_run_rule_completeness_passes_low_threshold(ddc_db):
    rule = {"check_type": "completeness", "table_name": "qsrc",
            "column_name": "email", "threshold": 50}
    result = qe.run_rule(rule, _conn_params(ddc_db))
    assert result["passed"] is True


def test_run_rule_uniqueness(ddc_db):
    rule = {"check_type": "uniqueness", "table_name": "qsrc",
            "column_name": "email", "threshold": 90}
    result = qe.run_rule(rule, _conn_params(ddc_db))
    # COUNT(DISTINCT email) excludes NULL → 2 distinct over 4 rows = 50%.
    assert result["actual_value"] == 50.0
    assert result["passed"] is False


def test_run_rule_range(ddc_db):
    rule = {"check_type": "range", "table_name": "qsrc", "column_name": "score",
            "threshold": 0, "params_json": {"min_val": 0, "max_val": 100}}
    result = qe.run_rule(rule, _conn_params(ddc_db))
    assert result["passed"] is True        # scores 10..40 inside [0,100]

    rule_fail = {"check_type": "range", "table_name": "qsrc", "column_name": "score",
                 "threshold": 0, "params_json": {"min_val": 0, "max_val": 25}}
    result_fail = qe.run_rule(rule_fail, _conn_params(ddc_db))
    assert result_fail["passed"] is False  # max 40 > 25


def test_run_rule_pattern(ddc_db):
    rule = {"check_type": "pattern", "table_name": "qsrc", "column_name": "email",
            "threshold": 90, "params_json": {"pattern": r"^[^@]+@[^@]+$"}}
    result = qe.run_rule(rule, _conn_params(ddc_db))
    # All 3 non-null emails match ⇒ 100%.
    assert result["actual_value"] == 100.0
    assert result["passed"] is True


def test_run_rule_unknown_check_type(ddc_db):
    rule = {"check_type": "completeness", "table_name": "qsrc",
            "column_name": "email", "threshold": 90}
    # Corrupt the type after validation to exercise the dispatcher's else-branch.
    rule["check_type"] = "not_a_type"
    result = qe.run_rule(rule, _conn_params(ddc_db))
    assert result["passed"] is False


# ── run_all_rules (batch + persistence) ───────────────────────────────────────

def test_run_all_rules_executes_enabled_rules(ddc_db):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    conn = init_db.get_connection()
    conn.execute(
        """INSERT INTO dd_quality_rules
           (id, design_id, name, table_name, column_name, check_type, threshold, enabled)
           VALUES (?,?,?,?,?,?,?,1)""",
        ("r1", "dz", "email completeness", "qsrc", "email", "completeness", 90),
    )
    conn.execute(
        """INSERT INTO dd_quality_rules
           (id, design_id, name, table_name, column_name, check_type, threshold, enabled)
           VALUES (?,?,?,?,?,?,?,0)""",
        ("r2", "dz", "disabled rule", "qsrc", "email", "uniqueness", 90),
    )
    conn.commit()

    output = qe.run_all_rules("dz", _conn_params(ddc_db), conn)
    # Only the enabled rule runs.
    assert len(output) == 1
    assert output[0]["rule"]["id"] == "r1"
    assert output[0]["result"]["actual_value"] == 75.0

    # A run row was persisted.
    run_count = conn.execute(
        "SELECT COUNT(*) FROM dd_quality_runs WHERE rule_id=?", ("r1",)
    ).fetchone()[0]
    assert run_count == 1
    conn.close()


# ── quality_score ─────────────────────────────────────────────────────────────

def test_quality_score_computation():
    runs = [
        {"result": {"passed": True}},
        {"result": {"passed": False}},
        {"result": {"passed": True}},
        {"result": {"passed": True}},
    ]
    assert qe.quality_score(runs) == 75.0
    assert qe.quality_score([]) == 100.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
