# CUI // SP-CTI
"""Unit coverage for the ODC observability scoring core (obx-test-01).

Exercises the four public pure functions in
``tools.observability_canvas.observability_engine``:

* ``assess_observability_design`` — rule dispatch + score/grade
* ``compute_coverage_score``      — recommended-source coverage
* ``compute_mitre_detection_coverage`` — MITRE technique coverage from a
  ``cmp-baseline`` node's ``config_json.techniques`` metadata
* ``detect_observability_gaps``   — gap categorisation from findings

Plus a structural-shape-only probe of ``check_nc_audit_to_siem_forwarder``
(the ODC-NDC-001 cross-canvas check) which is known-broken and being fixed
under obx-fix-04 — this file deliberately does NOT lock in its current
(fabricated) pass behaviour.

All engine functions under test are pure (dict in / dict out, no DB), so no
conftest schema additions are required. Node-type prefixes and the object
palette are taken from ``tools.observability_canvas.constants``.
"""

import pytest

from tools.observability_canvas.constants import (
    OBSERVABILITY_COMPLIANCE_RULES,
    RECOMMENDED_SOURCE_TYPES,
    SEVERITY_WEIGHTS,
)
from tools.observability_canvas.observability_engine import (
    assess_observability_design,
    check_nc_audit_to_siem_forwarder,
    compute_coverage_score,
    compute_mitre_detection_coverage,
    detect_observability_gaps,
)

# Rule-id -> rule dict, so custom rule lists stay in sync with constants.
_RULES_BY_ID = {r["id"]: r for r in OBSERVABILITY_COMPLIANCE_RULES}


# ── Fixture builders ─────────────────────────────────────────────────────────


def _node(nid, ntype, **extra):
    n = {"id": nid, "type": ntype, "label": extra.pop("label", nid)}
    n.update(extra)
    return n


def _edge(src, tgt, **extra):
    e = {"source": src, "target": tgt}
    e.update(extra)
    return e


def _rich_design():
    """A complete observability design that satisfies all 13 ODC-* rules.

    All 10 RECOMMENDED_SOURCE_TYPES present; every source wired to a
    collector; both collectors forward (TLS) to a SIEM platform; alert ->
    SOAR -> ticket chain; retention policy + MITRE baseline present.
    Expected: zero findings, score 100.0, grade A, coverage 100%.
    """
    sources = [
        "src-app-log", "src-os-log", "src-network-log", "src-cloud-log",
        "src-container-log", "src-db-audit", "src-endpoint", "src-iam",
        "src-metric", "src-trace",
    ]
    nodes = [_node(t, t) for t in sources]
    nodes += [
        _node("col-otel", "col-otel"),
        _node("col-s3", "col-s3"),
        _node("plt-splunk", "plt-splunk"),
        _node("auto-alert-rule", "auto-alert-rule"),
        _node("auto-soar", "auto-soar"),
        _node("auto-ticket", "auto-ticket"),
        _node("cmp-log-policy", "cmp-log-policy"),
        _node("cmp-baseline", "cmp-baseline"),
    ]
    edges = [_edge(t, "col-otel") for t in sources]
    edges += [
        _edge("col-otel", "plt-splunk", encrypted=True),
        _edge("col-s3", "plt-splunk", encrypted=True),
        _edge("auto-alert-rule", "auto-soar"),
        _edge("auto-soar", "auto-ticket"),
    ]
    return {"nodes": nodes, "edges": edges}


# Five CAT2 rules whose findings are each toggled by the presence/absence of a
# single, disjoint node type — used to construct designs with an EXACT number
# of firing findings and therefore an exact score/grade.
#   ODC-LOG-003 -> fires iff no col-s3
#   ODC-DET-001 -> fires iff no auto-alert-rule
#   ODC-DET-003 -> fires iff no cmp-baseline
#   ODC-SEC-002 -> fires iff no src-endpoint
#   ODC-INT-001 -> fires iff auto-soar present AND no auto-ticket
_CAT2_TOGGLES = ["ODC-LOG-003", "ODC-DET-001", "ODC-DET-003", "ODC-SEC-002", "ODC-INT-001"]


