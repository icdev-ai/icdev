# CUI // SP-CTI
"""E2E lifecycle test: Network config drift → DIC document regeneration pipeline.

Tests the full path:
  1. Generate synthetic network topology (baseline)
  2. Apply config drift (drifted variant)
  3. Run drift detection (drift_detector)
  4. Verify ACOIC events recorded (dic_drift_events)
  5. Verify DIC regen queue populated (dic_acoic_regen_queue)
  6. Run freshness engine scan
  7. Verify ACE roles load (network_doc_auditor, document_contributor,
     document_reviewer, document_editor)
  8. Verify drift triggers ACE role activation

All DB operations run against a temporary SQLite DB (conftest forces
ICDEV_STORAGE_BACKEND=sqlite). No network I/O, no LLM calls.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOPOLOGY_ID = "test-topo-lifecycle-01"
DOCUMENT_ID = "net-arch-doc-001"
COLLECTION_ID = "network-architecture"


@pytest.fixture(scope="module")
def acoic_db(tmp_path_factory):
    """Temporary SQLite DB wired to ACOIC and freshness schemas."""
    db_path = tmp_path_factory.mktemp("acoic") / "lifecycle.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # ACOIC tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dic_drift_events (
            event_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            entity TEXT,
            severity TEXT,
            detected_at TEXT NOT NULL,
            payload_json TEXT,
            processed INTEGER NOT NULL DEFAULT 0,
            tenant_id TEXT,
            classification TEXT
        );
        CREATE TABLE IF NOT EXISTS dic_acoic_regen_queue (
            item_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            event_id TEXT,
            drift_source TEXT,
            drift_entity TEXT,
            impact_level TEXT,
            impact_score REAL,
            state TEXT NOT NULL DEFAULT 'queued',
            queued_at TEXT NOT NULL,
            updated_at TEXT,
            ssp_fragment_id TEXT,
            tenant_id TEXT,
            classification TEXT
        );
        CREATE TABLE IF NOT EXISTS dic_ssp_fragments (
            fragment_id TEXT PRIMARY KEY,
            document_id TEXT,
            control_id TEXT NOT NULL,
            frameworks_json TEXT,
            fragment_text TEXT,
            status TEXT NOT NULL DEFAULT 'pending_review',
            assigned_to TEXT,
            origin TEXT NOT NULL DEFAULT 'ai_generated',
            ai_labeled INTEGER NOT NULL DEFAULT 1,
            verified INTEGER NOT NULL DEFAULT 0,
            abstained INTEGER NOT NULL DEFAULT 0,
            verify_reason TEXT,
            citations_json TEXT,
            cod_verdict_json TEXT,
            regen_item_id TEXT,
            created_at TEXT NOT NULL,
            reviewed_by TEXT,
            reviewed_at TEXT,
            tenant_id TEXT,
            classification TEXT
        );
        CREATE TABLE IF NOT EXISTS dic_doc_freshness (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            title TEXT,
            collection_id TEXT,
            state TEXT,
            score REAL,
            reason TEXT,
            source_event TEXT,
            recorded_at TEXT,
            tenant_id TEXT,
            classification TEXT
        );
        CREATE TABLE IF NOT EXISTS dic_freshness_scans (
            scan_id TEXT PRIMARY KEY,
            collection_id TEXT,
            stale_count INTEGER DEFAULT 0,
            aging_count INTEGER DEFAULT 0,
            fresh_count INTEGER DEFAULT 0,
            regen_priority REAL DEFAULT 0.0,
            scanned_at TEXT,
            tenant_id TEXT,
            classification TEXT
        );
    """)
    conn.commit()
    return conn, db_path


def _mock_get_connection(db_path):
    """Return a factory yielding a StorageConnection to the temp DB.

    This MUST go through tools.db.storage.get_connection, not raw sqlite3.
    Runtime code (acoic, drift_detector) authors PostgreSQL SQL with %s
    placeholders; StorageConnection translates those to ? for SQLite. Handing
    back a bare sqlite3 connection made every write raise
    "near %: syntax error", which the callers swallow and log — so these tests
    asserted against an empty DB instead of exercising the pipeline.
    conftest forces ICDEV_STORAGE_BACKEND=sqlite, so this stays file-backed and
    offline.
    """
    from tools.db.storage import get_connection as _storage_get_connection

    def _factory(*args, **kwargs):
        return _storage_get_connection(db_path=str(db_path))
    return _factory


