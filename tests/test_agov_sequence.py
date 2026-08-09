# CUI // SP-CTI
"""Sequence / chain evaluator — agov-det-04.

The chain this fixture models is the one that motivated the whole card: read
``.env``, then POST to an external host. Neither half is remarkable alone, which
is exactly why ICDEV could not see it before.

The negative cases carry the weight. Out-of-order must not fire (a POST followed
by a read is not exfiltration), a cross-session pair must not fire (ICDEV runs
many concurrent sessions against one database, so stitching two of them together
would generate false positives continuously), and an over-wide gap must not fire
(without a window, every long-lived session eventually does both things).
"""
from __future__ import annotations

import logging

import pytest

from tools.agent_detect import sequence as sequence_module
from tools.agent_detect.sequence import (
    MAX_MAX_MATCHES,
    SequenceMatch,
    SequenceSpec,
    SequenceSpecError,
    builtin_match_event,
    coerce_timestamp,
    evaluate_sequence,
    find_sequence_matches,
    parse_duration,
    partition_key,
    reset_matcher_cache,
)

READ_ENV = {"event_type": "file.read", "file_path_glob": "*/.env"}
EGRESS = {"event_type": "network.indicator", "url_matches": r"^https?://"}

RULE = {
    "id": "chains.secret_read_then_egress",
    "version": "1.0.0",
    "title": "Secret read followed by outbound network call",
    "severity": "high",
    "tags": ["T1041"],
    "sequence": {"within": "30m", "steps": [READ_ENV, EGRESS]},
}


def ev(event_id, ts, session_id="sess-a", **kwargs):
    """One normalized agent event (agov-det-01 shape), as a plain mapping."""
    event = {
        "event_id": event_id,
        "session_id": session_id,
        "ts": ts,
        "actor": kwargs.pop("actor", "claude"),
        "project_id": kwargs.pop("project_id", "icdev"),
        "source": kwargs.pop("source", "hook_events"),
    }
    event.update(kwargs)
    return event


def read_env(event_id, ts, **kwargs):
    return ev(event_id, ts, event_type="file.read", file_path="/repo/.env", **kwargs)


def egress(event_id, ts, **kwargs):
    return ev(
        event_id,
        ts,
        event_type="network.indicator",
        url="https://evil.example.com/collect",
        **kwargs,
    )


def noise(event_id, ts, **kwargs):
    """An ordinary event between the steps — the chain must survive it."""
    return ev(event_id, ts, event_type="file.write", file_path="/repo/tools/x.py", **kwargs)


def ids(findings):
    return [list(f.event_ids) for f in findings]


@pytest.fixture
def warnings():
    """Capture the module's warnings.

    ``icdev_logger`` sets ``propagate = False`` so records never reach the root
    handler ``caplog`` installs — the handler has to go on this logger.
    """
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    logger = sequence_module.logger
    previous = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


# ---------------------------------------------------------------------------
# the chain fires
# ---------------------------------------------------------------------------


def test_non_adjacent_steps_in_order_within_window_fire():
    events = [
        read_env("e1", "2026-08-09T12:00:00Z"),
        noise("e2", "2026-08-09T12:01:00Z"),
        noise("e3", "2026-08-09T12:02:00Z"),
        egress("e4", "2026-08-09T12:03:00Z"),
    ]

    findings = evaluate_sequence(RULE, events)

    assert len(findings) == 1
    assert findings[0].rule_id == "chains.secret_read_then_egress"
    assert findings[0].session_id == "sess-a"
    # Monitor-only by default: no `enforce` on the rule means nothing is blocked.
    assert findings[0].enforced is False
    assert findings[0].decision == "observed"


def test_finding_cites_contributing_event_ids_in_step_order():
    """A reviewer reconstructs the chain from the ids, not by re-running the engine."""
    events = [
        noise("n0", "2026-08-09T11:59:00Z"),
        read_env("read-1", "2026-08-09T12:00:00Z"),
        noise("n1", "2026-08-09T12:01:00Z"),
        egress("post-1", "2026-08-09T12:02:00Z"),
        noise("n2", "2026-08-09T12:03:00Z"),
    ]

    findings = evaluate_sequence(RULE, events)

    assert len(findings) == 1
    finding = findings[0]
    # Step order, not timestamp order by accident and not the noise around it.
    assert finding.event_ids == ("read-1", "post-1")
    assert finding.to_dict()["event_ids"] == ["read-1", "post-1"]
    assert "n0" not in finding.event_ids and "n1" not in finding.event_ids


