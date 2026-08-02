# CUI // SP-CTI
"""Tests for the IDP catalog + scorecard surface (idp-ui-01).

The acceptance contract this file pins, one section per clause:

  1. every registered component is listed, with owner, grade and level
  2. every dimension links to the evidence that produced its score
  3. unassessed reads as unassessed — never as passing, never as an F

Clause 3 is the one worth being pedantic about. ``0%`` and ``None`` are
different claims: the first says "we measured this and it failed", the second
says "nothing measured this". A page that renders them the same way is
asserting a finding it does not have, which is the exact posture the
platform's own TRUST rules forbid.

The evaluator is exercised against a synthetic in-memory collection so these
stay fast and deterministic; the integration tests at the end run the real
`idp.components` collection and the shipped scorecard.
"""
from __future__ import annotations

import textwrap

import pytest

from tools.idp.scorecard import (
    UNASSIGNED_DIMENSION,
    ScorecardError,
    evaluate,
    parse_scorecard,
)
from tools.iqe.executor import register_collection

# Two components with deliberately different shapes:
#   * `alpha` is measurable on every dimension
#   * `omega` is outside the filter of BOTH rules in the `covered` dimension,
#     so that dimension has nothing to say about it — the unassessed case
FIXTURE_ROWS = [
    {"key": "alpha", "kind": "canvas", "in_scope": True,
     "has_owner": True, "rls_clean": True, "gate_passed": True},
    {"key": "omega", "kind": "feature", "in_scope": False,
     "has_owner": False, "rls_clean": True, "gate_passed": False},
]

CARD_YAML = """
key: test-card
name: Test Card
collection: test.components
adapter_module: tools.iqe

dimensions:
- key: security
  label: Security
  column: security_score
- key: covered
  label: Covered
  column: compliance_score

grading:
  bands:
  - {letter: A, min: 90}
  - {letter: C, min: 50}
  - {letter: F, min: 0}

ladder:
  levels:
  - name: Bronze
    rank: 1

rules:
- identifier: rls-clean
  dimension: security
  level: Bronze
  weight: 10
  evidence: Coherence findings.
  expression: foreach c in test.components where c.rls_clean == true select c.key
- identifier: gate-passed
  dimension: covered
  weight: 10
  expression: foreach c in test.components where c.gate_passed == true select c.key
  filter: foreach c in test.components where c.in_scope == true select c.key
- identifier: has-owner
  dimension: covered
  weight: 10
  expression: foreach c in test.components where c.has_owner == true select c.key
  filter: foreach c in test.components where c.in_scope == true select c.key
"""


@pytest.fixture(autouse=True)
def _register_fixture_collection():
    register_collection("test.components", lambda conn=None: [dict(r) for r in FIXTURE_ROWS])
    yield


def _card(yaml_text: str = CARD_YAML):
    import yaml

    return parse_scorecard(yaml.safe_load(textwrap.dedent(yaml_text)), source_path="<test>")


def _result(report, entity):
    return next(r for r in report["results"] if r["entity"] == entity)


def _dimension(report, entity, key):
    return next(d for d in _result(report, entity)["dimensions"] if d["key"] == key)


# ---------------------------------------------------------------------------
# 3. Unassessed is not a zero, and not an F
# ---------------------------------------------------------------------------


def test_unassessed_dimension_scores_none_not_zero():
    """A dimension no rule applies to has no score — not 0%."""
    report = evaluate(_card(), conn=None)

    covered = _dimension(report, "omega", "covered")
    assert covered["score"] is None, "an unmeasured dimension was scored"
    assert covered["assessed"] is False
    assert covered["total_weight"] == 0

    # The measurable one still scores normally, so this is not just everything
    # returning None.
    assert _dimension(report, "alpha", "covered")["score"] == 100.0
    assert _dimension(report, "alpha", "covered")["assessed"] is True


def test_unassessed_dimension_gets_no_letter_grade():
    """No score means no letter. An F here would invent a finding."""
    covered = _dimension(evaluate(_card(), conn=None), "omega", "covered")
    assert covered["letter_grade"] is None
    assert covered["letter_grade"] != "F"


