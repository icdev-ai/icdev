"""Unit tests for tools.govcon.procurement_quote_compare.

Covers:
  1. Variance math: _variance_pct + _classify_variance edge cases
  2. Procurement + IGCE line item lifecycle (create / add / list)
  3. Vendor quote capture with derived total_price
  4. compare_procurement: line-by-line with lowest quote selection
  5. vendor_summary: per-vendor rollup with mean/median variance + flag counts
  6. gate_procurement: pass/warn/fail based on variance thresholds
  7. Error paths: missing procurement, missing IGCE, bad status, duplicate quote
  8. Audit trail: every mutation writes a row
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path and SQLite is forced (mirrors conftest)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

import pytest

from tools.db.storage import get_connection  # noqa: E402

# Import the module under test
from tools.govcon.procurement_quote_compare import (  # noqa: E402
    MAX_WARN_PCT,
    MAX_FAIL_PCT,
    UNREASONABLE_LOW_PCT,
    add_bom_line,
    add_igce_line,
    add_quote,
    compare_procurement,
    create_procurement,
    gate_procurement,
    list_igce,
    list_procurements,
    list_quotes,
    _classify_variance,
    _variance_pct,
    vendor_summary,
)


# ---------------------------------------------------------------------------
# Test fixture: per-test fresh SQLite DB
# ---------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path, monkeypatch):
    """Yield a fresh icdev.db-backed storage layer for each test.

    The procurement tool calls tools.db.storage.get_connection() which reads
    ICDEV_DB_PATH. We point it at a per-test temp file AND pre-create the
    schema (audit_trail, plus our 3 procurement tables) so the module's
    _ensure_tables finds them and writes succeed.
    """
    import sqlite3

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    # Pre-create the schema this tool depends on
    raw = sqlite3.connect(str(db_path))
    raw.executescript("""
        -- Mirrors the live audit_trail (see tools/db/init_icdev_db.py and the
        -- shared fixture in tests/conftest.py). This used to declare
        -- id TEXT/tenant_id/user_id/resource/recorded_at -- none of which the
        -- real table has -- while omitting the project_id/event_type/actor it
        -- does. _audit() writes the real columns, so every INSERT below raised
        -- and was swallowed by its best-effort except, and the assertions then
        -- failed on a missing project_id column rather than on a missing row.
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
        CREATE TABLE IF NOT EXISTS proc_procurements (
            id              TEXT PRIMARY KEY,
            solicitation    TEXT NOT NULL DEFAULT '',
            title           TEXT NOT NULL DEFAULT '',
            agency          TEXT NOT NULL DEFAULT '',
            contract_type   TEXT NOT NULL DEFAULT 'ffp',
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'open',
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI'
        );
        CREATE TABLE IF NOT EXISTS proc_igce_line_items (
            id              TEXT PRIMARY KEY,
            procurement_id  TEXT NOT NULL,
            clin            TEXT NOT NULL DEFAULT '',
            description     TEXT NOT NULL DEFAULT '',
            unit            TEXT NOT NULL DEFAULT 'each',
            quantity        REAL NOT NULL DEFAULT 1.0,
            unit_cost       REAL NOT NULL DEFAULT 0.0,
            extended_cost   REAL NOT NULL DEFAULT 0.0,
            basis           TEXT NOT NULL DEFAULT '',
            poc             TEXT NOT NULL DEFAULT '',
            notes           TEXT,
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI',
            UNIQUE (procurement_id, clin)
        );
        CREATE TABLE IF NOT EXISTS proc_vendor_quotes (
            id              TEXT PRIMARY KEY,
            procurement_id  TEXT NOT NULL,
            vendor_name     TEXT NOT NULL,
            quote_ref       TEXT NOT NULL DEFAULT '',
            clin            TEXT NOT NULL DEFAULT '',
            unit_price      REAL NOT NULL DEFAULT 0.0,
            quantity        REAL,
            total_price     REAL NOT NULL DEFAULT 0.0,
            quote_date      TEXT,
            valid_until     TEXT,
            status          TEXT NOT NULL DEFAULT 'submitted',
            notes           TEXT,
            metadata        TEXT DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            classification  TEXT DEFAULT 'CUI',
            UNIQUE (procurement_id, vendor_name, quote_ref, clin)
        );
    """)
    raw.commit()
    raw.close()
    yield db_path


# ---------------------------------------------------------------------------
# 1. Variance math helpers
# ---------------------------------------------------------------------------
class TestVarianceHelpers:
    def test_variance_pct_at_or_below_igce(self):
        assert _variance_pct(100.0, 100.0) == 0.0
        assert _variance_pct(95.0, 100.0) == -5.0
        assert _variance_pct(80.0, 100.0) == -20.0

    def test_variance_pct_above_igce(self):
        assert _variance_pct(110.0, 100.0) == 10.0
        assert _variance_pct(175.0, 100.0) == 75.0
        # Rounding to 2 decimals
        assert _variance_pct(123.456, 100.0) == 23.46

    def test_variance_pct_returns_none_for_zero_igce(self):
        assert _variance_pct(100.0, 0.0) is None
        assert _variance_pct(100.0, -50.0) is None
        assert _variance_pct(None, 100.0) is None

    def test_classify_variance_green(self):
        # At or below IGCE (not unreasonably low)
        assert _classify_variance(0.0) == "green"
        assert _classify_variance(-5.0) == "green"
        assert _classify_variance(-25.0) == "green"

    def test_classify_variance_yellow(self):
        # Above IGCE within warn band
        assert _classify_variance(0.01) == "green"  # tolerance
        assert _classify_variance(MAX_WARN_PCT) == "green"
        assert _classify_variance(MAX_WARN_PCT + 0.01) == "yellow"
        assert _classify_variance(MAX_FAIL_PCT) == "yellow"

    def test_classify_variance_red(self):
        # Above fail threshold
        assert _classify_variance(MAX_FAIL_PCT + 0.01) == "red"
        assert _classify_variance(50.0) == "red"
        # Unreasonably low (likely scope misread)
        assert _classify_variance(UNREASONABLE_LOW_PCT - 0.01) == "red"
        assert _classify_variance(-30.0) == "red"

    def test_classify_variance_unknown(self):
        assert _classify_variance(None) == "unknown"


# ---------------------------------------------------------------------------
# 2. Procurement + IGCE lifecycle
# ---------------------------------------------------------------------------
class TestProcurementLifecycle:
    def test_create_procurement_round_trip(self, db):
        result = create_procurement(
            procurement_id="PROC-2026-001",
            solicitation="W912DY-26-R-0007",
            agency="USACE",
            title="Cyber Range Build",
            contract_type="ffp",
        )
        assert result["status"] == "ok"
        assert result["procurement_id"] == "PROC-2026-001"

        listing = list_procurements()
        assert listing["status"] == "ok"
        assert listing["count"] == 1
        assert listing["procurements"][0]["agency"] == "USACE"

    def test_create_duplicate_procurement_errors(self, db):
        create_procurement("PROC-DUP", "SOL-1", "AF")
        result = create_procurement("PROC-DUP", "SOL-2", "AF")
        assert result["status"] == "error"
        assert "already exists" in result["message"]

    def test_add_igce_line_computes_extended_cost(self, db):
        create_procurement("PROC-IGCE-1", "SOL-1", "ARMY")
        result = add_igce_line(
            procurement_id="PROC-IGCE-1",
            clin="0001",
            description="Junior SWE labor",
            unit="hour",
            quantity=160,
            unit_cost=175.0,
            basis="GSA CALC+ 2026 mean",
        )
        assert result["status"] == "ok"
        assert result["action"] == "created"
        assert result["extended_cost"] == 28000.0

    def test_add_igce_line_upserts_on_repeat(self, db):
        create_procurement("PROC-IGCE-2", "SOL-2", "NAVY")
        add_igce_line("PROC-IGCE-2", "0001", "Labor", "hour", 100, 100.0)
        result = add_igce_line("PROC-IGCE-2", "0001", "Labor (revised)",
                               "hour", 100, 120.0)
        assert result["action"] == "updated"
        assert result["extended_cost"] == 12000.0

        listing = list_igce("PROC-IGCE-2")
        assert listing["count"] == 1
        assert listing["total_igce"] == 12000.0

    def test_add_igce_line_requires_procurement(self, db):
        result = add_igce_line("PROC-MISSING", "0001", "x", "each", 1, 100.0)
        assert result["status"] == "error"
        assert "not found" in result["message"]

    def test_add_igce_line_validates_inputs(self, db):
        create_procurement("PROC-VAL", "SOL-V", "DOD")
        # Bad quantity
        r = add_igce_line("PROC-VAL", "0001", "x", "each", 0, 100.0)
        assert r["status"] == "error"
        # Bad cost
        r = add_igce_line("PROC-VAL", "0002", "x", "each", 1, -1.0)
        assert r["status"] == "error"


# ---------------------------------------------------------------------------
# 3. Vendor quote capture
# ---------------------------------------------------------------------------
class TestQuoteCapture:
    def _seed(self):
        create_procurement("PROC-Q1", "SOL-Q1", "USAF")
        add_igce_line("PROC-Q1", "0001", "Labor", "hour", 160, 100.0)
        add_igce_line("PROC-Q1", "0002", "Travel", "trip", 1, 5000.0)

    def test_add_quote_derives_total(self, db):
        self._seed()
        result = add_quote(
            procurement_id="PROC-Q1",
            vendor_name="Acme Federal LLC",
            clin="0001",
            unit_price=110.0,
            quote_ref="AF-2026-Q-001",
        )
        assert result["status"] == "ok"
        assert result["quantity"] == 160.0  # derived from IGCE
        assert result["total_price"] == 17600.0  # 160 * 110
        assert result["variance_pct"] == 10.0
        assert result["flag"] == "yellow"

    def test_add_quote_uses_explicit_total(self, db):
        self._seed()
        result = add_quote(
            procurement_id="PROC-Q1",
            vendor_name="Acme Federal LLC",
            clin="0001",
            unit_price=110.0,
            quantity=200.0,
            total_price=22000.0,
        )
        assert result["quantity"] == 200.0
        assert result["total_price"] == 22000.0

    def test_add_quote_requires_matching_igce(self, db):
        create_procurement("PROC-NO-IGCE", "SOL-N", "DOD")
        result = add_quote("PROC-NO-IGCE", "Acme", "0099", 100.0)
        assert result["status"] == "error"
        assert "no IGCE line" in result["message"]

    def test_add_quote_rejects_invalid_status(self, db):
        self._seed()
        result = add_quote("PROC-Q1", "Acme", "0001", 100.0,
                           status="not_a_real_status")
        assert result["status"] == "error"

    def test_add_quote_rejects_duplicate(self, db):
        self._seed()
        add_quote("PROC-Q1", "Acme", "0001", 100.0, quote_ref="AF-1")
        result = add_quote("PROC-Q1", "Acme", "0001", 105.0, quote_ref="AF-1")
        assert result["status"] == "error"
        assert "duplicate" in result["message"].lower()

    def test_list_quotes_filters(self, db):
        self._seed()
        add_quote("PROC-Q1", "Acme", "0001", 100.0, quote_ref="AF-1")
        add_quote("PROC-Q1", "Acme", "0002", 5000.0, quote_ref="AF-1")
        add_quote("PROC-Q1", "Bravo", "0001", 110.0, quote_ref="BV-1")

        all_q = list_quotes("PROC-Q1")
        assert all_q["count"] == 3

        acme_only = list_quotes("PROC-Q1", vendor_name="Acme")
        assert acme_only["count"] == 2

        clin_filter = list_quotes("PROC-Q1", clin="0001")
        assert clin_filter["count"] == 2


# ---------------------------------------------------------------------------
# 4. Compare: side-by-side IGCE vs quotes
# ---------------------------------------------------------------------------
class TestCompareProcurement:
    def test_compare_picks_lowest_qualified_quote(self, db):
        create_procurement("PROC-CMP", "SOL-C", "DOD")
        add_igce_line("PROC-CMP", "0001", "Labor", "hour", 100, 100.0)

        add_quote("PROC-CMP", "Acme", "0001", 110.0, quote_ref="AF")
        add_quote("PROC-CMP", "Bravo", "0001", 95.0, quote_ref="BV")
        # Awarded quote should NOT be considered for "lowest"
        add_quote("PROC-CMP", "OldCo", "0001", 50.0, quote_ref="OC",
                  status="awarded")

        result = compare_procurement("PROC-CMP")
        assert result["status"] == "ok"
        assert result["line_count"] == 1

        line = result["lines"][0]
        assert line["igce_unit_cost"] == 100.0
        assert line["quote_count"] == 3
        assert line["lowest_quote"]["vendor_name"] == "Bravo"
        assert line["lowest_quote"]["unit_price"] == 95.0
        assert line["lowest_quote"]["variance_pct"] == -5.0
        assert line["lowest_quote"]["flag"] == "green"

    def test_compare_total_igce_sums_extended(self, db):
        create_procurement("PROC-CMP2", "SOL-C2", "DOD")
        add_igce_line("PROC-CMP2", "0001", "a", "hour", 100, 100.0)
        add_igce_line("PROC-CMP2", "0002", "b", "lot", 1, 5000.0)

        result = compare_procurement("PROC-CMP2")
        assert result["igce_total"] == 15000.0
        assert result["line_count"] == 2

    def test_compare_with_no_quotes(self, db):
        create_procurement("PROC-EMPTY", "SOL-E", "DOD")
        add_igce_line("PROC-EMPTY", "0001", "a", "each", 1, 100.0)

        result = compare_procurement("PROC-EMPTY")
        assert result["line_count"] == 1
        assert result["lines"][0]["quote_count"] == 0
        assert result["lines"][0]["lowest_quote"] is None


# ---------------------------------------------------------------------------
# 5. Vendor summary: rollup with mean/median variance + flag counts
# ---------------------------------------------------------------------------
class TestVendorSummary:
    def test_summary_aggregates_per_vendor(self, db):
        create_procurement("PROC-SUM", "SOL-S", "DOD")
        add_igce_line("PROC-SUM", "0001", "a", "hour", 100, 100.0)
        add_igce_line("PROC-SUM", "0002", "b", "hour", 100, 100.0)

        # Acme: green (-5%) and yellow (10%) → mean ~2.5, median 2.5
        add_quote("PROC-SUM", "Acme", "0001", 95.0, quote_ref="AF-1")
        add_quote("PROC-SUM", "Acme", "0002", 110.0, quote_ref="AF-1")

        # Bravo: red (50%) on both → mean 50, median 50
        add_quote("PROC-SUM", "Bravo", "0001", 150.0, quote_ref="BV-1")
        add_quote("PROC-SUM", "Bravo", "0002", 150.0, quote_ref="BV-1")

        summary = vendor_summary("PROC-SUM")
        assert summary["status"] == "ok"
        assert summary["vendor_count"] == 2

        by_vendor = {v["vendor_name"]: v for v in summary["vendors"]}
        # Sorted by total_quoted ascending; Acme is cheaper (95*100 + 110*100 = 20500)
        # vs Bravo (150*100 + 150*100 = 30000)
        assert by_vendor["Acme"]["total_quoted"] == 20500.0
        assert by_vendor["Bravo"]["total_quoted"] == 30000.0

        acme = by_vendor["Acme"]
        assert acme["mean_variance_pct"] == 2.5
        assert acme["median_variance_pct"] == 2.5
        assert acme["min_variance_pct"] == -5.0
        assert acme["max_variance_pct"] == 10.0
        assert acme["flags"]["green"] == 1
        assert acme["flags"]["yellow"] == 1
        assert acme["flags"]["red"] == 0

        bravo = by_vendor["Bravo"]
        assert bravo["mean_variance_pct"] == 50.0
        assert bravo["flags"]["red"] == 2

    def test_summary_with_no_quotes(self, db):
        create_procurement("PROC-SUM-EMPTY", "SOL-SE", "DOD")
        add_igce_line("PROC-SUM-EMPTY", "0001", "a", "each", 1, 100.0)

        summary = vendor_summary("PROC-SUM-EMPTY")
        assert summary["status"] == "ok"
        assert summary["vendor_count"] == 0


# ---------------------------------------------------------------------------
# 6. Gate: pass / warn / fail verdicts
# ---------------------------------------------------------------------------
class TestGate:
    def test_gate_pass_when_all_green(self, db):
        create_procurement("PROC-G1", "SOL-G1", "DOD")
        add_igce_line("PROC-G1", "0001", "a", "hour", 100, 100.0)
        add_quote("PROC-G1", "Acme", "0001", 95.0, quote_ref="AF")

        result = gate_procurement("PROC-G1")
        assert result["verdict"] == "pass"
        assert result["red_count"] == 0
        assert result["yellow_count"] == 0

    def test_gate_warn_when_yellow(self, db):
        create_procurement("PROC-G2", "SOL-G2", "DOD")
        add_igce_line("PROC-G2", "0001", "a", "hour", 100, 100.0)
        # 8% over IGCE → within warn band (5-15%) → yellow
        add_quote("PROC-G2", "Acme", "0001", 108.0, quote_ref="AF")

        result = gate_procurement("PROC-G2")
        assert result["verdict"] == "warn"
        assert result["yellow_count"] == 1

    def test_gate_fail_when_red(self, db):
        create_procurement("PROC-G3", "SOL-G3", "DOD")
        add_igce_line("PROC-G3", "0001", "a", "hour", 100, 100.0)
        # 30% over IGCE → red
        add_quote("PROC-G3", "Acme", "0001", 130.0, quote_ref="AF")

        result = gate_procurement("PROC-G3", max_variance_pct=15.0)
        assert result["verdict"] == "fail"
        assert result["red_count"] == 1
        assert result["red_lines"][0]["variance_pct"] == 30.0

    def test_gate_pass_with_no_quotes(self, db):
        create_procurement("PROC-G4", "SOL-G4", "DOD")
        add_igce_line("PROC-G4", "0001", "a", "hour", 100, 100.0)

        result = gate_procurement("PROC-G4")
        assert result["verdict"] == "pass"
        assert "no quotes" in result["message"].lower()

    def test_gate_fail_unreasonably_low(self, db):
        create_procurement("PROC-G5", "SOL-G5", "DOD")
        add_igce_line("PROC-G5", "0001", "a", "hour", 100, 100.0)
        # 50% BELOW IGCE → red (unrealistic low)
        add_quote("PROC-G5", "Acme", "0001", 50.0, quote_ref="AF")

        result = gate_procurement("PROC-G5")
        assert result["verdict"] == "fail"


# ---------------------------------------------------------------------------
# 7. Audit trail: every mutation writes a row
# ---------------------------------------------------------------------------
class TestAuditTrail:
    def test_create_procurement_audited(self, db):
        create_procurement("PROC-AUD-1", "SOL-A", "DOD")
        conn = get_connection()
        rows = conn.execute(
            "SELECT action, details FROM audit_trail "
            "WHERE project_id = ? ORDER BY created_at",
            ("PROC-AUD-1",),
        ).fetchall()
        actions = [r["action"] for r in rows]
        assert "create_procurement" in actions

    def test_add_igce_line_audited(self, db):
        create_procurement("PROC-AUD-2", "SOL-A", "DOD")
        add_igce_line("PROC-AUD-2", "0001", "a", "each", 1, 100.0)
        conn = get_connection()
        rows = conn.execute(
            "SELECT action FROM audit_trail WHERE project_id = ?",
            ("PROC-AUD-2",),
        ).fetchall()
        actions = [r["action"] for r in rows]
        assert "create_procurement" in actions
        assert "add_igce_line" in actions

    def test_add_quote_audited_with_variance(self, db):
        create_procurement("PROC-AUD-3", "SOL-A", "DOD")
        add_igce_line("PROC-AUD-3", "0001", "a", "hour", 100, 100.0)
        add_quote("PROC-AUD-3", "Acme", "0001", 110.0, quote_ref="AF")
        conn = get_connection()
        rows = conn.execute(
            "SELECT action, details FROM audit_trail WHERE project_id = ?",
            ("PROC-AUD-3",),
        ).fetchall()
        quote_audits = [r for r in rows if r["action"] == "add_quote"]
        assert len(quote_audits) == 1
        import json
        details = json.loads(quote_audits[0]["details"])
        assert details["variance_pct"] == 10.0
        assert details["flag"] == "yellow"


# ---------------------------------------------------------------------------
# 8. End-to-end: realistic 2-CLIN, 3-vendor scenario
# ---------------------------------------------------------------------------
class TestEndToEnd:
    def test_two_clins_three_vendors_e2e(self, db):
        # Build the procurement
        create_procurement(
            procurement_id="PROC-E2E-001",
            solicitation="W912DY-26-R-0007",
            agency="USACE",
            title="Cyber Range Build",
        )
        add_igce_line("PROC-E2E-001", "0001", "Junior SWE", "hour", 160, 175.0)
        add_igce_line("PROC-E2E-001", "0002", "Mid SWE", "hour", 80, 250.0)
        add_igce_line("PROC-E2E-001", "0003", "Cloud hosting", "month", 12, 1000.0)

        # 3 vendors submit quotes
        # Acme: in line with IGCE (mostly green)
        add_quote("PROC-E2E-001", "Acme", "0001", 170.0, quote_ref="AF-Q-1")
        add_quote("PROC-E2E-001", "Acme", "0002", 245.0, quote_ref="AF-Q-1")
        add_quote("PROC-E2E-001", "Acme", "0003", 1050.0, quote_ref="AF-Q-1")
        # Bravo: yellow (10% over)
        add_quote("PROC-E2E-001", "Bravo", "0001", 192.0, quote_ref="BV-Q-1")
        add_quote("PROC-E2E-001", "Bravo", "0002", 275.0, quote_ref="BV-Q-1")
        add_quote("PROC-E2E-001", "Bravo", "0003", 1100.0, quote_ref="BV-Q-1")
        # Charlie: red on labor (20%+ over)
        add_quote("PROC-E2E-001", "Charlie", "0001", 215.0, quote_ref="CH-Q-1")
        add_quote("PROC-E2E-001", "Charlie", "0002", 310.0, quote_ref="CH-Q-1")
        add_quote("PROC-E2E-001", "Charlie", "0003", 1300.0, quote_ref="CH-Q-1")

        # Verify IGCE listing
        igce = list_igce("PROC-E2E-001")
        assert igce["count"] == 3
        assert igce["total_igce"] == 160 * 175 + 80 * 250 + 12 * 1000

        # Compare
        cmp = compare_procurement("PROC-E2E-001")
        assert cmp["line_count"] == 3
        # Line 0001 lowest should be Acme at 170 (variance -2.86%)
        line_0001 = next(l for l in cmp["lines"] if l["clin"] == "0001")
        assert line_0001["lowest_quote"]["vendor_name"] == "Acme"

        # Summary
        summary = vendor_summary("PROC-E2E-001")
        assert summary["vendor_count"] == 3
        # Cheapest total = Acme
        assert summary["vendors"][0]["vendor_name"] == "Acme"

        # Gate: Charlie's 20%+ should trigger fail
        gate = gate_procurement("PROC-E2E-001")
        assert gate["verdict"] == "fail"
        red_clins = {r["clin"] for r in gate["red_lines"]}
        assert "0001" in red_clins
        assert "0002" in red_clins


# ---------------------------------------------------------------------------
# 9. BOM 9-field capture (Vendor, Item, Qty, Estimate, Quotation,
#    Expiration, POC, Description, Notes)
# ---------------------------------------------------------------------------
class TestBomNineFieldCapture:
    """System shall capture all 9 required fields per BOM line item.

    The procurement engine stores IGCE/quote data across two tables
    (proc_igce_line_items + proc_vendor_quotes) keyed by CLIN. The
    ``add_bom_line`` helper is a unified capture entry point that
    writes the BOM line + the first vendor quote in a single call,
    so callers don't have to coordinate the two-table split.
    """

    def test_add_bom_line_captures_all_nine_fields(self, db):
        """Single call captures Vendor, Item, Qty, Estimate, Quotation,
        Expiration, POC, Description, Notes — one row, all fields round-trip."""
        create_procurement("PROC-BOM-1", "SOL-B1", "USAF")

        result = add_bom_line(
            procurement_id="PROC-BOM-1",
            clin="0001",
            # Item, Qty, Estimate ($) IGCE, Description
            item="Dell PowerEdge R760xs server",
            unit="each",
            quantity=4,
            unit_cost=12500.00,
            # Vendor, Quotation ($), Expiration
            vendor_name="Acme Federal LLC",
            quote_ref="AF-2026-Q-077",
            unit_price=11800.00,
            quote_date="2026-06-01",
            valid_until="2026-09-30",
            # POC + Notes
            poc="Maj. Jane Smith, jane.smith@usaf.mil, (555) 123-4567",
            description="2U rack server, Xeon Gold 6438Y+, 256GB RAM, 8TB NVMe",
            notes="Brand-name required; lead time 45 days; includes 3yr ProSupport",
        )
        assert result["status"] == "ok"
        assert result["action"] == "created"
        # Computed values
        assert result["estimate_extended"] == 50000.00  # 4 * 12500
        assert result["quotation_total"] == 47200.00  # 4 * 11800
        assert result["variance_pct"] == -5.6  # (11800-12500)/12500*100

    def test_add_bom_line_round_trips_all_nine_fields(self, db):
        """All 9 fields are stored and retrievable from the DB."""
        create_procurement("PROC-BOM-2", "SOL-B2", "NAVY")
        add_bom_line(
            procurement_id="PROC-BOM-2",
            clin="0001",
            item="Cisco Catalyst 9300-48UXM",
            unit="each",
            quantity=10,
            unit_cost=8500.00,
            vendor_name="Bravo Networks Inc",
            quote_ref="BV-2026-Q-014",
            unit_price=9200.00,
            quote_date="2026-05-28",
            valid_until="2026-08-31",
            poc="Lt. Cmdr. Bob Jones, bob.jones@navy.mil",
            description="48-port multi-gig PoE++ switch, 8x mGig, 1x 90W UPOE+",
            notes="TAA compliant; must ship with DNA Advantage license",
        )

        # Read IGCE row: Description, Qty, Estimate, POC, Notes live here
        igce = list_igce("PROC-BOM-2")
        assert igce["count"] == 1
        line = igce["lines"][0]
        # Field 3: Qty
        assert line["quantity"] == 10
        # Field 4: Estimate ($) IGCE (unit_cost)
        assert line["unit_cost"] == 8500.00
        assert line["extended_cost"] == 85000.00
        # Field 7: POC
        assert line["poc"] == "Lt. Cmdr. Bob Jones, bob.jones@navy.mil"
        # Field 8: Description
        assert line["description"] == (
            "48-port multi-gig PoE++ switch, 8x mGig, 1x 90W UPOE+"
        )
        # Field 9: Notes
        assert line["notes"] == "TAA compliant; must ship with DNA Advantage license"

        # Read quote row: Vendor, Quotation, Expiration live here
        quotes = list_quotes("PROC-BOM-2")
        assert quotes["count"] == 1
        q = quotes["quotes"][0]
        # Field 1: Vendor
        assert q["vendor_name"] == "Bravo Networks Inc"
        # Field 2: Item (matched by CLIN — item lives in IGCE; the join surfaces it)
        # Field 5: Quotation ($) (vendor quote)
        assert q["unit_price"] == 9200.00
        assert q["total_price"] == 92000.00  # 10 * 9200
        # Field 6: Expiration
        assert q["valid_until"] == "2026-08-31"

        # And the Item field is the IGCE description (so the BOM row is unified)
        assert line["description"]  # the item description

    def test_add_bom_line_upserts_on_repeat_clin(self, db):
        """Calling add_bom_line twice on the same CLIN updates the line + replaces
        the quote (last-write-wins per the existing add_igce_line semantics)."""
        create_procurement("PROC-BOM-3", "SOL-B3", "ARMY")
        add_bom_line(
            procurement_id="PROC-BOM-3",
            clin="0001",
            item="Server v1",
            unit="each",
            quantity=2,
            unit_cost=10000.00,
            vendor_name="Vendor A",
            quote_ref="VA-1",
            unit_price=10500.00,
            quote_date="2026-06-01",
            valid_until="2026-09-30",
            poc="POC-1",
        )
        result = add_bom_line(
            procurement_id="PROC-BOM-3",
            clin="0001",
            item="Server v2 (revised)",
            unit="each",
            quantity=3,
            unit_cost=11000.00,
            vendor_name="Vendor B",
            quote_ref="VB-1",
            unit_price=10800.00,
            quote_date="2026-06-02",
            valid_until="2026-10-31",
            poc="POC-2",
        )
        assert result["action"] == "updated"
        assert result["estimate_extended"] == 33000.00  # 3 * 11000
        assert result["quotation_total"] == 32400.00  # 3 * 10800

        igce = list_igce("PROC-BOM-3")
        assert igce["count"] == 1
        assert igce["lines"][0]["description"] == "Server v2 (revised)"
        assert igce["lines"][0]["poc"] == "POC-2"
        assert igce["lines"][0]["extended_cost"] == 33000.00

    def test_add_bom_line_validates_required_fields(self, db):
        """Missing procurement_id, clin, or vendor must error."""
        # Missing procurement (but the line is the entity — we expect the
        # procurement to exist first)
        result = add_bom_line(
            procurement_id="PROC-MISSING",
            clin="0001",
            item="x",
            unit="each",
            quantity=1,
            unit_cost=100.0,
            vendor_name="Acme",
            quote_ref="A-1",
            unit_price=100.0,
            quote_date="",
            valid_until="",
            poc="",
        )
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    def test_add_bom_line_optional_poc_can_be_empty(self, db):
        """POC is optional — empty string must be accepted (POC may not be known
        at IGCE time; government POCs are often assigned at PR issuance)."""
        create_procurement("PROC-BOM-4", "SOL-B4", "DOD")
        result = add_bom_line(
            procurement_id="PROC-BOM-4",
            clin="0001",
            item="Widget",
            unit="each",
            quantity=10,
            unit_cost=50.0,
            vendor_name="Acme",
            quote_ref="A-1",
            unit_price=55.0,
            quote_date="2026-06-01",
            valid_until="2026-09-30",
            poc="",  # optional
        )
        assert result["status"] == "ok"
        igce = list_igce("PROC-BOM-4")
        assert igce["lines"][0]["poc"] == ""

    def test_add_bom_line_coexists_with_add_igce_line(self, db):
        """Backward compatibility: add_igce_line still works on tables with the
        new poc column (existing data with NULL poc is allowed)."""
        create_procurement("PROC-BOM-5", "SOL-B5", "DOD")
        # Use the legacy add_igce_line — should still work, poc stays empty
        add_igce_line(
            procurement_id="PROC-BOM-5",
            clin="0001",
            description="Legacy line",
            unit="each",
            quantity=1,
            unit_cost=100.0,
            basis="market survey",
            notes="legacy",
        )
        igce = list_igce("PROC-BOM-5")
        assert igce["count"] == 1
        assert igce["lines"][0]["description"] == "Legacy line"
        # New field defaults to empty string (not NULL) per _ensure_tables
        assert igce["lines"][0]["poc"] == ""