def test_input_order_does_not_matter_events_are_ordered_by_timestamp():
    events = [
        egress("post-1", "2026-08-09T12:05:00Z"),
        read_env("read-1", "2026-08-09T12:00:00Z"),
    ]

    findings = evaluate_sequence(RULE, events)

    assert ids(findings) == [["read-1", "post-1"]]


# ---------------------------------------------------------------------------
# the chain does not fire
# ---------------------------------------------------------------------------


def test_out_of_order_steps_do_not_fire():
    """POST first, then read .env — the reverse order is not the chain."""
    events = [
        egress("post-1", "2026-08-09T12:00:00Z"),
        noise("n1", "2026-08-09T12:01:00Z"),
        read_env("read-1", "2026-08-09T12:02:00Z"),
    ]

    assert evaluate_sequence(RULE, events) == []


def test_different_session_ids_never_chain():
    """PARTITIONING IS THE CORRECTNESS PROPERTY — two concurrent sessions are not one."""
    events = [
        read_env("read-1", "2026-08-09T12:00:00Z", session_id="sess-a"),
        egress("post-1", "2026-08-09T12:01:00Z", session_id="sess-b"),
    ]

    assert evaluate_sequence(RULE, events) == []

    # Same events, same session: the only difference is the partition.
    same_session = [
        read_env("read-1", "2026-08-09T12:00:00Z", session_id="sess-a"),
        egress("post-1", "2026-08-09T12:01:00Z", session_id="sess-a"),
    ]
    assert ids(evaluate_sequence(RULE, same_session)) == [["read-1", "post-1"]]


@pytest.mark.parametrize("differing", ["actor", "project_id", "source"])
def test_every_partition_field_isolates_a_chain(differing):
    events = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        egress("post-1", "2026-08-09T12:01:00Z", **{differing: "other"}),
    ]

    assert evaluate_sequence(RULE, events) == []


def test_gap_exceeding_within_does_not_fire():
    events = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        egress("post-1", "2026-08-09T12:45:00Z"),  # 45m > 30m
    ]

    assert evaluate_sequence(RULE, events) == []

    # Widen only the window and the same events fire.
    wider = dict(RULE, sequence={"within": "2h", "steps": [READ_ENV, EGRESS]})
    assert ids(evaluate_sequence(wider, events)) == [["read-1", "post-1"]]


def test_window_boundary_is_inclusive():
    events = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        egress("post-1", "2026-08-09T12:30:00Z"),  # exactly 30m
    ]

    assert ids(evaluate_sequence(RULE, events)) == [["read-1", "post-1"]]


def test_missing_step_does_not_fire():
    events = [read_env("read-1", "2026-08-09T12:00:00Z"), noise("n1", "2026-08-09T12:01:00Z")]

    assert evaluate_sequence(RULE, events) == []


def test_disabled_rule_yields_nothing():
    events = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        egress("post-1", "2026-08-09T12:01:00Z"),
    ]

    assert evaluate_sequence(dict(RULE, enabled=False), events) == []


# ---------------------------------------------------------------------------
# max_matches
# ---------------------------------------------------------------------------


def _two_chains():
    return [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        egress("post-1", "2026-08-09T12:01:00Z"),
        read_env("read-2", "2026-08-09T12:02:00Z"),
        egress("post-2", "2026-08-09T12:03:00Z"),
        read_env("read-3", "2026-08-09T12:04:00Z"),
        egress("post-3", "2026-08-09T12:05:00Z"),
    ]


def test_max_matches_defaults_to_one_per_partition():
    findings = evaluate_sequence(RULE, _two_chains())

    assert ids(findings) == [["read-1", "post-1"]]


def test_max_matches_caps_findings_per_partition():
    rule = dict(RULE, sequence={"within": "30m", "steps": [READ_ENV, EGRESS], "max_matches": 2})

    findings = evaluate_sequence(rule, _two_chains())

    # Capped at 2 even though a third chain exists, and the matches do not overlap.
    assert ids(findings) == [["read-1", "post-1"], ["read-2", "post-2"]]


def test_max_matches_is_per_partition_not_global():
    rule = dict(RULE, sequence={"within": "30m", "steps": [READ_ENV, EGRESS], "max_matches": 1})
    events = [
        read_env("a-read", "2026-08-09T12:00:00Z", session_id="sess-a"),
        egress("a-post", "2026-08-09T12:01:00Z", session_id="sess-a"),
        read_env("b-read", "2026-08-09T12:02:00Z", session_id="sess-b"),
        egress("b-post", "2026-08-09T12:03:00Z", session_id="sess-b"),
    ]

    findings = evaluate_sequence(rule, events)

    assert sorted(ids(findings)) == [["a-read", "a-post"], ["b-read", "b-post"]]


