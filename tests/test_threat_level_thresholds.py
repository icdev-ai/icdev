# CUI // SP-CTI
"""Tests for automated threat-level threshold configuration and PIR alert generation.

Validates:
- Baseline creation (create_baseline)
- Score validation against operator-defined baselines (validate_indicator_score)
- Auto PIR generation when threshold exceeded (auto_generate_pir_alert)
- PIR alert evaluator bridge (evaluate_indicator_and_alert)
- Baseline listing (list_baselines)

NIST 800-53: SI-4, RA-5
"""
from __future__ import annotations

import pytest

from tools.threat_analysis.service import (
    create_baseline,
    validate_indicator_score,
    auto_generate_pir_alert,
    list_baselines,
)
from tools.intelligence.pir_alert_generator import evaluate_indicator_and_alert


@pytest.fixture(autouse=True)
def _default_db_is_icdev_db(icdev_db, monkeypatch):
    """Point the process-default connection at the same temp DB as ``db_path``.

    Only half this flow accepts a ``db_path``. ``validate_indicator_score`` and
    ``create_baseline`` do; ``tools/intelligence/pir_manager.py::create_pir`` —
    which ``auto_generate_pir_alert`` and ``evaluate_indicator_and_alert`` call
    to persist the breach — takes no path at all and resolves ``get_connection()``
    against ``ICDEV_DB_PATH``. Threading ``db_path`` alone therefore reads the
    baseline from the temp DB and writes the PIR to the real ``data/icdev.db``.
    Redirecting the default keeps both halves on one file, and keeps the write
    off any developer's live database.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))


# ---------------------------------------------------------------------------
# create_baseline
# ---------------------------------------------------------------------------

class TestCreateBaseline:
    def test_creates_baseline_with_required_fields(self, icdev_db):
        result = create_baseline(
            indicator_name="malware_score",
            threshold_score=75.0,
            scope="project",
            scope_id="proj-001",
            operator_id="analyst-1",
            db_path=str(icdev_db),
        )
        assert result["indicator_name"] == "malware_score"
        assert result["threshold_score"] == 75.0
        assert result["scope"] == "project"
        assert "id" in result

    def test_creates_global_baseline_without_scope_id(self, icdev_db):
        result = create_baseline(
            indicator_name="network_anomaly",
            threshold_score=50.0,
            scope="global",
            operator_id="sys",
            db_path=str(icdev_db),
        )
        assert result["scope"] == "global"

    def test_rejects_invalid_scope(self, icdev_db):
        with pytest.raises(ValueError, match="Invalid scope"):
            create_baseline(
                indicator_name="x",
                threshold_score=10.0,
                scope="department",
                operator_id="op",
                db_path=str(icdev_db),
            )

    def test_rejects_negative_threshold(self, icdev_db):
        with pytest.raises(ValueError, match="non-negative"):
            create_baseline(
                indicator_name="x",
                threshold_score=-1.0,
                scope="global",
                operator_id="op",
                db_path=str(icdev_db),
            )


# ---------------------------------------------------------------------------
# validate_indicator_score
# ---------------------------------------------------------------------------

class TestValidateIndicatorScore:
    def test_score_below_threshold_is_valid(self, icdev_db):
        create_baseline(
            indicator_name="phishing_score",
            threshold_score=80.0,
            scope="project",
            scope_id="proj-a",
            operator_id="analyst",
            db_path=str(icdev_db),
        )
        result = validate_indicator_score(
            indicator_name="phishing_score",
            score=60.0,
            scope="project",
            scope_id="proj-a",
            db_path=str(icdev_db),
        )
        assert result["valid"] is True
        assert result["exceeded"] is False
        assert result["delta"] == 0.0
        assert result["unbounded"] is False

    def test_score_exceeding_threshold_triggers_breach(self, icdev_db):
        create_baseline(
            indicator_name="exfil_volume",
            threshold_score=100.0,
            scope="project",
            scope_id="proj-b",
            operator_id="analyst",
            severity_band="high",
            db_path=str(icdev_db),
        )
        result = validate_indicator_score(
            indicator_name="exfil_volume",
            score=150.0,
            scope="project",
            scope_id="proj-b",
            db_path=str(icdev_db),
        )
        assert result["exceeded"] is True
        assert result["valid"] is False
        assert result["delta"] == 50.0
        assert result["severity_band"] == "high"
        assert result["threshold"] == 100.0

    def test_no_baseline_returns_unbounded(self, icdev_db):
        result = validate_indicator_score(
            indicator_name="unknown_indicator",
            score=999.0,
            scope="project",
            scope_id="proj-x",
            db_path=str(icdev_db),
        )
        assert result["unbounded"] is True
        assert result["exceeded"] is False
        assert result["valid"] is True

    def test_global_baseline_fallback(self, icdev_db):
        create_baseline(
            indicator_name="c2_beacon_rate",
            threshold_score=20.0,
            scope="global",
            operator_id="global-admin",
            db_path=str(icdev_db),
        )
        # No project-scoped baseline exists; should fall back to global
        result = validate_indicator_score(
            indicator_name="c2_beacon_rate",
            score=25.0,
            scope="project",
            scope_id="any-project",
            db_path=str(icdev_db),
        )
        assert result["exceeded"] is True
        assert result["scope_used"] == "global"

    def test_rejects_negative_score(self, icdev_db):
        with pytest.raises(ValueError, match="non-negative"):
            validate_indicator_score(
                indicator_name="x",
                score=-1.0,
                db_path=str(icdev_db),
            )

    def test_project_baseline_wins_over_global(self, icdev_db):
        create_baseline(
            indicator_name="dns_entropy",
            threshold_score=50.0,
            scope="global",
            operator_id="global-admin",
            db_path=str(icdev_db),
        )
        create_baseline(
            indicator_name="dns_entropy",
            threshold_score=90.0,
            scope="project",
            scope_id="proj-c",
            operator_id="local-analyst",
            db_path=str(icdev_db),
        )
        # Score exceeds global but NOT project threshold
        result = validate_indicator_score(
            indicator_name="dns_entropy",
            score=70.0,
            scope="project",
            scope_id="proj-c",
            db_path=str(icdev_db),
        )
        assert result["exceeded"] is False  # project baseline (90) wins
        assert result["scope_used"] == "project"


# ---------------------------------------------------------------------------
# auto_generate_pir_alert
# ---------------------------------------------------------------------------

class TestAutoGeneratePirAlert:
    def test_pir_generated_on_breach(self, icdev_db):
        create_baseline(
            indicator_name="lateral_movement",
            threshold_score=40.0,
            scope="global",
            severity_band="critical",
            operator_id="sys",
            db_path=str(icdev_db),
        )
        result = auto_generate_pir_alert(
            indicator_name="lateral_movement",
            score=55.0,
            scope="global",
            operator_id="analyst-2",
            db_path=str(icdev_db),
        )
        assert result["pir_generated"] is True
        assert result["pir_id"] is not None
        assert result["exceeded"] is True

    def test_no_pir_when_score_under_threshold(self, icdev_db):
        create_baseline(
            indicator_name="port_scan",
            threshold_score=60.0,
            scope="global",
            operator_id="sys",
            db_path=str(icdev_db),
        )
        result = auto_generate_pir_alert(
            indicator_name="port_scan",
            score=30.0,
            scope="global",
            db_path=str(icdev_db),
        )
        assert result["pir_generated"] is False
        assert result["pir_id"] is None

    def test_no_pir_when_unbounded(self, icdev_db):
        result = auto_generate_pir_alert(
            indicator_name="no_such_indicator",
            score=99.0,
            scope="global",
            db_path=str(icdev_db),
        )
        assert result["pir_generated"] is False
        assert result["unbounded"] is True


# ---------------------------------------------------------------------------
# evaluate_indicator_and_alert (PIR alert generator bridge)
# ---------------------------------------------------------------------------

class TestEvaluateIndicatorAndAlert:
    def test_returns_alert_on_breach(self, icdev_db):
        create_baseline(
            indicator_name="ransomware_indicator",
            threshold_score=70.0,
            scope="project",
            scope_id="op-1",
            severity_band="critical",
            operator_id="analyst",
            db_path=str(icdev_db),
        )
        result = evaluate_indicator_and_alert(
            indicator_name="ransomware_indicator",
            score=85.0,
            scope="project",
            scope_id="op-1",
            operator_id="analyst",
            db_path=str(icdev_db),
        )
        assert result["alert"] is not None
        assert result["alert"]["pir_type"] == "PIR"
        assert "ransomware_indicator threshold exceeded" in result["alert"]["topic"]
        assert result["validation"]["exceeded"] is True

    def test_returns_no_alert_below_threshold(self, icdev_db):
        create_baseline(
            indicator_name="insider_threat",
            threshold_score=90.0,
            scope="project",
            scope_id="op-2",
            operator_id="analyst",
            db_path=str(icdev_db),
        )
        result = evaluate_indicator_and_alert(
            indicator_name="insider_threat",
            score=50.0,
            scope="project",
            scope_id="op-2",
            db_path=str(icdev_db),
        )
        assert result["alert"] is None
        assert result["validation"]["exceeded"] is False

    def test_pir_type_passthrough(self, icdev_db):
        create_baseline(
            indicator_name="supply_chain_risk",
            threshold_score=30.0,
            scope="global",
            operator_id="sys",
            db_path=str(icdev_db),
        )
        result = evaluate_indicator_and_alert(
            indicator_name="supply_chain_risk",
            score=40.0,
            scope="global",
            pir_type="CCIR",
            db_path=str(icdev_db),
        )
        assert result["alert"]["pir_type"] == "CCIR"


# ---------------------------------------------------------------------------
# list_baselines
# ---------------------------------------------------------------------------

class TestListBaselines:
    def test_list_all_baselines(self, icdev_db):
        create_baseline("ind_a", 10.0, scope="global", operator_id="op", db_path=str(icdev_db))
        create_baseline("ind_b", 20.0, scope="global", operator_id="op", db_path=str(icdev_db))
        baselines = list_baselines(db_path=str(icdev_db))
        names = [b["indicator_name"] for b in baselines]
        assert "ind_a" in names
        assert "ind_b" in names

    def test_filter_by_indicator_name(self, icdev_db):
        create_baseline("filter_me", 10.0, scope="global", operator_id="op", db_path=str(icdev_db))
        create_baseline("ignore_me", 20.0, scope="global", operator_id="op", db_path=str(icdev_db))
        baselines = list_baselines(indicator_name="filter_me", db_path=str(icdev_db))
        assert all(b["indicator_name"] == "filter_me" for b in baselines)
        assert len(baselines) == 1

    def test_filter_by_scope(self, icdev_db):
        create_baseline("scoped_ind", 10.0, scope="project", scope_id="p1", operator_id="op", db_path=str(icdev_db))
        create_baseline("global_ind", 20.0, scope="global", operator_id="op", db_path=str(icdev_db))
        project_baselines = list_baselines(scope="project", db_path=str(icdev_db))
        scopes = {b["scope"] for b in project_baselines}
        assert scopes == {"project"}
