# CUI // SP-CTI
"""dwo-vv-02 — the trigger filter language, operator by operator.

`tests/test_dwo_event_dispatch.py` (dwo-evt-02) covers the dispatch path end to
end: a cleared event starts its run, a replayed delivery id starts exactly one,
an unregistered source starts nothing, and an event above the workflow's IL is
refused. It samples the filter language with a single test.

The filter language is where a trigger silently misfires — a workflow bound to
`action equals opened` that also runs on `reopened` is a production incident,
not a test failure — so this file walks every operator the registry accepts, in
both directions, plus the field resolution that feeds them. It deliberately
does not restate the dispatch scenarios that file already owns.

The vocabulary is not new: `evaluate_filters` delegates to
`automation_builder.evaluate_condition`, so these cases also pin the promise
in the spike that Studio has exactly one condition DSL.

That delegation is what lets most of this file run *now*. The operator
semantics live in `automation_builder`, which is on `main`, so the matrix below
exercises them directly rather than waiting on the trigger surface to merge.
Only the parts `event_sources` genuinely owns — dotted-path field resolution,
the IL ordering, the audit vocabulary, and the delegation itself — are guarded;
`tools/studio/event_sources.py` is delivered by dwo-evt-02 and, as of this
commit, lives only on the unmerged branch `kanban/dwo-evt-02`.

The guarded tests are not speculative. This whole file was run against
`origin/kanban/dwo-evt-02` (at `0910fa81c`) in a detached worktree: all 52
tests pass with nothing skipped. So the guard below is hiding working tests
from an incomplete checkout, not deferring unfinished ones. If it ever fails
after that branch merges, the executor changed — re-verify before editing the
assertions. See the dwo-vv-02 handoff note.
"""

from __future__ import annotations

import pytest

from tools.studio.automation_builder import (
    CONDITION_OPERATORS,
    evaluate_condition,
)

from tools.studio import event_sources

#: dwo-evt-02 has merged, so the import above is unconditional and this marker is
#: a no-op kept only so the decorated cases below read unchanged.
#:
#: It used to be a skipif on `event_sources is None`, set by an ImportError
#: guard. That guard is how this file's failures stayed invisible: once
#: dwo-evt-02 landed the import succeeded, the tests stopped skipping, and 28 of
#: them began failing against a different API surface than the one specified —
#: `evaluate_filters` and `resolve_field` did not exist at all, and the audit
#: vocabulary had merged under different names. Nothing reported it, because the
#: CI Test job is a 12-file allowlist that does not include this file.
#:
#: A guard that silently converts "not built yet" into "built wrong" is worse
#: than no guard. If `event_sources` is ever absent again, the import fails loudly
#: at collection — which is the correct signal for a module this repo ships.
#:
#: Kept as an identity decorator so the cases below read unchanged; an empty
#: `pytest.mark.usefixtures()` would warn on every use.


def needs_event_sources(func):
    return func

_PAYLOAD = {
    "action": "opened",
    "severity": "HIGH",
    "count": 7,
    "note": "",
    "repository": {"name": "icdev", "owner": {"login": "icdev-ai"}},
    "labels": ["bug", "urgent"],
}


def _filter(field: str, operator: str, value) -> list[dict]:
    return [{"field": field, "operator": operator, "value": value}]


# ── Every operator, in both directions ─────────────────────────────────────

_MATCHES = [
    ("action", "equals", "opened"),
    ("action", "not_equals", "closed"),
    ("action", "contains", "open"),
    ("count", "greater_than", "3"),
    ("count", "less_than", "10"),
    ("action", "in_list", "opened,reopened"),
    ("note", "is_empty", ""),
    ("action", "is_not_empty", ""),
]

_REJECTS = [
    ("action", "equals", "reopened"),
    ("action", "not_equals", "opened"),
    ("action", "contains", "closed"),
    ("count", "greater_than", "10"),
    ("count", "less_than", "3"),
    ("action", "in_list", "closed,merged"),
    ("action", "is_empty", ""),
    ("note", "is_not_empty", ""),
]


@pytest.mark.parametrize(("field", "operator", "value"), _MATCHES)
def test_operator_matches(field, operator, value):
    assert evaluate_condition(_PAYLOAD[field], operator, value) is True


@pytest.mark.parametrize(("field", "operator", "value"), _REJECTS)
def test_operator_rejects(field, operator, value):
    assert evaluate_condition(_PAYLOAD[field], operator, value) is False


def test_every_registry_operator_is_covered_in_both_directions():
    """A new operator must arrive with cases here, not silently untested."""
    declared = {op["id"] for op in CONDITION_OPERATORS}

    assert {op for _, op, _ in _MATCHES} == declared
    assert {op for _, op, _ in _REJECTS} == declared


def test_comparison_is_case_insensitive():
    """`severity` arrives as `HIGH` from one platform and `high` from the next;
    a trigger must not depend on which."""
    assert evaluate_condition(_PAYLOAD["severity"], "equals", "high") is True


