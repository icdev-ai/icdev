# CUI // SP-CTI
"""Tests: scheduled at-rest PII scan + remediation reflex (trust-mask-03).

Covers:
    - db_scanner.remediation_plan() / _recommend_treatment()
    - redaction_scan_reflex._card_id() determinism
    - redaction_scan_reflex._file_remediation_cards() dedup ids + cap
    - redaction_scan_reflex.run() smoke (stubbed scanner + kanban)
"""

import importlib

ds = importlib.import_module("tools.redaction.db_scanner")
reflex = importlib.import_module("tools.genesis.reflexes.redaction_scan_reflex")
# Shim-aware: patch attributes on the resolved module objects, not string paths
# (tools.* vs icdev.tools.* are distinct under the compat shim).
_tf = importlib.import_module("tools.kanban.task_factory")
_dsmod = importlib.import_module("tools.redaction.db_scanner")


_SCAN = {
    "status": "ok",
    "tables_scanned": 2,
    "columns_with_pii": 3,
    "tables": {
        "proposal_knowledge_base": {
            "row_count": 10,
            "columns": {
                "body": {"pii_density": 0.8, "entity_types": {"US_SSN": 4, "PERSON": 2}},
                "title": {"pii_density": 0.1, "entity_types": {"PERSON": 1}},
            },
        },
        "pg_crm_contacts": {
            "row_count": 5,
            "columns": {
                "notes": {"pii_density": 0.4, "entity_types": {"PERSON": 3}},
            },
        },
    },
}


class TestRecommendTreatment:
    def test_hard_redact_for_ssn(self):
        assert ds._recommend_treatment({"US_SSN": 1, "PERSON": 2}) == "redact"

    def test_surrogate_for_person(self):
        assert ds._recommend_treatment({"PERSON": 3}) == "surrogate"

    def test_mask_default(self):
        assert ds._recommend_treatment({"EMAIL_ADDRESS": 2}) == "mask"
        assert ds._recommend_treatment({}) == "mask"


class TestRemediationPlan:
    def test_filters_by_threshold_and_sorts(self):
        plan = ds.remediation_plan(_SCAN, threshold=0.3)
        # body (0.8) and notes (0.4) pass; title (0.1) excluded
        assert [i["column"] for i in plan] == ["body", "notes"]
        assert plan[0]["table"] == "proposal_knowledge_base"
        assert plan[0]["recommended_treatment"] == "redact"  # SSN present
        assert plan[1]["recommended_treatment"] == "surrogate"  # PERSON only

    def test_empty_when_below_threshold(self):
        assert ds.remediation_plan(_SCAN, threshold=0.9) == []

    def test_handles_missing_tables(self):
        assert ds.remediation_plan({}, threshold=0.3) == []


class TestReflexCardFiling:
    def test_card_id_deterministic(self):
        a = reflex._card_id("t1", "c1")
        assert a == reflex._card_id("t1", "c1")
        assert a != reflex._card_id("t1", "c2")
        assert a.startswith("task-piiscan-")

    def test_files_deduped_cards(self, monkeypatch):
        captured = {}

        def _fake_create(specs):
            captured["specs"] = specs
            return [s["id"] for s in specs]

        monkeypatch.setattr(_tf, "create_tasks", _fake_create)
        plan = ds.remediation_plan(_SCAN, threshold=0.3)
        filed = reflex._file_remediation_cards(plan, max_cards=20)
        assert len(filed) == 2
        specs = captured["specs"]
        assert all(s["id"].startswith("task-piiscan-") for s in specs)
        assert "[PII-SCAN]" in specs[0]["title"]
        assert specs[0]["priority"] == "high"   # density 0.8 >= 0.6
        assert specs[1]["priority"] == "medium"  # density 0.4

    def test_cap_limits_cards(self, monkeypatch):
        monkeypatch.setattr(_tf, "create_tasks", lambda specs: [x["id"] for x in specs])
        plan = ds.remediation_plan(_SCAN, threshold=0.3)
        filed = reflex._file_remediation_cards(plan, max_cards=1)
        assert len(filed) == 1  # capped to top-1 by density


class TestReflexRun:
    def test_run_smoke_with_stubs(self, monkeypatch):
        class _Scanner:
            def __init__(self, *a, **k):
                pass

            def scan(self):
                return _SCAN

        monkeypatch.setattr(_dsmod, "DBScanner", _Scanner)
        monkeypatch.setattr(_tf, "create_tasks", lambda specs: [x["id"] for x in specs])
        result = reflex.run({"pii_density_threshold": 0.3}, None)
        assert result["success"] is True
        assert result["metric_value"] == 2.0
        assert result["details"]["cards_filed"] == 2
        assert result["details"]["flagged"] == 2

    def test_run_never_raises_on_scanner_error(self, monkeypatch):
        class _Boom:
            def __init__(self, *a, **k):
                pass

            def scan(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(_dsmod, "DBScanner", _Boom)
        result = reflex.run({}, None)
        assert result["success"] is False
        assert "db down" in result["error"]
