# CUI // SP-CTI
"""A failing scorecard rule becomes exactly one gated kanban task — and no more.

Two failure modes are worth more than the happy path here.

**The seeding storm.** 66 components times 11 rules is ~700 candidate tasks on
the first pass, and an unbounded seeder in this repo has already produced 353
branches in one go. So the caps are asserted directly, including the ordering
between them: capping only the run would let the single worst component consume
the entire budget and starve the estate.

**The silent no-op.** A seeder that reseeds on every run floods the board; one
that pre-filters incorrectly re-offers the same ten gaps forever and never
reaches the eleventh. Both are asserted against a real SQLite board rather than
a mock, because the dedupe lives in the INSERT path.
"""
from __future__ import annotations

import logging

import pytest

from tools.idp import gap_seeder
from tools.idp.gap_seeder import (
    Gap,
    GapSeederError,
    apply_caps,
    build_task_spec,
    filter_gaps,
    gaps_from_report,
    gate_spec,
    gate_state,
    load_config,
    prioritize,
    seed,
)
from tools.idp.scorecard import Level, Rule, Scorecard
from tools.kanban.gates import is_manual_gate

# ---------------------------------------------------------------------------
# Fixtures — a scorecard and a report in the shape evaluate() emits
# ---------------------------------------------------------------------------

BRONZE_RULE = Rule(
    identifier="rls-clean",
    expression="foreach c in idp.components where c.rls_clean == true select c.key",
    weight=15,
    level="Bronze",
    title="No canvas RLS-bypass violation",
    failure_message="Canvas DB access bypasses row-level security.",
)
GOLD_RULE = Rule(
    identifier="e2e-spec",
    expression="foreach c in idp.components where c.has_e2e_spec == true select c.key",
    weight=20,
    level="Gold",
    title="Has a Playwright E2E spec",
    failure_message="No tests/e2e/<key>*.spec.ts.",
)
UNGATED_RULE = Rule(
    identifier="nav-reachable",
    expression="foreach c in idp.components where c.has_nav == true select c.key",
    weight=5,
    level=None,
    title="Declares navigation metadata",
    failure_message="No nav: block.",
)


def _scorecard() -> Scorecard:
    return Scorecard(
        key="component-readiness",
        name="Component Readiness",
        collection="idp.components",
        levels=(Level(name="Bronze", rank=1), Level(name="Gold", rank=3)),
        rules=(BRONZE_RULE, GOLD_RULE, UNGATED_RULE),
        source_path="args/scorecards/component-readiness.yaml",
    )


def _outcome(rule: Rule, status: str) -> dict:
    return {
        "identifier": rule.identifier,
        "status": status,
        "weight": rule.weight,
        "level": rule.level,
        "message": rule.failure_message if status == "fail" else "",
    }


def _report(entities: dict[str, dict[str, str]]) -> dict:
    """``{component: {rule_id: status}}`` -> an evaluate()-shaped report."""
    by_id = {r.identifier: r for r in (BRONZE_RULE, GOLD_RULE, UNGATED_RULE)}
    return {
        "scorecard": "component-readiness",
        "results": [
            {
                "entity": entity,
                "level": None,
                "level_rank": 0,
                "score": 40.0,
                "rules": [_outcome(by_id[rid], status) for rid, status in statuses.items()],
            }
            for entity, statuses in entities.items()
        ],
    }


def _gap(component: str, rule: Rule, scorecard_key: str = "component-readiness") -> Gap:
    ranks = {"Bronze": 1, "Gold": 3}
    return Gap(
        scorecard_key=scorecard_key,
        scorecard_name="Component Readiness",
        collection="idp.components",
        component=component,
        rule_id=rule.identifier,
        rule_title=rule.title,
        expression=rule.expression,
        failure_message=rule.failure_message,
        weight=rule.weight,
        level=rule.level,
        level_rank=ranks.get(rule.level or "", 10_000),
        component_level=None,
        component_score=40.0,
    )


# ---------------------------------------------------------------------------
# Gap extraction
# ---------------------------------------------------------------------------


def test_a_failing_rule_produces_exactly_one_gap_per_component():
    report = _report(
        {
            "ndc": {"rls-clean": "fail", "e2e-spec": "fail"},
            "qdc": {"e2e-spec": "fail"},
        }
    )
    gaps = gaps_from_report(_scorecard(), report)
    assert sorted((g.component, g.rule_id) for g in gaps) == [
        ("ndc", "e2e-spec"),
        ("ndc", "rls-clean"),
        ("qdc", "e2e-spec"),
    ]


