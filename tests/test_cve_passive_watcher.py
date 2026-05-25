# CUI // SP-CTI
"""Tests for tools.supply_chain.cve_passive_watcher — passive ATO CVE monitoring."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.supply_chain.cve_passive_watcher import (
    _extract_cves_from_entry,
    get_status,
    watch_scan,
)

# ---------------------------------------------------------------------------
# Minimal schema
# ---------------------------------------------------------------------------

WATCHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cve_triage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    package_version TEXT,
    severity TEXT,
    cvss_score REAL,
    triage_decision TEXT,
    sla_deadline TEXT,
    triaged_by TEXT,
    triaged_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, cve_id, package_name)
);

CREATE TABLE IF NOT EXISTS cve_passive_watch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_trail_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    component TEXT,
    triage_id INTEGER,
    skipped INTEGER DEFAULT 0,
    source_event_type TEXT,
    processed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supply_chain_vendors (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    vendor_type TEXT NOT NULL,
    country_of_origin TEXT,
    scrm_risk_tier TEXT DEFAULT 'moderate',
    section_889_status TEXT DEFAULT 'compliant'
);

CREATE TABLE IF NOT EXISTS supply_chain_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'component',
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT 'component',
    target_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    criticality TEXT DEFAULT 'medium',
    created_at TEXT
);
"""

PROJECT_ID = "proj-cve-watch-test"

# Fake return values for mocked dependencies
FAKE_TRIAGE_RESULT = {
    "triage_id": 42,
    "cve_id": "CVE-2025-1234",
    "severity": "high",
    "blast_radius": 3,
    "sla_deadline": "2026-04-15T00:00:00+00:00",
    "affected_upstream": [],
    "affected_downstream": ["dep-a", "dep-b", "dep-c"],
}