def test_entity_with_no_applicable_rule_is_unassessed():
    """Zero applicable rules across the whole card → score None, grade None."""
    yaml_text = """
    key: nothing-applies
    name: Nothing Applies
    collection: test.components
    adapter_module: tools.iqe
    dimensions:
    - {key: security, label: Security}
    ladder:
      levels:
      - name: Bronze
        rank: 1
    rules:
    - identifier: unreachable
      dimension: security
      weight: 10
      expression: foreach c in test.components where c.rls_clean == true select c.key
      filter: foreach c in test.components where c.kind == "nonexistent" select c.key
    """
    report = evaluate(_card(yaml_text), conn=None)
    for entity in ("alpha", "omega"):
        row = _result(report, entity)
        assert row["score"] is None, f"{entity} was scored with no applicable rule"
        assert row["letter_grade"] is None
        assert row["assessed"] is False

    # And the histogram keeps them in their own bucket rather than under F.
    assert report["grade_distribution"]["unassessed"] == 2
    assert report["grade_distribution"]["F"] == 0


def test_a_measured_zero_stays_a_zero():
    """The mirror image: failing everything IS 0% and IS an F.

    Without this, "never score 0" could be satisfied by never scoring 0 — the
    distinction only means something if a real failure still reads as one.
    """
    yaml_text = """
    key: all-fail
    name: All Fail
    collection: test.components
    adapter_module: tools.iqe
    dimensions:
    - {key: security, label: Security}
    ladder:
      levels:
      - name: Bronze
        rank: 1
    rules:
    - identifier: impossible
      dimension: security
      weight: 10
      expression: foreach c in test.components where c.kind == "nonexistent" select c.key
    """
    row = _result(evaluate(_card(yaml_text), conn=None), "alpha")
    assert row["score"] == 0.0
    assert row["assessed"] is True
    assert row["letter_grade"] == "F"


def test_unassessed_and_failing_are_distinguishable_in_the_catalog():
    """build_catalog must not collapse the two into one rendering."""
    from tools.idp.portal import build_catalog

    report = evaluate(_card(), conn=None)
    rows = build_catalog([dict(r) for r in FIXTURE_ROWS], report)
    by_key = {r["key"]: r for r in rows}

    omega_covered = next(c for c in by_key["omega"]["dimensions"] if c["key"] == "covered")
    assert omega_covered["assessed"] is False
    assert omega_covered["score"] is None
    # Not the danger badge: an unassessed cell must not be coloured like a failure.
    assert "danger" not in omega_covered["grade_class"]

    alpha_covered = next(c for c in by_key["alpha"]["dimensions"] if c["key"] == "covered")
    assert alpha_covered["assessed"] is True
    assert alpha_covered["score"] == 100.0


# ---------------------------------------------------------------------------
# 1. Every component listed, with owner, grade and level
# ---------------------------------------------------------------------------


def test_every_component_has_a_grade_and_level_field():
    report = evaluate(_card(), conn=None)
    assert {r["entity"] for r in report["results"]} == {"alpha", "omega"}
    for row in report["results"]:
        assert "letter_grade" in row
        assert "level" in row
        assert "dimensions" in row


def test_catalog_carries_ownership_for_every_row():
    """Owner, contact and on-call ride on every catalog row, populated or not."""
    from tools.idp.portal import build_catalog

    facts = [
        {**FIXTURE_ROWS[0], "owner": "Platform", "owner_contact": "p@example.mil",
         "on_call": "pager", "has_owner": True},
        {**FIXTURE_ROWS[1], "owner": "", "has_owner": False},
    ]
    rows = {r["key"]: r for r in build_catalog(facts, evaluate(_card(), conn=None))}

    assert rows["alpha"]["owner"] == "Platform"
    assert rows["alpha"]["owner_contact"] == "p@example.mil"
    assert rows["alpha"]["on_call"] == "pager"
    assert rows["alpha"]["has_owner"] is True

    # Unowned is explicit, not an absent key the template would render blank.
    assert rows["omega"]["owner"] == ""
    assert rows["omega"]["has_owner"] is False


def test_dimension_cells_are_positionally_stable():
    """Every row gets one cell per declared dimension, in declared order.

    A row that simply omits a dimension it was not assessed on would shift the
    remaining cells left and silently misalign the table headers.
    """
    from tools.idp.portal import build_catalog

    report = evaluate(_card(), conn=None)
    order = [d["key"] for d in report["dimensions"]]
    for row in build_catalog([dict(r) for r in FIXTURE_ROWS], report):
        assert [c["key"] for c in row["dimensions"]] == order


def test_unassessed_sorts_below_a_measured_zero():
    """Triage order: a real 0% outranks a None, which has nothing to act on."""
    from tools.idp.portal import build_catalog

    report = evaluate(_card(), conn=None)
    rows = build_catalog([dict(r) for r in FIXTURE_ROWS], report)
    assert [r["key"] for r in rows][0] == "alpha"