@pytest.mark.parametrize("status", ["pass", "exempt", "not_applicable"])
def test_only_failures_become_gaps(status):
    """An exemption is a decision someone already made; N/A was never in scope."""
    gaps = gaps_from_report(_scorecard(), _report({"ndc": {"e2e-spec": status}}))
    assert gaps == []


def test_gap_carries_the_failure_message_and_the_evidence_query():
    gaps = gaps_from_report(_scorecard(), _report({"ndc": {"e2e-spec": "fail"}}))
    assert gaps[0].failure_message == GOLD_RULE.failure_message
    assert gaps[0].expression == GOLD_RULE.expression


def test_a_rule_the_scorecard_does_not_define_is_skipped_not_guessed():
    report = _report({"ndc": {"e2e-spec": "fail"}})
    report["results"][0]["rules"].append(
        {"identifier": "invented-rule", "status": "fail", "weight": 1, "level": None}
    )
    gaps = gaps_from_report(_scorecard(), report)
    assert [g.rule_id for g in gaps] == ["e2e-spec"]


# ---------------------------------------------------------------------------
# Identity — this is what makes a re-run a no-op
# ---------------------------------------------------------------------------


def test_idempotency_key_is_stable_across_constructions():
    assert _gap("ndc", GOLD_RULE).idempotency_key == _gap("ndc", GOLD_RULE).idempotency_key
    assert _gap("ndc", GOLD_RULE).idempotency_key == "idp-gap:component-readiness:ndc:e2e-spec"


def test_task_ids_are_distinct_across_components_rules_and_scorecards():
    ids = {
        _gap("ndc", GOLD_RULE).task_id,
        _gap("qdc", GOLD_RULE).task_id,
        _gap("ndc", BRONZE_RULE).task_id,
        _gap("ndc", GOLD_RULE, scorecard_key="other-card").task_id,
    }
    assert len(ids) == 4


def test_task_id_does_not_collide_across_the_component_rule_boundary():
    """``a-b`` + ``c`` must not produce the same id as ``a`` + ``b-c``.

    A colliding id is skipped silently by the task factory — a seeded task that
    quietly never exists.
    """
    left = Gap(**{**_gap("a-b", GOLD_RULE).__dict__, "component": "a-b", "rule_id": "c"})
    right = Gap(**{**_gap("a", GOLD_RULE).__dict__, "component": "a", "rule_id": "b-c"})
    assert left.task_id != right.task_id


def test_task_id_prefix_is_not_the_idp_card_prefix():
    """Sharing ``idp-`` would fold remediation tasks into the IDP card's progress."""
    assert not _gap("ndc", GOLD_RULE).task_id.startswith("idp-")


# ---------------------------------------------------------------------------
# Ordering and caps
# ---------------------------------------------------------------------------


def test_lower_ladder_rungs_are_seeded_before_higher_and_ungated_last():
    gaps = [_gap("x", UNGATED_RULE), _gap("x", GOLD_RULE), _gap("x", BRONZE_RULE)]
    assert [g.rule_id for g in prioritize(gaps)] == ["rls-clean", "e2e-spec", "nav-reachable"]


def test_prioritize_is_deterministic():
    gaps = [_gap(c, GOLD_RULE) for c in ("qdc", "ndc", "odc")]
    assert [g.component for g in prioritize(gaps)] == [g.component for g in prioritize(gaps[::-1])]


def test_per_component_cap_truncates_and_names_the_capped_component():
    gaps = prioritize(
        [_gap("ndc", r) for r in (BRONZE_RULE, GOLD_RULE, UNGATED_RULE)]
        + [_gap("qdc", BRONZE_RULE)]
    )
    kept, truncation = apply_caps(gaps, max_per_component=2, max_per_run=100)

    assert len(kept) == 3
    assert sum(1 for g in kept if g.component == "ndc") == 2
    assert truncation["by_component_cap"] == 1
    assert truncation["components_capped"] == ["ndc"]
    assert truncation["truncated"] is True


def test_per_run_cap_truncates_and_reports():
    gaps = prioritize([_gap(f"c{i}", BRONZE_RULE) for i in range(20)])
    kept, truncation = apply_caps(gaps, max_per_component=2, max_per_run=5)

    assert len(kept) == 5
    assert truncation["by_run_cap"] == 15
    assert truncation["truncated"] is True


