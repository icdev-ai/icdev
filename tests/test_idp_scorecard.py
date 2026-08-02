# CUI // SP-CTI
"""Tests for scorecard-as-code — YAML ladder + IQE rule expressions (idp-score-02).

The evaluator takes its catalog as a plain list of dicts, so the ladder
arithmetic is tested against a synthetic catalog with no registry, no database
and no file system. The shipped scorecard is then evaluated against the real
component registry to prove the wiring end to end.
"""
from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

import pytest

from tools.idp.component_facts import DEFAULT_WINDOW_DAYS, parse_window
from tools.idp.scorecard import (
    SCORECARD_DIR,
    ScorecardError,
    evaluate_scorecard,
    find_scorecard,
    list_scorecards,
    load_all_scorecards,
    load_scorecard,
    parse_scorecard,
)

SHIPPED_SCORECARD = "component-readiness"


# ---------------------------------------------------------------------------
# Fixtures — a synthetic three-rung ladder over a four-entity catalog
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog() -> list[dict]:
    """Four components chosen to land on each rung of the ladder."""
    return [
        # Clears every gating rule → Gold.
        {"key": "gold", "kind": "canvas", "owned": True, "has_iqe_adapter": True,
         "complete": True, "extra": True},
        # Fails the Gold rule only → Silver.
        {"key": "silver", "kind": "canvas", "owned": True, "has_iqe_adapter": True,
         "complete": False, "extra": False},
        # Fails the Silver rule → Bronze, and cannot reach Gold even though the
        # Gold rule passes: a ladder gates on every rank at or below the level.
        {"key": "bronze", "kind": "canvas", "owned": True, "has_iqe_adapter": False,
         "complete": True, "extra": False},
        # Fails the Bronze rule → Unrated. Not a canvas, so the Silver IQE rule
        # is filtered out entirely.
        {"key": "unrated", "kind": "feature", "owned": False, "has_iqe_adapter": False,
         "complete": True, "extra": True},
    ]


@pytest.fixture
def spec() -> dict:
    """A scorecard exercising levels, weights, a filter and a score-only rule."""
    return {
        "key": "test-card",
        "name": "Test Card",
        "collection": "test.components",
        "entity_key": "key",
        "evaluation": {"window": "30d"},
        "ladder": {
            "levels": [
                {"name": "Bronze", "rank": 1, "color": "#cd7f32"},
                {"name": "Silver", "rank": 2},
                {"name": "Gold", "rank": 3},
            ]
        },
        "rules": [
            {
                "identifier": "owned",
                "title": "Has an owner",
                "level": "Bronze",
                "weight": 3,
                "expression": "foreach c in test.components where c.owned == true select c.key",
                "failure_message": "no owner",
            },
            {
                "identifier": "iqe",
                "title": "Has an IQE adapter",
                "level": "Silver",
                "weight": 2,
                "filter": "foreach c in test.components where c.kind == 'canvas' select c.key",
                "expression": (
                    "foreach c in test.components where c.has_iqe_adapter == true select c.key"
                ),
            },
            {
                "identifier": "complete",
                "title": "Complete",
                "level": "Gold",
                "weight": 2,
                "expression": "foreach c in test.components where c.complete == true select c.key",
            },
            {
                # No level: scores, never gates.
                "identifier": "extra",
                "title": "Nice to have",
                "weight": 1,
                "expression": "foreach c in test.components where c.extra == true select c.key",
            },
        ],
    }


def _grade(spec: dict, catalog: list[dict], today: date | None = None) -> dict[str, dict]:
    report = evaluate_scorecard(parse_scorecard(spec), catalog=catalog, today=today)
    return {r["entity"]: r for r in report["results"]}


def _status(result: dict, rule_id: str) -> str:
    return next(r["status"] for r in result["rules"] if r["identifier"] == rule_id)


# ---------------------------------------------------------------------------
# Ladder
# ---------------------------------------------------------------------------


def test_ladder_assigns_a_level_to_every_entity(spec, catalog):
    graded = _grade(spec, catalog)
    assert {k: v["level"] for k, v in graded.items()} == {
        "gold": "Gold",
        "silver": "Silver",
        "bronze": "Bronze",
        "unrated": "Unrated",
    }
    assert graded["gold"]["rank"] == 3
    assert graded["unrated"]["rank"] == 0


def test_a_failed_low_rank_rule_blocks_every_level_above_it(spec, catalog):
    """'bronze' passes the Gold rule but fails Silver, so it stops at Bronze."""
    graded = _grade(spec, catalog)
    assert _status(graded["bronze"], "complete") == "pass"
    assert _status(graded["bronze"], "iqe") == "fail"
    assert graded["bronze"]["level"] == "Bronze"


def test_rule_without_a_level_scores_but_does_not_gate(spec, catalog):
    """'silver' fails the level-less rule and still holds its ladder level."""
    graded = _grade(spec, catalog)
    assert _status(graded["silver"], "extra") == "fail"
    assert graded["silver"]["level"] == "Silver"
    # 3 (owned) + 2 (iqe) earned of 3 + 2 + 2 + 1 possible.
    assert graded["silver"]["score"] == pytest.approx(62.5)