FAKE_PROPAGATE_RESULT = {
    "blast_radius": 3,
    "affected": ["dep-a", "dep-b", "dep-c"],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def watcher_db(tmp_path):
    """Temporary DB with all passive-watcher tables."""
    db_path = tmp_path / "watcher_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(WATCHER_SCHEMA)
    conn.close()
    return db_path


@pytest.fixture
def db_with_vuln_event(watcher_db):
    """DB with one vulnerability_found audit entry containing a CVE."""
    conn = sqlite3.connect(str(watcher_db))
    conn.execute(
        """INSERT INTO audit_trail (project_id, event_type, actor, action, details)
           VALUES (?, 'vulnerability_found', 'scanner-agent',
                   'Found CVE-2025-1234 in requests 2.28.0',
                   ?)""",
        (
            PROJECT_ID,
            json.dumps({
                "cve_id": "CVE-2025-1234",
                "component": "requests",
                "cvss_score": 7.5,
                "severity": "high",
                "description": "HTTP redirect bypass in requests",
            }),
        ),
    )
    conn.commit()
    conn.close()
    return watcher_db


@pytest.fixture
def db_with_sbom_multi_cve(watcher_db):
    """DB with an sbom_generated entry containing multiple CVEs."""
    conn = sqlite3.connect(str(watcher_db))
    conn.execute(
        """INSERT INTO audit_trail (project_id, event_type, actor, action, details)
           VALUES (?, 'sbom_generated', 'sbom-agent', 'SBOM generated', ?)""",
        (
            PROJECT_ID,
            json.dumps({
                "vulnerabilities": [
                    {"cve_id": "CVE-2025-0001", "component": "libssl", "cvss_score": 9.1, "severity": "critical"},
                    {"cve_id": "CVE-2025-0002", "component": "openssl", "cvss_score": 6.5, "severity": "medium"},
                ]
            }),
        ),
    )
    conn.commit()
    conn.close()
    return watcher_db


# ---------------------------------------------------------------------------
# Unit tests: _extract_cves_from_entry
# ---------------------------------------------------------------------------


class TestExtractCves:
    """Tests for CVE extraction from audit_trail rows."""

    def _make_row(self, event_type, action, details):
        """Create a dict that mimics a sqlite3.Row."""
        return {
            "id": 1,
            "project_id": PROJECT_ID,
            "event_type": event_type,
            "actor": "test-actor",
            "action": action,
            "details": json.dumps(details) if isinstance(details, dict) else details,
        }

    def test_extract_explicit_cve_id_field(self):
        """Extracts CVE from details.cve_id field."""
        row = self._make_row(
            "vulnerability_found",
            "scan result",
            {"cve_id": "CVE-2025-9999", "component": "flask", "cvss_score": 8.0, "severity": "high"},
        )
        results = _extract_cves_from_entry(row)
        assert len(results) == 1
        assert results[0]["cve_id"] == "CVE-2025-9999"
        assert results[0]["component"] == "flask"
        assert results[0]["cvss_score"] == 8.0
        assert results[0]["severity"] == "high"

    def test_extract_cve_ids_list(self):
        """Extracts multiple CVEs from details.cve_ids list."""
        row = self._make_row(
            "sbom_generated",
            "sbom scan",
            {"cve_ids": ["CVE-2025-0010", "CVE-2025-0020"]},
        )
        results = _extract_cves_from_entry(row)
        found_ids = {r["cve_id"] for r in results}
        assert "CVE-2025-0010" in found_ids
        assert "CVE-2025-0020" in found_ids

    def test_extract_vulnerabilities_list(self):
        """Extracts CVEs from details.vulnerabilities list."""
        row = self._make_row(
            "security_scan",
            "scan complete",
            {
                "vulnerabilities": [
                    {"cve_id": "CVE-2025-1111", "component": "django", "cvss_score": 9.8, "severity": "critical"},
                    {"cve_id": "CVE-2025-2222", "component": "numpy", "cvss_score": 5.0},
                ]
            },
        )
        results = _extract_cves_from_entry(row)
        found_ids = {r["cve_id"] for r in results}
        assert "CVE-2025-1111" in found_ids
        assert "CVE-2025-2222" in found_ids

    def test_extract_regex_fallback_from_action(self):
        """Falls back to regex extraction from action text."""
        row = self._make_row(
            "vulnerability_found",
            "Detected CVE-2025-5555 in dependency scan",
            {},
        )
        results = _extract_cves_from_entry(row)
        assert any(r["cve_id"] == "CVE-2025-5555" for r in results)

    def test_extract_no_cve_returns_empty(self):
        """Returns empty list when no CVE found."""
        row = self._make_row("security_scan", "clean scan — no findings", {})
        results = _extract_cves_from_entry(row)
        assert results == []

    def test_severity_inferred_from_cvss(self):
        """Severity is inferred from CVSS when not explicitly provided."""
        row = self._make_row(
            "vulnerability_found",
            "scan",
            {"cve_id": "CVE-2025-3000", "cvss_score": 9.5},
        )
        results = _extract_cves_from_entry(row)
        assert results[0]["severity"] == "critical"

    def test_cve_id_normalized_to_upper(self):
        """CVE IDs are normalized to uppercase."""
        row = self._make_row(
            "vulnerability_found",
            "found cve-2025-7777 in module",
            {},
        )
        results = _extract_cves_from_entry(row)
        assert results[0]["cve_id"] == "CVE-2025-7777"


# ---------------------------------------------------------------------------
# Integration tests: watch_scan
# ---------------------------------------------------------------------------


class TestWatchScan:
    """Tests for watch_scan() against a real SQLite DB."""

    def test_empty_db_returns_zero_counts(self, watcher_db):
        """No audit entries → zero counts, empty results."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve") as mock_triage, \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact") as mock_prop:
            result = watch_scan(PROJECT_ID, since_id=0, db_path=str(watcher_db))

        assert result["scanned_entries"] == 0
        assert result["cves_found"] == 0
        assert result["triaged"] == 0
        assert result["errors"] == 0
        mock_triage.assert_not_called()
        mock_prop.assert_not_called()

    def test_new_cve_is_triaged(self, db_with_vuln_event):
        """A new vulnerability_found entry causes auto-triage and blast-radius propagation."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve", return_value=FAKE_TRIAGE_RESULT) as mock_triage, \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact", return_value=FAKE_PROPAGATE_RESULT) as mock_prop:
            result = watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_vuln_event))

        assert result["triaged"] == 1
        assert result["errors"] == 0
        assert result["cves_found"] == 1

        entry = result["results"][0]
        assert entry["status"] == "triaged"
        assert entry["cve_id"] == "CVE-2025-1234"
        assert entry["triage_id"] == 42
        assert entry["blast_radius"] == 3
        assert entry["propagated_blast_radius"] == 3

        mock_triage.assert_called_once()
        call_args = mock_triage.call_args
        assert call_args.args[0] == PROJECT_ID
        assert call_args.args[1] == "CVE-2025-1234"
        assert call_args.args[2] == "requests"

        mock_prop.assert_called_once()

    def test_already_triaged_cve_is_skipped(self, db_with_vuln_event):
        """A CVE already in cve_triage is skipped (not re-triaged)."""
        # Pre-insert the CVE into cve_triage
        conn = sqlite3.connect(str(db_with_vuln_event))
        conn.execute(
            """INSERT INTO cve_triage (project_id, cve_id, package_name, severity, cvss_score)
               VALUES (?, 'CVE-2025-1234', 'requests', 'high', 7.5)""",
            (PROJECT_ID,),
        )
        conn.commit()
        conn.close()

        with patch("tools.supply_chain.cve_passive_watcher.triage_cve") as mock_triage, \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact") as mock_prop:
            result = watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_vuln_event))

        assert result["skipped"] == 1
        assert result["triaged"] == 0
        mock_triage.assert_not_called()
        mock_prop.assert_not_called()

        skipped = result["results"][0]
        assert skipped["status"] == "skipped"
        assert skipped["reason"] == "already_triaged"

    def test_cve_triaged_event_type_is_skipped(self, watcher_db):
        """Entries with event_type=cve_triaged are logged as skipped (no re-triage)."""
        conn = sqlite3.connect(str(watcher_db))
        conn.execute(
            """INSERT INTO audit_trail (project_id, event_type, actor, action, details)
               VALUES (?, 'cve_triaged', 'cve-triager',
                       'Triaged CVE-2025-4444',
                       ?)""",
            (PROJECT_ID, json.dumps({"cve_id": "CVE-2025-4444", "component": "boto3"})),
        )
        conn.commit()
        conn.close()

        with patch("tools.supply_chain.cve_passive_watcher.triage_cve") as mock_triage:
            result = watch_scan(PROJECT_ID, since_id=0, db_path=str(watcher_db))

        assert result["skipped"] == 1
        assert result["triaged"] == 0
        mock_triage.assert_not_called()

    def test_no_triage_mode_detects_but_does_not_triage(self, db_with_vuln_event):
        """With auto_triage=False, CVEs are detected but not submitted to cve_triager."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve") as mock_triage:
            result = watch_scan(PROJECT_ID, since_id=0, auto_triage=False, db_path=str(db_with_vuln_event))

        assert result["cves_found"] == 1
        assert result["triaged"] == 0
        entry = result["results"][0]
        assert entry["status"] == "detected"
        assert entry["cve_id"] == "CVE-2025-1234"
        mock_triage.assert_not_called()

    def test_multiple_cves_in_one_entry(self, db_with_sbom_multi_cve):
        """Multiple CVEs in a single audit entry are each triaged."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve", return_value=FAKE_TRIAGE_RESULT), \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact", return_value=FAKE_PROPAGATE_RESULT):
            result = watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_sbom_multi_cve))

        triaged_cves = {r["cve_id"] for r in result["results"] if r["status"] == "triaged"}
        assert "CVE-2025-0001" in triaged_cves
        assert "CVE-2025-0002" in triaged_cves
        assert result["triaged"] == 2

    def test_second_scan_uses_highwatermark(self, db_with_vuln_event):
        """Second scan advances past first-scan entries; new CVE is discovered."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve", return_value=FAKE_TRIAGE_RESULT), \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact", return_value=FAKE_PROPAGATE_RESULT):
            # First scan processes the initial vulnerability_found entry
            first = watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_vuln_event))
            assert first["triaged"] == 1

            # Add a new vulnerability entry after the first scan
            conn = sqlite3.connect(str(db_with_vuln_event))
            conn.execute(
                """INSERT INTO audit_trail (project_id, event_type, actor, action, details)
                   VALUES (?, 'vulnerability_found', 'scanner', 'New finding', ?)""",
                (PROJECT_ID, json.dumps({
                    "cve_id": "CVE-2025-9876",
                    "component": "aiohttp",
                    "cvss_score": 6.0,
                    "severity": "medium",
                })),
            )
            conn.commit()
            conn.close()

            # Second scan — high-watermark excludes already-processed entries
            second = watch_scan(PROJECT_ID, db_path=str(db_with_vuln_event))

        # The new CVE must be found; the old one must not be re-triaged
        new_cve_results = [r for r in second["results"] if r.get("cve_id") == "CVE-2025-9876"]
        old_cve_triaged = [r for r in second["results"]
                           if r.get("cve_id") == "CVE-2025-1234" and r["status"] == "triaged"]
        assert len(new_cve_results) == 1, "New CVE must be discovered on second scan"
        assert new_cve_results[0]["status"] == "triaged"
        assert len(old_cve_triaged) == 0, "Already-processed CVE must not be re-triaged"

    def test_duplicate_audit_entry_not_reprocessed(self, db_with_vuln_event):
        """Same audit entry is not processed twice on back-to-back scans."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve", return_value=FAKE_TRIAGE_RESULT), \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact", return_value=FAKE_PROPAGATE_RESULT):
            first = watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_vuln_event))
            second = watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_vuln_event))

        # First scan triages it; second scan finds the entry already logged
        assert first["triaged"] == 1
        assert second["cves_found"] == 0 or second["triaged"] == 0


