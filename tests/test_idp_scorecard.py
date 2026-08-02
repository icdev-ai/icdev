# CUI // SP-CTI
"""Tests for scorecard-as-code (idp-score-02).

The point of the feature is that a scorecard is *configuration*: a ladder plus
IQE rule expressions in YAML. These tests assert that contract — most notably
that adding a rule changes the outcome with no Python change at all.

The evaluator is exercised against a synthetic in-memory collection so the
tests are fast and deterministic; one integration test covers the real
`idp.components` collection and the shipped scorecard.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools.idp.scorecard import (
    ScorecardError,
    evaluate,
    load_scorecard,
    load_scorecards,
    parse_scorecard,
)
from tools.iqe.executor import register_collection

REPO_ROOT = Path(__file__).resolve().parent.parent

# A tiny synthetic estate: one component per interesting shape.
FIXTURE_ROWS = [
    # clears every gate
    {"key": "alpha", "kind": "canvas", "has_owner": True, "has_e2e_spec": True,
     "rls_clean": True, "graded": True, "extra": True},
    # fails the top gate only
    {"key": "bravo", "kind": "canvas", "has_owner": False, "has_e2e_spec": True,
     "rls_clean": True, "graded": True, "extra": True},
    # fails the bottom gate but passes the top one — must stay unranked
    {"key": "charlie", "kind": "canvas", "has_owner": True, "has_e2e_spec": True,
     "rls_clean": False, "graded": True, "extra": False},
    # not graded by the filtered rule
    {"key": "delta", "kind": "feature", "has_owner": False, "has_e2e_spec": False,
     "rls_clean": True, "graded": False, "extra": False},
]

BASE_YAML = """
key: test-card
name: Test Card
collection: test.components
adapter_module: tools.iqe
ladder:
  levels:
  - name: Bronze
    rank: 1
  - name: Gold
    rank: 2
rules:
- identifier: rls-clean
  level: Bronze
  weight: 10
  expression: foreach c in test.components where c.rls_clean == true select c.key
- identifier: has-owner
  level: Gold
  weight: 10
  expression: foreach c in test.components where c.has_owner == true select c.key
"""


@pytest.fixture(autouse=True)
def _register_fixture_collection():
    """Expose FIXTURE_ROWS as the IQE collection `test.components`."""
    register_collection("test.components", lambda conn=None: [dict(r) for r in FIXTURE_ROWS])
    yield


def _card(yaml_text: str):
    import yaml

    return parse_scorecard(yaml.safe_load(textwrap.dedent(yaml_text)), source_path="<test>")


def _levels(report) -> dict[str, str | None]:
    return {r["entity"]: r["level"] for r in report["results"]}


def _scores(report) -> dict[str, float]:
    return {r["entity"]: r["score"] for r in report["results"]}


# ---------------------------------------------------------------------------
# The headline contract
# ---------------------------------------------------------------------------


def test_adding_a_rule_requires_no_python_change():
    """Appending a rule to the YAML changes the outcome — nothing else moves."""
    before = evaluate(_card(BASE_YAML), conn=None)
    assert _levels(before)["bravo"] == "Bronze"

    # Same evaluator, same collection, same Python: only the YAML grew.
    extended = BASE_YAML + """
- identifier: has-e2e-spec
  level: Bronze
  weight: 10
  expression: foreach c in test.components where c.has_e2e_spec == true select c.key