def _toggle_design(firing_ids):
    """Build a design where exactly the given CAT2 toggle rules fire.

    For every toggle NOT in ``firing_ids`` we add the node that suppresses it;
    for those IN ``firing_ids`` we omit it (or, for INT-001, add the SOAR node
    that triggers it). Node types are disjoint so toggles are independent.
    """
    firing = set(firing_ids)
    nodes = []
    if "ODC-LOG-003" not in firing:
        nodes.append(_node("s3", "col-s3"))
    if "ODC-DET-001" not in firing:
        nodes.append(_node("alert", "auto-alert-rule"))
    if "ODC-DET-003" not in firing:
        nodes.append(_node("baseline", "cmp-baseline"))
    if "ODC-SEC-002" not in firing:
        nodes.append(_node("edr", "src-endpoint"))
    if "ODC-INT-001" in firing:
        # SOAR present + no ticket -> ODC-INT-001 fires.
        nodes.append(_node("soar", "auto-soar"))
    # else: no SOAR node -> ODC-INT-001 returns [] (does not fire)
    return {"nodes": nodes, "edges": []}


# ── assess_observability_design: grade boundaries ────────────────────────────


class TestGradeBoundaries:
    """Grade thresholds are score>=90:A, >=80:B, >=70:C, >=60:D, else F.

    Using 5 uniform-CAT2 rules, score = 100 - 10 * (num firing), so f firing
    findings yields exactly 100/90/80/70/60/50 -> A/A/B/C/D/F. This lands each
    boundary value exactly-at (higher grade) and the next step below it.
    """

    @pytest.mark.parametrize(
        "num_firing,expected_score,expected_grade",
        [
            (0, 100.0, "A"),   # perfect
            (1, 90.0, "A"),    # exactly at A/B boundary -> A (inclusive)
            (2, 80.0, "B"),    # exactly at B/C boundary -> B; also "below 90" -> not A
            (3, 70.0, "C"),    # exactly at C/D boundary -> C
            (4, 60.0, "D"),    # exactly at D/F boundary -> D (inclusive)
            (5, 50.0, "F"),    # below 60 -> F (floor band)
        ],
    )
    def test_uniform_cat2_grade_ladder(self, num_firing, expected_score, expected_grade):
        rules = [_RULES_BY_ID[rid] for rid in _CAT2_TOGGLES]
        # Sanity: the five toggles really are all CAT2 (score math depends on it).
        assert {r["severity"] for r in rules} == {"CAT2"}
        firing_ids = _CAT2_TOGGLES[:num_firing]
        design = _toggle_design(firing_ids)
        result = assess_observability_design(design, rules=rules)
        assert result["total_findings"] == num_firing
        assert result["score"] == expected_score
        assert result["grade"] == expected_grade

    def test_just_below_ninety_is_grade_b(self):
        """A single CAT1 finding among CAT2 padding yields 83.3 (< 90) -> B.

        rules = [SEC-003 (CAT1,10, fires: no src-iam)] + 4 non-firing CAT2.
        max_penalty = (10 + 4*5) * 2 = 60 ; penalty = 10 ; score = 83.3.
        Confirms a value just below the A threshold drops to grade B.
        """
        assert SEVERITY_WEIGHTS["CAT1"] == 10 and SEVERITY_WEIGHTS["CAT2"] == 5
        rules = [_RULES_BY_ID["ODC-SEC-003"]] + [_RULES_BY_ID[r] for r in _CAT2_TOGGLES[:4]]
        # SEC-003 fires (no src-iam); the 4 CAT2 toggles are all suppressed.
        design = _toggle_design([])  # suppresses all four CAT2 toggles; has no src-iam
        result = assess_observability_design(design, rules=rules)
        assert result["total_findings"] == 1
        assert result["findings"][0]["rule_id"] == "ODC-SEC-003"
        assert result["score"] == 83.3
        assert result["grade"] == "B"

    def test_score_never_negative(self):
        """Score floors at 0 — many CAT1 findings cannot drive it below 0."""
        # Empty design against the full default rule set: worst realistic case.
        result = assess_observability_design({"nodes": [], "edges": []})
        assert result["score"] >= 0.0
        assert result["grade"] in {"A", "B", "C", "D", "F"}


