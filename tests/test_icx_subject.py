# CUI // SP-CTI
"""icx-sub-01 -- a verification result names what it assessed (ICDEV[OS] Phase 162).

tools/modernization/apptwin_export.py computes ICDEV[OS]'s inventory digest exactly as ICDEV[OS] does (proved against a
bundle ICDEV[OS] itself bound: tests/fixtures/icos/syn-isr-d.apptwin.json); tools/compliance/portable_result.py and
tools/workflow/app_coherence.py record it as the result's ``subject`` when told which bundle's code they assessed; the
exporter never stamps one.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.compliance import portable_result
from tools.modernization import apptwin_export as ex
from tools.workflow import app_coherence

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "icos" / "syn-isr-d.apptwin.json"


def _bundle() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_digest_is_the_one_icdev_os_computed():
    b = _bundle()
    assert ex.inventory_digest(b) == b["ivv"]["subject"]["inventory_digest"]    # ICDEV[OS] wrote this value
    assert ex.subject(b) == {"version": "7.1", "inventory_digest": b["ivv"]["subject"]["inventory_digest"]}


def test_left_out_and_given_empty_are_the_same_and_a_change_is_not():
    b = _bundle()
    d = ex.inventory_digest(b)
    for f in b["features"]:
        f["evidence"] = []
        f["signatures"]["routes"] = []
    b["components"][0]["path"] = b["components"][0].get("path")
    assert ex.inventory_digest(b) == d
    b["features"][0]["title"] += " (refactored)"
    assert ex.inventory_digest(b) != d
    b.pop("consumers")
    assert ex.inventory_digest(b) != ex.inventory_digest({**b, "consumers": []})       # left out is null, not empty


def test_the_assessors_record_the_subject_and_the_exporter_never_stamps_one(tmp_path, monkeypatch):
    b = _bundle()
    sec = app_coherence.bundle_section({"assessed_at": "2026-11-08T07:00:00Z",
                                        "checks": [{"id": "schema_code", "status": "pass"}]}, b)
    assert sec["subject"] == ex.subject(b)
    assert "subject" not in app_coherence.bundle_section({"assessed_at": "x", "checks": []})
    p = tmp_path / "app.json"
    p.write_text(json.dumps(b))
    out = tmp_path / "sbd.json"
    monkeypatch.setattr("tools.compliance.sbd_assessor.run_sbd_assessment",
                        lambda pid, project_dir=None: {"overall_score": 81.0, "critical_not_satisfied": 0}, raising=False)
    monkeypatch.setattr(portable_result, "sbd_section", lambda r, at: {"tool": "icdev-it/sbd_assessor",
                                                                       "assessed_at": "2026-11-08T07:00:00Z",
                                                                       "score": 81.0, "critical_not_satisfied": 0})
    assert portable_result.main(["sbd", "--project-id", "p", "--emit-json", str(out), "--bundle", str(p)]) == 0
    assert json.loads(out.read_text())["subject"] == ex.subject(b)
    assert "never stamps a subject" in ex.__doc__