"""
    after = evaluate(_card(extended), conn=None)

    assert [r["identifier"] for r in after["rules"]] == [
        "rls-clean", "has-owner", "has-e2e-spec",
    ]
    # delta now fails the new Bronze rule and drops off the ladder entirely.
    assert _levels(before)["delta"] == "Bronze"
    assert _levels(after)["delta"] is None


def test_every_registered_component_gets_a_level():
    """The shipped scorecard evaluates the whole registry and ranks each entry."""
    card = load_scorecard("component-readiness", REPO_ROOT / "args" / "scorecards")
    report = evaluate(card, conn=None)

    from tools.config.component_registry import get_registry

    expected = {c.key for c in get_registry().list_all()}
    assert report["entity_count"] == len(expected)
    assert {r["entity"] for r in report["results"]} == expected

    ladder_names = {lv["name"] for lv in report["ladder"]} | {None}
    for row in report["results"]:
        assert row["level"] in ladder_names
        assert 0.0 <= row["score"] <= 100.0
    # Distribution accounts for every component exactly once.
    assert sum(report["level_distribution"].values()) == len(expected)


# ---------------------------------------------------------------------------
# Ladder semantics
# ---------------------------------------------------------------------------


def test_ladder_is_contiguous():
    """Passing a high rung does not skip a failed lower one."""
    report = evaluate(_card(BASE_YAML), conn=None)
    levels = _levels(report)
    assert levels["alpha"] == "Gold"
    assert levels["bravo"] == "Bronze"
    # charlie passes has-owner (Gold) but fails rls-clean (Bronze).
    assert levels["charlie"] is None


def test_unlevelled_rule_scores_but_does_not_gate():
    """A rule with no `level` moves the score and never blocks progression."""
    with_extra = BASE_YAML + """
- identifier: extra
  weight: 10
  expression: foreach c in test.components where c.extra == true select c.key
"""
    before = evaluate(_card(BASE_YAML), conn=None)
    after = evaluate(_card(with_extra), conn=None)

    # delta fails `extra`, so its score drops...
    assert _scores(after)["delta"] < _scores(before)["delta"]
    # ...but its ladder position is untouched.
    assert _levels(after)["delta"] == _levels(before)["delta"] == "Bronze"

    rule = next(r for r in after["rules"] if r["identifier"] == "extra")
    assert rule["gates_ladder"] is False


def test_filter_excludes_entity_from_score_and_ladder():
    """A filtered rule is 'not applicable' — it neither scores nor gates."""
    filtered = BASE_YAML + """
- identifier: graded-only
  level: Gold
  weight: 50
  expression: foreach c in test.components where c.has_e2e_spec == true select c.key
  filter: foreach c in test.components where c.graded == true select c.key
"""
    report = evaluate(_card(filtered), conn=None)

    delta = next(r for r in report["results"] if r["entity"] == "delta")
    outcome = next(o for o in delta["rules"] if o["identifier"] == "graded-only")
    assert outcome["status"] == "not_applicable"
    # The 50-weight rule is excluded from delta's denominator.
    assert delta["total_weight"] == 20

    rule = next(r for r in report["rules"] if r["identifier"] == "graded-only")
    assert rule["applicable"] == 3


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------


def test_active_exemption_credits_like_a_pass():
    exempted = BASE_YAML + """
exemptions:
- identifier: has-owner
  entity: bravo
  reason: Vendor-managed component.
  expires: '2099-01-01'
"""
    report = evaluate(_card(exempted), conn=None, today="2026-08-02")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert outcome["status"] == "exempt"
    assert outcome["message"] == "Vendor-managed component."
    # An exemption unblocks the rung it waives.
    assert bravo["level"] == "Gold"
    assert bravo["score"] == 100.0


def test_expired_exemption_stops_applying():
    expired = BASE_YAML + """
exemptions:
- identifier: has-owner
  entity: bravo
  reason: Temporary waiver.
  expires: '2026-01-01'