def test_an_unknown_operator_rejects_rather_than_matching():
    """Fail closed: a typo in a filter must stop the trigger, not open it."""
    assert evaluate_condition(_PAYLOAD["action"], "starts_with", "open") is False


# ── The trigger surface reuses that DSL rather than restating it ───────────

@needs_event_sources
@pytest.mark.parametrize(("field", "operator", "value"), _MATCHES + _REJECTS)
def test_evaluate_filters_agrees_with_the_shared_dsl(field, operator, value):
    """The spike promises Studio has exactly one condition DSL. A second
    implementation inside `event_sources` would show up here as a disagreement
    on some operator, which is precisely the drift worth catching."""
    assert event_sources.evaluate_filters(
        _PAYLOAD, _filter(field, operator, value)
    ) is evaluate_condition(_PAYLOAD[field], operator, value)


# ── Conditions combine as AND ──────────────────────────────────────────────

@needs_event_sources
def test_all_conditions_must_pass():
    both = _filter("action", "equals", "opened") + _filter("count", "greater_than", "3")
    assert event_sources.evaluate_filters(_PAYLOAD, both) is True


@needs_event_sources
def test_one_failing_condition_rejects_the_event():
    mixed = _filter("action", "equals", "opened") + _filter("count", "greater_than", "99")
    assert event_sources.evaluate_filters(_PAYLOAD, mixed) is False


@needs_event_sources
def test_no_filters_matches_everything():
    """An unfiltered trigger is a legitimate binding — every event on the
    source starts the workflow."""
    assert event_sources.evaluate_filters(_PAYLOAD, []) is True
    assert event_sources.evaluate_filters(_PAYLOAD, None) is True


# ── Field resolution ───────────────────────────────────────────────────────

@needs_event_sources
def test_a_dotted_path_reaches_a_nested_field():
    assert event_sources.resolve_field(_PAYLOAD, "repository.owner.login") == "icdev-ai"


@needs_event_sources
def test_a_list_index_is_addressable():
    assert event_sources.resolve_field(_PAYLOAD, "labels.0") == "bug"


@needs_event_sources
def test_an_out_of_range_index_resolves_to_none():
    assert event_sources.resolve_field(_PAYLOAD, "labels.9") is None


@needs_event_sources
def test_a_missing_path_resolves_to_none_rather_than_raising():
    """Webhook payloads differ per platform; an absent field is normal input,
    not an error."""
    assert event_sources.resolve_field(_PAYLOAD, "pull_request.merged") is None
    assert event_sources.resolve_field(_PAYLOAD, "action.nested.deeper") is None


@needs_event_sources
def test_a_filter_on_a_missing_field_rejects():
    """The decisive case: an event that simply lacks the field must not start
    the run."""
    assert event_sources.evaluate_filters(
        _PAYLOAD, _filter("pull_request.merged", "equals", "true")
    ) is False


@needs_event_sources
def test_a_missing_field_reads_as_empty():
    """`is_empty` on an absent path is the documented way to bind a trigger to
    'this key was not sent'."""
    assert event_sources.evaluate_filters(
        _PAYLOAD, _filter("pull_request.merged", "is_empty", "")
    ) is True


# ── Classification ordering ────────────────────────────────────────────────

@needs_event_sources
@pytest.mark.parametrize(
    ("event_il", "workflow_il", "permitted"),
    [
        ("IL2", "IL6", True),
        ("IL4", "IL4", True),
        ("IL5", "IL6", True),
        ("IL5", "IL4", False),
        ("IL6", "IL2", False),
    ],
)
def test_an_event_may_only_start_a_workflow_rated_at_or_above_it(
    event_il, workflow_il, permitted
):
    assert event_sources.classification_allows(event_il, workflow_il) is permitted


@needs_event_sources
def test_the_il_ordering_is_the_gateway_ordering():
    """Redefining it here would let a canvas raised to IL5 leave the trigger
    surface enforcing the old order."""
    from tools.gateway.response_filter import IL_ORDER

    assert event_sources.IL_ORDER == IL_ORDER


# ── The audit vocabulary ───────────────────────────────────────────────────

@needs_event_sources
def test_an_outcome_that_started_nothing_is_still_an_outcome():
    """"Why did this run NOT start" has to be answerable from the audit trail."""
    assert "no_match" in event_sources.OUTCOMES
    assert "refused_classification" in event_sources.OUTCOMES


@needs_event_sources
def test_a_refusal_is_not_recorded_as_a_match():
    """A refused event matched a trigger but dispatched nothing; the legacy
    `matched` flag must not claim otherwise."""
    assert "refused_classification" not in event_sources.MATCHED_OUTCOMES
    assert set(event_sources.MATCHED_OUTCOMES) <= set(event_sources.OUTCOMES)
