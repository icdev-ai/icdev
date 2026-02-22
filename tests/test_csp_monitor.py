#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for CSP Service Monitor and Changelog Generator.

Covers: tools/cloud/csp_monitor.py, tools/cloud/csp_changelog.py
ADRs: D239, D240, D241
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── PATH SETUP ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── FIXTURES ────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database with innovation_signals table."""
    db_path = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE innovation_signals (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_type TEXT,
            title TEXT,
            description TEXT,
            url TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            community_score REAL DEFAULT 0.0,
            content_hash TEXT UNIQUE,
            discovered_at TEXT,
            status TEXT DEFAULT 'new',
            category TEXT,
            innovation_score REAL,
            score_breakdown TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def tmp_registry(tmp_path):
    """Create a temporary CSP service registry."""
    registry_path = tmp_path / "csp_service_registry.json"
    registry = {
        "_metadata": {
            "version": "1.0.0",
            "last_updated": "2026-02-21T00:00:00Z",
            "updated_by": "test",
        },
        "services": {
            "aws": {
                "s3": {
                    "display_name": "Amazon S3",
                    "category": "storage",
                    "status": "active",
                    "compliance_programs": ["fedramp_high", "hipaa"],
                    "govcloud_available": True,
                    "commercial_available": True,
                    "fips_validated": True,
                    "regions": {
                        "government": ["us-gov-west-1"],
                        "commercial": ["us-east-1"],
                    },
                    "icdev_provider_mapping": "storage",
                },
                "eks": {
                    "display_name": "Amazon EKS",
                    "category": "compute",
                    "status": "active",
                    "compliance_programs": ["fedramp_high"],
                    "govcloud_available": True,
                    "commercial_available": True,
                    "fips_validated": True,
                    "regions": {
                        "government": ["us-gov-west-1"],
                        "commercial": ["us-east-1"],
                    },
                    "icdev_provider_mapping": "compute",
                },
            },
            "azure": {
                "aks": {
                    "display_name": "Azure Kubernetes Service",
                    "category": "compute",
                    "status": "active",
                    "compliance_programs": ["fedramp_high"],
                    "govcloud_available": True,
                    "commercial_available": True,
                    "fips_validated": True,
                    "regions": {
                        "government": ["usgovvirginia"],
                        "commercial": ["eastus"],
                    },
                    "icdev_provider_mapping": "compute",
                },
            },
        },
    }
    with open(registry_path, "w") as f:
        json.dump(registry, f)
    return registry_path


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary CSP monitor config."""
    config_path = tmp_path / "csp_monitor_config.yaml"
    config_content = """
sources:
  aws:
    enabled: true
    scan_interval_hours: 12
    endpoints:
      - name: whats_new
        url: https://aws.amazon.com/about-aws/whats-new
        type: rss
        filter_keywords: [govcloud, fedramp]
  azure:
    enabled: true
    scan_interval_hours: 12
    endpoints:
      - name: updates_feed
        url: https://azure.microsoft.com/en-us/updates/
        type: rss
        filter_keywords: [government]
  gcp:
    enabled: false
    scan_interval_hours: 24
    endpoints: []
  oci:
    enabled: false
    scan_interval_hours: 24
    endpoints: []
  ibm:
    enabled: false
    scan_interval_hours: 24
    endpoints: []

signals:
  source_name: csp_monitor
  category_mapping:
    new_service: infrastructure
    service_deprecation: modernization
    compliance_scope_change: compliance_gap
  community_score_mapping:
    new_service: 0.6
    service_deprecation: 0.8
    compliance_scope_change: 0.9
  government_boost: 1.3
  compliance_boost: 1.5

diff:
  match_by: [service_name, csp]
  dedup_window_days: 30

scheduling:
  daemon_mode: false
  default_scan_interval_hours: 12
  quiet_hours: "02:00-06:00"

registry:
  auto_update: false
  require_review: true

audit:
  log_scans: true
  log_signals: true
"""
    with open(config_path, "w") as f:
        f.write(config_content)
    return config_path


