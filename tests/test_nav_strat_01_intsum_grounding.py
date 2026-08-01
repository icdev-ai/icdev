# CUI // SP-CTI
"""nav-strat-01 — INTSUM citation-grounding (TRUST invariant).

Verifies that the INTSUM synthesis pipeline in ``tools/strategos/intsum.py``:

  1. carries inline ``[source: <id>]`` citations that resolve to the supplied
     evidence set -> grounding verdict "grounded";
  2. flags a fabricated citation (id not in evidence) -> verdict "ungrounded",
     paragraph marked not grounded, HTTP-facing status downgraded;
  3. degrades to labelled template prose (no citations) when the LLM is
     unavailable -> verdict "template", no fabricated citations;
  4. persists the grounding verdict on the INTSUM row and paragraphs, and
     audits an explicit ``force_ungrounded`` HITL override.

The LLM is mocked at the ``_synthesize_paragraph`` seam so no provider is hit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from tools.db import storage as storage_mod
import tools.strategos.intsum as intsum


_SCHEMA = """
CREATE TABLE sg_intsums (
    id TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    theater TEXT DEFAULT 'global',
    classification TEXT DEFAULT 'CUI // SP-CTI',
    prepared_by TEXT DEFAULT 'ICDEV Strategos / INTSUM Engine',
    status TEXT DEFAULT 'draft',
    model_used TEXT,
    latency_ms INTEGER DEFAULT 0,
    grounding_status TEXT,
    grounding_json TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE sg_intsum_paragraphs (
    id TEXT PRIMARY KEY,
    intsum_id TEXT NOT NULL,
    para_num INTEGER NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    grounded INTEGER DEFAULT 1,
    require_citations INTEGER DEFAULT 0,
    citations TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE sg_intsum_grounding_audit (
    id TEXT PRIMARY KEY,
    intsum_id TEXT NOT NULL,
    findings TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE sg_sigint_events (
    id TEXT PRIMARY KEY,
    description TEXT,
    collected_at TEXT
);
CREATE TABLE sg_socmint_signals (
    id TEXT PRIMARY KEY,
    text TEXT,
    posted_at TEXT
);
"""


@pytest.fixture
def intsum_db(tmp_path, monkeypatch):
    """Isolated SQLite DB routed through the real StorageConnection wrapper.

    Using the wrapper (not raw sqlite3) so the ``%s`` placeholders in
    intsum.py are translated exactly as they are in production.
    """
    db_path = tmp_path / "intsum_grounding.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    conn = storage_mod.get_connection(db_path=str(db_path))
    try:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        # One SIGINT row -> a single evidence item with citation handle "S1".
        conn.execute(
            "INSERT INTO sg_sigint_events (id, description, collected_at) "
            "VALUES (%s, %s, %s)",
            (str(uuid.uuid4()),
             "PLA naval vessels observed massing near the strait approaches.",
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _mock_synth(text: str, llm_used: bool = True, model_id: str = "mock-model"):
    """Return a drop-in for intsum._synthesize_paragraph with fixed output."""
    def _synth(prompt: str):
        return (text, llm_used, model_id)
    return _synth


def test_grounded_citation_passes(intsum_db, monkeypatch):
    """Paragraphs citing a real evidence id -> grounding verdict passes."""
    monkeypatch.setattr(
        intsum, "_synthesize_paragraph",
        _mock_synth("Adversary vessels are massing near the strait [source: S1]."),
    )
    result = intsum.generate_intsum(theater="western_pacific", lookback_hours=24)

    assert "error" not in result, result
    grounding = result["grounding"]
    assert grounding["status"] == "grounded"
    assert grounding["evidence_count"] == 1
    assert grounding["gate_findings"] == []

    # Grounded paragraphs (1, 2, 5) require citations and resolve to S1.
    by_num = {p["para_num"]: p for p in result["paragraphs"]}
    for n in (1, 2, 5):
        assert by_num[n]["require_citations"] is True
        assert by_num[n]["grounded"] is True
        assert "S1" in by_num[n]["citations"]
        assert by_num[n]["hallucinated"] == []


def test_fabricated_citation_is_flagged(intsum_db, monkeypatch):
    """A citation to an id not in evidence -> ungrounded verdict + gate finding."""
    monkeypatch.setattr(
        intsum, "_synthesize_paragraph",
        _mock_synth("Unsupported claim about enemy intent [source: S99]."),
    )
    result = intsum.generate_intsum(theater="western_pacific", lookback_hours=24)

    grounding = result["grounding"]
    assert grounding["status"] == "ungrounded"
    assert grounding["gate_findings"], "expected citation gate to flag defects"

    by_num = {p["para_num"]: p for p in result["paragraphs"]}
    assert by_num[1]["grounded"] is False
    assert "S99" in by_num[1]["hallucinated"]


def test_llm_unavailable_falls_back_to_template(intsum_db, monkeypatch):
    """No LLM -> deterministic template prose, labelled, with no fake citations."""
    monkeypatch.setattr(
        intsum, "_synthesize_paragraph",
        _mock_synth("", llm_used=False, model_id=""),
    )
    result = intsum.generate_intsum(theater="western_pacific", lookback_hours=24)

    grounding = result["grounding"]
    assert grounding["status"] == "template"
    assert result["model_used"] == "template"

    for p in result["paragraphs"]:
        assert "TEMPLATE-GENERATED" in p["content"]
        assert p["method"] == "template"
        # Template prose must never assert citations.
        assert p["citations"] == []
        assert p["hallucinated"] == []


def test_grounding_verdict_is_persisted(intsum_db, monkeypatch):
    """The persisted INTSUM row and paragraphs carry the grounding verdict."""
    monkeypatch.setattr(
        intsum, "_synthesize_paragraph",
        _mock_synth("Vessels massing near the strait [source: S1]."),
    )
    result = intsum.generate_intsum(theater="western_pacific", lookback_hours=24)
    intsum_id = result["intsum_id"]

    detail = intsum.get_intsum_detail(intsum_id)
    assert detail is not None
    assert detail["grounding_status"] == "grounded"
    assert detail["grounding"]["status"] == "grounded"

    by_num = {p["para_num"]: p for p in detail["paragraphs"]}
    assert by_num[1]["grounded"] is True
    assert by_num[1]["require_citations"] is True
    assert "S1" in by_num[1]["citations"]
    # A non-grounded paragraph (Distribution) does not require citations.
    assert by_num[6]["require_citations"] is False


def test_force_ungrounded_override_is_audited(intsum_db, monkeypatch):
    """HITL force override changes status and writes an append-only audit row."""
    monkeypatch.setattr(
        intsum, "_synthesize_paragraph",
        _mock_synth("Unsupported claim [source: S99]."),
    )
    result = intsum.generate_intsum(
        theater="western_pacific", lookback_hours=24, force_ungrounded=True,
    )
    assert result["grounding"]["status"] == "ungrounded_forced"
    assert result["grounding"]["forced"] is True

    conn = storage_mod.get_connection(db_path=str(intsum_db))
    try:
        rows = conn.execute(
            "SELECT intsum_id, findings FROM sg_intsum_grounding_audit "
            "WHERE intsum_id = %s",
            (result["intsum_id"],),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "expected exactly one force-override audit row"
    assert rows[0][0] == result["intsum_id"]