# ---------------------------------------------------------------------------
# within_events
# ---------------------------------------------------------------------------


def test_within_events_bounds_the_positional_span():
    rule = dict(RULE, sequence={"within_events": 3, "steps": [READ_ENV, EGRESS]})
    tight = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        noise("n1", "2026-08-09T12:00:10Z"),
        egress("post-1", "2026-08-09T12:00:20Z"),
    ]
    wide = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        noise("n1", "2026-08-09T12:00:10Z"),
        noise("n2", "2026-08-09T12:00:20Z"),
        egress("post-1", "2026-08-09T12:00:30Z"),
    ]

    assert ids(evaluate_sequence(rule, tight)) == [["read-1", "post-1"]]
    assert evaluate_sequence(rule, wide) == []


def test_match_reports_its_spans():
    matches = find_sequence_matches(
        SequenceSpec.from_dict({"within": "30m", "steps": [READ_ENV, EGRESS]}),
        [
            read_env("read-1", "2026-08-09T12:00:00Z"),
            noise("n1", "2026-08-09T12:01:00Z"),
            egress("post-1", "2026-08-09T12:02:00Z"),
        ],
    )

    assert len(matches) == 1
    assert isinstance(matches[0], SequenceMatch)
    assert matches[0].span_events == 3
    assert matches[0].span_seconds == 120.0


# ---------------------------------------------------------------------------
# events that cannot be chained safely
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["session_id", "event_id", "ts"])
def test_events_missing_a_load_bearing_field_are_excluded(missing):
    """Cannot attribute, cannot cite, or cannot order — all fail closed."""
    read = read_env("read-1", "2026-08-09T12:00:00Z")
    post = egress("post-1", "2026-08-09T12:01:00Z")
    post[missing] = None

    assert evaluate_sequence(RULE, [read, post]) == []


def test_unparseable_timestamp_is_excluded():
    read = read_env("read-1", "2026-08-09T12:00:00Z")
    post = egress("post-1", "yesterday afternoon")

    assert evaluate_sequence(RULE, [read, post]) == []


def test_attribute_style_events_are_supported():
    """Works against agov-det-01's AgentEvent dataclass, not just mappings."""

    class Event:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    events = [Event(**read_env("read-1", "2026-08-09T12:00:00Z")),
              Event(**egress("post-1", "2026-08-09T12:01:00Z"))]

    assert ids(evaluate_sequence(RULE, events)) == [["read-1", "post-1"]]


def test_agent_and_actor_are_the_same_partition_field():
    read = read_env("read-1", "2026-08-09T12:00:00Z")
    post = egress("post-1", "2026-08-09T12:01:00Z")
    post.pop("actor")
    post["agent"] = "claude"

    assert partition_key(read) == partition_key(post)
    assert ids(evaluate_sequence(RULE, [read, post])) == [["read-1", "post-1"]]


# ---------------------------------------------------------------------------
# spec validation — malformed is inert, never match-all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        {"within": "30m", "steps": [READ_ENV]},  # < 2 steps
        {"within": "30m", "steps": [READ_ENV] * 9},  # > 8 steps
        {"steps": [READ_ENV, EGRESS]},  # neither within nor within_events
        {"within": "30 months", "steps": [READ_ENV, EGRESS]},  # unparseable duration
        {"within": "0m", "steps": [READ_ENV, EGRESS]},  # non-positive
        {"within": "30m", "steps": [READ_ENV, EGRESS], "max_matches": 0},
        {"within": "30m", "steps": [READ_ENV, EGRESS], "max_matches": MAX_MAX_MATCHES + 1},
        {"within": "30m", "steps": [READ_ENV, {}]},  # empty step
        {"within": "30m", "steps": "not-a-list"},
        {"within": "30m", "steps": [READ_ENV, EGRESS], "widthin_events": 4},  # typo
        {"within_events": 0, "steps": [READ_ENV, EGRESS]},
        {"within_events": True, "steps": [READ_ENV, EGRESS]},
    ],
)
def test_malformed_sequence_specs_are_rejected_at_parse(spec):
    with pytest.raises(SequenceSpecError):
        SequenceSpec.from_dict(spec)


