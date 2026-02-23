# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.mosa.icd_generator -- ICD generation for MOSA compliance."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tools.mosa.icd_generator import (
    CUI_BANNER,
    _build_icd_content,
    _discover_interfaces,
    _ensure_table,
    generate_icd,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """Create an in-memory-style SQLite DB with projects + audit_trail tables."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'webapp',
            classification TEXT DEFAULT 'CUI',
            status TEXT DEFAULT 'active',
            directory_path TEXT DEFAULT '/tmp',
            impact_level TEXT DEFAULT 'IL5',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT,
            actor TEXT,
            action TEXT,
            details TEXT,
            affected_files TEXT,
            classification TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "INSERT INTO projects (id, name, type, directory_path) VALUES (?, ?, ?, ?)",
        ("proj-1", "TestApp", "webapp", str(tmp_path)),
    )
    conn.commit()
    return db_path, conn


def _sample_spec():
    """Return a minimal OpenAPI 3.x specification dict."""
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Widget API",
            "version": "2.1.0",
            "description": "Manages widgets.",
            "contact": {"name": "Consumer Org"},
        },
        "servers": [{"url": "https://api.example.mil/v1"}],
        "paths": {
            "/widgets": {
                "get": {"summary": "List widgets", "operationId": "listWidgets"},
                "post": {"summary": "Create widget"},
            },
            "/widgets/{id}": {
                "delete": {"operationId": "deleteWidget"},
            },
        },
    }


def _default_config():
    return {
        "allowed_protocols": ["REST", "gRPC", "AMQP"],
        "allowed_data_formats": ["JSON", "XML", "Protobuf"],
    }


# ---------------------------------------------------------------------------
# TestDiscoverInterfaces
# ---------------------------------------------------------------------------

class TestDiscoverInterfaces:
    """_discover_interfaces: scanning project directory for OpenAPI specs."""

    def test_empty_directory_returns_empty(self, tmp_path):
        result = _discover_interfaces(str(tmp_path))
        assert result == []

    def test_none_directory_returns_empty(self):
        result = _discover_interfaces(None)
        assert result == []

    def test_nonexistent_directory_returns_empty(self):
        result = _discover_interfaces("/nonexistent/path/xyz")
        assert result == []

    def test_discovers_json_spec(self, tmp_path):
        spec = _sample_spec()
        spec_file = tmp_path / "openapi.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        result = _discover_interfaces(str(tmp_path))
        assert len(result) == 1
        assert result[0]["name"] == "Widget API"
        assert result[0]["spec"]["openapi"] == "3.0.3"

    def test_discovers_nested_spec(self, tmp_path):
        sub = tmp_path / "services" / "auth"
        sub.mkdir(parents=True)
        spec = {"info": {"title": "Auth Service"}}
        (sub / "openapi.json").write_text(json.dumps(spec), encoding="utf-8")
        result = _discover_interfaces(str(tmp_path))
        assert len(result) >= 1
        names = [r["name"] for r in result]
        assert "Auth Service" in names

    def test_deduplicates_same_file(self, tmp_path):
        spec = {"info": {"title": "Dedup Test"}}
        (tmp_path / "openapi.json").write_text(json.dumps(spec), encoding="utf-8")
        result = _discover_interfaces(str(tmp_path))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestBuildICDContent
# ---------------------------------------------------------------------------

class TestBuildICDContent:
    """_build_icd_content: generating markdown ICD from interface + project."""

    def test_basic_content_fields(self):
        ifc = {"name": "Widget API", "spec": _sample_spec(), "path": "/tmp/openapi.json"}
        project = {"name": "TestApp"}
        result = _build_icd_content(ifc, project, _default_config())
        assert result["name"] == "Widget API"
        assert result["version"] == "2.1.0"
        assert result["protocol"] == "REST"
        assert result["data_format"] == "JSON"
        assert result["source_system"] == "TestApp"
        assert result["target_system"] == "Consumer Org"

    def test_content_has_cui_banner(self):
        ifc = {"name": "X", "spec": _sample_spec(), "path": ""}
        result = _build_icd_content(ifc, {"name": "P"}, _default_config())
        assert "CUI // SP-CTI" in result["content"]

    def test_endpoint_table_rendered(self):
        ifc = {"name": "X", "spec": _sample_spec(), "path": ""}
        result = _build_icd_content(ifc, {"name": "P"}, _default_config())
        assert "GET" in result["content"]
        assert "/widgets" in result["content"]
        assert "List widgets" in result["content"]

    def test_grpc_protocol_detection(self):
        spec = _sample_spec()
        spec["servers"] = [{"url": "grpc://services.mil:443"}]
        ifc = {"name": "gRPC Svc", "spec": spec, "path": ""}
        result = _build_icd_content(ifc, {"name": "P"}, _default_config())
        assert result["protocol"] == "gRPC"
        assert result["data_format"] == "Protobuf"

    def test_no_endpoints_message(self):
        spec = {"info": {"title": "Empty"}, "paths": {}}
        ifc = {"name": "Empty", "spec": spec, "path": ""}
        result = _build_icd_content(ifc, {"name": "P"}, _default_config())
        assert "No endpoints discovered" in result["content"]

    def test_missing_info_uses_defaults(self):
        ifc = {"name": "Fallback", "spec": {}, "path": ""}
        result = _build_icd_content(ifc, {"name": "Proj"}, _default_config())
        assert result["name"] == "Fallback"
        assert result["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# TestGenerateICD
# ---------------------------------------------------------------------------

class TestGenerateICD:
    """generate_icd: end-to-end ICD generation + DB persist."""

    def test_creates_file_and_db_row(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        out_dir = tmp_path / "out"
        ifc = {"name": "Svc", "spec": _sample_spec(), "path": "/tmp/spec.json"}
        result = generate_icd(conn, "proj-1", ifc, str(out_dir), _default_config())
        assert result["id"].startswith("icd-")
        assert result["status"] == "draft"
        assert result["approval_status"] == "pending"
        assert Path(result["file_path"]).exists()
        row = conn.execute("SELECT * FROM icd_documents WHERE id = ?",
                           (result["id"],)).fetchone()
        assert row is not None
        assert dict(row)["interface_name"] == "Widget API"
        conn.close()

    def test_audit_trail_logged(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        out_dir = tmp_path / "audit_out"
        ifc = {"name": "AuditSvc", "spec": _sample_spec(), "path": ""}
        generate_icd(conn, "proj-1", ifc, str(out_dir), _default_config())
        rows = conn.execute("SELECT * FROM audit_trail WHERE project_id = ?",
                            ("proj-1",)).fetchall()
        assert len(rows) >= 1
        assert "ICD generated" in dict(rows[0])["action"]
        conn.close()


# ---------------------------------------------------------------------------
# TestCuiBanners
# ---------------------------------------------------------------------------

class TestCuiBanners:
    """CUI banner and classification marking presence."""

    def test_cui_banner_constant_format(self):
        assert "CUI // SP-CTI" in CUI_BANNER
        assert "Controlled by: Department of Defense" in CUI_BANNER

    def test_generated_file_starts_with_cui(self, tmp_path):
        db_path, conn = _make_db(tmp_path)
        out_dir = tmp_path / "cui_out"
        ifc = {"name": "CuiSvc", "spec": _sample_spec(), "path": ""}
        result = generate_icd(conn, "proj-1", ifc, str(out_dir), _default_config())
        content = Path(result["file_path"]).read_text(encoding="utf-8")
        assert content.startswith("CUI // SP-CTI")
        conn.close()


# ---------------------------------------------------------------------------
# TestCLI
# ---------------------------------------------------------------------------

class TestCLI:
    """CLI argument handling in main()."""

    def test_requires_project_id(self):
        with pytest.raises(SystemExit):
            from tools.mosa.icd_generator import main
            with patch("sys.argv", ["icd_generator.py", "--all"]):
                main()

    def test_requires_interface_id_or_all(self):
        with pytest.raises(SystemExit):
            from tools.mosa.icd_generator import main
            with patch("sys.argv", ["icd_generator.py", "--project-id", "proj-1"]):
                main()


# [TEMPLATE: CUI // SP-CTI]