# ── assess_observability_design: empty / rich / determinism ──────────────────


class TestAssessStructure:
    def test_empty_design_returns_sane_structure(self):
        result = assess_observability_design({})
        for key in (
            "assessment_id", "findings", "total_findings", "cat1_findings",
            "cat2_findings", "score", "grade", "by_category", "assessed_at",
        ):
            assert key in result
        assert isinstance(result["findings"], list)
        assert result["total_findings"] == len(result["findings"])
        assert 0.0 <= result["score"] <= 100.0
        assert result["grade"] in {"A", "B", "C", "D", "F"}

    def test_rich_design_scores_full_marks(self):
        result = assess_observability_design(_rich_design())
        assert result["total_findings"] == 0
        assert result["score"] == 100.0
        assert result["grade"] == "A"

    def test_rich_beats_empty(self):
        rich = assess_observability_design(_rich_design())
        empty = assess_observability_design({"nodes": [], "edges": []})
        assert rich["score"] > empty["score"]
        assert rich["total_findings"] < empty["total_findings"]

    def test_assess_deterministic_on_stable_fields(self):
        """Same input -> identical deterministic output.

        NOTE: assessment_id, each finding's ``id``, and assessed_at are
        intentionally non-deterministic (uuid4 / wall-clock). Determinism is
        asserted on the load-bearing fields only.
        """
        design = _toggle_design(["ODC-LOG-003", "ODC-DET-001"])
        rules = [_RULES_BY_ID[rid] for rid in _CAT2_TOGGLES]
        r1 = assess_observability_design(design, rules=rules)
        r2 = assess_observability_design(design, rules=rules)
        assert r1["score"] == r2["score"]
        assert r1["grade"] == r2["grade"]
        assert r1["total_findings"] == r2["total_findings"]
        assert r1["by_category"] == r2["by_category"]

        def _stable(res):
            return sorted(
                (f["rule_id"], f["severity"], f["category"], f["affected_entity"], f["detail"])
                for f in res["findings"]
            )

        assert _stable(r1) == _stable(r2)

    def test_cat_counts_match_severities(self):
        rules = [_RULES_BY_ID[rid] for rid in _CAT2_TOGGLES]
        result = assess_observability_design(_toggle_design(_CAT2_TOGGLES), rules=rules)
        assert result["cat1_findings"] == 0
        assert result["cat2_findings"] == 5
        assert result["total_findings"] == 5

    def test_specific_rule_fires_for_missing_component(self):
        """A design missing only col-s3 fires exactly ODC-LOG-003."""
        design = _toggle_design(["ODC-LOG-003"])
        rules = [_RULES_BY_ID[rid] for rid in _CAT2_TOGGLES]
        result = assess_observability_design(design, rules=rules)
        fired = {f["rule_id"] for f in result["findings"]}
        assert fired == {"ODC-LOG-003"}


# ── compute_coverage_score ───────────────────────────────────────────────────


class TestCoverageScore:
    def test_empty_design_floor(self):
        cov = compute_coverage_score({})
        assert cov["coverage_pct"] == 0.0
        assert cov["present"] == []
        assert cov["present_count"] == 0
        assert cov["missing"] == list(RECOMMENDED_SOURCE_TYPES)
        assert cov["total_recommended"] == len(RECOMMENDED_SOURCE_TYPES)

    def test_full_coverage(self):
        cov = compute_coverage_score(_rich_design())
        assert cov["coverage_pct"] == 100.0
        assert cov["present_count"] == len(RECOMMENDED_SOURCE_TYPES)
        assert cov["missing"] == []

    def test_partial_coverage_direction(self):
        one = compute_coverage_score({"nodes": [_node("a", "src-app-log")]})
        two = compute_coverage_score(
            {"nodes": [_node("a", "src-app-log"), _node("b", "src-os-log")]}
        )
        assert 0.0 < one["coverage_pct"] < two["coverage_pct"] < 100.0
        assert one["present_count"] == 1
        assert two["present_count"] == 2

    def test_deterministic(self):
        design = {"nodes": [_node("a", "src-app-log"), _node("b", "src-iam")]}
        assert compute_coverage_score(design) == compute_coverage_score(design)