def test_the_component_cap_runs_first_so_one_bad_component_cannot_eat_the_budget():
    """The load-bearing ordering. Run-cap-first would seed only ``bad``."""
    gaps = prioritize(
        [_gap("bad", r) for r in (BRONZE_RULE, GOLD_RULE, UNGATED_RULE)]
        + [_gap("ok1", BRONZE_RULE), _gap("ok2", BRONZE_RULE)]
    )
    kept, _ = apply_caps(gaps, max_per_component=1, max_per_run=3)
    assert sorted(g.component for g in kept) == ["bad", "ok1", "ok2"]


def test_truncation_is_logged_at_warning():
    """A cap that drops work silently reads as 'nothing left to do'."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # noqa: D102
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    gap_seeder.LOG.addHandler(handler)
    try:
        apply_caps(prioritize([_gap(f"c{i}", BRONZE_RULE) for i in range(9)]), 2, 4)
    finally:
        gap_seeder.LOG.removeHandler(handler)

    messages = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
    assert any("per-run cap" in m for m in messages), messages


def test_no_caps_when_set_to_zero():
    gaps = prioritize([_gap(f"c{i}", BRONZE_RULE) for i in range(30)])
    kept, truncation = apply_caps(gaps, max_per_component=0, max_per_run=0)
    assert len(kept) == 30
    assert truncation["truncated"] is False


# ---------------------------------------------------------------------------
# Selection filters
# ---------------------------------------------------------------------------


def test_only_gating_rules_drops_un_levelled_rules():
    gaps = [_gap("x", UNGATED_RULE), _gap("x", GOLD_RULE)]
    assert [g.rule_id for g in filter_gaps(gaps, {"only_gating_rules": True})] == ["e2e-spec"]


def test_exclude_and_include_rules():
    gaps = [_gap("x", UNGATED_RULE), _gap("x", GOLD_RULE)]
    assert [g.rule_id for g in filter_gaps(gaps, {"exclude_rules": ["e2e-spec"]})] == [
        "nav-reachable"
    ]
    assert [g.rule_id for g in filter_gaps(gaps, {"include_rules": ["e2e-spec"]})] == ["e2e-spec"]


# ---------------------------------------------------------------------------
# Task shape — nothing dispatches without confirmation
# ---------------------------------------------------------------------------


def test_seeded_task_is_suggested_and_waits_behind_the_gate():
    spec = build_task_spec(_gap("ndc", GOLD_RULE), load_config())
    assert spec["status"] == "suggested"
    assert spec["depends_on_task_id"] == "idpgap-gate-00"
    assert is_manual_gate(spec["depends_on_task_id"], None)


def test_priority_is_never_critical():
    """A critical card is auto-promoted out of 'suggested' by the deadlock-breaker."""
    config = dict(load_config())
    config["priority_by_level"] = {"Bronze": "critical"}
    config["default_priority"] = "critical"
    for rule in (BRONZE_RULE, GOLD_RULE, UNGATED_RULE):
        assert build_task_spec(_gap("ndc", rule), config)["priority"] != "critical"


def test_description_carries_the_failure_message():
    spec = build_task_spec(_gap("ndc", GOLD_RULE), load_config())
    assert GOLD_RULE.failure_message in spec["description"]


def test_acceptance_criteria_names_the_evidence_source_and_forbids_gaming_it():
    spec = build_task_spec(_gap("ndc", GOLD_RULE), load_config())
    criteria = spec["acceptance_criteria"]
    assert GOLD_RULE.expression in criteria
    assert "idp.components" in criteria
    assert "exemption" in criteria


def test_gate_spec_is_recognised_as_a_manual_gate():
    spec = gate_spec("idpgap-gate-00")
    assert is_manual_gate(spec["id"], spec["title"])
    assert spec["status"] == "in_progress"


def test_shipped_config_is_disabled_and_capped():
    """The dry run has to be provable before anything can be written."""
    config = load_config()
    assert config["enabled"] is False
    assert 0 < int(config["max_tasks_per_run"]) <= 25
    assert 0 < int(config["max_tasks_per_component"]) <= 5


def test_missing_config_falls_back_to_safe_defaults(tmp_path):
    config = load_config(tmp_path / "does-not-exist.yaml")
    assert config["enabled"] is False


# ---------------------------------------------------------------------------
# Board writes — against a real SQLite kanban table
# ---------------------------------------------------------------------------


@pytest.fixture
def board(tmp_path, monkeypatch):
    """An empty kanban board on a throwaway SQLite file."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "kanban.db"))

    from tools.db.storage import get_connection
    from tools.kanban.init_db import init_kanban_tables

    init_kanban_tables()
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _count(conn) -> int:
    return int(dict(conn.execute("SELECT COUNT(*) AS n FROM kanban_tasks").fetchone())["n"])