"""
    report = evaluate(_card(expired), conn=None, today="2026-08-02")

    bravo = next(r for r in report["results"] if r["entity"] == "bravo")
    outcome = next(o for o in bravo["rules"] if o["identifier"] == "has-owner")
    assert outcome["status"] == "fail"
    assert bravo["level"] == "Bronze"


# ---------------------------------------------------------------------------
# Validation — a malformed scorecard must fail loudly, not score silently wrong
# ---------------------------------------------------------------------------


_BAD_CARD_HEAD = (
    "key: bad-card\n"
    "collection: test.components\n"
    "ladder:\n"
    "  levels:\n"
    "  - name: Bronze\n"
    "    rank: 1\n"
)


@pytest.mark.parametrize(
    "rules_block, expected",
    [
        pytest.param(
            "rules:\n"
            "- identifier: x\n"
            "  expression: foreach c in test.components select c.key\n"
            "  level: Nonexistent\n",
            "not on the ladder",
            id="level-not-on-ladder",
        ),
        pytest.param(
            "rules:\n- identifier: x\n  level: Bronze\n",
            "missing 'expression'",
            id="missing-expression",
        ),
        pytest.param(
            "rules: []\n",
            "defines no rules",
            id="no-rules",
        ),
    ],
)
def test_invalid_rule_is_rejected(rules_block, expected):
    with pytest.raises(ScorecardError, match=expected):
        _card(_BAD_CARD_HEAD + rules_block)


def test_duplicate_rule_identifier_is_rejected():
    with pytest.raises(ScorecardError, match="duplicate rule identifier"):
        _card(BASE_YAML + """
- identifier: has-owner
  weight: 1
  expression: foreach c in test.components select c.key
""")


def test_rule_querying_the_wrong_collection_is_rejected():
    """A rule must read the collection its scorecard declares."""
    wrong = BASE_YAML + """
- identifier: strays
  weight: 1
  expression: foreach c in some.other where c.rls_clean == true select c.key
"""
    with pytest.raises(ScorecardError, match="declares 'test.components'"):
        evaluate(_card(wrong), conn=None)


def test_unparseable_expression_is_rejected():
    broken = BASE_YAML + """
- identifier: broken
  weight: 1
  expression: foreach c in test.components where select
