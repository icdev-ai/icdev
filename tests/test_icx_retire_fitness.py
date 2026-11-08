# CUI // SP-CTI
"""icx-rtr-01 -- 7R retire fitness reads a MEASURED redundancy, and its criteria are the catalog's own.

The old check returned low_usage/redundant_functionality/no_active_development = 0.5 under names no catalog
criterion has, so ``_score_strategy`` scored them POSITIONALLY as other criteria: redundant_functionality counted
as data_disposition, and every application looked half-retirable on no evidence at all.
"""
from __future__ import annotations

import json

from tools.modernization import seven_r_assessor as sr

PROFILE = {"framework": "spring", "framework_version": "5.3", "primary_language": "java", "language_version": "17"}


def _retire_criteria():
    return next(s for s in sr.load_seven_rs_catalog()["strategies"] if s["id"] == "retire")["scoring_criteria"]


def test_retire_checks_are_keyed_by_the_catalogs_criteria():
    crit = _retire_criteria()
    assert set(sr._check_retire_fitness(PROFILE)) == set(crit), "no positional fallback for retire"
    assert abs(sum(c["weight"] for c in crit.values()) - 1.0) < 1e-9


def test_unmeasured_is_left_out_never_half():
    got = sr._check_retire_fitness(PROFILE)
    assert got["functional_redundancy"] is None and got["dependency_analysis"] is None
    assert got["risk_reduction"] == 0.0, "spring 5 / java 17 is not end-of-life"
    # with only risk_reduction measured, the score is exactly it -- not pulled towards 0.5 by unmeasured criteria
    assert sr._score_strategy("retire", got, _retire_criteria()) == 0.0


def test_a_measured_redundancy_moves_retire():
    crit = _retire_criteria()
    none = sr._score_strategy("retire", sr._check_retire_fitness(PROFILE), crit)
    high = sr._score_strategy("retire", sr._check_retire_fitness(PROFILE, {"share": 0.9, "source": "icos overlap"}), crit)
    w_r, w_k = crit["functional_redundancy"]["weight"], crit["risk_reduction"]["weight"]
    assert none == 0.0 and abs(high - (0.9 * w_r) / (w_r + w_k)) < 1e-9
    assert sr._check_retire_fitness(PROFILE, {"share": 4})["functional_redundancy"] == 1.0, "clamped to [0, 1]"


def test_the_redundancy_file_reads_its_three_shapes(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"source": "icdev-os sda.portfolio overlap", "applications": {
        "app-a": {"share": 0.62}, "app-b": 0.1, "app-c": {"note": "not measured"}}}), encoding="utf-8")
    got = sr.load_redundancy(p)
    assert got == {"app-a": {"share": 0.62, "source": "icdev-os sda.portfolio overlap"},
                   "app-b": {"share": 0.1, "source": "icdev-os sda.portfolio overlap"}}


def test_the_mcp_tool_passes_the_measured_redundancy_through(tmp_path, monkeypatch):
    from tools.mcp import modernization_server as ms
    seen = {}

    def fake_assess(project_id, app_id, custom_weights=None, redundancy=None):
        seen.update(app=app_id, redundancy=redundancy)
        return {"ok": True}
    real = ms._import_tool
    monkeypatch.setattr(ms, "_import_tool", lambda mod, fn: fake_assess if fn == "run_seven_r_assessment" else real(mod, fn))
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"applications": {"app-a": {"share": 0.5, "source": "s"}}}), encoding="utf-8")
    assert ms.handle_assess_seven_r({"project_id": "p", "app_id": "app-a", "redundancy_path": str(p)}) == {"ok": True}
    assert seen == {"app": "app-a", "redundancy": {"share": 0.5, "source": "s"}}
    assert ms.handle_assess_seven_r({"project_id": "p", "app_id": "app-a"}) == {"ok": True} and seen["redundancy"] is None