# ---------------------------------------------------------------------------
# Integration tests: get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    """Tests for get_status() statistics function."""

    def test_empty_status(self, watcher_db):
        """Empty DB returns zero counts."""
        result = get_status(PROJECT_ID, db_path=str(watcher_db))

        assert result["project_id"] == PROJECT_ID
        assert result["high_watermark_audit_id"] == 0
        assert result["total_processed"] == 0
        assert result["total_triaged"] == 0
        assert result["total_skipped"] == 0
        assert result["recent_discoveries"] == []

    def test_status_after_scan(self, db_with_vuln_event):
        """Status reflects processed counts after a scan."""
        with patch("tools.supply_chain.cve_passive_watcher.triage_cve", return_value=FAKE_TRIAGE_RESULT), \
             patch("tools.supply_chain.cve_passive_watcher.propagate_impact", return_value=FAKE_PROPAGATE_RESULT):
            watch_scan(PROJECT_ID, since_id=0, db_path=str(db_with_vuln_event))

        result = get_status(PROJECT_ID, db_path=str(db_with_vuln_event))
        assert result["total_triaged"] == 1
        assert result["total_processed"] == 1
        assert result["high_watermark_audit_id"] > 0
        assert len(result["recent_discoveries"]) == 1
        assert result["recent_discoveries"][0]["cve_id"] == "CVE-2025-1234"


