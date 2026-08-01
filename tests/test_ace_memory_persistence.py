# CUI // SP-CTI
"""Tests: persist refined context to ace_coworker_memory on instance completion.

Acceptance criterion: completing an ACE instance with a non-empty artifact
creates >= 1 ace_coworker_memory row.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from icdev.tools.ace.db.init_db import SCHEMA as _ACE_SCHEMA

_ACE_COWORKER_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS ace_coworker_memory (
    id               TEXT PRIMARY KEY,
    role_id          TEXT NOT NULL,
    fact_type        TEXT NOT NULL DEFAULT 'observation',
    content          TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.8,
    source_task_id   TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ace_db(tmp_path):
    """SQLite DB with full ACE canvas schema."""
    db_path = tmp_path / "ace_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_ACE_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def nova_db(tmp_path):
    """SQLite DB with ace_coworker_memory table (NOVA subset)."""
    db_path = tmp_path / "nova_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_ACE_COWORKER_MEMORY_DDL)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def patched_dbs(monkeypatch, ace_db, nova_db):
    """Patch get_canvas_connection → ace_db, get_connection → nova_db.

    Both route through the REAL storage layer rather than sqlite3.connect.
    _finalize_instance authors %s placeholders for PostgreSQL, and it is
    StorageConnection that rewrites them to ? for SQLite. Handing back a raw
    sqlite3 connection skipped that step, so every statement raised a syntax
    error — swallowed by the method's best-effort `except Exception` — and the
    test then asserted against zero rows it had caused itself.

    The real function is captured before patching, so the lambdas below cannot
    recurse into their own replacements.
    """
    import icdev.tools.db.storage as _storage

    _real_get_connection = _storage.get_connection

    monkeypatch.setattr(
        _storage, "get_canvas_connection",
        lambda *a, **kw: _real_get_connection(str(ace_db)),
    )
    monkeypatch.setattr(
        _storage, "get_connection",
        lambda *a, **kw: _real_get_connection(str(nova_db)),
    )
    return ace_db, nova_db


def _seed_instance(ace_db_path: Path, role_id: str = "ai_developer") -> str:
    """Insert an ace_instances row; return its id."""
    iid = f"ace-{uuid.uuid4().hex[:12]}"
    conn = sqlite3.connect(str(ace_db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO ace_instances (id, name, role_id, state) VALUES (?, ?, ?, 'complete')",
        (iid, "test-instance", role_id),
    )
    conn.commit()
    conn.close()
    return iid


def _seed_artifact(ace_db_path: Path, instance_id: str, content_md: str) -> None:
    """Insert an ace_artifacts row for *instance_id*."""
    conn = sqlite3.connect(str(ace_db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO ace_artifacts (id, instance_id, content_md) VALUES (?, ?, ?)",
        (uuid.uuid4().hex, instance_id, content_md),
    )
    conn.commit()
    conn.close()


def _memory_rows(nova_db_path: Path, role_id: str) -> list[tuple]:
    conn = sqlite3.connect(str(nova_db_path))
    rows = conn.execute(
        "SELECT id, role_id, fact_type, content, confidence, source_task_id"
        " FROM ace_coworker_memory WHERE role_id = ?",
        (role_id,),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Acceptance: completing instance with non-empty artifact creates >= 1 row
# ---------------------------------------------------------------------------

def test_finalize_instance_creates_memory_row(patched_dbs):
    ace_db_path, nova_db_path = patched_dbs
    role_id = "ai_developer"

    iid = _seed_instance(ace_db_path, role_id)
    _seed_artifact(
        ace_db_path,
        iid,
        "The team decided to use FastAPI for the REST layer.\n"
        "We completed the data pipeline implementation.\n"
        "Lesson: always validate input schemas upfront.\n",
    )

    from icdev.tools.ace.controller import ACEController
    ctrl = ACEController.__new__(ACEController)  # skip __init__ (no executor needed)
    ctrl._finalize_instance(iid)

    rows = _memory_rows(nova_db_path, role_id)
    assert len(rows) >= 1, f"Expected >= 1 memory row, got {len(rows)}"


# ---------------------------------------------------------------------------
# fact_type and confidence are set correctly
# ---------------------------------------------------------------------------

def test_finalize_instance_row_fields(patched_dbs):
    ace_db_path, nova_db_path = patched_dbs
    role_id = "qa_manager"

    iid = _seed_instance(ace_db_path, role_id)
    _seed_artifact(
        ace_db_path,
        iid,
        "QA decided to implement integration tests for all API endpoints.\n"
        "The team resolved the flaky test issue with database isolation.\n",
    )

    from icdev.tools.ace.controller import ACEController
    ctrl = ACEController.__new__(ACEController)
    ctrl._finalize_instance(iid)

    rows = _memory_rows(nova_db_path, role_id)
    assert rows, "Expected at least one memory row"
    _, got_role, fact_type, _, confidence, source_task_id = rows[0]
    assert got_role == role_id
    assert fact_type == "outcome"
    assert abs(confidence - 0.8) < 1e-6
    assert source_task_id == iid


# ---------------------------------------------------------------------------
# Empty artifact → no rows inserted (no crash)
# ---------------------------------------------------------------------------

def test_finalize_instance_empty_artifact_no_rows(patched_dbs):
    ace_db_path, nova_db_path = patched_dbs
    role_id = "ai_developer"

    iid = _seed_instance(ace_db_path, role_id)
    _seed_artifact(ace_db_path, iid, "")  # empty content

    from icdev.tools.ace.controller import ACEController
    ctrl = ACEController.__new__(ACEController)
    ctrl._finalize_instance(iid)

    rows = _memory_rows(nova_db_path, role_id)
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# No artifact → no rows inserted (no crash)
# ---------------------------------------------------------------------------

def test_finalize_instance_no_artifact_no_rows(patched_dbs):
    ace_db_path, nova_db_path = patched_dbs
    role_id = "ai_developer"

    iid = _seed_instance(ace_db_path, role_id)
    # intentionally no artifact row

    from icdev.tools.ace.controller import ACEController
    ctrl = ACEController.__new__(ACEController)
    ctrl._finalize_instance(iid)

    rows = _memory_rows(nova_db_path, role_id)
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# Cap: inserting > 50 facts via repeated calls trims to 50
# ---------------------------------------------------------------------------

def test_finalize_instance_caps_at_50(patched_dbs):
    ace_db_path, nova_db_path = patched_dbs
    role_id = "ai_developer"

    # Each call adds up to 10 facts; 6 calls = 60 insertions → capped to 50
    for _ in range(6):
        iid = _seed_instance(ace_db_path, role_id)
        lines = "\n".join(
            f"The team decided to implement step {i} of the pipeline."
            for i in range(10)
        )
        _seed_artifact(ace_db_path, iid, lines)

        from icdev.tools.ace.controller import ACEController
        ctrl = ACEController.__new__(ACEController)
        ctrl._finalize_instance(iid)

    rows = _memory_rows(nova_db_path, role_id)
    assert len(rows) <= 50, f"Expected <= 50 rows after cap, got {len(rows)}"


# ---------------------------------------------------------------------------
# _extract_facts unit tests
# ---------------------------------------------------------------------------

def test_extract_facts_keyword_match():
    from icdev.tools.ace.controller import _extract_facts

    md = (
        "# Heading\n"
        "The team decided to use PostgreSQL for storage.\n"
        "We completed the migration successfully.\n"
        "Short.\n"
        "Lesson: validate all inputs before processing them in the pipeline.\n"
    )
    facts = _extract_facts(md)
    assert len(facts) >= 2
    assert any("decided" in f.lower() or "completed" in f.lower() or "lesson" in f.lower() for f in facts)


def test_extract_facts_fallback_no_keywords():
    from icdev.tools.ace.controller import _extract_facts

    md = (
        "This sentence has no trigger words but is long enough to qualify.\n"
        "Another plain sentence that also qualifies by length and content.\n"
    )
    facts = _extract_facts(md)
    assert len(facts) >= 1


def test_extract_facts_respects_max():
    from icdev.tools.ace.controller import _extract_facts

    md = "\n".join(f"The team decided on step {i} of the pipeline workflow." for i in range(20))
    facts = _extract_facts(md, max_facts=5)
    assert len(facts) <= 5


def test_extract_facts_skips_headings_and_code():
    from icdev.tools.ace.controller import _extract_facts

    md = (
        "# Section Header\n"
        "```python\nprint('hello')\n```\n"
        "| col1 | col2 |\n"
        "The team decided to adopt TDD for all new modules going forward.\n"
    )
    facts = _extract_facts(md)
    assert all(not f.startswith("#") for f in facts)
    assert all("```" not in f for f in facts)
    assert any("decided" in f for f in facts)
