# CUI // SP-CTI
"""Tests for tools/kanban/seed_validator.py — structural + content gate."""
import sqlite3

import pytest

from tools.kanban import seed_validator
from tools.kanban.seed_validator import validate_batch


@pytest.fixture(autouse=True)
def _kanban_db(tmp_path, monkeypatch):
    # validate_batch only reads (optionally) from kanban_tasks; isolate anyway.
    db_path = tmp_path / "validator_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS kanban_tasks (id TEXT PRIMARY KEY, status TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    yield

GOOD_DESC = (
    "Build the indexer in tools/bar/indexer.py, reusing the existing store helper "
    "in tools/bar/store.py and the schema in tools/bar/db/init_db.py. "
    "Acceptance criteria: index() persists rows, is idempotent on re-run, and "
    "returns the row count. Test plan: add tests/bar/test_indexer.py with pytest "
    "covering the happy path and the duplicate-row path; run pytest -q to verify."
)


def _task(tid, desc=GOOD_DESC, **kw):
    d = {"id": tid, "title": tid, "description": desc,
         "task_type": "build", "priority": "high"}
    d.update(kw)
    return d


def test_good_batch_passes_deterministic():
    rep = validate_batch("bv", [_task("bv-a-01"), _task("bv-a-02", depends_on_task_id="bv-a-01")],
                         llm_grade=False)
    assert rep.ok
    assert rep.llm_used is False


def test_thin_description_fails():
    rep = validate_batch("bv", [_task("bv-b-01", desc="do the thing")], llm_grade=False)
    assert not rep.ok
    errs = rep.findings[0].content_errors
    assert any("too thin" in e for e in errs)


def test_missing_acceptance_and_test_flagged():
    desc = "Edit tools/x/y.py to add a helper. " * 8  # long, has a path, but no accept/test
    rep = validate_batch("bv", [_task("bv-c-01", desc=desc)], llm_grade=False)
    errs = " ".join(rep.findings[0].content_errors)
    assert "acceptance criteria" in errs
    assert "test/verification" in errs


def test_missing_dep_fails():
    rep = validate_batch("bv", [_task("bv-d-01", depends_on_task_id="bv-nope-99")], llm_grade=False)
    assert not rep.ok
    assert any("unknown task" in e for e in rep.findings[0].struct_errors)


def test_cycle_fails():
    rep = validate_batch("bv", [
        _task("bv-e-01", depends_on_task_id="bv-e-02"),
        _task("bv-e-02", depends_on_task_id="bv-e-01"),
    ], llm_grade=False)
    assert not rep.ok
    assert rep.batch_errors and "cycle" in rep.batch_errors[0]


def test_bad_id_and_wrong_prefix_fail():
    rep = validate_batch("bv", [_task("WRONG")], llm_grade=False)
    f = rep.findings[0]
    assert not f.struct_ok
    assert any("convention" in e for e in f.struct_errors)


def test_scheduled_without_time_is_structural_error():
    rep = validate_batch("bv", [_task("bv-f-01", status="scheduled")], llm_grade=False)
    assert not rep.ok
    assert any("scheduled_at" in e for e in rep.findings[0].struct_errors)


def test_llm_absent_degrades_to_deterministic(monkeypatch):
    # Simulate no LLM provider: _grade_with_llm returns None.
    import importlib
    mod = importlib.import_module("tools.kanban.seed_validator")
    monkeypatch.setattr(mod, "_grade_with_llm", lambda task: None)
    rep = validate_batch("bv", [_task("bv-g-01")], llm_grade=True)
    assert rep.llm_used is False        # degraded
    assert rep.ok                        # deterministic checks still pass
    assert any("rubric unavailable" in w for w in rep.findings[0].warnings)


def test_scorecard_renders():
    rep = validate_batch("bv", [_task("bv-h-01")], llm_grade=False)
    card = rep.scorecard()
    assert "bv-h-01" in card and "PASS" in card