# ── compute_mitre_detection_coverage ─────────────────────────────────────────
#
# Contract note (finding, not a bug): coverage is derived SOLELY from a
# cmp-baseline node's config_json.techniques[*].{id,covered} metadata. It is
# NOT a function of exporters/signals and imports no technique catalog. Tests
# assert the real behaviour and coverage DIRECTION, not hardcoded catalog counts.


def _baseline_design(techniques, as_string=False):
    import json

    cfg = {"techniques": techniques}
    node = _node("bl", "cmp-baseline")
    node["config_json"] = json.dumps(cfg) if as_string else cfg
    return {"nodes": [node]}


class TestMitreCoverage:
    def test_no_baseline_zero(self):
        res = compute_mitre_detection_coverage({"nodes": [_node("a", "src-app-log")]})
        assert res["has_baseline"] is False
        assert res["total_techniques"] == 0
        assert res["covered_techniques"] == 0
        assert res["coverage_pct"] == 0.0
        assert res["technique_gaps"] == []

    def test_empty_design_no_baseline(self):
        res = compute_mitre_detection_coverage({})
        assert res["has_baseline"] is False
        assert res["coverage_pct"] == 0.0

    def test_baseline_partial_coverage(self):
        techs = [
            {"id": "T1078", "covered": True},
            {"id": "T1059", "covered": True},
            {"id": "T1110", "covered": False},
            {"id": "T1566", "covered": False},
        ]
        res = compute_mitre_detection_coverage(_baseline_design(techs))
        assert res["has_baseline"] is True
        assert res["total_techniques"] == 4
        assert res["covered_techniques"] == 2
        assert res["coverage_pct"] == 50.0
        assert res["technique_gaps"] == ["T1110", "T1566"]  # sorted uncovered

    def test_coverage_increases_when_more_techniques_covered(self):
        low = _baseline_design(
            [
                {"id": "T1078", "covered": True},
                {"id": "T1059", "covered": False},
                {"id": "T1110", "covered": False},
                {"id": "T1566", "covered": False},
            ]
        )
        high = _baseline_design(
            [
                {"id": "T1078", "covered": True},
                {"id": "T1059", "covered": True},
                {"id": "T1110", "covered": True},
                {"id": "T1566", "covered": False},
            ]
        )
        low_res = compute_mitre_detection_coverage(low)
        high_res = compute_mitre_detection_coverage(high)
        assert high_res["coverage_pct"] > low_res["coverage_pct"]
        assert high_res["covered_techniques"] > low_res["covered_techniques"]
        assert len(high_res["technique_gaps"]) < len(low_res["technique_gaps"])

    def test_config_json_as_string_is_parsed(self):
        techs = [{"id": "T1078", "covered": True}, {"id": "T1059", "covered": False}]
        res = compute_mitre_detection_coverage(_baseline_design(techs, as_string=True))
        assert res["has_baseline"] is True
        assert res["total_techniques"] == 2
        assert res["covered_techniques"] == 1
        assert res["coverage_pct"] == 50.0

    def test_invalid_config_json_string_handled(self):
        """Malformed config_json string must NOT raise — engine try/excepts it."""
        node = _node("bl", "cmp-baseline")
        node["config_json"] = "{not valid json"
        res = compute_mitre_detection_coverage({"nodes": [node]})
        assert res["has_baseline"] is True
        assert res["total_techniques"] == 0
        assert res["coverage_pct"] == 0.0

    def test_deterministic(self):
        design = _baseline_design(
            [{"id": "T1110", "covered": True}, {"id": "T1078", "covered": False}]
        )
        assert compute_mitre_detection_coverage(design) == compute_mitre_detection_coverage(design)


# ── detect_observability_gaps ────────────────────────────────────────────────