# ── Phase 1: Synthetic data generation ──────────────────────────────────────

class TestSyntheticConfigGen:
    def test_generate_topology_returns_all_devices(self):
        from tools.network.synthetic_config_gen import generate_topology
        topo = generate_topology(TOPOLOGY_ID, device_count=4, seed=1)
        assert len(topo.device_configs) == 4
        assert topo.topology_id == TOPOLOGY_ID

    def test_baseline_has_valid_ios_syntax(self):
        from tools.network.synthetic_config_gen import generate_topology
        topo = generate_topology(TOPOLOGY_ID, device_count=3, seed=1)
        for device_id, cfg in topo.device_configs.items():
            assert "hostname" in cfg, f"Device {device_id} missing hostname"
            assert "end" in cfg, f"Device {device_id} missing 'end'"

    def test_apply_drift_medium_creates_differences(self):
        from tools.network.synthetic_config_gen import generate_topology, apply_drift
        baseline = generate_topology(TOPOLOGY_ID, device_count=4, seed=1)
        drifted = apply_drift(baseline, intensity="medium", seed=42)
        # At least one device config must differ
        changed = sum(
            1 for d in baseline.device_configs
            if baseline.device_configs.get(d) != drifted.device_configs.get(d)
        )
        assert changed >= 1, "Medium drift should change at least one device"

    def test_apply_drift_high_removes_device(self):
        from tools.network.synthetic_config_gen import generate_topology, apply_drift
        baseline = generate_topology(TOPOLOGY_ID, device_count=4, seed=1)
        drifted = apply_drift(baseline, intensity="high", seed=42)
        assert len(drifted.device_configs) < len(baseline.device_configs), \
            "High drift should remove at least one device"

    def test_topology_to_snapshot_format(self):
        from tools.network.synthetic_config_gen import generate_topology, topology_to_snapshot
        topo = generate_topology(TOPOLOGY_ID, device_count=3, seed=1)
        snap = topology_to_snapshot(topo)
        assert isinstance(snap, dict)
        assert all(isinstance(v, str) for v in snap.values())


# ── Phase 2: Drift detection ─────────────────────────────────────────────────