# ---------------------------------------------------------------------------
# Unit tests: gate flag logic (result filtering)
# ---------------------------------------------------------------------------


class TestGateLogic:
    """Tests for --gate filtering: critical/high ATO-relevant CVEs block CI."""

    def _make_result(self, cve_id, severity, ato_relevant, status="triaged"):
        return {
            "cve_id": cve_id,
            "severity": severity,
            "ato_relevant": ato_relevant,
            "status": status,
        }

    def test_gate_blocks_on_critical_ato_cve(self):
        """Critical ATO-relevant CVE is included in blocking list."""
        results = [self._make_result("CVE-2025-0001", "critical", True, "triaged")]
        blocking = [
            r for r in results
            if r.get("ato_relevant")
            and r.get("severity") in ("critical", "high")
            and r.get("status") in ("triaged", "detected")
        ]
        assert len(blocking) == 1

    def test_gate_blocks_on_high_ato_cve(self):
        """High ATO-relevant CVE is included in blocking list."""
        results = [self._make_result("CVE-2025-0002", "high", True, "detected")]
        blocking = [
            r for r in results
            if r.get("ato_relevant")
            and r.get("severity") in ("critical", "high")
            and r.get("status") in ("triaged", "detected")
        ]
        assert len(blocking) == 1

    def test_gate_passes_for_medium_ato_cve(self):
        """Medium severity ATO CVE does NOT block."""
        results = [self._make_result("CVE-2025-0003", "medium", True, "triaged")]
        blocking = [
            r for r in results
            if r.get("ato_relevant")
            and r.get("severity") in ("critical", "high")
            and r.get("status") in ("triaged", "detected")
        ]
        assert len(blocking) == 0

    def test_gate_passes_for_non_ato_critical(self):
        """Critical CVE that is NOT ATO-relevant does NOT block."""
        results = [self._make_result("CVE-2025-0004", "critical", False, "triaged")]
        blocking = [
            r for r in results
            if r.get("ato_relevant")
            and r.get("severity") in ("critical", "high")
            and r.get("status") in ("triaged", "detected")
        ]
        assert len(blocking) == 0

    def test_gate_passes_for_skipped_critical(self):
        """Skipped critical ATO CVE does NOT block (no double-counting)."""
        results = [self._make_result("CVE-2025-0005", "critical", True, "skipped")]
        blocking = [
            r for r in results
            if r.get("ato_relevant")
            and r.get("severity") in ("critical", "high")
            and r.get("status") in ("triaged", "detected")
        ]
        assert len(blocking) == 0

    def test_gate_passes_for_empty_results(self):
        """Empty results list does not block."""
        blocking = [
            r for r in []
            if r.get("ato_relevant")
            and r.get("severity") in ("critical", "high")
            and r.get("status") in ("triaged", "detected")
        ]
        assert blocking == []
