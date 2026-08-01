# CUI // SP-CTI
"""Unit tests for document ingestion, section router, and report generator."""
from __future__ import annotations

import os
import sqlite3
from unittest.mock import patch

import pytest

# ── Minimal schema fixture ─────────────────────────────────────────────────────

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_templates (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, canvas_type TEXT,
    stages_json TEXT NOT NULL DEFAULT '[]', roles_json TEXT NOT NULL DEFAULT '{}',
    approval_policy TEXT NOT NULL DEFAULT 'any_one', kickback_limit INTEGER NOT NULL DEFAULT 3,
    is_default INTEGER NOT NULL DEFAULT 0, is_system INTEGER NOT NULL DEFAULT 0,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS wf_instances (
    id TEXT PRIMARY KEY, template_id TEXT NOT NULL,
    task_id TEXT, project_id TEXT, canvas_type TEXT,
    current_stage TEXT NOT NULL DEFAULT 'build',
    kickback_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS wf_document_templates (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, doc_type TEXT NOT NULL,
    schema_json TEXT NOT NULL DEFAULT '{}', canvas_type TEXT, stage_scope TEXT,
    is_ai_reference INTEGER NOT NULL DEFAULT 0,
    is_human_required INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL DEFAULT '1', is_system INTEGER NOT NULL DEFAULT 0,
    created_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS wf_ingested_files (
    id TEXT PRIMARY KEY, doc_template_id TEXT NOT NULL, filename TEXT NOT NULL,
    file_type TEXT NOT NULL, file_size_bytes INTEGER, content_hash TEXT NOT NULL,
    storage_path TEXT, page_count INTEGER, section_count INTEGER,
    ingestion_status TEXT NOT NULL DEFAULT 'pending', error_message TEXT,
    chunk_count INTEGER DEFAULT 0, ingested_by TEXT,
    ingested_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS wf_report_section_defs (
    id TEXT PRIMARY KEY, report_type TEXT NOT NULL, section_key TEXT NOT NULL,
    section_name TEXT NOT NULL, description TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0, required INTEGER NOT NULL DEFAULT 1,
    source_hints TEXT, min_chunks INTEGER DEFAULT 1,
    max_chunks INTEGER DEFAULT 8, max_words INTEGER DEFAULT 800,
    UNIQUE(report_type, section_key)
);
CREATE TABLE IF NOT EXISTS wf_generated_reports (
    id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, report_type TEXT NOT NULL,
    style_guide_id TEXT, status TEXT NOT NULL DEFAULT 'pending',
    content_html TEXT, content_json TEXT, word_count INTEGER,
    section_count INTEGER, citation_count INTEGER, error_message TEXT,
    generated_by TEXT, generated_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS wf_report_section_chunks (
    id TEXT PRIMARY KEY, report_id TEXT NOT NULL, section_key TEXT NOT NULL,
    chunk_id TEXT NOT NULL, relevance_score REAL, rank INTEGER NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY, content TEXT, content_hash TEXT, embedding TEXT,
    source_type TEXT, source_id TEXT, source_table TEXT,
    chunk_index INTEGER, total_chunks INTEGER, metadata TEXT,
    tier TEXT, classification TEXT, tenant_id TEXT, project_id TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS wf_citations (
    id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, stage TEXT NOT NULL,
    source_doc TEXT NOT NULL, source_type TEXT, doc_version TEXT,
    section TEXT, page_number INTEGER, excerpt TEXT, cited_by TEXT NOT NULL,
    cited_in_type TEXT NOT NULL, cited_in_id TEXT NOT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS wg_style_guides (
    id TEXT PRIMARY KEY, scope TEXT, scope_id TEXT, version TEXT,
    guide_name TEXT, guide_yaml TEXT, inherits_from TEXT,
    created_by TEXT, created_at TEXT, is_active INTEGER DEFAULT 1, classification TEXT
);
"""


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "test_report.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(EXTRA_SCHEMA)
    # Seed report section defs
    for row in [
        ("s1", "standard_audit", "exec_summary", "Executive Summary",
         "High-level overview of audit scope and key findings.", 1, 1, '["standard"]', 1, 5, 400),
        ("s2", "standard_audit", "findings", "Findings",
         "Detailed audit findings including observed gaps.", 2, 1, '["sop_reference"]', 1, 8, 800),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO wf_report_section_defs VALUES (?,?,?,?,?,?,?,?,?,?,?)", row
        )
    # Seed instance + template
    conn.execute(
        "INSERT INTO wf_templates (id, name, stages_json, roles_json) VALUES ('t1','T','[]','{}')"
    )
    conn.execute(
        "INSERT INTO wf_instances (id, template_id, canvas_type) VALUES ('inst-001','t1','NDC')"
    )
    # Seed doc template
    conn.execute(
        "INSERT INTO wf_document_templates (id, name, doc_type, schema_json, is_ai_reference) VALUES "
        "('dt-001','NDC Naming Standard','standard','{}',1)"
    )
    conn.commit()
    conn.close()

    def _get():
        # StorageConnection, NOT a bare sqlite3 connection. The modules under
        # test author PostgreSQL-dialect SQL (`%s` placeholders) -- correct,
        # since PG is the primary backend -- which sqlite3 cannot parse. A raw
        # connection made `get_sections` raise `near "%": syntax error`, so the
        # test failed on the fixture rather than on the behaviour it asserts.
        from tools.db.storage import StorageConnection

        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return StorageConnection(c, "sqlite")

    # Patch both the canonical location and the already-bound module references
    # (blueprint.py imports report_generator at module level, so the binding
    #  exists in the module namespace even before the fixture runs)
    _targets = [
        "tools.db.storage.get_connection",
        "tools.workflow_hitl.report_schema.get_connection",
        "tools.workflow_hitl.document_ingestion.get_connection",
        "tools.workflow_hitl.report_generator.get_connection",
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for t in _targets:
            try:
                stack.enter_context(patch(t, side_effect=_get))
            except AttributeError:
                pass
        yield db_path


# ── report_schema ─────────────────────────────────────────────────────────────

class TestReportSchema:
    def test_get_sections_returns_seeded(self, tmp_db):
        from tools.workflow_hitl.report_schema import get_sections
        sections = get_sections("standard_audit")
        assert len(sections) == 2
        keys = [s.key for s in sections]
        assert "exec_summary" in keys
        assert "findings" in keys

    def test_get_sections_empty_for_unknown(self, tmp_db):
        from tools.workflow_hitl.report_schema import get_sections
        sections = get_sections("nonexistent_type")
        assert sections == []

    def test_list_report_types(self, tmp_db):
        from tools.workflow_hitl.report_schema import list_report_types
        types = list_report_types()
        assert any(t["report_type"] == "standard_audit" for t in types)

    def test_create_custom_report_type(self, tmp_db):
        from tools.workflow_hitl.report_schema import create_custom_report_type, get_sections
        count = create_custom_report_type("my_custom", [
            {"section_key": "intro", "section_name": "Introduction", "description": "Intro."},
            {"section_key": "body",  "section_name": "Body",         "description": "Main content."},
        ])
        assert count == 2
        sections = get_sections("my_custom")
        assert len(sections) == 2


# ── document_ingestion ────────────────────────────────────────────────────────

class TestDocumentIngestion:
    def _seed_doc_template(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT OR IGNORE INTO wf_document_templates (id, name, doc_type, schema_json) "
            "VALUES ('dt-002','Test SOP','sop_reference','{}')"
        )
        conn.commit()
        conn.close()

    def test_ingest_markdown_file(self, tmp_db, tmp_path):
        self._seed_doc_template(tmp_db)
        md_file = tmp_path / "test_sop.md"
        md_file.write_text("# Introduction\n\nThis is the intro.\n\n## Section 2\n\nBody text here.")

        # Patch chunk storage to avoid needing rag/ chunker
        with patch("tools.workflow_hitl.document_ingestion._store_chunks", return_value=5):
            ingest_id = __import__("tools.workflow_hitl.document_ingestion",
                                   fromlist=["ingest_file"]).ingest_file(
                doc_template_id="dt-002",
                file_path=str(md_file),
                filename="test_sop.md",
                ingested_by="test-user",
            )
        assert ingest_id.startswith("wfi-file-")

    def test_ingest_dedup_same_hash(self, tmp_db, tmp_path):
        self._seed_doc_template(tmp_db)
        md_file = tmp_path / "dup.md"
        md_file.write_text("# Unique Content\n\nSome text.")
        mod = __import__("tools.workflow_hitl.document_ingestion", fromlist=["ingest_file"])
        with patch("tools.workflow_hitl.document_ingestion._store_chunks", return_value=2):
            id1 = mod.ingest_file("dt-002", str(md_file), "dup.md", "user")
        # Second call: should detect completed file and return same id
        with patch("tools.workflow_hitl.document_ingestion._store_chunks", return_value=2):
            id2 = mod.ingest_file("dt-002", str(md_file), "dup.md", "user")
        assert id1 == id2

    def test_ingest_file_not_found(self, tmp_db):
        mod = __import__("tools.workflow_hitl.document_ingestion", fromlist=["ingest_file"])
        with pytest.raises(FileNotFoundError):
            mod.ingest_file("dt-002", "/nonexistent/path/file.pdf", ingested_by="user")

    def test_extract_markdown_sections(self, tmp_path):
        from tools.workflow_hitl.document_ingestion import _extract_text_file
        md = tmp_path / "t.md"
        md.write_text("# Intro\n\nParagraph one.\n\n## Section Two\n\nParagraph two.")
        pages = _extract_text_file(md)
        headings = [p["section_heading"] for p in pages if p["is_section_heading"]]
        assert "Intro" in headings
        assert "Section Two" in headings

    def test_extract_html_sections(self, tmp_path):
        from tools.workflow_hitl.document_ingestion import _extract_html
        html = tmp_path / "t.html"
        html.write_text("<html><body><h1>Title</h1><p>Para 1</p><h2>Sub</h2><p>Para 2</p></body></html>")
        pages = _extract_html(html)
        headings = [p["section_heading"] for p in pages if p["is_section_heading"]]
        assert "Title" in headings
        assert "Sub" in headings

    def test_get_ingested_files(self, tmp_db, tmp_path):
        self._seed_doc_template(tmp_db)
        md = tmp_path / "g.md"
        md.write_text("# Hello\n\nWorld.")
        mod = __import__("tools.workflow_hitl.document_ingestion",
                         fromlist=["ingest_file", "get_ingested_files"])
        with patch("tools.workflow_hitl.document_ingestion._store_chunks", return_value=1):
            mod.ingest_file("dt-002", str(md), "g.md", "user")
        files = mod.get_ingested_files("dt-002")
        assert len(files) >= 1
        assert files[0]["filename"] == "g.md"


# ── section_router ────────────────────────────────────────────────────────────

class TestSectionRouter:
    def test_route_falls_back_to_text_search(self, tmp_db, tmp_path):
        """With no Retriever, section router uses text search fallback."""
        # Seed rag_chunks with wf_doc content
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "INSERT INTO rag_chunks (id, content, source_type, source_id, chunk_index, "
            "total_chunks, metadata, created_at, updated_at) "
            "VALUES ('c1','Executive overview of audit findings','wf_doc','ing-001',0,1,"
            "'{\"wf_document_template_id\":\"dt-001\"}','2026-01-01','2026-01-01')"
        )
        conn.commit()
        conn.close()

        from tools.workflow_hitl.section_router import SectionRouter
        from tools.workflow_hitl.report_schema import get_sections

        sections = get_sections("standard_audit")
        router = SectionRouter()
        # Force fallback by making Retriever unavailable
        with patch.dict("sys.modules", {"tools.rag.retriever": None}):
            results = router.route(sections, applicable_template_ids=["dt-001"])
        assert isinstance(results, list)

    def test_deduplication_assigns_chunk_to_best_section(self, tmp_db):
        from tools.workflow_hitl.section_router import SectionRouter, RoutedChunk
        from tools.workflow_hitl.report_schema import ReportSection

        sections = [
            ReportSection(key="sec_a", name="A", description="desc a", sort_order=1, max_chunks=3),
            ReportSection(key="sec_b", name="B", description="desc b", sort_order=2, max_chunks=3),
        ]
        chunk = RoutedChunk(chunk_id="c-shared", content="shared content",
                            relevance_score=0.9, rank=0)
        chunk_b = RoutedChunk(chunk_id="c-shared", content="shared content",
                              relevance_score=0.6, rank=0)
        candidates = {
            "sec_a": [chunk],
            "sec_b": [chunk_b],
        }
        router = SectionRouter()
        results = router._deduplicate(sections, candidates)
        # Chunk should be assigned only once (to sec_a which has higher score)
        all_assigned = [(r.section_key, c.chunk_id) for r in results for c in r.chunks]
        assigned_to_a = [a for a in all_assigned if a[0] == "sec_a" and a[1] == "c-shared"]
        assigned_to_b = [a for a in all_assigned if a[0] == "sec_b" and a[1] == "c-shared"]
        assert len(assigned_to_a) == 1
        assert len(assigned_to_b) == 0


# ── report_generator ──────────────────────────────────────────────────────────

class TestReportGenerator:
    def test_generate_report_no_llm(self, tmp_db):
        os.environ["ICDEV_NO_LLM"] = "true"
        try:
            with patch("tools.workflow_hitl.section_router.SectionRouter.route", return_value=[]):
                report_id = __import__(
                    "tools.workflow_hitl.report_generator", fromlist=["generate_report"]
                ).generate_report(
                    instance_id="inst-001",
                    report_type="standard_audit",
                    generated_by="test-user",
                )
            assert report_id.startswith("wfr-")
            from tools.workflow_hitl.report_generator import get_report
            report = get_report(report_id)
            assert report is not None
            assert report["status"] == "completed"
        finally:
            del os.environ["ICDEV_NO_LLM"]

    def test_get_report_not_found(self, tmp_db):
        from tools.workflow_hitl.report_generator import get_report
        assert get_report("wfr-nonexistent") is None

    def test_list_reports_by_instance(self, tmp_db):
        os.environ["ICDEV_NO_LLM"] = "true"
        try:
            with patch("tools.workflow_hitl.section_router.SectionRouter.route", return_value=[]):
                from tools.workflow_hitl.report_generator import generate_report, list_reports
                generate_report("inst-001", "standard_audit", generated_by="u1")
                reports = list_reports(instance_id="inst-001")
            assert len(reports) >= 1
        finally:
            del os.environ["ICDEV_NO_LLM"]

    def test_generate_report_html_output(self, tmp_db):
        """Generated report HTML should contain classification banner."""
        os.environ["ICDEV_NO_LLM"] = "true"
        try:
            with patch("tools.workflow_hitl.section_router.SectionRouter.route", return_value=[]):
                from tools.workflow_hitl.report_generator import generate_report, get_report
                rid = generate_report("inst-001", "standard_audit", generated_by="u1")
                report = get_report(rid)
            html = report.get("content_html", "")
            assert "CUI" in html or "audit" in html.lower()
        finally:
            del os.environ["ICDEV_NO_LLM"]

    def test_assemble_section_no_llm(self, tmp_db):
        from tools.workflow_hitl.report_generator import _assemble_section_no_llm
        from tools.workflow_hitl.report_schema import ReportSection
        from tools.workflow_hitl.section_router import RoutedChunk
        section = ReportSection(key="test", name="Test Section", description="desc",
                                sort_order=1, max_words=400)
        chunks = [
            RoutedChunk(chunk_id="c1", content="Chunk content one.", relevance_score=0.9, rank=1,
                        section_heading="Intro", filename="sop.pdf"),
        ]
        citations = [{"inline_ref": "[1]", "chunk_id": "c1", "source_doc": "sop.pdf",
                      "section": "Intro", "page_number": 3, "excerpt": "Chunk content one."}]
        prose = _assemble_section_no_llm(section, chunks, citations, {})
        assert "[1]" in prose
        assert "Chunk content one." in prose