class TestDriftDetector:
    def test_detect_no_drift_identical_snapshots(self):
        from tools.network.drift_detector import detect_drift
        snap = {"rtr-01": "hostname RTR-01\ninterface Gi0/0\n ip address 10.1.1.1 255.255.255.0\nend"}
        report = detect_drift(TOPOLOGY_ID, snap, snap)
        assert report.drift_count == 0
        assert report.overall_severity == "info"

    def test_detect_ip_change(self):
        from tools.network.drift_detector import detect_drift
        baseline = {"rtr-01": "hostname RTR-01\ninterface Gi0/0\n ip address 10.1.1.1 255.255.255.0\nend"}
        current  = {"rtr-01": "hostname RTR-01\ninterface Gi0/0\n ip address 10.1.1.99 255.255.255.0\nend"}
        report = detect_drift(TOPOLOGY_ID, baseline, current)
        assert report.drift_count >= 1
        assert any(i.category == "ip_address_change" for i in report.items)
        assert report.overall_severity == "high"

    def test_detect_missing_device(self):
        from tools.network.drift_detector import detect_drift
        baseline = {"rtr-01": "hostname RTR-01\nend", "rtr-02": "hostname RTR-02\nend"}
        current  = {"rtr-01": "hostname RTR-01\nend"}
        report = detect_drift(TOPOLOGY_ID, baseline, current)
        critical_items = [i for i in report.items if i.category == "config_removed"]
        assert len(critical_items) == 1
        assert critical_items[0].severity == "critical"
        assert report.overall_severity == "critical"

    def test_detect_acl_removal(self):
        from tools.network.drift_detector import detect_drift
        baseline = {"fw-01": "ip access-list extended MGMT-IN\n permit tcp 10.0.0.0 0.255.255.255 any eq 443\n deny ip any any\nend"}
        current  = {"fw-01": "ip access-list extended MGMT-IN\n deny ip any any\nend"}
        report = detect_drift(TOPOLOGY_ID, baseline, current)
        assert any(i.category == "acl_change" for i in report.items)
        assert any(i.severity == "high" for i in report.items)

    def test_detect_vlan_change(self):
        from tools.network.drift_detector import detect_drift
        baseline = {"sw-01": "vlan 10\n name MGMT\nvlan 20\n name SERVERS\nend"}
        current  = {"sw-01": "vlan 10\n name MGMT\nvlan 20\n name APP-SERVERS\nend"}
        report = detect_drift(TOPOLOGY_ID, baseline, current)
        vlan_items = [i for i in report.items if i.category == "vlan_change"]
        assert len(vlan_items) >= 1

    def test_detect_interface_shutdown(self):
        from tools.network.drift_detector import detect_drift
        baseline = {"rtr-01": "interface Gi0/2\n no shutdown\nend"}
        current  = {"rtr-01": "interface Gi0/2\n shutdown\nend"}
        report = detect_drift(TOPOLOGY_ID, baseline, current)
        state_items = [i for i in report.items if i.category == "interface_state"]
        assert len(state_items) >= 1

    def test_severity_ordering_critical_first(self):
        from tools.network.drift_detector import detect_drift
        baseline = {
            "fw-01": "hostname FW\nvlan 20\n name SERVERS\nend",
            "rtr-02": "hostname RTR\nend",
        }
        current  = {
            "fw-01": "hostname FW\nvlan 20\n name APP-SERVERS\nend",
            # rtr-02 is absent → critical
        }
        report = detect_drift(TOPOLOGY_ID, baseline, current)
        assert report.items[0].severity == "critical"

    def test_full_synthetic_drift_detected(self):
        from tools.network.synthetic_config_gen import (
            generate_topology, apply_drift, topology_to_snapshot
        )
        from tools.network.drift_detector import detect_drift
        baseline = generate_topology(TOPOLOGY_ID, device_count=4, seed=1)
        drifted  = apply_drift(baseline, intensity="medium", seed=42)
        report = detect_drift(
            TOPOLOGY_ID,
            topology_to_snapshot(baseline),
            topology_to_snapshot(drifted),
        )
        assert report.drift_count > 0


# ── Phase 3: ACOIC integration ───────────────────────────────────────────────

