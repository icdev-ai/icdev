#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for PROV-AGENT provenance recorder (D287)."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.observability.provenance.prov_recorder import ProvRecorder


def _create_test_db(db_path: Path) -> None:
    """Create minimal DB with provenance tables."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS prov_entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            label TEXT,
            content_hash TEXT,
            content TEXT,
            attributes TEXT,
            trace_id TEXT,
            span_id TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS prov_activities (
            id TEXT PRIMARY KEY,
            activity_type TEXT NOT NULL,
            label TEXT,
            start_time TEXT,
            end_time TEXT,
            attributes TEXT,
            trace_id TEXT,
            span_id TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS prov_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relation_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            attributes TEXT,
            trace_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


class TestProvRecorderInit(unittest.TestCase):
    """Test ProvRecorder initialization."""

    def test_default_init(self):
        recorder = ProvRecorder()
        self.assertIsNotNone(recorder)

    def test_custom_db_path(self):
        recorder = ProvRecorder(db_path=Path("/tmp/test.db"))
        self.assertEqual(recorder._db_path, Path("/tmp/test.db"))

    def test_agent_and_project_ids(self):
        recorder = ProvRecorder(agent_id="builder", project_id="proj-1")
        self.assertEqual(recorder._agent_id, "builder")
        self.assertEqual(recorder._project_id, "proj-1")

    def test_classification_default(self):
        recorder = ProvRecorder()
        self.assertEqual(recorder._classification, "CUI")

    def test_classification_custom(self):
        recorder = ProvRecorder(classification="SECRET")
        self.assertEqual(recorder._classification, "SECRET")


class TestRecordEntity(unittest.TestCase):
    """Test entity recording."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _create_test_db(self.db_path)
        self.recorder = ProvRecorder(
            db_path=self.db_path,
            agent_id="test-agent",
            project_id="proj-test",
        )

    def test_record_entity_returns_id(self):
        eid = self.recorder.record_entity("prompt", "User query")
        self.assertIsNotNone(eid)
        self.assertIsInstance(eid, str)

    def test_record_entity_stores_in_db(self):
        eid = self.recorder.record_entity("prompt", "User query")
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT * FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_record_entity_with_content_hash(self):
        eid = self.recorder.record_entity(
            "response", "Model output", content_hash="abc123"
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content_hash FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["content_hash"], "abc123")

    def test_record_entity_auto_hash_from_content(self):
        """When content provided without hash, hash is auto-computed."""
        eid = self.recorder.record_entity(
            "document", "Test doc", content="Hello world"
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content_hash FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row["content_hash"])
        self.assertEqual(len(row["content_hash"]), 64)  # SHA-256 hex

    def test_content_not_stored_by_default(self):
        """Content should NOT be stored when content tracing is disabled."""
        eid = self.recorder.record_entity(
            "prompt", "Test", content="Secret content"
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertIsNone(row["content"])

    @unittest.mock.patch.dict(os.environ, {"ICDEV_CONTENT_TRACING_ENABLED": "true"})
    def test_content_stored_when_tracing_enabled(self):
        """Content stored when ICDEV_CONTENT_TRACING_ENABLED=true."""
        eid = self.recorder.record_entity(
            "prompt", "Test", content="Visible content"
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["content"], "Visible content")

    def test_record_entity_with_trace_id(self):
        eid = self.recorder.record_entity(
            "code", "Generated module",
            trace_id="abc123", span_id="def456",
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT trace_id, span_id FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["trace_id"], "abc123")
        self.assertEqual(row["span_id"], "def456")

    def test_record_entity_with_attributes(self):
        eid = self.recorder.record_entity(
            "artifact", "SBOM",
            attributes={"format": "cyclonedx", "version": "1.5"},
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT attributes FROM prov_entities WHERE id = ?", (eid,)
        ).fetchone()
        conn.close()
        attrs = json.loads(row["attributes"])
        self.assertEqual(attrs["format"], "cyclonedx")

    def test_record_entity_no_db_returns_none(self):
        recorder = ProvRecorder(db_path=Path("/nonexistent/db.db"))
        eid = recorder.record_entity("prompt", "Test")
        self.assertIsNone(eid)


class TestRecordActivity(unittest.TestCase):
    """Test activity recording."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _create_test_db(self.db_path)
        self.recorder = ProvRecorder(
            db_path=self.db_path,
            agent_id="test-agent",
            project_id="proj-test",
        )

    def test_record_activity_returns_id(self):
        aid = self.recorder.record_activity("llm_call", "Claude invocation")
        self.assertIsNotNone(aid)

    def test_record_activity_stores_in_db(self):
        aid = self.recorder.record_activity("tool_invocation", "ssp_generate")
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM prov_activities WHERE id = ?", (aid,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["activity_type"], "tool_invocation")

    def test_record_activity_with_timestamps(self):
        aid = self.recorder.record_activity(
            "decision", "Architecture choice",
            start_time="2025-01-01T00:00:00Z",
            end_time="2025-01-01T00:01:00Z",
        )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT start_time, end_time FROM prov_activities WHERE id = ?", (aid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["start_time"], "2025-01-01T00:00:00Z")

    def test_record_activity_no_db_returns_none(self):
        recorder = ProvRecorder(db_path=Path("/nonexistent/db.db"))
        aid = recorder.record_activity("llm_call", "Test")
        self.assertIsNone(aid)


class TestRecordRelation(unittest.TestCase):
    """Test relation recording."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _create_test_db(self.db_path)
        self.recorder = ProvRecorder(
            db_path=self.db_path,
            project_id="proj-test",
        )

    def test_record_relation_returns_true(self):
        eid = self.recorder.record_entity("prompt", "Q")
        aid = self.recorder.record_activity("llm_call", "A")
        result = self.recorder.record_relation("wasGeneratedBy", eid, aid)
        self.assertTrue(result)

    def test_record_relation_stores_in_db(self):
        eid = self.recorder.record_entity("response", "Answer")
        aid = self.recorder.record_activity("llm_call", "Call")
        self.recorder.record_relation("wasGeneratedBy", eid, aid)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM prov_relations WHERE subject_id = ?", (eid,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["relation_type"], "wasGeneratedBy")

    def test_record_multiple_relations(self):
        e1 = self.recorder.record_entity("prompt", "Input")
        e2 = self.recorder.record_entity("response", "Output")
        a1 = self.recorder.record_activity("llm_call", "Invoke")
        self.recorder.record_relation("used", a1, e1)
        self.recorder.record_relation("wasGeneratedBy", e2, a1)
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM prov_relations").fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    def test_record_relation_no_db_returns_false(self):
        recorder = ProvRecorder(db_path=Path("/nonexistent/db.db"))
        result = recorder.record_relation("used", "a", "b")
        self.assertFalse(result)


class TestGetLineage(unittest.TestCase):
    """Test provenance lineage queries."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _create_test_db(self.db_path)
        self.recorder = ProvRecorder(
            db_path=self.db_path,
            project_id="proj-test",
        )
        # Build a lineage chain: prompt → llm_call → response → review → approved
        self.prompt = self.recorder.record_entity("prompt", "User question")
        self.call = self.recorder.record_activity("llm_call", "Claude call")
        self.response = self.recorder.record_entity("response", "Model output")
        self.review = self.recorder.record_activity("review", "Human review")
        self.approved = self.recorder.record_entity("document", "Approved doc")

        self.recorder.record_relation("used", self.call, self.prompt)
        self.recorder.record_relation("wasGeneratedBy", self.response, self.call)
        self.recorder.record_relation("used", self.review, self.response)
        self.recorder.record_relation("wasGeneratedBy", self.approved, self.review)

    def test_backward_lineage(self):
        """Backward lineage from approved doc traces back through chain."""
        lineage = self.recorder.get_lineage(self.approved, direction="backward")
        self.assertGreater(len(lineage), 0)

    def test_forward_lineage(self):
        """Forward lineage from prompt traces forward."""
        lineage = self.recorder.get_lineage(self.prompt, direction="forward")
        self.assertGreater(len(lineage), 0)

    def test_lineage_max_depth(self):
        """Lineage respects max_depth."""
        lineage = self.recorder.get_lineage(self.approved, max_depth=1)
        self.assertLessEqual(len(lineage), 2)

    def test_lineage_no_db_returns_empty(self):
        recorder = ProvRecorder(db_path=Path("/nonexistent/db.db"))
        lineage = recorder.get_lineage("entity-1")
        self.assertEqual(lineage, [])


class TestExportProvJSON(unittest.TestCase):
    """Test PROV-JSON export."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        _create_test_db(self.db_path)
        self.recorder = ProvRecorder(
            db_path=self.db_path,
            project_id="proj-test",
        )

    def test_export_empty_project(self):
        result = self.recorder.export_prov_json()
        self.assertIn("entity", result)
        self.assertIn("activity", result)
        self.assertIn("relation", result)
        self.assertEqual(len(result["entity"]), 0)

    def test_export_with_data(self):
        eid = self.recorder.record_entity("prompt", "Q", content_hash="abc123")
        aid = self.recorder.record_activity("llm_call", "A")
        self.recorder.record_relation("wasGeneratedBy", eid, aid)

        result = self.recorder.export_prov_json()
        self.assertEqual(len(result["entity"]), 1)
        self.assertEqual(len(result["activity"]), 1)
        self.assertEqual(len(result["relation"]), 1)

    def test_export_prov_json_format(self):
        eid = self.recorder.record_entity("code", "Module", content_hash="def456")
        result = self.recorder.export_prov_json()

        self.assertIn("prefix", result)
        self.assertIn("prov", result["prefix"])
        entity = result["entity"][eid]
        self.assertEqual(entity["prov:type"], "code")
        self.assertEqual(entity["prov:label"], "Module")

    def test_export_no_db_returns_empty(self):
        recorder = ProvRecorder(db_path=Path("/nonexistent/db.db"))
        result = recorder.export_prov_json()
        self.assertEqual(result["entity"], {})


if __name__ == "__main__":
    unittest.main()
