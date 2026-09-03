# CUI // SP-CTI
"""The four RMF standing claims (rmf-ui-02).

Every mitigation in the RMF project reduces to one rule: a surface may not
render a number whose supporting evidence nothing independently re-derived.
Each card fixed one face of it with a fixture-based test pinning the function
it changed; these claims put the same question to the LIVE surface and the
LIVE primary data every six hours through claim_verifier_reflex.

What is asserted here is the CONTRACT of tools/awareness/claims.py, not the
board: each claim is registered, cites the card it was learned from, has two
sides that share no code, and its `agree` function refuses exactly the
regression the card removed while accepting the honest empty-substrate state
the live board is in today.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.awareness import claims as C  # noqa: E402
from tools.awareness.claim_verifier import (  # noqa: E402
    AGREES, DISAGREES, UNMEASURABLE, Claim, verify,
)

FOUR = {
    "asset_visibility_has_denominator": "rmf-vis-01",
    "zt_score_has_evidence": "rmf-zt-02",
    "rmf_baseline_recorded": "rmf-cyc-01",
    "classification_method_declared": "rmf-ident-01",
}


def _claim(claim_id: str) -> Claim:
    return next(c for c in C.REGISTRY if c.claim_id == claim_id)


# --------------------------------------------------------------------------- #
# 1. Registered, cited, independent
# --------------------------------------------------------------------------- #
def test_the_four_claims_are_registered_and_cite_their_incident():
    for claim_id, task_id in FOUR.items():
        claim = _claim(claim_id)
        assert claim.incident is not None, f"{claim_id} cites no incident"
        assert task_id in claim.incident.task_ids
        assert claim.incident.observed_on == "2026-09-02"
        assert claim.incident.fixed_by
        assert claim.tier == "propose"


def test_the_two_sides_share_no_implementation():
    for claim_id in FOUR:
        claim = _claim(claim_id)
        assert claim.reported is not claim.derived
        assert claim.reported.__code__ is not claim.derived.__code__


def test_the_derived_side_imports_none_of_the_surface_it_checks():
    """The independent derivation reads YAML and raw SQL. If it imported the
    surface's loader, resolver or classifier it would prove only that the
    function is deterministic -- which was never in question."""
    import inspect

    forbidden = {
        "_derived_visibility_denominators": ("tools.assets.visibility",),
        "_derived_zt_evidence_backed": ("tools.devsecops.zta_maturity_scorer",),
        "_derived_rmf_baseline": ("tools.compliance.rmf_cycle_time",
                                  "tools.compliance.rmf_stage_recorder"),
        "_derived_classification_methods": ("tools.assets.identity",),
    }
    for name, modules in forbidden.items():
        src = inspect.getsource(getattr(C, name))
        for mod in modules:
            assert mod not in src, f"{name} reaches into {mod}"


# --------------------------------------------------------------------------- #
# 2. asset_visibility_has_denominator
# --------------------------------------------------------------------------- #
def test_a_percentage_over_an_undeclared_fabric_disagrees():
    reported = {"measurable": True, "identity_rows": 12,
                "assessed": {"enterprise": {"pct": 100.0, "source": "approved_cmdb"}}}
    derived = {"identity_rows": 12, "declared": {}}
    assert C._visibility_pct_is_backed(reported, derived) is False


def test_a_percentage_crediting_a_kind_the_fabric_did_not_declare_disagrees():
    reported = {"measurable": True, "identity_rows": 12,
                "assessed": {"enterprise": {"pct": 43.0, "source": "approved_cmdb"}}}
    derived = {"identity_rows": 12, "declared": {"enterprise": ["dhcp_scope"]}}
    assert C._visibility_pct_is_backed(reported, derived) is False


def test_a_measurable_report_over_an_empty_identity_table_disagrees():
    reported = {"measurable": True, "identity_rows": 0, "assessed": {}}
    derived = {"identity_rows": 0, "declared": {}}
    assert C._visibility_pct_is_backed(reported, derived) is False


def test_not_assessed_over_an_empty_identity_table_agrees_and_is_measured():
    """The live board today: nothing ingested, nothing declared, no percentage.
    That is a MEASURED agreement -- the surface says unmeasurable and the
    substrate is empty -- not a vacuous one, because each side carries the
    scalar the surface actually asserts."""
    claim = Claim(
        claim_id="t", description="",
        reported=lambda: {"measurable": False, "identity_rows": 0, "assessed": {}},
        derived=lambda: {"identity_rows": 0, "declared": {}},
        agree=C._visibility_pct_is_backed,
    )
    assert verify(claim).verdict == AGREES


def test_a_declared_fabric_with_no_percentage_is_fine():
    reported = {"measurable": True, "identity_rows": 3, "assessed": {}}
    derived = {"identity_rows": 3, "declared": {"enterprise": ["approved_cmdb"]}}
    assert C._visibility_pct_is_backed(reported, derived) is True


def test_visibility_derivation_reads_the_yaml_and_the_table(monkeypatch):
    monkeypatch.setattr(C, "_yaml_at", lambda _p: {
        "kinds": [{"kind": "approved_cmdb"}, {"kind": "derived_if_mib"}],
        "fabrics": {
            "enterprise": [{"kind": "approved_cmdb", "value": 10},
                           {"kind": "typo_kind", "value": 4}],
            "lab": {"kind": "derived_if_mib"},
            "bare": [],
        },
    })
    monkeypatch.setattr(C, "_count_rows", lambda _t: 7)
    assert C._derived_visibility_denominators() == {
        "identity_rows": 7,
        "declared": {"enterprise": ["approved_cmdb"], "lab": ["derived_if_mib"]},
    }


def test_visibility_derivation_is_none_when_the_table_is_unreadable(monkeypatch):
    monkeypatch.setattr(C, "_yaml_at", lambda _p: {"kinds": [], "fabrics": {}})
    monkeypatch.setattr(C, "_count_rows", lambda _t: None)
    assert C._derived_visibility_denominators() is None


# --------------------------------------------------------------------------- #
# 3. zt_score_has_evidence
# --------------------------------------------------------------------------- #
def test_a_scored_pillar_with_no_signal_disagrees():
    reported = {"project_id": "p", "scored": ["device"], "unmeasured": [],
                "overall_scored": True}
    derived = {"project_id": "p", "evidence_backed": []}
    assert C._scored_pillar_has_evidence(reported, derived) is False


def test_an_overall_number_with_no_backed_pillar_disagrees():
    reported = {"project_id": "p", "scored": [], "unmeasured": ["device"],
                "overall_scored": True}
    derived = {"project_id": "p", "evidence_backed": []}
    assert C._scored_pillar_has_evidence(reported, derived) is False


def test_a_different_project_on_each_side_disagrees():
    reported = {"project_id": "p", "scored": [], "unmeasured": [], "overall_scored": False}
    derived = {"project_id": "q", "evidence_backed": []}
    assert C._scored_pillar_has_evidence(reported, derived) is False


def test_all_unmeasured_over_empty_evidence_agrees():
    """The live board today: eight `unmeasured` rows with a NULL score over an
    empty zta_posture_evidence table."""
    reported = {"project_id": "p", "scored": [],
                "unmeasured": ["device", "network"], "overall_scored": False}
    derived = {"project_id": "p", "evidence_backed": []}
    assert C._scored_pillar_has_evidence(reported, derived) is True


def test_a_backed_pillar_without_a_number_is_a_stale_assessment_not_a_fabrication():
    reported = {"project_id": "p", "scored": [], "unmeasured": ["device"],
                "overall_scored": False}
    derived = {"project_id": "p", "evidence_backed": ["device"]}
    assert C._scored_pillar_has_evidence(reported, derived) is True


def test_zt_evidence_presence_treats_a_measured_zero_as_evidence():
    assert C._zt_evidence_present(None) is False
    assert C._zt_evidence_present("") is False
    assert C._zt_evidence_present("{}") is False
    assert C._zt_evidence_present("null") is False
    assert C._zt_evidence_present("0") is True
    assert C._zt_evidence_present(0) is True
    assert C._zt_evidence_present(False) is True
    assert C._zt_evidence_present('{"mfa": true}') is True


def test_never_assessed_is_unmeasurable_not_agreement(monkeypatch):
    monkeypatch.setattr(C, "_reported_zt_scores", lambda: None)
    claim = Claim(claim_id="t", description="",
                  reported=C._reported_zt_scores,
                  derived=lambda: {"project_id": "p", "evidence_backed": []},
                  agree=C._scored_pillar_has_evidence)
    assert verify(claim).verdict == UNMEASURABLE


# --------------------------------------------------------------------------- #
# 4. rmf_baseline_recorded
# --------------------------------------------------------------------------- #
def test_a_ratio_over_an_unquantified_baseline_disagrees():
    reported = {"state": "measured", "ratio_emitted": True, "baseline_hours": 720.0,
                "refused": []}
    derived = {"declared_hours": None, "includes_decision_latency": True,
               "stage_rows": 9, "ratio_permitted": False}
    assert C._ratio_only_over_a_recorded_baseline(reported, derived) is False


def test_a_ratio_against_hours_the_declaration_does_not_record_disagrees():
    reported = {"state": "measured", "ratio_emitted": True, "baseline_hours": 720.0,
                "refused": []}
    derived = {"declared_hours": 480.0, "includes_decision_latency": False,
               "stage_rows": 9, "ratio_permitted": True}
    assert C._ratio_only_over_a_recorded_baseline(reported, derived) is False


def test_no_ratio_while_the_baseline_is_a_word_agrees():
    """The live board today: value_hours null, includes_decision_latency true,
    rmf_workflow_stages empty, comparison refused."""
    reported = {"state": "never_recorded", "ratio_emitted": False,
                "baseline_hours": None,
                "refused": ["baseline_includes_decision_latency", "baseline_unquantified"]}
    derived = {"declared_hours": None, "includes_decision_latency": True,
               "stage_rows": 0, "ratio_permitted": False}
    assert C._ratio_only_over_a_recorded_baseline(reported, derived) is True


def test_rmf_derivation_applies_the_files_own_rules(monkeypatch):
    monkeypatch.setattr(C, "_count_rows", lambda _t: 4)
    monkeypatch.setattr(C, "_yaml_at", lambda _p: {
        "baseline": {"value_hours": 600, "includes_decision_latency": True},
        "comparison": {"refuse_when_baseline_includes_decision_latency": True},
    })
    assert C._derived_rmf_baseline()["ratio_permitted"] is False

    monkeypatch.setattr(C, "_yaml_at", lambda _p: {
        "baseline": {"value_hours": 600, "includes_decision_latency": False},
    })
    assert C._derived_rmf_baseline()["ratio_permitted"] is True

    monkeypatch.setattr(C, "_count_rows", lambda _t: 0)
    assert C._derived_rmf_baseline()["ratio_permitted"] is False


def test_rmf_derivation_is_none_without_the_table(monkeypatch):
    monkeypatch.setattr(C, "_yaml_at", lambda _p: {"baseline": {}})
    monkeypatch.setattr(C, "_count_rows", lambda _t: None)
    assert C._derived_rmf_baseline() is None


# --------------------------------------------------------------------------- #
# 5. classification_method_declared
# --------------------------------------------------------------------------- #
def test_null_folded_into_rule_disagrees():
    reported = {"total": 5, "methods": {"rule": 5}}
    derived = {"total": 5, "methods": {"rule": 2, "unclassified": 3},
               "outside_vocabulary": []}
    assert C._methods_declared_bucket_for_bucket(reported, derived) is False


def test_a_value_outside_the_vocabulary_disagrees():
    reported = {"total": 1, "methods": {"guess": 1}}
    derived = {"total": 1, "methods": {"guess": 1}, "outside_vocabulary": ["guess"]}
    assert C._methods_declared_bucket_for_bucket(reported, derived) is False


def test_an_empty_table_agrees_bucket_for_bucket():
    reported = {"total": 0, "methods": {}}
    derived = {"total": 0, "methods": {}, "outside_vocabulary": []}
    assert C._methods_declared_bucket_for_bucket(reported, derived) is True


def test_classification_derivation_buckets_null_separately(monkeypatch):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, _sql, _params=()):
            class _R:
                def fetchall(_self):
                    return [{"m": None, "n": 3}, {"m": "model", "n": 2}, {"m": "rule", "n": 1}]
            return _R()

    monkeypatch.setattr(C, "_conn", lambda: _Conn())
    assert C._derived_classification_methods() == {
        "total": 6,
        "methods": {"unclassified": 3, "model": 2, "rule": 1},
        "outside_vocabulary": [],
    }


def test_classification_reported_side_is_none_when_stats_cannot_measure(monkeypatch):
    import tools.assets.identity as identity

    monkeypatch.setattr(identity, "stats", lambda: {"measurable": False, "total": None})
    assert C._reported_classification_methods() is None


def test_a_live_disagreement_carries_both_sides():
    claim = Claim(
        claim_id="t", description="NULL read as rule",
        reported=lambda: {"total": 2, "methods": {"rule": 2}},
        derived=lambda: {"total": 2, "methods": {"unclassified": 2},
                         "outside_vocabulary": []},
        agree=C._methods_declared_bucket_for_bucket,
    )
    result = verify(claim)
    assert result.verdict == DISAGREES
    assert result.reported["methods"] == {"rule": 2}
    assert result.derived["methods"] == {"unclassified": 2}
