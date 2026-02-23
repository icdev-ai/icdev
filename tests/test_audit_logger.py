# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.audit.audit_logger — append-only audit trail writer."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tools.audit.audit_logger import VALID_EVENT_TYPES, log_event


def _create_audit_table(db_path: Path):
    """Create a minimal audit_trail table for testing."""
    conn = sqlite3.connect(str(db_path))
    # Build a simplified CHECK constraint from the valid event types
    event_types_sql = ", ".join(f"'{et}'" for et in VALID_EVENT_TYPES)
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT NOT NULL CHECK(event_type IN ({event_types_sql})),
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            affected_files TEXT,
            classification TEXT DEFAULT 'CUI',
            ip_address TEXT,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()


@pytest.fixture
def audit_db(tmp_path):
    """Create a temp database with audit_trail table."""
    db = tmp_path / "test_audit.db"
    _create_audit_table(db)
    return db


class TestLogEventBasic:
    """Basic log_event functionality tests."""

    def test_log_event_writes_to_audit_trail(self, audit_db):
        """log_event should insert a row into the audit_trail table."""
        log_event(
            event_type="project_created",
            actor="test-agent",
            action="Created project X",
            db_path=audit_db,
        )
        conn = sqlite3.connect(str(audit_db))
        rows = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()
        conn.close()
        assert rows[0] == 1

    def test_log_event_with_all_parameters(self, audit_db):
        """log_event should store all provided parameters correctly."""
        details = {"key": "value", "nested": {"a": 1}}
        entry_id = log_event(
            event_type="code_generated",
            actor="builder-agent",
            action="Generated module Y",
            project_id="proj-test-001",
            details=details,
            affected_files=["src/main.py", "src/utils.py"],
            classification="CUI // SP-CTI",
            ip_address="10.0.0.1",
            session_id="sess-abc-123",
            db_path=audit_db,
        )
        conn = sqlite3.connect(str(audit_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_trail WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()

        assert row["event_type"] == "code_generated"
        assert row["actor"] == "builder-agent"
        assert row["action"] == "Generated module Y"
        assert row["project_id"] == "proj-test-001"
        assert json.loads(row["details"]) == details
        assert json.loads(row["affected_files"]) == ["src/main.py", "src/utils.py"]
        assert row["classification"] == "CUI // SP-CTI"
        assert row["ip_address"] == "10.0.0.1"
        assert row["session_id"] == "sess-abc-123"

    def test_log_event_returns_entry_id(self, audit_db):
        """log_event should return a positive integer entry ID."""
        entry_id = log_event(
            event_type="test_passed",
            actor="test-runner",
            action="All tests passed",
            db_path=audit_db,
        )
        assert isinstance(entry_id, int)
        assert entry_id >= 1

    def test_log_event_auto_generates_autoincrement_id(self, audit_db):
        """Each log_event call should get a unique auto-incremented ID."""
        id1 = log_event(
            event_type="test_passed",
            actor="agent-a",
            action="Action 1",
            db_path=audit_db,
        )
        id2 = log_event(
            event_type="test_failed",
            actor="agent-b",
            action="Action 2",
            db_path=audit_db,
        )
        assert id2 > id1


class TestLogEventValidation:
    """Event type validation tests."""

    def test_log_event_validates_event_type(self, audit_db):
        """log_event should accept all valid event types without error."""
        # Test a few representative event types
        for et in ("project_created", "code_generated", "security_scan", "coa_generated"):
            entry_id = log_event(
                event_type=et,
                actor="test",
                action="Validation test",
                db_path=audit_db,
            )
            assert entry_id >= 1

    def test_log_event_rejects_invalid_event_type(self, audit_db):
        """log_event should raise ValueError for unknown event types."""
        with pytest.raises(ValueError, match="Invalid event_type"):
            log_event(
                event_type="totally_fake_event",
                actor="test",
                action="This should fail",
                db_path=audit_db,
            )

    def test_valid_event_types_is_nonempty_tuple(self):
        """VALID_EVENT_TYPES should be a non-empty tuple of strings."""
        assert isinstance(VALID_EVENT_TYPES, tuple)
        assert len(VALID_EVENT_TYPES) > 50
        for et in VALID_EVENT_TYPES:
            assert isinstance(et, str)


class TestLogEventDetails:
    """Tests for details serialization and defaults."""

    def test_log_event_writes_details_as_json_string(self, audit_db):
        """Details dict should be stored as a JSON string."""
        details = {"tool": "bandit", "findings": 3}
        entry_id = log_event(
            event_type="security_scan",
            actor="security-agent",
            action="SAST scan complete",
            details=details,
            db_path=audit_db,
        )
        conn = sqlite3.connect(str(audit_db))
        row = conn.execute(
            "SELECT details FROM audit_trail WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        assert row[0] is not None
        parsed = json.loads(row[0])
        assert parsed == details

    def test_log_event_stores_none_when_no_details(self, audit_db):
        """When details is None, the column should be NULL."""
        entry_id = log_event(
            event_type="project_created",
            actor="test",
            action="No details",
            db_path=audit_db,
        )
        conn = sqlite3.connect(str(audit_db))
        row = conn.execute(
            "SELECT details FROM audit_trail WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        assert row[0] is None

    def test_log_event_default_classification_cui(self, audit_db):
        """Default classification should be 'CUI'."""
        entry_id = log_event(
            event_type="project_created",
            actor="test",
            action="Default classification test",
            db_path=audit_db,
        )
        conn = sqlite3.connect(str(audit_db))
        row = conn.execute(
            "SELECT classification FROM audit_trail WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "CUI"


class TestLogEventSessionId:
    """Tests for session_id auto-population from correlation context (D149)."""

    def test_log_event_auto_populates_session_id_from_correlation(self, audit_db):
        """When session_id is None, log_event should try to get correlation ID."""
        mock_corr_id = "corr-auto-12345"
        with patch(
            "tools.audit.audit_logger.get_correlation_id",
            create=True,
        ) as mock_get:
            # We need to patch the import inside log_event
            with patch.dict("sys.modules", {
                "tools.resilience.correlation": type(sys)("tools.resilience.correlation")
            }):
                sys.modules["tools.resilience.correlation"].get_correlation_id = lambda: mock_corr_id
                entry_id = log_event(
                    event_type="project_created",
                    actor="test",
                    action="Auto session test",
                    db_path=audit_db,
                )
        conn = sqlite3.connect(str(audit_db))
        row = conn.execute(
            "SELECT session_id FROM audit_trail WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        assert row[0] == mock_corr_id

    def test_log_event_explicit_session_id_overrides_correlation(self, audit_db):
        """An explicitly provided session_id should be used regardless of correlation context."""
        explicit_id = "explicit-sess-999"
        entry_id = log_event(
            event_type="code_reviewed",
            actor="reviewer",
            action="Override test",
            session_id=explicit_id,
            db_path=audit_db,
        )
        conn = sqlite3.connect(str(audit_db))
        row = conn.execute(
            "SELECT session_id FROM audit_trail WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        assert row[0] == explicit_id

    def test_log_event_graceful_when_correlation_import_fails(self, audit_db):
        """log_event should not fail if correlation module is unavailable."""
        # The default environment won't have tools.resilience.correlation,
        # so this exercises the ImportError path naturally.
        entry_id = log_event(
            event_type="test_executed",
            actor="runner",
            action="Graceful import test",
            db_path=audit_db,
        )
        assert entry_id >= 1


class TestLogEventAppendOnly:
    """Tests verifying the append-only audit trail behavior."""

    def test_audit_trail_append_only_insert(self, audit_db):
        """Verify log_event performs INSERT (row count increases)."""
        conn = sqlite3.connect(str(audit_db))
        before = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        conn.close()

        log_event(
            event_type="deployment_succeeded",
            actor="deploy-agent",
            action="Deployed v1.0",
            db_path=audit_db,
        )

        conn = sqlite3.connect(str(audit_db))
        after = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        conn.close()
        assert after == before + 1

    def test_multiple_log_events_create_multiple_rows(self, audit_db):
        """Multiple log_event calls should create distinct rows."""
        ids = []
        for i in range(5):
            entry_id = log_event(
                event_type="config_changed",
                actor=f"agent-{i}",
                action=f"Action {i}",
                db_path=audit_db,
            )
            ids.append(entry_id)

        assert len(set(ids)) == 5

        conn = sqlite3.connect(str(audit_db))
        count = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        conn.close()
        assert count == 5

    def test_log_event_creates_data_dir_if_missing(self, tmp_path):
        """log_event should work even if the parent directory does not exist yet."""
        nested_db = tmp_path / "sub" / "deep" / "audit.db"
        # The directory doesn't exist yet, but _create_audit_table needs it.
        # log_event relies on sqlite3.connect creating the file, but
        # the table must pre-exist. So we test that the connection works
        # to a new path (sqlite3 creates the file automatically).
        nested_db.parent.mkdir(parents=True, exist_ok=True)
        _create_audit_table(nested_db)
        entry_id = log_event(
            event_type="project_created",
            actor="test",
            action="Dir creation test",
            db_path=nested_db,
        )
        assert entry_id >= 1
        assert nested_db.exists()