# ---------------------------------------------------------------------------
# 2. Dimensions link to their evidence
# ---------------------------------------------------------------------------


def test_every_rule_outcome_carries_evidence():
    report = evaluate(_card(), conn=None)
    for row in report["results"]:
        for outcome in row["rules"]:
            ev = outcome["evidence"]
            assert ev is not None, f"{outcome['identifier']} has no evidence"
            assert ev["expression"], "evidence names no query"
            assert ev["collection"] == "test.components"
            assert ev["adapter_module"]


def test_evidence_reports_the_observed_value_not_just_the_verdict():
    """The fact the predicate read, for this entity, with its actual value."""
    report = evaluate(_card(), conn=None)
    outcome = next(
        o for o in _result(report, "omega")["rules"] if o["identifier"] == "has-owner"
    )
    assert outcome["evidence"]["fields"] == ["has_owner"]
    assert outcome["evidence"]["observed"] == {"has_owner": False}


def test_evidence_omits_a_field_the_collection_does_not_expose():
    """A field that was never read is absent, not rendered as null.

    Null would read as "the value is empty"; absent reads as "this was not
    read", which is what actually happened.
    """
    yaml_text = CARD_YAML.replace(
        "expression: foreach c in test.components where c.rls_clean == true select c.key",
        "expression: foreach c in test.components where c.nonexistent_field == true select c.key",
        1,
    )
    report = evaluate(_card(yaml_text), conn=None)
    outcome = next(
        o for o in _result(report, "alpha")["rules"] if o["identifier"] == "rls-clean"
    )
    assert outcome["evidence"]["fields"] == ["nonexistent_field"]
    assert outcome["evidence"]["observed"] == {}


def test_evidence_excludes_the_entity_key_itself():
    """`key` is how the row is addressed, not something a rule measures."""
    report = evaluate(_card(), conn=None)
    for row in report["results"]:
        for outcome in row["rules"]:
            assert "key" not in outcome["evidence"]["fields"]


def test_each_dimension_carries_the_outcomes_behind_its_score():
    """A dimension score links to its rules — not to a table somewhere else."""
    report = evaluate(_card(), conn=None)
    covered = _dimension(report, "alpha", "covered")
    assert {o["identifier"] for o in covered["rules"]} == {"gate-passed", "has-owner"}
    for outcome in covered["rules"]:
        assert outcome["evidence"]["expression"]


def test_component_detail_builds_an_evidence_url_per_outcome():
    """Every rendered outcome gets a link that re-derives it."""
    from tools.idp.portal import component_detail

    detail = component_detail("idp")
    assert detail["found"] is True
    assert detail["dimensions"], "no dimensions on the detail view"
    linked = 0
    for dim in detail["dimensions"]:
        for outcome in dim["outcomes"]:
            assert outcome["evidence_url"].startswith("/idp/evidence?")
            assert "component=idp" in outcome["evidence_url"]
            assert f"rule={outcome['identifier']}" in outcome["evidence_url"]
            linked += 1
    assert linked, "no outcome carried an evidence link"


def test_rule_evidence_reruns_the_check_rather_than_replaying_it():
    """The evidence endpoint executes the rule's own query live."""
    from tools.idp.portal import rule_evidence

    result = rule_evidence("idp", "has-owner")
    assert result["found"] is True
    assert result["evidence"]["expression"]
    assert isinstance(result["passing_count"], int)
    assert result["component_passes"] is (result["status"] == "pass")
    assert result["adapter_module"] == "tools.iqe.adapters.idp"


def test_rule_evidence_rejects_an_unknown_rule_or_component():
    from tools.idp.portal import rule_evidence

    assert rule_evidence("idp", "no-such-rule")["found"] is False
    assert rule_evidence("no-such-component", "has-owner")["found"] is False


def test_probe_backed_rule_attaches_its_rows_and_flags_absence():
    """`probes-healthy` links to awareness_component_health, and says when it is empty.

    `measured: False` is the important assertion — an empty probe set must be
    reported as absent evidence, not summarised as "0 failures".
    """
    from tools.idp.portal import rule_evidence

    result = rule_evidence("idp", "probes-healthy")
    assert result["found"] is True
    source = next(s for s in result["sources"] if s["kind"] == "probe_rows")
    assert source["table"] == "awareness_component_health"
    assert source["measured"] is bool(source["rows"])


# ---------------------------------------------------------------------------
# Dimensions and grading are configuration
# ---------------------------------------------------------------------------