def test_score_is_weighted(spec, catalog):
    graded = _grade(spec, catalog)
    assert graded["gold"]["score"] == pytest.approx(100.0)
    assert graded["gold"]["weight_possible"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Filters and applicability
# ---------------------------------------------------------------------------


def test_filter_makes_a_rule_not_applicable_and_removes_its_weight(spec, catalog):
    """The non-canvas entity is out of scope for the Silver rule."""
    graded = _grade(spec, catalog)
    assert _status(graded["unrated"], "iqe") == "not_applicable"
    # 3 (owned) + 2 (complete) + 1 (extra) — the filtered rule's 2 is excluded.
    assert graded["unrated"]["weight_possible"] == pytest.approx(6.0)


def test_a_rank_with_no_applicable_rules_is_vacuously_satisfied(spec, catalog):
    """A component is never blocked by a level it cannot be measured on."""
    spec["rules"][0]["level"] = None  # drop the Bronze gate
    graded = _grade(spec, catalog)
    # 'unrated' now has no applicable gating rule at Bronze or Silver, and
    # passes the Gold rule, so it climbs all the way.
    assert graded["unrated"]["level"] == "Gold"


# ---------------------------------------------------------------------------
# Exemptions
# ---------------------------------------------------------------------------


def test_exemption_lifts_the_ladder_and_removes_the_weight(spec, catalog):
    spec["exemptions"] = [
        {"rule": "iqe", "component": "bronze", "reason": "headless", "approved_by": "qa"}
    ]
    graded = _grade(spec, catalog)
    assert _status(graded["bronze"], "iqe") == "exempt"
    assert graded["bronze"]["level"] == "Gold"
    assert graded["bronze"]["weight_possible"] == pytest.approx(6.0)


def test_expired_exemption_is_ignored(spec, catalog):
    spec["exemptions"] = [
        {"rule": "iqe", "component": "bronze", "expires": "2020-01-01"}
    ]
    graded = _grade(spec, catalog, today=date(2026, 8, 2))
    assert _status(graded["bronze"], "iqe") == "fail"
    assert graded["bronze"]["level"] == "Bronze"


def test_unexpired_exemption_is_honoured(spec, catalog):
    spec["exemptions"] = [
        {"rule": "iqe", "component": "bronze", "expires": "2099-01-01"}
    ]
    graded = _grade(spec, catalog, today=date(2026, 8, 2))
    assert _status(graded["bronze"], "iqe") == "exempt"


# ---------------------------------------------------------------------------
# "Adding a rule requires no Python change"
# ---------------------------------------------------------------------------


def test_a_rule_added_only_in_yaml_is_evaluated(tmp_path: Path, catalog):
    """The acceptance criterion: a new rule is config, not code."""
    yaml_text = textwrap.dedent(
        """
        key: yaml-only
        name: YAML Only
        collection: test.components
        ladder:
          levels:
            - name: Bronze
              rank: 1
        rules:
          - identifier: owned
            title: Has an owner
            level: Bronze
            expression: foreach c in test.components where c.owned == true select c.key
          - identifier: added-later
            title: A rule that exists only in this file
            weight: 5
            expression: foreach c in test.components where c.extra == true select c.key
            failure_message: add the thing
        """
    )
    path = tmp_path / "yaml_only.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    report = evaluate_scorecard(load_scorecard(path), catalog=catalog)
    graded = {r["entity"]: r for r in report["results"]}

    assert report["scorecard"]["rule_count"] == 2
    assert _status(graded["gold"], "added-later") == "pass"
    assert _status(graded["silver"], "added-later") == "fail"
    # The new rule's weight (5) dominates the ladder rule's default weight (1).
    assert graded["silver"]["score"] == pytest.approx(100.0 * 1 / 6, abs=0.1)


def test_scorecards_are_discovered_from_the_directory(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(
        "key: a\nladder:\n  levels:\n    - {name: L, rank: 1}\n"
        "rules:\n  - {identifier: r, expression: 'foreach c in x select c.key'}\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text("ignored", encoding="utf-8")
    assert [p.name for p in list_scorecards(tmp_path)] == ["a.yaml"]
    assert find_scorecard("a", tmp_path).key == "a"


# ---------------------------------------------------------------------------
# Spec validation — a malformed scorecard fails loudly, not silently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: s.pop("key"), "missing required field 'key'"),
        (lambda s: s.update(ladder={"levels": []}), "at least one level"),
        (lambda s: s.update(rules=[]), "no rules"),
        (lambda s: s["rules"][0].update(level="Platinum"), "unknown ladder level"),
        (lambda s: s["rules"][0].update(weight=-1), "negative weight"),
        (lambda s: s["rules"].append(dict(s["rules"][0])), "duplicate rule identifier"),
        (lambda s: s["ladder"]["levels"].append({"name": "Bronze", "rank": 9}),
         "duplicate ladder level name"),
        (lambda s: s["ladder"]["levels"].append({"name": "Tin", "rank": 1}),
         "duplicate ladder rank"),
        (lambda s: s["ladder"]["levels"].append({"name": "Tin", "rank": 0}),
         "ranks must be >= 1"),
        (lambda s: s.update(exemptions=[{"rule": "nope", "component": "gold"}]),
         "unknown rule"),
    ],
)
def test_malformed_scorecard_is_rejected(spec, mutate, message):
    mutate(spec)
    with pytest.raises(ScorecardError, match=message):
        parse_scorecard(spec)


def test_rule_that_does_not_project_the_entity_key_is_an_error(spec, catalog):
    spec["rules"][0]["expression"] = (
        "foreach c in test.components where c.owned == true select c.kind"
    )
    with pytest.raises(ScorecardError, match="must project the entity key"):
        evaluate_scorecard(parse_scorecard(spec), catalog=catalog)


def test_rule_with_invalid_iqe_is_an_error(spec, catalog):
    spec["rules"][0]["expression"] = "foreach c in test.components where ??? select c.key"
    with pytest.raises(ScorecardError, match="IQE parse error"):
        evaluate_scorecard(parse_scorecard(spec), catalog=catalog)


# ---------------------------------------------------------------------------
# Evaluation window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, DEFAULT_WINDOW_DAYS),
        (30, 30),
        ("30", 30),
        ("90d", 90),
        ("12w", 84),
        ("48h", 2),
        ("36h", 2),          # rounds up — facts have daily resolution at best
        ("garbage", DEFAULT_WINDOW_DAYS),
    ],
)
def test_parse_window(raw, expected):
    assert parse_window(raw) == expected