# ── CSP MONITOR TESTS ──────────────────────────────────────────────────

class TestCSPMonitorImport:
    """Test that CSP monitor can be imported."""

    def test_import_module(self):
        from tools.cloud import csp_monitor
        assert hasattr(csp_monitor, "CSPMonitor")

    def test_import_changelog(self):
        from tools.cloud import csp_changelog
        assert hasattr(csp_changelog, "generate_markdown_changelog")


class TestChangeClassification:
    """Test announcement classification logic."""

    def test_classify_deprecation(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("Service X deprecated", "This service is being retired") == "service_deprecation"
        assert _classify_change("End of Life notice", "Service will be decommissioned") == "service_deprecation"

    def test_classify_breaking_change(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("API v2 migration required", "Breaking change") == "api_breaking_change"

    def test_classify_compliance(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("Service now FedRAMP authorized", "In scope for FedRAMP") == "compliance_scope_change"
        assert _classify_change("HIPAA compliance added", "Now HIPAA compliant") == "compliance_scope_change"

    def test_classify_region(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("New region launch", "Now available in us-west-3") == "region_expansion"

    def test_classify_security(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("Security advisory", "CVE-2026-1234 patch") == "security_update"

    def test_classify_pricing(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("Price reduction", "30% cost savings") == "pricing_change"

    def test_classify_new_service_default(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("Amazon CloudFront now supports", "General availability") == "new_service"

    def test_classify_certification(self):
        from tools.cloud.csp_monitor import _classify_change
        assert _classify_change("New SOC report available", "SOC report audit") == "certification_change"


class TestGovernmentDetection:
    """Test government-specific announcement detection."""

    def test_detect_govcloud(self):
        from tools.cloud.csp_monitor import _detect_government
        assert _detect_government("AWS GovCloud update", "New feature") is True

    def test_detect_fedramp(self):
        from tools.cloud.csp_monitor import _detect_government
        assert _detect_government("Service achieves FedRAMP", "Authorization") is True

    def test_detect_azure_government(self):
        from tools.cloud.csp_monitor import _detect_government
        assert _detect_government("Azure Government availability", "") is True

    def test_detect_ic4g(self):
        from tools.cloud.csp_monitor import _detect_government
        assert _detect_government("IBM IC4G update", "") is True

    def test_detect_non_government(self):
        from tools.cloud.csp_monitor import _detect_government
        assert _detect_government("New pricing for S3", "Cost savings") is False


class TestContentHash:
    """Test deduplication hash generation."""

    def test_hash_consistency(self):
        from tools.cloud.csp_monitor import _content_hash
        h1 = _content_hash(["aws", "New S3 feature", "new_service"])
        h2 = _content_hash(["aws", "New S3 feature", "new_service"])
        assert h1 == h2

    def test_hash_different_inputs(self):
        from tools.cloud.csp_monitor import _content_hash
        h1 = _content_hash(["aws", "New S3 feature", "new_service"])
        h2 = _content_hash(["azure", "New Blob feature", "new_service"])
        assert h1 != h2


class TestSignalGeneration:
    """Test converting announcements to innovation signals."""

    def test_basic_signal(self):
        from tools.cloud.csp_monitor import announcements_to_signals
        announcements = [{
            "csp": "aws",
            "title": "New S3 Intelligent Tiering",
            "description": "General availability of S3 Intelligent Tiering",
            "url": "https://aws.amazon.com/about-aws/whats-new/...",
            "endpoint_name": "whats_new",
            "change_type": "new_service",
            "is_government": False,
            "published": "2026-02-20",
        }]
        config = {"signals": {}}
        signals = announcements_to_signals(announcements, config)
        assert len(signals) == 1
        assert signals[0]["source"] == "csp_monitor"
        assert signals[0]["source_type"] == "new_service"
        assert signals[0]["category"] == "infrastructure"
        assert signals[0]["community_score"] == 0.6
        assert "[AWS]" in signals[0]["title"]

    def test_government_boost(self):
        from tools.cloud.csp_monitor import announcements_to_signals
        announcements = [{
            "csp": "aws",
            "title": "GovCloud update",
            "description": "New in GovCloud",
            "url": "",
            "endpoint_name": "govcloud_updates",
            "change_type": "new_service",
            "is_government": True,
            "published": "2026-02-20",
        }]
        config = {"signals": {"government_boost": 1.3}}
        signals = announcements_to_signals(announcements, config)
        assert signals[0]["community_score"] == round(0.6 * 1.3, 4)

    def test_compliance_boost(self):
        from tools.cloud.csp_monitor import announcements_to_signals
        announcements = [{
            "csp": "azure",
            "title": "New FedRAMP authorization",
            "description": "Service now in scope",
            "url": "",
            "endpoint_name": "compliance",
            "change_type": "compliance_scope_change",
            "is_government": False,
            "published": "2026-02-20",
        }]
        config = {"signals": {"compliance_boost": 1.5}}
        signals = announcements_to_signals(announcements, config)
        # 0.9 * 1.5 = 1.35, capped at 1.0
        assert signals[0]["community_score"] == 1.0

    def test_signal_has_required_fields(self):
        from tools.cloud.csp_monitor import announcements_to_signals
        announcements = [{
            "csp": "gcp",
            "title": "Test",
            "description": "Test description",
            "url": "https://example.com",
            "endpoint_name": "test",
            "change_type": "new_service",
            "is_government": False,
            "published": "",
        }]
        signals = announcements_to_signals(announcements, {"signals": {}})
        s = signals[0]
        assert "id" in s and s["id"].startswith("sig-")
        assert "source" in s
        assert "source_type" in s
        assert "title" in s
        assert "description" in s
        assert "content_hash" in s
        assert "discovered_at" in s
        assert "category" in s
        assert "community_score" in s


class TestRegistryDiff:
    """Test registry diffing logic."""

    def test_diff_new_service(self, tmp_registry):
        from tools.cloud.csp_monitor import diff_registry, _load_registry
        registry = _load_registry(tmp_registry)
        signals = [{
            "id": "sig-test1",
            "source": "csp_monitor",
            "source_type": "new_service",
            "title": "[AWS] New Lambda feature",
            "description": "New service",
            "metadata": {"csp": "aws"},
            "content_hash": "abc123",
            "community_score": 0.6,
            "discovered_at": "2026-02-21T00:00:00Z",
            "category": "infrastructure",
        }]
        changes = diff_registry(registry, signals)
        # "Lambda" not in registry, so it should show as new
        assert len(changes) == 1
        assert changes[0]["action"] == "add_to_registry"

    def test_diff_known_service(self, tmp_registry):
        from tools.cloud.csp_monitor import diff_registry, _load_registry
        registry = _load_registry(tmp_registry)
        signals = [{
            "id": "sig-test2",
            "source": "csp_monitor",
            "source_type": "new_service",
            "title": "[AWS] Amazon S3 feature update",
            "description": "New S3 feature",
            "metadata": {"csp": "aws"},
            "content_hash": "def456",
            "community_score": 0.6,
            "discovered_at": "2026-02-21T00:00:00Z",
            "category": "infrastructure",
        }]
        changes = diff_registry(registry, signals)
        # "Amazon S3" is already in registry — should NOT show as new
        assert len(changes) == 0

    def test_diff_deprecation(self, tmp_registry):
        from tools.cloud.csp_monitor import diff_registry, _load_registry
        registry = _load_registry(tmp_registry)
        signals = [{
            "id": "sig-test3",
            "source": "csp_monitor",
            "source_type": "service_deprecation",
            "title": "[AWS] Service deprecated",
            "description": "End of life",
            "metadata": {"csp": "aws"},
            "content_hash": "ghi789",
            "community_score": 0.8,
            "discovered_at": "2026-02-21T00:00:00Z",
            "category": "modernization",
        }]
        changes = diff_registry(registry, signals)
        assert len(changes) == 1
        assert changes[0]["action"] == "mark_deprecated"

    def test_diff_compliance_change(self, tmp_registry):
        from tools.cloud.csp_monitor import diff_registry, _load_registry
        registry = _load_registry(tmp_registry)
        signals = [{
            "id": "sig-test4",
            "source": "csp_monitor",
            "source_type": "compliance_scope_change",
            "title": "[AZURE] New HIPAA scope",
            "description": "Service added to HIPAA",
            "metadata": {"csp": "azure"},
            "content_hash": "jkl012",
            "community_score": 0.9,
            "discovered_at": "2026-02-21T00:00:00Z",
            "category": "compliance_gap",
        }]
        changes = diff_registry(registry, signals)
        assert len(changes) == 1
        assert changes[0]["action"] == "update_compliance"


class TestCSPMonitorClass:
    """Test CSPMonitor class methods."""

    def test_init_with_defaults(self):
        from tools.cloud.csp_monitor import CSPMonitor
        monitor = CSPMonitor()
        assert monitor is not None

    def test_init_with_custom_paths(self, tmp_config, tmp_registry, tmp_db):
        from tools.cloud.csp_monitor import CSPMonitor
        monitor = CSPMonitor(
            config_path=str(tmp_config),
            registry_path=str(tmp_registry),
            db_path=str(tmp_db),
        )
        assert monitor is not None

    def test_get_status_empty_db(self, tmp_config, tmp_registry, tmp_db):
        from tools.cloud.csp_monitor import CSPMonitor
        monitor = CSPMonitor(
            config_path=str(tmp_config),
            registry_path=str(tmp_registry),
            db_path=str(tmp_db),
        )
        result = monitor.get_status()
        assert result["status"] == "ok"
        assert result["total_signals"] == 0
        assert result["last_scan"] is None

    def test_diff_empty_db(self, tmp_config, tmp_registry, tmp_db):
        from tools.cloud.csp_monitor import CSPMonitor
        monitor = CSPMonitor(
            config_path=str(tmp_config),
            registry_path=str(tmp_registry),
            db_path=str(tmp_db),
        )
        result = monitor.diff()
        assert result["status"] == "ok"
        assert result["signals_analyzed"] == 0
        assert result["changes_detected"] == 0

    def test_generate_changelog_empty(self, tmp_config, tmp_registry, tmp_db):
        from tools.cloud.csp_monitor import CSPMonitor
        monitor = CSPMonitor(
            config_path=str(tmp_config),
            registry_path=str(tmp_registry),
            db_path=str(tmp_db),
        )
        result = monitor.generate_changelog(days=30)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0


class TestSignalStorage:
    """Test signal deduplication and storage."""

    def test_store_new_signal(self, tmp_db):
        from tools.cloud.csp_monitor import _get_db, _store_signal
        conn = _get_db(tmp_db)
        signal = {
            "id": "sig-test001",
            "source": "csp_monitor",
            "source_type": "new_service",
            "title": "Test signal",
            "description": "Test description",
            "url": "",
            "metadata": {"csp": "aws"},
            "community_score": 0.5,
            "content_hash": "unique_hash_1",
            "discovered_at": "2026-02-21T00:00:00Z",
            "category": "infrastructure",
        }
        result = _store_signal(conn, signal)
        conn.commit()
        assert result is True

        # Verify stored
        row = conn.execute("SELECT * FROM innovation_signals WHERE id = ?",
                           ("sig-test001",)).fetchone()
        conn.close()
        assert row is not None
        assert row["source"] == "csp_monitor"

    def test_dedup_same_hash(self, tmp_db):
        from tools.cloud.csp_monitor import _get_db, _store_signal
        conn = _get_db(tmp_db)
        signal1 = {
            "id": "sig-test002",
            "source": "csp_monitor",
            "source_type": "new_service",
            "title": "Test signal",
            "description": "Test",
            "url": "",
            "metadata": {},
            "community_score": 0.5,
            "content_hash": "duplicate_hash",
            "discovered_at": "2026-02-21T00:00:00Z",
            "category": "infrastructure",
        }
        signal2 = {
            "id": "sig-test003",
            "source": "csp_monitor",
            "source_type": "new_service",
            "title": "Same signal again",
            "description": "Test",
            "url": "",
            "metadata": {},
            "community_score": 0.5,
            "content_hash": "duplicate_hash",  # same hash
            "discovered_at": "2026-02-21T01:00:00Z",
            "category": "infrastructure",
        }
        r1 = _store_signal(conn, signal1)
        conn.commit()
        r2 = _store_signal(conn, signal2)
        conn.commit()
        assert r1 is True
        assert r2 is False  # duplicate skipped

        count = conn.execute(
            "SELECT COUNT(*) FROM innovation_signals WHERE content_hash = 'duplicate_hash'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


# ── CHANGELOG TESTS ─────────────────────────────────────────────────────

class TestChangelogGenerator:
    """Test changelog generation."""

    def test_generate_markdown_empty(self):
        from tools.cloud.csp_changelog import generate_markdown_changelog
        md = generate_markdown_changelog([], days=30)
        assert "No CSP changes detected" in md

    def test_generate_markdown_with_entries(self):
        from tools.cloud.csp_changelog import generate_markdown_changelog
        entries = [
            {
                "id": "sig-1",
                "date": "2026-02-20T12:00:00Z",
                "csp": "aws",
                "change_type": "new_service",
                "title": "[AWS] New Lambda feature",
                "description": "General availability of new feature",
                "url": "https://example.com",
                "score": 0.6,
                "status": "new",
                "category": "infrastructure",
                "is_government": False,
            },
            {
                "id": "sig-2",
                "date": "2026-02-19T12:00:00Z",
                "csp": "azure",
                "change_type": "compliance_scope_change",
                "title": "[AZURE] FedRAMP update",
                "description": "Service added to FedRAMP scope",
                "url": "",
                "score": 0.9,
                "status": "new",
                "category": "compliance_gap",
                "is_government": True,
            },
        ]
        md = generate_markdown_changelog(entries, days=30)
        assert "## AWS" in md
        assert "## AZURE" in md
        assert "CRITICAL" in md.upper() or "critical" in md.lower()
        assert "Recommended Action" in md

    def test_generate_markdown_no_recommendations(self):
        from tools.cloud.csp_changelog import generate_markdown_changelog
        entries = [{
            "id": "sig-1",
            "date": "2026-02-20T12:00:00Z",
            "csp": "aws",
            "change_type": "new_service",
            "title": "Test",
            "description": "Test",
            "url": "",
            "score": 0.6,
            "status": "new",
            "category": "infrastructure",
            "is_government": False,
        }]
        md = generate_markdown_changelog(entries, days=30, include_recommendations=False)
        assert "Recommended Action" not in md

    def test_generate_summary(self):
        from tools.cloud.csp_changelog import generate_summary
        entries = [
            {"csp": "aws", "change_type": "new_service", "is_government": False},
            {"csp": "aws", "change_type": "service_deprecation", "is_government": True},
            {"csp": "azure", "change_type": "compliance_scope_change", "is_government": True},
        ]
        summary = generate_summary(entries)
        assert summary["total_changes"] == 3
        assert summary["by_csp"]["AWS"] == 2
        assert summary["by_csp"]["AZURE"] == 1
        assert summary["government_changes"] == 2
        assert summary["commercial_changes"] == 1
        assert summary["by_urgency"]["critical"] >= 1  # compliance_scope_change

    def test_recommendations_exist(self):
        from tools.cloud.csp_changelog import RECOMMENDATIONS
        assert "new_service" in RECOMMENDATIONS
        assert "service_deprecation" in RECOMMENDATIONS
        assert "compliance_scope_change" in RECOMMENDATIONS
        assert "api_breaking_change" in RECOMMENDATIONS
        assert "certification_change" in RECOMMENDATIONS
        # Each recommendation has required fields
        for ct, rec in RECOMMENDATIONS.items():
            assert "action" in rec
            assert "details" in rec
            assert "urgency" in rec
            assert "affected_files" in rec


# ── REGISTRY TESTS ──────────────────────────────────────────────────────

class TestRegistryLoader:
    """Test registry loading and saving."""

    def test_load_registry(self, tmp_registry):
        from tools.cloud.csp_monitor import _load_registry
        registry = _load_registry(tmp_registry)
        assert "_metadata" in registry
        assert "services" in registry
        assert "aws" in registry["services"]
        assert "s3" in registry["services"]["aws"]

    def test_load_missing_registry(self, tmp_path):
        from tools.cloud.csp_monitor import _load_registry
        registry = _load_registry(tmp_path / "nonexistent.json")
        assert registry == {}

    def test_save_registry_backup(self, tmp_registry):
        from tools.cloud.csp_monitor import _load_registry, _save_registry
        registry = _load_registry(tmp_registry)
        _save_registry(registry, tmp_registry)

        # Verify backup was created
        backup_files = list(tmp_registry.parent.glob("*.backup-*.json"))
        assert len(backup_files) == 1

        # Verify metadata updated
        with open(tmp_registry) as f:
            updated = json.load(f)
        assert updated["_metadata"]["updated_by"] == "csp_monitor"


class TestRegistryContent:
    """Test the actual CSP service registry file."""

    def test_registry_file_exists(self):
        registry_path = BASE_DIR / "context" / "cloud" / "csp_service_registry.json"
        assert registry_path.exists(), "CSP service registry not found"

    def test_registry_valid_json(self):
        registry_path = BASE_DIR / "context" / "cloud" / "csp_service_registry.json"
        with open(registry_path) as f:
            data = json.load(f)
        assert "_metadata" in data
        assert "services" in data

    def test_registry_has_all_csps(self):
        registry_path = BASE_DIR / "context" / "cloud" / "csp_service_registry.json"
        with open(registry_path) as f:
            data = json.load(f)
        services = data["services"]
        for csp in ["aws", "azure", "gcp", "oci", "ibm"]:
            assert csp in services, f"Missing CSP: {csp}"
            assert len(services[csp]) > 0, f"No services for CSP: {csp}"

    def test_registry_service_schema(self):
        """Verify each service has required fields."""
        registry_path = BASE_DIR / "context" / "cloud" / "csp_service_registry.json"
        with open(registry_path) as f:
            data = json.load(f)

        required_fields = [
            "display_name", "category", "status",
            "compliance_programs", "govcloud_available",
            "commercial_available", "fips_validated",
            "regions", "icdev_provider_mapping",
        ]
        for csp, services in data["services"].items():
            for svc_id, svc in services.items():
                for field in required_fields:
                    assert field in svc, (
                        f"Missing field '{field}' in {csp}/{svc_id}"
                    )

    def test_registry_service_count(self):
        """Verify minimum service count."""
        registry_path = BASE_DIR / "context" / "cloud" / "csp_service_registry.json"
        with open(registry_path) as f:
            data = json.load(f)
        total = sum(len(svc) for svc in data["services"].values())
        assert total >= 40, f"Expected at least 40 services, got {total}"


# ── CONFIG TESTS ────────────────────────────────────────────────────────

class TestConfigLoader:
    """Test config loading."""

    def test_load_config(self, tmp_config):
        from tools.cloud.csp_monitor import _load_config
        config = _load_config(tmp_config)
        assert "sources" in config
        assert "aws" in config["sources"]

    def test_load_missing_config(self, tmp_path):
        from tools.cloud.csp_monitor import _load_config
        config = _load_config(tmp_path / "nonexistent.yaml")
        assert config == {}

    def test_config_file_exists(self):
        config_path = BASE_DIR / "args" / "csp_monitor_config.yaml"
        assert config_path.exists(), "CSP monitor config not found"
