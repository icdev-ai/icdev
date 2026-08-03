"""Unit tests for tools.govcon.procurement_vehicles.

Covers:
  1. Schema: procurement_vehicles table contains the four mandated fields
     (contract_number, naics_code, ceiling_value, expiration_date) and the
     four supporting fields (vehicle_name, full_name, agency, vehicle_type).
  2. CRUD: create / get / list / update / delete round-trip.
  3. Seed: seed_known_vehicles() is idempotent and populates the canonical
     set (OASIS+, Polaris, CIO-SP4, GSA MAS, SEWP).
  4. Expiration helpers: list_expiring_soon / is_expired / days_to_expiration.
  5. Validation: missing required fields, duplicate vehicle_name, bad ceiling.
  6. Audit trail: every mutation writes a row.
  7. CLI smoke: argparse subcommands wire through to the right function.
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure repo root is on sys.path and SQLite is forced (mirrors conftest)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

import pytest

from tools.db.storage import get_connection  # noqa: E402

# Import the module under test
from tools.govcon.procurement_vehicles import (  # noqa: E402
    KNOWN_VEHICLE_SEEDS,
    REQUIRED_FIELDS,
    create_vehicle,
    days_to_expiration,
    delete_vehicle,
    get_vehicle,
    is_expired,
    list_expiring_soon,
    list_vehicles,
    main,
    seed_known_vehicles,
    update_vehicle,
    _ensure_tables,
)


# ---------------------------------------------------------------------------
# Test fixture: per-test fresh SQLite DB
# ---------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path, monkeypatch):
    """Yield a fresh icdev.db-backed storage layer for each test."""
    import sqlite3

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    # Pre-create the schema this tool depends on
    raw = sqlite3.connect(str(db_path))
    # Shaped like the LIVE audit_trail. This fixture used to declare
    # (id TEXT, tenant_id, user_id, resource, recorded_at) -- the same columns
    # procurement_vehicles._audit was writing to, and none of which exist on the
    # real table. Because the fixture and the bug agreed with each other, the
    # audit assertions below passed while every write raised CheckViolation on
    # live PostgreSQL and was swallowed by _audit's best-effort except.
    raw.executescript("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            affected_files TEXT,
            classification TEXT DEFAULT 'CUI',
            ip_address TEXT,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hash TEXT,
            previous_hash TEXT,
            signature TEXT
        );
    """)
    raw.commit()
    raw.close()
    # Make sure the module's _ensure_tables runs against the fresh DB.
    _ensure_tables()
    yield db_path


# ---------------------------------------------------------------------------
# 1. Schema: required fields exist
# ---------------------------------------------------------------------------
class TestSchema:
    def test_procurement_vehicles_table_exists(self, db):
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='procurement_vehicles'"
        ).fetchone()
        assert row is not None

    def test_required_fields_present(self, db):
        conn = get_connection()
        rows = conn.execute("PRAGMA table_info(procurement_vehicles)").fetchall()
        col_names = {r[1] for r in rows}
        for field in REQUIRED_FIELDS:
            assert field in col_names, f"required field '{field}' missing"

    def test_mandated_metadata_fields_present(self, db):
        """Task mandates: contract_number, NAICS code, ceiling value, expiration date."""
        conn = get_connection()
        rows = conn.execute("PRAGMA table_info(procurement_vehicles)").fetchall()
        col_names = {r[1] for r in rows}
        assert "contract_number" in col_names
        assert "naics_code" in col_names
        assert "ceiling_value" in col_names
        assert "expiration_date" in col_names

    def test_indexes_created(self, db):
        conn = get_connection()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='procurement_vehicles'"
        ).fetchall()
        names = {r[0] for r in rows}
        # Unique index on vehicle_name expected
        assert any("vehicle_name" in (n or "") for n in names)
        # Index on naics_code expected
        assert any("naics" in (n or "") for n in names)