@pytest.fixture
def two_gaps(monkeypatch):
    """Stub the evaluation so the write path is what is under test."""
    gaps = [_gap("ndc", BRONZE_RULE), _gap("qdc", GOLD_RULE)]
    monkeypatch.setattr(
        gap_seeder, "collect_gaps", lambda *a, **k: (list(gaps), ["component-readiness"])
    )
    return gaps


def test_dry_run_writes_nothing(board, two_gaps):
    report = seed(board, dry_run=True, config={**load_config(), "enabled": True})
    assert report["selected"] == 2
    assert report["created"] == []
    assert _count(board) == 0


def test_seed_creates_one_task_per_gap_and_a_held_gate(board, two_gaps):
    report = seed(board, dry_run=False, config={**load_config(), "enabled": True})

    assert len(report["created"]) == 2
    assert report["gate_created"] is True

    rows = {
        r["id"]: r
        for r in (
            dict(x)
            for x in board.execute(
                "SELECT id, status, depends_on_task_id, acceptance_criteria FROM kanban_tasks"
            ).fetchall()
        )
    }
    assert rows["idpgap-gate-00"]["status"] == "in_progress"
    for task_id in report["created"]:
        assert rows[task_id]["status"] == "suggested"
        assert rows[task_id]["depends_on_task_id"] == "idpgap-gate-00"
        assert rows[task_id]["acceptance_criteria"]


def test_re_running_creates_nothing(board, two_gaps):
    config = {**load_config(), "enabled": True}
    seed(board, dry_run=False, config=config)
    before = _count(board)

    second = seed(board, dry_run=False, config=config)

    assert second["already_seeded"] == 2
    assert second["candidates"] == 0
    assert second["created"] == []
    assert _count(board) == before


def test_already_seeded_gaps_do_not_consume_the_cap(board, monkeypatch):
    """Otherwise the first N gaps are re-offered forever and N+1 never lands."""
    gaps = [_gap(f"c{i}", BRONZE_RULE) for i in range(4)]
    monkeypatch.setattr(gap_seeder, "collect_gaps", lambda *a, **k: (list(gaps), ["sc"]))
    config = {**load_config(), "enabled": True, "max_tasks_per_run": 2}

    first = seed(board, dry_run=False, config=config)
    second = seed(board, dry_run=False, config=config)

    assert len(first["created"]) == 2
    assert len(second["created"]) == 2
    assert {*first["created"]}.isdisjoint(second["created"])
    assert _count(board) == 5  # 4 tasks + the gate


def test_seeding_is_refused_while_the_config_is_disabled(board, two_gaps):
    report = seed(board, dry_run=False, config={**load_config(), "enabled": False})
    assert report["refused"]
    assert report["created"] == []
    assert _count(board) == 0


def test_force_overrides_the_disabled_config(board, two_gaps):
    report = seed(board, dry_run=False, force=True, config={**load_config(), "enabled": False})
    assert report["refused"] is None
    assert len(report["created"]) == 2


def test_seeding_is_refused_when_the_gate_has_been_released(board, two_gaps):
    from tools.kanban.task_factory import create_tasks

    create_tasks([{**gate_spec("idpgap-gate-00"), "status": "done"}])

    report = seed(board, dry_run=False, config={**load_config(), "enabled": True})

    assert report["refused"] and "idpgap-gate-00" in report["refused"]
    assert report["created"] == []
    assert _count(board) == 1  # the gate only


def test_gate_state_rejects_an_id_that_is_not_a_gate(board):
    with pytest.raises(GapSeederError):
        gate_state(board, "idpgap-not-a-gate")


def test_no_candidates_means_no_gate_and_no_tasks(board, monkeypatch):
    """A gate with nothing behind it is litter on the board."""
    monkeypatch.setattr(gap_seeder, "collect_gaps", lambda *a, **k: ([], ["sc"]))
    report = seed(board, dry_run=False, config={**load_config(), "enabled": True})
    assert report["created"] == []
    assert report["gate_created"] is False
    assert _count(board) == 0