class TestDetectGaps:
    def test_complete_design_no_fabricated_gaps(self):
        assessment = assess_observability_design(_rich_design())
        gaps = detect_observability_gaps(assessment)
        assert gaps["total_gaps"] == 0
        assert gaps["critical_count"] == 0
        assert gaps["recommended_count"] == 0
        assert gaps["recommendations"] == []
        assert all(v == [] for v in gaps["gaps"].values())

    def test_missing_archive_maps_to_collector_gap(self):
        design = _toggle_design(["ODC-LOG-003"])
        rules = [_RULES_BY_ID["ODC-LOG-003"]]
        assessment = assess_observability_design(design, rules=rules)
        gaps = detect_observability_gaps(assessment)
        assert "col-s3" in gaps["gaps"]["missing_collectors"]
        assert any(r["rule_id"] == "ODC-LOG-003" for r in gaps["recommendations"])

    def test_empty_assessment_dict_handled(self):
        gaps = detect_observability_gaps({})
        assert gaps["total_gaps"] == 0
        assert gaps["recommendations"] == []

    def test_recommendations_sorted_critical_first(self):
        """CAT1 (critical) recommendations sort ahead of CAT2 (recommended)."""
        # Empty design against defaults yields a mix of CAT1 and CAT2 findings.
        assessment = assess_observability_design({"nodes": [], "edges": []})
        gaps = detect_observability_gaps(assessment)
        priorities = [r["priority"] for r in gaps["recommendations"]]
        assert priorities == sorted(priorities, key=lambda p: 0 if p == "critical" else 1)
        assert gaps["critical_count"] >= 1  # empty design misses CAT1 controls

    def test_gap_counts_consistent(self):
        assessment = assess_observability_design({"nodes": [], "edges": []})
        gaps = detect_observability_gaps(assessment)
        assert gaps["total_gaps"] == sum(len(v) for v in gaps["gaps"].values())
        assert gaps["critical_count"] + gaps["recommended_count"] == len(gaps["recommendations"])


# ── Failure paths (test ACTUAL behaviour; engine is NOT modified) ────────────


class TestFailurePaths:
    def test_missing_nodes_edges_keys_ok(self):
        # .get(...) defaults mean absent keys do not raise.
        assert assess_observability_design({})["total_findings"] >= 0
        assert compute_coverage_score({})["coverage_pct"] == 0.0
        assert compute_mitre_detection_coverage({})["has_baseline"] is False

    def test_node_without_id_is_skipped(self):
        """Fixed under obx-fix-04: a node missing 'id' is skipped gracefully
        instead of raising KeyError."""
        res = assess_observability_design({"nodes": [{"type": "src-app-log"}], "edges": []})
        assert res["total_findings"] >= 0

    def test_string_graph_data_is_parsed(self):
        """Fixed under obx-fix-04: string graph_json is json.loads'd; invalid
        JSON degrades to an empty design instead of raising AttributeError."""
        assert compute_coverage_score("{\"nodes\": []}")["coverage_pct"] == 0.0
        assert compute_coverage_score("not valid json")["coverage_pct"] == 0.0

    def test_edge_without_source_target_is_skipped(self):
        """Fixed under obx-fix-04: malformed edges (missing source/target) are
        skipped instead of raising KeyError."""
        design = {"nodes": [_node("a", "src-app-log")], "edges": [{"foo": "bar"}]}
        res = assess_observability_design(design)
        assert res["total_findings"] >= 0


# ── check_nc_audit_to_siem_forwarder (ODC-NDC-001) — shape only ──────────────


class TestCrossCanvasShapeOnly:
    def test_fail_closed_unknown_without_data(self):
        """Fixed under obx-fix-04: with no NDC/ODC data available the check
        fail-closes to status='unknown' with a reason and NO score — never a
        fabricated pass."""
        res = check_nc_audit_to_siem_forwarder("proj-does-not-exist", "design-does-not-exist")
        assert res["rule_id"] == "ODC-NDC-001"
        assert res["status"] == "unknown"
        assert "score" not in res
        assert res.get("reason")
        assert isinstance(res["violations"], list)