class TestAcoicIntegration:
    def test_record_drift_event(self, acoic_db):
        conn, db_path = acoic_db
        with patch("tools.db.storage.get_connection", _mock_get_connection(db_path)):
            from tools.document_intelligence import acoic
            # Force module to use our mock connection
            with patch.object(
                sys.modules.get("tools.document_intelligence.acoic") or acoic,
                "get_connection",
                _mock_get_connection(db_path),
            ):
                event_id = acoic.record_drift_event(
                    source="network.ip_address_change",
                    entity="rtr-01",
                    severity="high",
                    payload={"topology_id": TOPOLOGY_ID, "description": "IP changed"},
                )
        assert event_id.startswith("dic_drift_")
        row = conn.execute(
            "SELECT * FROM dic_drift_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        assert row is not None
        assert row["severity"] == "high"
        assert row["processed"] == 0

    def test_enqueue_regen(self, acoic_db):
        conn, db_path = acoic_db
        with patch("tools.document_intelligence.acoic.get_connection", _mock_get_connection(db_path)):
            from tools.document_intelligence import acoic
            result = acoic.enqueue_regen(
                DOCUMENT_ID,
                event_id="evt-test-001",
                drift_source=f"network/{TOPOLOGY_ID}",
                drift_entity=TOPOLOGY_ID,
                severity="high",
            )
        assert result["impact_level"] == "high"
        assert result["state"] == "queued"
        row = conn.execute(
            "SELECT * FROM dic_acoic_regen_queue WHERE document_id = ?", (DOCUMENT_ID,)
        ).fetchone()
        assert row is not None
        assert float(row["impact_score"]) >= 0.75

    def test_emit_drift_events_from_report(self, acoic_db):
        conn, db_path = acoic_db
        from tools.network.drift_detector import DriftReport, DriftItem
        # Build a minimal report with two items
        report = DriftReport(
            topology_id=TOPOLOGY_ID,
            baseline_snapshot_id="snap-a",
            current_snapshot_id="snap-b",
        )
        report.items = [
            DriftItem(
                device_id="fw-01",
                category="acl_change",
                severity="high",
                description="ACL entry removed: permit tcp ...",
                baseline_value="permit tcp 10.0.0.0 0.255.255.255 any eq 443",
                current_value="<removed>",
            ),
            DriftItem(
                device_id="rtr-01",
                category="ip_address_change",
                severity="high",
                description="Interface Gi0/0: IP changed",
                baseline_value="10.1.1.1 255.255.255.0",
                current_value="10.1.1.99 255.255.255.0",
            ),
        ]
        report.overall_severity = "high"

        with patch("tools.document_intelligence.acoic.get_connection", _mock_get_connection(db_path)):
            from tools.network.drift_detector import emit_drift_events
            result = emit_drift_events(
                report,
                document_id=DOCUMENT_ID,
                tenant_id="tenant-test",
                classification="CUI",
            )

        assert len(result["event_ids"]) == 2
        assert "enqueued" in result

    def test_handle_drift_event(self, acoic_db):
        conn, db_path = acoic_db
        with patch("tools.document_intelligence.acoic.get_connection", _mock_get_connection(db_path)):
            from tools.document_intelligence import acoic
            result = acoic.handle_drift({
                "source": "network",
                "entity": "rtr-01",
                "severity": "medium",
                "document_id": DOCUMENT_ID,
                "control_ids": [],
            })
        assert "event_id" in result
        assert result["event_id"].startswith("dic_drift_")

    def test_full_pipeline_synthetic_to_acoic(self, acoic_db):
        conn, db_path = acoic_db
        from tools.network.synthetic_config_gen import (
            generate_topology, apply_drift, topology_to_snapshot
        )
        from tools.network.drift_detector import detect_drift, emit_drift_events

        baseline = generate_topology(TOPOLOGY_ID, device_count=4, seed=1)
        drifted  = apply_drift(baseline, intensity="high", seed=42)
        report = detect_drift(
            TOPOLOGY_ID,
            topology_to_snapshot(baseline),
            topology_to_snapshot(drifted),
        )
        assert report.drift_count > 0
        assert report.has_critical  # high drift removes a device

        with patch("tools.document_intelligence.acoic.get_connection", _mock_get_connection(db_path)):
            emit_result = emit_drift_events(
                report,
                document_id=DOCUMENT_ID,
                tenant_id="",
                classification="CUI",
            )

        assert len(emit_result["event_ids"]) > 0
        # Verify events persisted
        rows = conn.execute(
            "SELECT COUNT(*) FROM dic_drift_events WHERE source LIKE 'network.%'"
        ).fetchone()
        assert rows[0] > 0


# ── Phase 4: Freshness engine ─────────────────────────────────────────────────

class TestFreshnessEngine:
    def test_freshness_result_dataclass(self):
        from tools.document_intelligence.freshness_engine import FreshnessResult
        r = FreshnessResult(
            doc_id="test-doc-01",
            title="Network Architecture Guide",
            collection_id=COLLECTION_ID,
            state="stale",
            score=0.85,
            reason="Last updated 120 days ago; 3 drift events since last review",
        )
        assert r.state == "stale"
        assert r.score == 0.85

    def test_scan_result_dataclass(self):
        from tools.document_intelligence.freshness_engine import ScanResult, FreshnessResult
        scan = ScanResult(
            scan_id="scan-001",
            collection_id=COLLECTION_ID,
            stale_count=2,
            aging_count=1,
            fresh_count=5,
            regen_priority=0.6,
            docs=[
                FreshnessResult(
                    doc_id="doc-001",
                    state="stale",
                    score=0.9,
                    reason="Drift events detected",
                )
            ],
        )
        assert scan.stale_count == 2
        assert len(scan.docs) == 1


# ── Phase 5: ACE role loading ────────────────────────────────────────────────

class TestNewAceRolesLoad:
    NEW_ROLES = [
        "network_doc_auditor",
        "document_contributor",
        "document_reviewer",
        "document_editor",
    ]

    def test_all_new_roles_have_yaml_files(self):
        roles_dir = REPO_ROOT / "args" / "ace" / "roles"
        for role_id in self.NEW_ROLES:
            role_file = roles_dir / f"{role_id}.yaml"
            assert role_file.exists(), f"Missing role file: {role_file}"

    def test_new_roles_load_via_role_loader(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        for role_id in self.NEW_ROLES:
            role = loader.get_role(role_id)
            assert role is not None, f"RoleLoader could not load {role_id}"
            assert role.role_id == role_id
            assert role.display_name
            assert len(role.steps) > 0

    def test_network_doc_auditor_has_drift_step(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        role = loader.get_role("network_doc_auditor")
        step_names = [s.name for s in role.steps]
        assert "scan_network_drift" in step_names

    def test_document_contributor_has_submit_step(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        role = loader.get_role("document_contributor")
        step_names = [s.name for s in role.steps]
        assert "submit_for_review" in step_names

    def test_document_reviewer_has_verdict_step(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        role = loader.get_role("document_reviewer")
        step_names = [s.name for s in role.steps]
        assert "emit_verdict" in step_names

    def test_document_editor_has_publish_step(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        role = loader.get_role("document_editor")
        step_names = [s.name for s in role.steps]
        assert "publish_to_collection" in step_names

    def test_all_new_roles_have_communication_config(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        for role_id in self.NEW_ROLES:
            role = loader.get_role(role_id)
            assert role.communication is not None, f"{role_id} missing communication config"
            assert "listen_topics" in role.communication
            assert "emit_topics" in role.communication

    def test_all_new_roles_have_tool_permissions(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        for role_id in self.NEW_ROLES:
            role = loader.get_role(role_id)
            assert role.tool_permissions, f"{role_id} has no tool_permissions"

    # ACE roles identify their canvas by its short registry key ('dic', 'ndc',
    # 'sdc', ...) — the convention every role in args/ace/roles/ follows. These
    # two roles originally shipped as 'network'/'document_intelligence' and were
    # normalized to 'ndc'/'dic' in 36d611a31, which did not update these tests;
    # they have asserted the pre-normalization names ever since.
    def test_network_doc_auditor_canvas_is_ndc(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        role = loader.get_role("network_doc_auditor")
        assert role.canvas == "ndc", \
            f"network_doc_auditor.canvas should be the 'ndc' registry key, got '{role.canvas}'"

    def test_document_roles_canvas_is_dic(self):
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        for role_id in ["document_contributor", "document_reviewer", "document_editor"]:
            role = loader.get_role(role_id)
            assert role.canvas == "dic", \
                f"{role_id}.canvas should be the 'dic' registry key, got '{role.canvas}'"

    def test_all_new_roles_use_short_canvas_keys(self):
        """Guard the convention itself, so a future rename can't silently drift."""
        from tools.ace.role_loader import RoleLoader
        loader = RoleLoader()
        for role_id in self.NEW_ROLES:
            canvas = loader.get_role(role_id).canvas
            assert canvas in {"ndc", "dic"}, f"{role_id} has unexpected canvas {canvas!r}"


# ── Phase 6: DriftReport serialisation ────────────────────────────────────────

class TestDriftReportStructure:
    def test_to_dict_is_json_serializable(self):
        from tools.network.drift_detector import DriftReport, DriftItem
        report = DriftReport(
            topology_id=TOPOLOGY_ID,
            baseline_snapshot_id="snap-a",
            current_snapshot_id="snap-b",
        )
        report.items = [
            DriftItem(
                device_id="rtr-01",
                category="ip_address_change",
                severity="high",
                description="IP changed on Gi0/0",
                baseline_value="10.1.1.1/24",
                current_value="10.1.1.99/24",
            )
        ]
        report.overall_severity = "high"
        report.summary = "1 drift item detected."
        d = report.to_dict()
        # Must be JSON-serializable
        serialized = json.dumps(d)
        assert len(serialized) > 0
        parsed = json.loads(serialized)
        assert parsed["topology_id"] == TOPOLOGY_ID
        assert parsed["drift_count"] == 1

    def test_run_drift_check_returns_structured_output(self):
        from tools.network.drift_detector import run_drift_check
        baseline = {"rtr-01": "hostname RTR\ninterface Gi0/0\n ip address 10.1.1.1 255.255.255.0\nend"}
        current  = {"rtr-01": "hostname RTR\ninterface Gi0/0\n ip address 10.1.1.50 255.255.255.0\nend"}
        result = run_drift_check(
            TOPOLOGY_ID, baseline, current,
            document_id=DOCUMENT_ID,
        )
        assert "report" in result
        assert result["report"]["topology_id"] == TOPOLOGY_ID
        assert result["report"]["drift_count"] >= 1