"""
    with pytest.raises(ScorecardError, match="IQE parse error"):
        evaluate(_card(broken), conn=None)


# ---------------------------------------------------------------------------
# The shipped file
# ---------------------------------------------------------------------------


def test_shipped_scorecards_are_valid():
    """Every args/scorecards/*.yaml parses and declares a usable ladder."""
    cards = load_scorecards(REPO_ROOT / "args" / "scorecards")
    assert cards, "no scorecards shipped"
    for card in cards:
        ranks = [lv.rank for lv in card.ladder()]
        assert ranks == sorted(ranks)
        assert card.rules
        # A ladder is only useful if something gates it and something does not.
        assert any(r.gates_ladder for r in card.rules)
        assert any(not r.gates_ladder for r in card.rules)


def test_shipped_rules_are_parseable_iqe():
    """Every shipped rule and filter is valid IQE against its own collection."""
    from tools.iqe import parse

    for card in load_scorecards(REPO_ROOT / "args" / "scorecards"):
        for rule in card.rules:
            for expr in filter(None, (rule.expression, rule.filter_expression)):
                ast = parse(expr)
                assert str(ast.collection) == card.collection


def test_every_shipped_rule_field_exists_in_the_collection():
    """A rule may only assert on facts the adapter actually emits.

    A typo'd field name is not a parse error — IQE resolves the missing attr to
    None, the comparison is simply false, and the rule fails for every single
    component while looking like a real finding. Pin the fact vocabulary so that
    silent-total-failure mode cannot ship.
    """
    from tools.iqe import parse
    from tools.iqe.ast_nodes import AttrRef, BinOp
    from tools.iqe.adapters.idp import components_adapter

    available = set(components_adapter(None)[0])

    def _attr_names(node, var):
        if isinstance(node, BinOp):
            for side in (node.left, node.right):
                yield from _attr_names(side, var)
        elif isinstance(node, AttrRef):
            parts = node.parts[1:] if node.parts and node.parts[0] == var else node.parts
            if len(parts) == 1:
                yield parts[0]

    card = load_scorecard("component-readiness", REPO_ROOT / "args" / "scorecards")
    referenced: set[str] = set()
    for rule in card.rules:
        for expr in filter(None, (rule.expression, rule.filter_expression)):
            ast = parse(expr)
            for clause in ast.where_clauses:
                referenced.update(_attr_names(clause.predicate, ast.var))

    assert referenced, "no facts referenced — the walker is broken, not the rules"
    assert referenced <= available, (
        f"rules assert on facts the adapter does not emit: "
        f"{sorted(referenced - available)}"
    )


def test_placeholder_owner_does_not_satisfy_the_ownership_rule():
    """`owner: TBD` must read as unowned, not as owned-by-TBD.

    idp-cat-01 scrubs UNOWNED_SENTINELS on the Component dataclass. The fact
    layer must read the scrubbed field, not raw YAML — a stub owner that scores
    as owned routes an incident to nobody while the scorecard reports Platinum.
    """
    from tools.config.component_registry import UNOWNED_SENTINELS, Component

    from tools.iqe.adapters import idp as idp_adapter

    assert "tbd" in UNOWNED_SENTINELS

    real = Component(
        key="stub", kind="feature", cli_name="stub", display_name="Stub",
        description="", env_flag="", extra_env_flags=[], default_enabled=False,
        module=None, blueprint_attr=None, url_prefix="", min_il="", min_tier="",
        default_roles=[], nav={}, iqe={}, completeness={},
        raw={"owner": "TBD", "on_call": "TBD"},
        owner=None, owner_contact=None, on_call=None,
    )
    assert real.is_owned is False

    class _Registry:
        def list_all(self, kind=None):
            return [real]

    monkey = idp_adapter
    monkey.reset_cache()
    import tools.config.component_registry as registry_module

    original = registry_module.get_registry
    registry_module.get_registry = lambda *a, **k: _Registry()
    try:
        rows = monkey._collect_components(None)
    finally:
        registry_module.get_registry = original
        monkey.reset_cache()

    assert len(rows) == 1
    assert rows[0]["has_owner"] is False
    assert rows[0]["owner"] == ""


def test_probe_read_is_bounded_and_filtered():
    """The probe fact reads a bounded newest-first window, not the whole log.

    awareness_component_health is append-only and held 465k rows on 2026-08-02.
    An unbounded SELECT would make every scorecard run scale with probe history.
    """
    import sqlite3

    from tools.iqe.adapters.idp import _PROBE_WINDOW, _latest_failing_routes

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE awareness_component_health "
        "(node_id TEXT, probe_type TEXT, status TEXT, probed_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO awareness_component_health VALUES (?, ?, ?, ?)",
        [
            # An older cycle where /ndc failed, then a newer one where it passed.
            ("route::/ndc", "http_head", "fail", "2026-01-01T00:00:00Z"),
            ("route::/ndc", "http_head", "pass", "2026-06-01T00:00:00Z"),
            ("route::/pdc", "http_head", "fail", "2026-06-01T00:00:00Z"),
            # A different probe type must not be mistaken for a route probe.
            ("route::/qdc", "module_import", "fail", "2026-06-01T00:00:00Z"),
        ],
    )
    conn.commit()

    routes, probed = _latest_failing_routes(conn)
    assert probed is True
    assert routes == {"/pdc"}, "recovered route still counted, or wrong probe type read"
    assert _PROBE_WINDOW > 0


def test_never_probed_is_not_reported_as_healthy():
    """An empty probe table yields probed=False so the rule filters out, not passes."""
    import sqlite3

    from tools.iqe.adapters.idp import _latest_failing_routes

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE awareness_component_health "
        "(node_id TEXT, probe_type TEXT, status TEXT, probed_at TEXT)"
    )
    conn.commit()
    routes, probed = _latest_failing_routes(conn)
    assert routes == set()
    assert probed is False
