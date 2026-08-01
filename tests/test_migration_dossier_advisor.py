#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the Migration Canvas Dossier Advisor (cnr-mdc-01).

Regression guard: the advisor previously mixed ``?`` and ``%s`` placeholders and
used the global RLS-bearing connection, so it silently returned ``[]`` on
PostgreSQL (psycopg2 syntax error swallowed by a bare ``except``).  These tests
assert a non-empty advisory for seeded challenges via the canvas-safe connection
and a single placeholder style.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.migration_canvas.db import init_db as init_db_mod
from tools.migration_canvas import dossier_advisor


_RESEARCH_CHALLENGES_DDL = """
CREATE TABLE IF NOT EXISTS research_challenges (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    category        TEXT NOT NULL,
    composite_score REAL,
    severity        TEXT DEFAULT 'notable'
)
"""


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """Point the canvas DB at a temp SQLite file and seed research_challenges."""
    db_path = tmp_path / "migration_canvas_dossier.db"
    monkeypatch.setenv("MC_DB_PATH", str(db_path))
    monkeypatch.setattr(init_db_mod, "DB_PATH", db_path)
    init_db_mod.init_db()

    # Seed challenges keyed to the advisor's target session.
    sid = dossier_advisor.TARGET_SESSION_ID
    rows = [
        # (id, category, composite_score, severity) — step 1 maps to compliance/security
        (f"rchal-{uuid.uuid4().hex[:8]}", "compliance", 0.91, "critical"),
        (f"rchal-{uuid.uuid4().hex[:8]}", "security", 0.84, "notable"),
        (f"rchal-{uuid.uuid4().hex[:8]}", "infrastructure", 0.77, "notable"),
        # unscored — must be filtered out (composite_score IS NULL)
        (f"rchal-{uuid.uuid4().hex[:8]}", "compliance", None, "notable"),
    ]
    with init_db_mod.get_connection() as conn:
        conn.execute(_RESEARCH_CHALLENGES_DDL)
        for cid, cat, score, sev in rows:
            conn.execute(
                "INSERT INTO research_challenges "
                "(id, session_id, title, description, category, composite_score, severity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, sid, f"Challenge {cid}", f"desc for {cat}", cat, score, sev),
            )
        conn.commit()
    return db_path


def test_guidance_non_empty_for_seeded_step(seeded_db):
    """Step 1 (compliance/security) returns seeded challenges, not []."""
    result = dossier_advisor.get_guidance_for_step(1, top_k=3)
    assert result, "advisor returned empty despite seeded challenges (mdc-01 regression)"
    cats = {item["category"] for item in result}
    assert cats <= {"compliance", "security"}
    # Highest composite_score first.
    assert result[0]["category"] == "compliance"


def test_unscored_challenges_excluded(seeded_db):
    """Challenges with NULL composite_score are not surfaced."""
    result = dossier_advisor.get_guidance_for_step(1, top_k=10)
    assert all(item["title"] for item in result)
    # Only 2 scored challenges match step 1 (compliance + security).
    assert len(result) == 2


def test_top_k_limit_respected(seeded_db):
    """top_k caps the number of returned items."""
    result = dossier_advisor.get_guidance_for_step(1, top_k=1)
    assert len(result) == 1


def test_unknown_step_returns_empty(seeded_db):
    """An unmapped wizard step returns an empty list without error."""
    assert dossier_advisor.get_guidance_for_step(99) == []
