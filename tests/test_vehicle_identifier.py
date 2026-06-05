"""Unit tests for tools.govcon.vehicle_identifier.

Covers:
  1. Schema: pgv_procurement_vehicles, pgv_vehicle_match, pgv_initiative_viability
  2. Catalog seed: default vehicles are inserted and idempotent
  3. NAICS match logic: prefix-based matching against vehicle's naics_prefixes
  4. Scoring: GWAC/IDIQ rank above open market; non-matching NAICS scores 0
  5. match_initiative: persists top-N matches, marks one as recommended,
     sets viability + recommended_path
  6. flag_unviable_initiatives: surfaces initiatives with viable=0
  7. CLI: --seed, --match-initiative, --flag-unviable, --summary
  8. Idempotency: running match_initiative twice clears stale matches
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

# Ensure repo root is on sys.path and SQLite is forced (mirrors conftest)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"

import pytest

from tools.db.storage import get_connection  # noqa: E402

# Import the module under test
from tools.govcon import vehicle_identifier  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: per-test fresh SQLite DB
# ---------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path, monkeypatch):
    """Yield a fresh icdev.db for each test."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    # Bootstrap schema
    vehicle_identifier.ensure_tables(get_connection())
    yield db_path


# ---------------------------------------------------------------------------
# 1. Schema
# ---------------------------------------------------------------------------
class TestSchema:
    def test_procurement_vehicles_table_exists(self, db):
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='pgv_procurement_vehicles'"
        ).fetchone()
        assert row is not None

    def test_vehicle_match_table_exists(self, db):
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='pgv_vehicle_match'"
        ).fetchone()
        assert row is not None

    def test_viability_table_exists(self, db):
        conn = get_connection()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='pgv_initiative_viability'"
        ).fetchone()
        assert row is not None

    def test_indexes_created(self, db):
        # vehicle_type index lives on pgv_procurement_vehicles
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='pgv_procurement_vehicles'"
            ).fetchall()
            names = {r[0] for r in rows}
            # Debug: print all index names if assertion fails
            if not any("vehicle_type" in (n or "") for n in names):
                print("DEBUG indexes on pgv_procurement_vehicles:", names)
                # Try fetching from raw sqlite3 too
                import sqlite3
                import os
                raw = sqlite3.connect(os.environ.get("ICDEV_DB_PATH", "data/icdev.db"))
                raw_rows = raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='pgv_procurement_vehicles'"
                ).fetchall()
                print("DEBUG raw sqlite3 indexes:", [r[0] for r in raw_rows])
                raw.close()
            assert any("vehicle_type" in (n or "") for n in names)
            # match.recommended index lives on pgv_vehicle_match
            rows2 = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='pgv_vehicle_match'"
            ).fetchall()
            names2 = {r[0] for r in rows2}
            assert any("recommended" in (n or "") for n in names2)