# ---------------------------------------------------------------------------
# 2. CRUD round-trip
# ---------------------------------------------------------------------------
class TestCRUD:
    def test_create_vehicle_minimal(self, db):
        result = create_vehicle(
            vehicle_name="OASIS+",
            full_name="One Acquisition Solution for Integrated Services Plus",
            agency="GSA",
            vehicle_type="GWAC",
            contract_number="GS00Q14OADU21",
            naics_code="541512",
            ceiling_value=60000000000.0,
            expiration_date="2030-09-30",
        )
        assert result["status"] == "ok"
        assert result["vehicle_id"]
        assert result["vehicle_name"] == "OASIS+"

    def test_get_vehicle_round_trip(self, db):
        create_vehicle(
            vehicle_name="Polaris",
            full_name="Polaris GWAC",
            agency="GSA",
            vehicle_type="GWAC",
            contract_number="47QTCB22D0014",
            naics_code="541512",
            ceiling_value=15000000000.0,
            expiration_date="2031-12-19",
        )
        result = get_vehicle(vehicle_name="Polaris")
        assert result["status"] == "ok"
        v = result["vehicle"]
        assert v["vehicle_name"] == "Polaris"
        assert v["contract_number"] == "47QTCB22D0014"
        assert v["naics_code"] == "541512"
        assert v["ceiling_value"] == 15000000000.0
        assert v["expiration_date"] == "2031-12-19"
        assert v["agency"] == "GSA"

    def test_get_vehicle_missing(self, db):
        result = get_vehicle(vehicle_name="Nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_list_vehicles_empty(self, db):
        result = list_vehicles()
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_list_vehicles_returns_all(self, db):
        for name in ("OASIS+", "Polaris", "CIO-SP4", "GSA MAS", "SEWP"):
            create_vehicle(
                vehicle_name=name,
                full_name=name,
                agency="GSA",
                vehicle_type="GWAC",
                contract_number=f"CN-{name}",
                naics_code="541512",
                ceiling_value=1000.0,
                expiration_date="2030-01-01",
            )
        result = list_vehicles()
        assert result["count"] == 5
        names = {v["vehicle_name"] for v in result["vehicles"]}
        assert {"OASIS+", "Polaris", "CIO-SP4", "GSA MAS", "SEWP"} == names

    def test_list_vehicles_filter_by_agency(self, db):
        create_vehicle(
            vehicle_name="OASIS+", full_name="OASIS+", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        create_vehicle(
            vehicle_name="SEWP", full_name="SEWP V", agency="NASA",
            vehicle_type="GWAC", contract_number="X2", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        result = list_vehicles(agency="GSA")
        assert result["count"] == 1
        assert result["vehicles"][0]["vehicle_name"] == "OASIS+"

    def test_update_vehicle_mutates_fields(self, db):
        create_vehicle(
            vehicle_name="OASIS+", full_name="OASIS+", agency="GSA",
            vehicle_type="GWAC", contract_number="OLD",
            naics_code="541512", ceiling_value=1.0,
            expiration_date="2030-01-01",
        )
        result = update_vehicle(
            vehicle_name="OASIS+",
            contract_number="NEW",
            ceiling_value=9999.0,
            expiration_date="2031-12-31",
        )
        assert result["status"] == "ok"
        # Confirm
        v = get_vehicle(vehicle_name="OASIS+")["vehicle"]
        assert v["contract_number"] == "NEW"
        assert v["ceiling_value"] == 9999.0
        assert v["expiration_date"] == "2031-12-31"

    def test_update_vehicle_missing(self, db):
        result = update_vehicle(vehicle_name="ghost", contract_number="X")
        assert result["status"] == "error"

    def test_delete_vehicle_removes_row(self, db):
        create_vehicle(
            vehicle_name="Polaris", full_name="Polaris", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        result = delete_vehicle(vehicle_name="Polaris")
        assert result["status"] == "ok"
        # Confirm gone
        second = get_vehicle(vehicle_name="Polaris")
        assert second["status"] == "error"

    def test_delete_vehicle_missing(self, db):
        result = delete_vehicle(vehicle_name="ghost")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# 3. Seed: idempotent and complete
# ---------------------------------------------------------------------------
class TestSeed:
    def test_seed_inserts_known_vehicles(self, db):
        result = seed_known_vehicles()
        assert result["status"] == "ok"
        assert result["seeded"] >= 5
        # All entries should now be present
        listing = list_vehicles()
        names = {v["vehicle_name"] for v in listing["vehicles"]}
        for name in KNOWN_VEHICLE_SEEDS:
            assert name in names

    def test_seed_is_idempotent(self, db):
        seed_known_vehicles()
        result = seed_known_vehicles()
        # Second call: nothing new
        assert result["seeded"] == 0
        assert result["skipped"] >= 5

    def test_seeded_vehicles_carry_metadata(self, db):
        seed_known_vehicles()
        oasis = get_vehicle(vehicle_name="OASIS+")["vehicle"]
        # All 4 mandated metadata fields populated
        assert oasis["contract_number"]
        assert oasis["naics_code"]
        assert oasis["ceiling_value"] is not None and oasis["ceiling_value"] > 0
        assert oasis["expiration_date"]


# ---------------------------------------------------------------------------
# 4. Expiration helpers
# ---------------------------------------------------------------------------
def _in_days(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=n)).date().isoformat()


class TestExpirationHelpers:
    def test_is_expired_true(self, db):
        assert is_expired(_in_days(-1)) is True

    def test_is_expired_false(self, db):
        assert is_expired(_in_days(30)) is False

    def test_is_expired_invalid(self, db):
        assert is_expired("not-a-date") is True  # treat unparseable as expired

    def test_days_to_expiration_future(self, db):
        days = days_to_expiration(_in_days(10))
        # Allow ±1 day slack for test execution boundary
        assert days in (9, 10, 11)

    def test_days_to_expiration_past(self, db):
        days = days_to_expiration(_in_days(-5))
        assert days == -5

    def test_days_to_expiration_invalid(self, db):
        assert days_to_expiration("not-a-date") is None

    def test_list_expiring_soon_default_window(self, db):
        create_vehicle(
            vehicle_name="SOON", full_name="Soon", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date=_in_days(30),
        )
        create_vehicle(
            vehicle_name="LATER", full_name="Later", agency="GSA",
            vehicle_type="GWAC", contract_number="X2", naics_code="541512",
            ceiling_value=1.0, expiration_date=_in_days(400),
        )
        result = list_expiring_soon(days=180)
        assert result["status"] == "ok"
        names = {v["vehicle_name"] for v in result["vehicles"]}
        assert "SOON" in names
        assert "LATER" not in names

    def test_list_expiring_soon_excludes_already_expired(self, db):
        create_vehicle(
            vehicle_name="DEAD", full_name="Dead", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date=_in_days(-10),
        )
        result = list_expiring_soon(days=180)
        names = {v["vehicle_name"] for v in result["vehicles"]}
        assert "DEAD" not in names


# ---------------------------------------------------------------------------
# 5. Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_create_duplicate_vehicle_name(self, db):
        create_vehicle(
            vehicle_name="OASIS+", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        result = create_vehicle(
            vehicle_name="OASIS+", full_name="y", agency="GSA",
            vehicle_type="GWAC", contract_number="X2", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "error"
        assert "exists" in result["message"].lower()

    def test_create_missing_vehicle_name(self, db):
        result = create_vehicle(
            vehicle_name="", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "error"
        assert "vehicle_name" in result["message"].lower()

    def test_create_negative_ceiling(self, db):
        result = create_vehicle(
            vehicle_name="Bad", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=-100.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "error"
        assert "ceiling" in result["message"].lower()

    def test_create_bad_naics(self, db):
        # NAICS must be 6 digits
        result = create_vehicle(
            vehicle_name="Bad", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="ABC",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "error"
        assert "naics" in result["message"].lower()

    def test_create_bad_vehicle_type(self, db):
        result = create_vehicle(
            vehicle_name="X", full_name="x", agency="GSA",
            vehicle_type="NOT_A_TYPE", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "error"

    def test_create_blank_contract_number_rejected(self, db):
        # Mandated field: must be captured
        result = create_vehicle(
            vehicle_name="X", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "error"

    def test_create_missing_expiration_date_rejected(self, db):
        result = create_vehicle(
            vehicle_name="X", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="",
        )
        assert result["status"] == "error"

    def test_naics_optional_allows_blank(self, db):
        # NAICS is informational; some multi-NAICS vehicles legitimately
        # cover many codes. Function should accept missing NAICS gracefully.
        result = create_vehicle(
            vehicle_name="MultiNAICS", full_name="x", agency="GSA",
            vehicle_type="BPA", contract_number="X1", naics_code="",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# 6. Audit trail
# ---------------------------------------------------------------------------
class TestAuditTrail:
    def test_create_audited(self, db):
        create_vehicle(
            vehicle_name="OASIS+", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        conn = get_connection()
        rows = conn.execute(
            "SELECT action FROM audit_trail "
            "WHERE project_id = ? OR details LIKE ?",
            ("OASIS+", "%OASIS+"),
        ).fetchall()
        actions = [r["action"] for r in rows]
        assert "create_vehicle" in actions

    def test_update_audited(self, db):
        create_vehicle(
            vehicle_name="Polaris", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        update_vehicle(vehicle_name="Polaris", contract_number="NEW2")
        conn = get_connection()
        rows = conn.execute(
            "SELECT action FROM audit_trail "
            "WHERE details LIKE '%Polaris%'"
        ).fetchall()
        actions = [r["action"] for r in rows]
        assert "create_vehicle" in actions
        assert "update_vehicle" in actions

    def test_delete_audited(self, db):
        create_vehicle(
            vehicle_name="Polaris", full_name="x", agency="GSA",
            vehicle_type="GWAC", contract_number="X1", naics_code="541512",
            ceiling_value=1.0, expiration_date="2030-01-01",
        )
        delete_vehicle(vehicle_name="Polaris")
        conn = get_connection()
        rows = conn.execute(
            "SELECT action FROM audit_trail "
            "WHERE details LIKE '%Polaris%'"
        ).fetchall()
        actions = [r["action"] for r in rows]
        assert "delete_vehicle" in actions


# ---------------------------------------------------------------------------
# 7. CLI smoke
# ---------------------------------------------------------------------------
class TestCLI:
    def _run_main(self, *argv):
        """Invoke tools/govcon/procurement_vehicles.main() with argv, capture JSON stdout."""
        saved = sys.argv
        sys.argv = ["procurement_vehicles.py", *argv]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                main()
        finally:
            sys.argv = saved
        return buf.getvalue().strip()

    def test_cli_create_and_list(self, db):
        self._run_main(
            "--create",
            "--vehicle-name", "OASIS+",
            "--full-name", "OASIS+",
            "--agency", "GSA",
            "--vehicle-type", "GWAC",
            "--contract-number", "GS00Q14OADU21",
            "--naics-code", "541512",
            "--ceiling-value", "60000000000",
            "--expiration-date", "2030-09-30",
            "--json",
        )
        out = self._run_main("--list", "--json")
        data = json.loads(out)
        assert data["count"] >= 1
        names = {v["vehicle_name"] for v in data["vehicles"]}
        assert "OASIS+" in names

    def test_cli_seed(self, db):
        self._run_main("--seed", "--json")
        out = self._run_main("--list", "--json")
        data = json.loads(out)
        assert data["count"] >= 5

    def test_cli_get(self, db):
        self._run_main(
            "--create",
            "--vehicle-name", "Polaris",
            "--full-name", "Polaris GWAC",
            "--agency", "GSA",
            "--vehicle-type", "GWAC",
            "--contract-number", "47QTCB22D0014",
            "--naics-code", "541512",
            "--ceiling-value", "15000000000",
            "--expiration-date", "2031-12-19",
            "--json",
        )
        out = self._run_main("--get", "--vehicle-name", "Polaris", "--json")
        data = json.loads(out)
        assert data["vehicle"]["contract_number"] == "47QTCB22D0014"
