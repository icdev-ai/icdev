# CUI // SP-CTI
"""Pytest tests for VOC TranscriptIngestor and VOCEngine — 6 tests."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path


from tools.voc.transcript_ingestor import TranscriptIngestor
from tools.voc.voc_engine import VOCEngine

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _insert_statement(conn: sqlite3.Connection, raw_text: str, doc_id: str = "doc-seed") -> str:
    sid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO voc_job_statements "
        "(id, document_id, raw_text, job_category, frequency, severity_score, "
        "strategic_fit_score, composite_score, creative_gap_id, analyzed_at) "
        "VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)",
        (sid, doc_id, raw_text, "2026-04-28T00:00:00+00:00"),
    )
    return sid


# ─── test 1: TranscriptIngestor extracts job statements from .txt ─────────────

def test_ingestor_extracts_job_statements_from_txt(icdev_db, tmp_path, monkeypatch):
    """TranscriptIngestor.ingest() extracts sentences with job-statement keywords from a .txt fixture."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    txt = tmp_path / "interviews.txt"
    txt.write_text(
        "Everything is great today. "
        "When I need to export reports, the process is very slow. "
        "I am frustrated by the lack of dark mode. "
        "Also the login screen looks nice.",
        encoding="utf-8",
    )

    ingestor = TranscriptIngestor()
    result = ingestor.ingest(txt)

    assert result["document_id"] is not None
    assert result["skipped_reason"] is None
    assert result["job_statement_count"] >= 2  # "need to" and "frustrated by"

    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT raw_text FROM voc_job_statements WHERE document_id = ?",
        (result["document_id"],),
    ).fetchall()
    conn.close()
    assert len(rows) >= 2


# ─── test 2: PDF/docx gracefully skipped when deps missing ────────────────────

def test_pdf_docx_skipped_when_deps_missing(tmp_path, monkeypatch):
    """TranscriptIngestor.ingest() returns skipped_reason when pdfminer/python-docx absent."""
    # Block pdfminer so PDF extraction raises ImportError
    monkeypatch.setitem(sys.modules, "pdfminer", None)
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", None)

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")

    ingestor = TranscriptIngestor()
    pdf_result = ingestor.ingest(pdf)

    assert pdf_result["document_id"] is None
    assert pdf_result["job_statement_count"] == 0
    assert pdf_result["skipped_reason"] is not None
    assert "not installed" in pdf_result["skipped_reason"]

    # Block python-docx so DOCX extraction raises ImportError
    monkeypatch.setitem(sys.modules, "docx", None)

    docx = tmp_path / "notes.docx"
    docx.write_bytes(b"PK fake docx")

    docx_result = ingestor.ingest(docx)

    assert docx_result["document_id"] is None
    assert docx_result["job_statement_count"] == 0
    assert docx_result["skipped_reason"] is not None
    assert "not installed" in docx_result["skipped_reason"]


# ─── test 3: VOCEngine clusters statements by keyword overlap ─────────────────

def test_engine_clusters_statements_by_keyword(icdev_db, monkeypatch):
    """VOCEngine._cluster_and_signal() assigns matching job_category to each statement."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    fb_ids = [
        _insert_statement(conn, f"I am frustrated by slow exports #{i}")
        for i in range(3)
    ]
    iw_ids = [
        _insert_statement(conn, f"I want a cleaner dashboard #{i}")
        for i in range(2)
    ]
    conn.commit()
    conn.close()

    engine = VOCEngine()
    engine._cluster_and_signal()

    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    for sid in fb_ids:
        row = conn.execute(
            "SELECT job_category FROM voc_job_statements WHERE id = ?", (sid,)
        ).fetchone()
        assert row["job_category"] == "frustrated_by", (
            f"Expected 'frustrated_by', got {row['job_category']!r}"
        )
    for sid in iw_ids:
        row = conn.execute(
            "SELECT job_category FROM voc_job_statements WHERE id = ?", (sid,)
        ).fetchone()
        assert row["job_category"] == "i_want", (
            f"Expected 'i_want', got {row['job_category']!r}"
        )
    conn.close()


# ─── test 4: cluster above threshold cross-registers to creative_feature_gaps ──

def test_engine_high_cluster_creates_gap_signal(icdev_db, monkeypatch):
    """VOCEngine._cluster_and_signal() inserts to creative_feature_gaps when composite >= threshold."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    # 4 "frustrated by" statements → single cluster, freq_norm=1.0, severity=0.90
    # composite = 1.0*0.40 + 0.90*0.35 + 0.50*0.25 = 0.840 > 0.65 threshold
    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    for i in range(4):
        _insert_statement(conn, f"Frustrated by the wizard at step #{i}")
    conn.commit()
    conn.close()

    engine = VOCEngine()
    _, signals_created = engine._cluster_and_signal()

    assert signals_created >= 1

    conn = sqlite3.connect(str(icdev_db))
    conn.row_factory = sqlite3.Row
    gaps = conn.execute(
        "SELECT metadata FROM creative_feature_gaps WHERE metadata LIKE '%voc_signal%'"
    ).fetchall()
    conn.close()

    assert len(gaps) >= 1
    meta = json.loads(gaps[0]["metadata"])
    assert meta["gap_type"] == "voc_signal"
    assert meta["category"] == "frustrated_by"


# ─── test 5: empty upload_dir returns {documents_processed:0} ─────────────────

def test_engine_empty_upload_dir_returns_zero(icdev_db, tmp_path, monkeypatch):
    """VOCEngine.run() returns documents_processed=0 and raises no exception on empty upload_dir."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    engine = VOCEngine()
    engine._upload_dir = upload_dir

    result = engine.run()

    assert result["documents_processed"] == 0
    assert result["job_statements"] == 0
    assert result["signals_created"] == 0


# ─── test 6: CLI --json output has required keys ──────────────────────────────

def test_cli_json_has_required_keys(tmp_path):
    """CLI --run --json emits valid JSON containing 'documents_processed' and 'signals_created'."""
    db_path = tmp_path / "cli_voc_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS voc_documents (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, source_type TEXT NOT NULL,
            ingested_at TEXT NOT NULL, word_count INTEGER,
            job_statement_count INTEGER, classification TEXT
        );
        CREATE TABLE IF NOT EXISTS voc_job_statements (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, raw_text TEXT NOT NULL,
            job_category TEXT, frequency INTEGER, severity_score REAL,
            strategic_fit_score REAL, composite_score REAL,
            creative_gap_id TEXT, analyzed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS creative_feature_gaps (
            id TEXT PRIMARY KEY, pain_point_id TEXT, feature_name TEXT NOT NULL,
            description TEXT NOT NULL, requested_by_count INTEGER DEFAULT 0,
            competitor_coverage TEXT DEFAULT '{}', gap_score REAL DEFAULT 0.0,
            market_demand REAL DEFAULT 0.0, signal_ids TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'identified', metadata TEXT DEFAULT '{}',
            discovered_at TEXT NOT NULL, classification TEXT DEFAULT 'CUI'
        );
    """)
    conn.close()

    env = {
        **os.environ,
        "ICDEV_DB_PATH": str(db_path),
        "ICDEV_STORAGE_BACKEND": "sqlite",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "tools.voc.voc_engine", "--run", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_PROJECT_ROOT),
    )
    assert proc.returncode == 0, f"CLI exited {proc.returncode}:\n{proc.stderr}"
    data = json.loads(proc.stdout)
    assert "documents_processed" in data
    assert "signals_created" in data