# ---------------------------------------------------------------------------
# 2. Catalog seed
# ---------------------------------------------------------------------------
class TestSeed:
    def test_seed_inserts_all_defaults(self, db):
        result = vehicle_identifier.seed_default_vehicles()
        assert result["inserted"] == len(vehicle_identifier.DEFAULT_VEHICLES)
        assert result["skipped"] == 0

    def test_seed_is_idempotent(self, db):
        first = vehicle_identifier.seed_default_vehicles()
        second = vehicle_identifier.seed_default_vehicles()
        assert first["inserted"] > 0
        assert second["inserted"] == 0
        assert second["skipped"] == first["inserted"]

    def test_seed_includes_canonical_vehicles(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicles = vehicle_identifier.list_vehicles()
        names = {v["vehicle_name"] for v in vehicles}
        # The task explicitly enumerates these vehicle types
        for required in ("OASIS+", "GSA MAS IT-70", "Open Market RFP"):
            assert required in names, f"{required} missing from seed"


# ---------------------------------------------------------------------------
# 3. NAICS match logic
# ---------------------------------------------------------------------------
class TestNaicsMatch:
    def test_empty_vehicle_prefixes_only_matches_empty_naics(self, db):
        # Open vehicle matches ONLY when initiative NAICS is also unspecified
        assert vehicle_identifier._naics_matches("", "") is True
        assert vehicle_identifier._naics_matches("541512", "") is False

    def test_empty_initiative_naics_only_matches_open(self, db):
        assert vehicle_identifier._naics_matches("", "541511,541512") is False
        assert vehicle_identifier._naics_matches("", "") is True

    def test_exact_naics_match(self, db):
        # Full 6-digit NAICS — exact match required
        assert vehicle_identifier._naics_matches("541512", "541511,541512") is True
        assert vehicle_identifier._naics_matches("541519", "541511,541512") is False
        assert vehicle_identifier._naics_matches("541512", "541511") is False
        assert vehicle_identifier._naics_matches("541511", "541511") is True

    def test_unrelated_naics_does_not_match(self, db):
        assert vehicle_identifier._naics_matches("999999", "541511,541512") is False


# ---------------------------------------------------------------------------
# 4. Scoring
# ---------------------------------------------------------------------------
class TestScoring:
    def test_gwac_outscores_open_market(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicles = {v["vehicle_name"]: v for v in vehicle_identifier.list_vehicles()}
        gwac_score = vehicle_identifier._score_vehicle_for_initiative(
            vehicles["OASIS+"], "541512", 1_500_000
        )
        open_market = vehicle_identifier._score_vehicle_for_initiative(
            vehicles["Open Market RFP"], "541512", 1_500_000
        )
        assert gwac_score > open_market

    def test_non_matching_naics_scores_zero(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicles = {v["vehicle_name"]: v for v in vehicle_identifier.list_vehicles()}
        # Polaris is GWAC; OASIS+ accepts 541512. Polaris prefixes are 541511,541512,541519,541715
        # (still includes 541512). Try a NAICS that won't match any prefix-restricted vehicle.
        score = vehicle_identifier._score_vehicle_for_initiative(
            vehicles["OASIS+"], "999999", 1_000_000
        )
        assert score == 0.0

    def test_score_capped_at_100(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicles = {v["vehicle_name"]: v for v in vehicle_identifier.list_vehicles()}
        score = vehicle_identifier._score_vehicle_for_initiative(
            vehicles["OASIS+"], "541512", 1_000_000
        )
        assert 0.0 < score <= 100.0


# ---------------------------------------------------------------------------
# 5. match_initiative
# ---------------------------------------------------------------------------
class TestMatchInitiative:
    def test_match_with_matching_naics_marks_viable(self, db):
        vehicle_identifier.seed_default_vehicles()
        result = vehicle_identifier.match_initiative(
            initiative_code="INIT-2026-CLD",
            title="Cloud Modernization",
            agency="DoD",
            naics="541512",
            ceiling_usd=1_500_000,
        )
        assert result["viable"] is True
        assert result["recommended_vehicle"] != ""
        assert result["total_matches"] > 0
        assert result["recommended_path"] != ""

    def test_match_persists_recommended_match(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicle_identifier.match_initiative(
            initiative_code="INIT-2026-CYB",
            title="Cybersecurity",
            agency="DoD",
            naics="541512",
            ceiling_usd=2_000_000,
        )
        matches = vehicle_identifier.list_matches(
            initiative_code="INIT-2026-CYB", recommended_only=True
        )
        assert len(matches) == 1
        assert matches[0]["recommended"] is True
        assert matches[0]["viability_score"] > 0

    def test_match_with_no_matching_naics_flags_unviable(self, db):
        vehicle_identifier.seed_default_vehicles()
        result = vehicle_identifier.match_initiative(
            initiative_code="INIT-2026-WEIRD",
            title="Niche initiative",
            agency="DoD",
            naics="999999",
            ceiling_usd=500_000,
        )
        assert result["viable"] is False
        assert result["recommended_vehicle"] == ""
        assert result["flagged_reason"] != ""
        assert "open-market" in result["recommended_path"].lower() or \
               "open market" in result["recommended_path"].lower() or \
               "no standing vehicle" in result["recommended_path"].lower()

    def test_match_idempotent_clears_stale_rows(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicle_identifier.match_initiative(
            initiative_code="INIT-2026-A",
            title="First",
            agency="DoD",
            naics="541512",
            ceiling_usd=1_000_000,
        )
        vehicle_identifier.match_initiative(
            initiative_code="INIT-2026-A",
            title="Updated",
            agency="DoD",
            naics="541512",
            ceiling_usd=2_000_000,
        )
        # Only one set of recommended matches should remain
        matches = vehicle_identifier.list_matches(initiative_code="INIT-2026-A")
        rec_count = sum(1 for m in matches if m["recommended"])
        assert rec_count == 1

    def test_match_blank_initiative_code_raises(self, db):
        with pytest.raises(ValueError):
            vehicle_identifier.match_initiative(initiative_code="")

    def test_persists_viability_record(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicle_identifier.match_initiative(
            initiative_code="INIT-2026-PERS",
            title="Persisted",
            agency="DoD",
            naics="541512",
            ceiling_usd=1_000_000,
        )
        viability = vehicle_identifier.list_viability()
        codes = {v["initiative_code"] for v in viability}
        assert "INIT-2026-PERS" in codes


# ---------------------------------------------------------------------------
# 6. flag_unviable_initiatives
# ---------------------------------------------------------------------------
class TestFlagUnviable:
    def test_flags_only_unviable(self, db):
        vehicle_identifier.seed_default_vehicles()
        # Viable
        vehicle_identifier.match_initiative(
            initiative_code="INIT-OK",
            title="OK",
            agency="DoD",
            naics="541512",
            ceiling_usd=1_000_000,
        )
        # Unviable
        vehicle_identifier.match_initiative(
            initiative_code="INIT-BAD",
            title="Bad",
            agency="DoD",
            naics="999999",
            ceiling_usd=1_000_000,
        )
        flagged = vehicle_identifier.flag_unviable_initiatives()
        codes = {v["initiative_code"] for v in flagged}
        assert "INIT-BAD" in codes
        assert "INIT-OK" not in codes

    def test_empty_when_all_viable(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicle_identifier.match_initiative(
            initiative_code="INIT-OK2",
            title="OK",
            agency="DoD",
            naics="541512",
            ceiling_usd=1_000_000,
        )
        flagged = vehicle_identifier.flag_unviable_initiatives()
        assert flagged == []


# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
class TestSummary:
    def test_summary_counts(self, db):
        vehicle_identifier.seed_default_vehicles()
        vehicle_identifier.match_initiative(
            initiative_code="INIT-A",
            title="A", agency="DoD", naics="541512", ceiling_usd=1_000_000,
        )
        vehicle_identifier.match_initiative(
            initiative_code="INIT-B",
            title="B", agency="DoD", naics="999999", ceiling_usd=1_000_000,
        )
        s = vehicle_identifier.get_viability_summary()
        assert s["initiatives_total"] == 2
        assert s["initiatives_viable"] == 1
        assert s["initiatives_flagged"] == 1
        assert s["vehicles_cataloged"] == len(vehicle_identifier.DEFAULT_VEHICLES)
        assert s["matches_persisted"] >= 1
        assert s["flag_pct"] == 50.0


# ---------------------------------------------------------------------------
# 8. CLI smoke
# ---------------------------------------------------------------------------
class TestCLI:
    def _run_main(self, *argv):
        saved = sys.argv
        sys.argv = ["vehicle_identifier.py", *argv]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                code = vehicle_identifier.main(list(argv))
        finally:
            sys.argv = saved
        return buf.getvalue().strip(), code

    def test_cli_seed(self, db):
        out, code = self._run_main("--seed", "--json")
        assert code == 0
        data = json.loads(out)
        assert data["ok"] is True
        assert data["inserted"] > 0

    def test_cli_match_then_flag(self, db):
        self._run_main("--seed", "--json")
        out, code = self._run_main(
            "--match-initiative",
            "--initiative-code", "INIT-2026-CLI",
            "--title", "CLI Test",
            "--agency", "DoD",
            "--naics", "541512",
            "--ceiling", "1000000",
            "--json",
        )
        assert code == 0
        data = json.loads(out)
        assert data["ok"] is True
        assert data["result"]["viable"] is True

        # Now flag unviable — should be empty (we only made a viable one)
        out2, code2 = self._run_main("--flag-unviable", "--json")
        data2 = json.loads(out2)
        assert data2["ok"] is True
        assert data2["initiatives"] == []

    def test_cli_summary(self, db):
        self._run_main("--seed", "--json")
        self._run_main(
            "--match-initiative",
            "--initiative-code", "INIT-CLI-SUM",
            "--naics", "541512", "--json",
        )
        out, code = self._run_main("--summary", "--json")
        assert code == 0
        data = json.loads(out)
        assert data["ok"] is True
        assert data["summary"]["initiatives_total"] == 1
        assert data["summary"]["vehicles_cataloged"] >= 5

    def test_cli_no_args(self, db):
        out, code = self._run_main()
        # No subcommand → returns 1 (parser.print_help)
        assert code == 1