def test_evaluate_swallows_a_malformed_spec_and_reports_nothing(warnings):
    """A broken rule is inert, not match-all, and must not crash its caller."""
    rule = dict(RULE, sequence={"steps": [READ_ENV, EGRESS]})  # no window
    events = [
        read_env("read-1", "2026-08-09T12:00:00Z"),
        egress("post-1", "2026-08-09T12:01:00Z"),
    ]

    assert evaluate_sequence(rule, events) == []
    # Named, so an operator can find the file rather than hunt a silent no-op.
    assert any("chains.secret_read_then_egress" in message for message in warnings)


def test_max_matches_defaults_and_boundaries():
    assert SequenceSpec.from_dict({"within": "30m", "steps": [READ_ENV, EGRESS]}).max_matches == 1
    at_cap = SequenceSpec.from_dict(
        {"within": "30m", "steps": [READ_ENV, EGRESS], "max_matches": MAX_MAX_MATCHES}
    )
    assert at_cap.max_matches == MAX_MAX_MATCHES


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("45s", 45.0), ("30m", 1800.0), ("2h", 7200.0), ("1d", 86400.0), ("1h30m", 5400.0), (90, 90.0)],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


def test_coerce_timestamp_accepts_the_shapes_icdev_writes():
    aware = coerce_timestamp("2026-08-09T12:00:00+00:00")
    assert coerce_timestamp("2026-08-09T12:00:00Z") == aware
    assert coerce_timestamp("2026-08-09 12:00:00") == aware
    assert coerce_timestamp(aware.timestamp()) == aware
    assert coerce_timestamp("not a time") is None


# ---------------------------------------------------------------------------
# the built-in step matcher
# ---------------------------------------------------------------------------


def test_unknown_matcher_key_fails_closed(warnings):
    """A typo makes the step unmatchable — never match-all."""
    event = read_env("read-1", "2026-08-09T12:00:00Z")

    assert builtin_match_event({"file_path_glob": "*/.env"}, event) is True
    assert builtin_match_event({"file_path_glob": "*/.env", "bogus_key": "x"}, event) is False
    assert any("bogus_key" in message for message in warnings)


def test_matcher_keys_and_together_lists_or():
    event = read_env("read-1", "2026-08-09T12:00:00Z", tool_name="Read")

    assert builtin_match_event({"event_type": ["file.write", "file.read"]}, event) is True
    assert builtin_match_event({"event_type": "file.read", "tool_name": "Read"}, event) is True
    assert builtin_match_event({"event_type": "file.read", "tool_name": "Bash"}, event) is False
    assert builtin_match_event({"not_actor": "claude"}, event) is False
    assert builtin_match_event({"not_actor": ["someone-else"]}, event) is True


def test_matcher_never_substring_matches_a_flattened_tool_input():
    """The args/agent_approval_policy.yaml:107-126 fail-open, as a regression.

    A ``git push`` carrying a note that mentions ``mkdir`` must not read as a
    ``mkdir``. Matching is against structured fields, never a concatenated blob.
    """
    event = ev(
        "e1",
        "2026-08-09T12:00:00Z",
        event_type="command.exec",
        tool_name="Bash",
        command="git push origin main",
        command_name="git",
        argv=["git", "push", "origin", "main"],
        note="mkdir logs",
    )

    assert builtin_match_event({"command_name": "mkdir"}, event) is False
    assert builtin_match_event({"argv_contains": "mkdir"}, event) is False
    assert builtin_match_event({"command_name": "git", "argv_contains": "push"}, event) is True


def test_uncompilable_regex_is_skipped_without_raising(warnings):
    event = egress("post-1", "2026-08-09T12:00:00Z")
    reset_matcher_cache()  # the compile cache would swallow a repeat warning

    assert builtin_match_event({"url_matches": "([unclosed"}, event) is False
    assert any("([unclosed" in message for message in warnings)


def test_a_custom_match_fn_is_honoured():
    """agov-det-03's matcher drops in without touching this module."""
    calls = []

    def always(matcher, event):
        calls.append(matcher)
        return True

    spec = SequenceSpec.from_dict({"within": "30m", "steps": [READ_ENV, EGRESS]})
    matches = find_sequence_matches(
        spec,
        [noise("n1", "2026-08-09T12:00:00Z"), noise("n2", "2026-08-09T12:01:00Z")],
        match_fn=always,
    )

    assert ids(matches) == [["n1", "n2"]]
    assert calls
