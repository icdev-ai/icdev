# CUI // SP-CTI
"""icx-ivv-01 -- IV&V, Secure by Design and RTM as portable, dated results for ICDEV[OS]'s application twin.

* each section says when it was run, that it is ``automated`` (no tool run is an independent IV&V), and when it
  expires; the recommendation and the SbD score come from the same rules the reports use;
* an IV&V area below 60 is seen through the assessor's area CODES (the report's rule reads area names);
* every section meets the ``icos-apptwin/v1`` contract ICDEV[OS] publishes (vendored here by icx-bun-01 when
  present; the shapes are asserted directly otherwise).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tools.compliance import portable_result as pr

AT = datetime(2026, 11, 7, tzinfo=timezone.utc)


def _ivv(overall, areas, findings=()):
    return {"scores": {"overall_score": overall, "area_scores": areas}, "findings": list(findings)}


def test_an_ivv_section_is_dated_automated_and_expires():
    s = pr.ivv_section(_ivv(85.0, {"REQ": 90.0, "CODE": 82.0}), at=AT, valid_days=30)
    assert s["kind"] == "automated" and s["assessor"] is None
    assert s["assessed_at"] == "2026-11-07T00:00:00Z" and s["expires_at"] == "2026-12-07T00:00:00Z"
    assert s["recommendation"] == "CERTIFY" and s["areas"] == {"CODE": 82.0, "REQ": 90.0}


def test_the_recommendation_is_the_reports_rule_area_codes_included():
    assert pr.ivv_section(_ivv(85.0, {"REQ": 90.0, "CODE": 50.0}), at=AT)["recommendation"] == "CONDITIONAL", \
        "an area below 60 holds certification back, though the assessor keys areas by code"
    crit = [{"severity": "critical", "status": "open"}]
    s = pr.ivv_section(_ivv(95.0, {"REQ": 95.0}, crit), at=AT)
    assert s["recommendation"] == "DENY" and s["critical_findings"] == 1
    assert pr.ivv_section(_ivv(55.0, {"REQ": 55.0}), at=AT)["recommendation"] == "DENY"


def test_sbd_uses_the_reports_score_and_counts_critical_gaps():
    results = [{"domain": "Authentication", "priority": "critical", "status": "not_satisfied"},
               {"domain": "Authentication", "priority": "high", "status": "satisfied"},
               {"domain": "Secure Defaults", "priority": "high", "status": "partially_satisfied"}]
    s = pr.sbd_section({"results": results}, at=AT)
    from tools.compliance.sbd_report_generator import _calculate_domain_scores, _calculate_overall_status
    assert s["score"] == _calculate_overall_status(_calculate_domain_scores(results))[0]
    assert s["critical_not_satisfied"] == 1 and s["tool"] == "icdev-it/sbd_assessor"


def test_rtm_carries_coverage_and_each_requirements_tests(tmp_path):
    data = {"requirements": [{"id": "REQ-2"}, {"id": "REQ-1"}],
            "forward_trace": [{"requirement_id": "REQ-1", "test_files": [{"file_path": "tests/test_receive.py"}]}]}
    jf = tmp_path / "rtm-data.json"
    jf.write_text(json.dumps(data), encoding="utf-8")
    s = pr.rtm_section({"coverage": 50.0, "json_file": str(jf)}, at=AT)
    assert s["coverage_pct"] == 50.0
    assert s["requirements"] == [{"id": "REQ-1", "tests": ["tests/test_receive.py"]}, {"id": "REQ-2", "tests": []}]


def test_each_section_meets_the_published_contract():
    schema_path = Path(pr._BASE) / "context" / "icos" / "apptwin.schema.json"
    secs = {"ivv": pr.ivv_section(_ivv(70.0, {"REQ": 70.0}), at=AT), "sbd": pr.sbd_section({"results": []}, at=AT),
            "rtm": pr.rtm_section({"coverage": 0}, at=AT)}
    if not schema_path.exists():                     # the contract arrives with icx-bun-01
        assert set(secs["ivv"]) >= {"assessed_at", "tool", "recommendation", "overall_score", "critical_findings",
                                    "kind", "expires_at"}
        return
    import jsonschema
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    for name, sec in secs.items():
        sub = {"$defs": schema["$defs"], **schema["$defs"][name.capitalize()]}
        jsonschema.Draft202012Validator(sub, format_checker=jsonschema.FormatChecker()).validate(sec)


def test_an_application_from_its_bundle_is_registered_through_the_canonical_creator(monkeypatch, tmp_path):
    made, sql = {}, []

    class Conn:
        def execute(self, q, p):
            sql.append((q, p))

        def commit(self):
            sql.append("commit")

        def close(self):
            pass
    monkeypatch.setattr("tools.project.project_create.create_project",
                        lambda **kw: made.update(kw) or {"project_id": "proj-123"})
    monkeypatch.setattr("tools.db.storage.get_connection", lambda *a, **k: Conn())
    out = pr.register({"application": {"id": "app-a", "title": "Depot stock", "languages": ["java"]}}, tmp_path)
    assert out == {"project_id": "proj-123", "application": "app-a", "directory": str(tmp_path)}
    assert made["name"] == "apptwin-app-a" and made["skip_scaffold"] is True and made["tech_backend"] == "java"
    assert sql[0] == ("UPDATE projects SET directory_path = %s WHERE id = %s", (str(tmp_path), "proj-123"))
