# CUI // SP-CTI
"""Unit test: _uc_load_all(tenant_id) filters user-created UCs by tenant."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS use_case_overrides (
    id TEXT PRIMARY KEY,
    label TEXT, description TEXT, icon TEXT, badge TEXT,
    agent_model TEXT, ricoas INTEGER, boost_threshold INTEGER,
    system_prompt TEXT, seed_message TEXT,
    canvas_wiring TEXT, quick_actions TEXT,
    updated_at TEXT, updated_by TEXT,
    classification TEXT DEFAULT NULL,
    fast_track INTEGER DEFAULT 0,
    skip_requirement_types TEXT,
    user_config TEXT,
    is_user_created INTEGER DEFAULT 0,
    created_by TEXT DEFAULT NULL,
    created_at TEXT DEFAULT NULL,
    template_requirements TEXT DEFAULT NULL,
    category TEXT DEFAULT NULL,
    tenant_id TEXT DEFAULT '',
    canvas_seeds TEXT DEFAULT NULL,
    workflow_steps TEXT DEFAULT NULL
)
"""

_YAML_BASE_UCS = [
    {"id": "base-uc-1", "label": "Base UC 1", "description": "YAML-backed use case 1"},
    {"id": "base-uc-2", "label": "Base UC 2", "description": "YAML-backed use case 2"},
]


@pytest.fixture()
def seeded_db(icdev_db):
    """Seed use_case_overrides with one user-created UC per tenant."""
    conn = sqlite3.connect(str(icdev_db))
    conn.execute(_CREATE_TABLE_SQL)
    conn.execute(
        "INSERT INTO use_case_overrides (id, label, is_user_created, tenant_id) VALUES (?, ?, 1, ?)",
        ("uc-tenant-a", "Tenant A Use Case", "tenant-a"),
    )
    conn.execute(
        "INSERT INTO use_case_overrides (id, label, is_user_created, tenant_id) VALUES (?, ?, 1, ?)",
        ("uc-tenant-b", "Tenant B Use Case", "tenant-b"),
    )
    conn.commit()
    conn.close()
    return icdev_db


class TestUcLoadAllTenantFilter:
    def _run(self, seeded_db, monkeypatch, tenant_id):
        monkeypatch.setenv("ICDEV_DB_PATH", str(seeded_db))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        from tools.dashboard.api.chat import _uc_load_all
        with patch("tools.dashboard.api.chat._uc_load_yaml", return_value=list(_YAML_BASE_UCS)):
            return _uc_load_all(tenant_id)

    def test_tenant_a_uc_in_result(self, seeded_db, monkeypatch):
        result = self._run(seeded_db, monkeypatch, "tenant-a")
        ids = [uc["id"] for uc in result]
        assert "uc-tenant-a" in ids

    def test_tenant_b_uc_not_in_result(self, seeded_db, monkeypatch):
        result = self._run(seeded_db, monkeypatch, "tenant-a")
        ids = [uc["id"] for uc in result]
        assert "uc-tenant-b" not in ids

    def test_yaml_base_ucs_always_present(self, seeded_db, monkeypatch):
        """YAML-backed UCs appear regardless of which tenant is queried."""
        monkeypatch.setenv("ICDEV_DB_PATH", str(seeded_db))
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        from tools.dashboard.api.chat import _uc_load_all
        with patch("tools.dashboard.api.chat._uc_load_yaml", return_value=list(_YAML_BASE_UCS)):
            result_a = _uc_load_all("tenant-a")
            result_b = _uc_load_all("tenant-b")
        ids_a = [uc["id"] for uc in result_a]
        ids_b = [uc["id"] for uc in result_b]
        for base in _YAML_BASE_UCS:
            assert base["id"] in ids_a, f"{base['id']} missing from tenant-a results"
            assert base["id"] in ids_b, f"{base['id']} missing from tenant-b results"