# ---------------------------------------------------------------------------
# The shipped scorecard, against the real component registry
# ---------------------------------------------------------------------------


def test_shipped_scorecard_is_valid():
    cards = load_all_scorecards()
    assert cards, f"no scorecards found in {SCORECARD_DIR}"
    card = find_scorecard(SHIPPED_SCORECARD)
    assert card.collection == "idp.components"
    assert [lv.rank for lv in card.levels] == [1, 2, 3]
    # The distinction the ladder depends on: some rules gate, some only score.
    assert any(r.level for r in card.rules)
    assert any(r.level is None for r in card.rules)
    assert any(r.filter for r in card.rules)
    assert all(r.failure_message or r.level is None for r in card.rules)


def test_shipped_scorecard_grades_every_registered_component():
    """End to end: YAML rules over the real registry, every component graded."""
    from tools.config.component_registry import get_registry

    expected = {c.key for c in get_registry().list_all()}
    report = evaluate_scorecard(find_scorecard(SHIPPED_SCORECARD))

    assert report["entity_count"] == len(expected)
    assert {r["entity"] for r in report["results"]} == expected

    valid_levels = {lv.name for lv in find_scorecard(SHIPPED_SCORECARD).levels} | {"Unrated"}
    for result in report["results"]:
        assert result["level"] in valid_levels
        assert 0 <= result["score"] <= 100  # noqa: PLR2004
        assert len(result["rules"]) == report["scorecard"]["rule_count"]
        assert all(
            r["status"] in ("pass", "fail", "not_applicable", "exempt")
            for r in result["rules"]
        )

    assert sum(report["summary"]["level_distribution"].values()) == len(expected)


def test_iqe_adapter_registers_the_collection_the_scorecard_queries():
    """Importing tools.iqe.adapters.idp must register `idp.components`.

    Registration is an import side effect, so a rename or a moved
    register_collection call would leave the scorecard silently falling through
    to the executor's raw-SQL path, looking for a table that does not exist.
    """
    import tools.iqe.adapters.idp as adapter  # noqa: PLC0415
    from tools.iqe.executor import list_collections  # noqa: PLC0415

    assert "idp.components" in list_collections()
    assert find_scorecard(SHIPPED_SCORECARD).collection in list_collections()

    rows = adapter.components_adapter(None)
    assert rows and all("key" in r for r in rows)


def test_mcp_registry_entry_resolves_to_a_real_handler():
    """The idp_scorecard MCP entry must name a module and handler that exist."""
    import importlib  # noqa: PLC0415

    from tools.mcp.tool_registry import TOOL_REGISTRY  # noqa: PLC0415

    entry = TOOL_REGISTRY["idp_scorecard"]
    handler = getattr(importlib.import_module(entry["module"]), entry["handler"])
    assert callable(handler)
    # Every declared property must be a real keyword argument of the handler.
    import inspect  # noqa: PLC0415

    params = inspect.signature(handler).parameters
    assert set(entry["input_schema"]["properties"]) <= set(params)


def test_component_facts_expose_every_field_the_shipped_rules_reference():
    """A rule referencing a field the facts never produce would silently fail."""
    import re

    from tools.idp.component_facts import build_component_facts

    row = build_component_facts()[0]
    card = find_scorecard(SHIPPED_SCORECARD)
    referenced = set()
    for rule in card.rules:
        for query in (rule.expression, rule.filter):
            if query:
                referenced |= set(re.findall(r"\bc\.(\w+)", query))
    missing = referenced - set(row)
    assert not missing, f"shipped rules reference unknown fact fields: {sorted(missing)}"