def test_dimensions_and_bands_come_from_yaml():
    card = _card()
    assert [d.key for d in card.dimensions] == ["security", "covered"]
    assert card.grade_bands == (("A", 90.0), ("C", 50.0), ("F", 0.0))
    assert card.letter_grade(95.0) == "A"
    assert card.letter_grade(50.0) == "C"
    assert card.letter_grade(10.0) == "F"
    assert card.letter_grade(None) is None


def test_default_dimensions_match_the_developer_scorecards_columns():
    """The default buckets are the five columns that table already has.

    Inventing a sixth would mean the persisted history (idp-score-03) has
    nowhere to write it.
    """
    from tools.idp.scorecard import DEFAULT_DIMENSIONS

    assert [d["column"] for d in DEFAULT_DIMENSIONS] == [
        "code_quality_score",
        "security_score",
        "compliance_score",
        "test_coverage_score",
        "velocity_score",
    ]


def test_an_undeclared_dimension_is_rejected():
    bad = CARD_YAML.replace("dimension: security", "dimension: typo_here", 1)
    with pytest.raises(ScorecardError, match="not declared"):
        _card(bad)


def test_the_reserved_bucket_cannot_be_declared():
    bad = CARD_YAML.replace("- key: security", f"- key: {UNASSIGNED_DIMENSION}", 1)
    with pytest.raises(ScorecardError, match="reserved"):
        _card(bad)


def test_an_unclassified_rule_surfaces_instead_of_vanishing():
    """A rule with no `dimension` lands in a visible bucket, still scored."""
    yaml_text = CARD_YAML + """
- identifier: stray
  weight: 10
  expression: foreach c in test.components where c.rls_clean == true select c.key
"""
    report = evaluate(_card(yaml_text), conn=None)
    assert report["dimensions"][-1]["key"] == UNASSIGNED_DIMENSION

    stray = _dimension(report, "alpha", UNASSIGNED_DIMENSION)
    assert [o["identifier"] for o in stray["rules"]] == ["stray"]
    assert stray["score"] == 100.0


def test_no_unassigned_bucket_when_every_rule_is_classified():
    report = evaluate(_card(), conn=None)
    assert UNASSIGNED_DIMENSION not in {d["key"] for d in report["dimensions"]}


# ---------------------------------------------------------------------------
# The shipped scorecard, against the real component collection
# ---------------------------------------------------------------------------


def test_shipped_scorecard_classifies_every_rule():
    """Every rule in the shipped card names a dimension.

    Not enforced by the parser on purpose — a third-party scorecard may leave
    rules unclassified — but the one ICDEV ships should have no strays.
    """
    from tools.idp.scorecard import load_scorecard

    card = load_scorecard("component-readiness")
    strays = [r.identifier for r in card.rules if r.dimension == UNASSIGNED_DIMENSION]
    assert not strays, f"unclassified rules in the shipped scorecard: {strays}"


def test_shipped_scorecard_dimensions_map_to_real_columns():
    from tools.idp.scorecard import load_scorecard

    card = load_scorecard("component-readiness")
    columns = {
        "code_quality_score",
        "security_score",
        "compliance_score",
        "test_coverage_score",
        "velocity_score",
    }
    for dim in card.dimensions:
        assert dim.column in columns, f"{dim.key} names column {dim.column!r}"


def test_every_registered_component_appears_in_the_catalog():
    """Clause 1: the catalog lists every component in the registry."""
    from tools.config.component_registry import get_registry
    from tools.idp.portal import portal_overview

    registered = {c.key for c in get_registry().list_all()}
    listed = {r["key"] for r in portal_overview()["rows"]}
    assert registered == listed, f"missing from catalog: {sorted(registered - listed)}"
    assert len(listed) >= 60, "the registry should carry ~66 components"


def test_catalog_rows_all_carry_grade_level_and_dimensions():
    from tools.idp.portal import portal_overview

    overview = portal_overview()
    order = [d["key"] for d in overview["dimensions"]]
    for row in overview["rows"]:
        assert "letter_grade" in row and "level" in row and "owner" in row
        assert [c["key"] for c in row["dimensions"]] == order
        # The invariant that matters: no score without a grade, no grade
        # without a score.
        assert (row["score"] is None) == (row["letter_grade"] is None)


def test_totals_separate_assessed_from_unassessed():
    from tools.idp.portal import portal_overview

    totals = portal_overview()["totals"]
    assert totals["assessed"] + totals["unassessed"] == totals["components"]


# The persisted-history half of the unassessed contract lives with the fixture
# that applies the real migration:
# tests/test_idp_score_history.py::test_unassessed_component_is_not_recorded_as_zero
