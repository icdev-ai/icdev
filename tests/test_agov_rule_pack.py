#!/usr/bin/env python3
# CUI // SP-CTI
"""agov-det-05 — every shipped rule loads clean, and every shipped rule is monitor-only.

The second half is the load-bearing one. `args/agent_rules/` is the pack ICDEV
ships turned on; `enforce: true` on any file in it would mean the pack blocks
live tool calls the moment it is installed, and a detection rule written against
a normalized event view is wrong at first by construction. Enforcement is opted
into per rule by an operator, in an operator-controlled directory, wired in
agov-det-06. That safety story is one boolean wide, so it gets a test.

This validates the rules DIRECTLY against the documented schema rather than
through `tools/agent_detect/rules.py`, because the loader (agov-det-03) and the
sequence evaluator (agov-det-04) are separate cards. When the loader lands,
`test_rules_load_through_the_loader_when_available` starts exercising it too and
this file becomes the contract both sides are held to.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

RULES_DIR = pathlib.Path(__file__).resolve().parents[1] / "args" / "agent_rules"

#: Matcher keys from the agov-det-03 contract. A rule using a key outside this
#: set is either a typo or a silent no-op — the loader is specified to SKIP an
#: unknown key rather than treat it as match-all, so a typo does not fail open,
#: it fails silent. Which is why it is caught here instead.
MATCHER_KEYS = {
    "event_type",
    "tool_name",
    "actor",
    "file_path_glob",
    "command_name",
    "command_matches",
    "url_matches",
    "argv_contains",
}
ALL_MATCHER_KEYS = MATCHER_KEYS | {f"not_{k}" for k in MATCHER_KEYS}

#: Mutually exclusive event types from the agov-det-01 contract.
EVENT_TYPES = {
    "command.exec",
    "file.read",
    "file.write",
    "file.delete",
    "network.indicator",
    "tool.call",
}

SEVERITIES = {"info", "low", "medium", "high", "critical"}

#: Keys that are regular expressions, not globs or literals — compiled here so a
#: bad pattern fails the build rather than being skipped at runtime.
REGEX_KEYS = {"command_matches", "url_matches", "not_command_matches", "not_url_matches"}

REQUIRED_KEYS = {"id", "version", "title", "severity", "tags", "enabled", "enforce"}
DENY_MESSAGE_MAX_BYTES = 512
_ID_RE = re.compile(r"^[a-z0-9]+(\.[a-z0-9_]+)+$")
_DURATION_RE = re.compile(r"^\d+(ms|s|m|h|d)$")


def _rule_files() -> list[pathlib.Path]:
    return sorted(RULES_DIR.rglob("*.yaml")) + sorted(RULES_DIR.rglob("*.yml"))


def _load(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(RULES_DIR).as_posix()


RULE_FILES = _rule_files()


def test_the_pack_is_not_empty():
    """A passing suite over zero rules would prove nothing at all."""
    assert len(RULE_FILES) >= 10, (
        f"expected the seed pack under {RULES_DIR}; found {len(RULE_FILES)} rule files"
    )


def test_every_group_is_represented():
    """secrets / exfil / persistence / tamper / chains, per the DET card."""
    groups = {p.relative_to(RULES_DIR).parts[0] for p in RULE_FILES}
    assert {"secrets", "exfil", "persistence", "tamper", "chains"} <= groups, (
        f"missing rule groups: {groups}"
    )


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_rule_parses_as_a_yaml_mapping(path: pathlib.Path):
    rule = _load(path)
    assert isinstance(rule, dict), f"{_rel(path)} is not a YAML mapping"


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_rule_is_monitor_only(path: pathlib.Path):
    """THE test. Nothing shipped in this directory may block a live tool call.

    If this fails, do not relax it. Move the rule into the operator-controlled
    enforcement directory instead — that separation is the entire safety design.
    """
    rule = _load(path)
    assert "enforce" in rule, f"{_rel(path)} does not declare `enforce`"
    assert rule["enforce"] is False, (
        f"{_rel(path)} ships with enforce={rule['enforce']!r}. Every rule under "
        "args/agent_rules/ must be monitor-only; enforcement is opted into per "
        "rule by an operator (agov-det-06)."
    )


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_rule_has_the_required_metadata(path: pathlib.Path):
    rule = _load(path)
    missing = REQUIRED_KEYS - set(rule)
    assert not missing, f"{_rel(path)} is missing required keys: {sorted(missing)}"

    assert _ID_RE.match(str(rule["id"])), (
        f"{_rel(path)}: id {rule['id']!r} must be dot-separated lowercase"
    )
    assert str(rule["severity"]) in SEVERITIES, (
        f"{_rel(path)}: severity {rule['severity']!r} not in {sorted(SEVERITIES)}"
    )
    assert isinstance(rule["tags"], list) and rule["tags"], f"{_rel(path)}: tags must be a non-empty list"
    assert isinstance(rule["enabled"], bool), f"{_rel(path)}: enabled must be a boolean"
    assert str(rule["title"]).strip(), f"{_rel(path)}: title must not be empty"
    # version is compared as a string in findings; a bare YAML number would make
    # `1` and `"1"` two different rule versions in the same table.
    assert isinstance(rule["version"], str), (
        f"{_rel(path)}: version must be quoted so 1 and \"1\" cannot diverge"
    )

    deny = rule.get("deny_message", "")
    assert len(str(deny).encode("utf-8")) <= DENY_MESSAGE_MAX_BYTES, (
        f"{_rel(path)}: deny_message exceeds {DENY_MESSAGE_MAX_BYTES} bytes"
    )


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_rule_ids_match_their_location(path: pathlib.Path):
    """`id` names its group, so a finding's rule_id points at the file."""
    rule = _load(path)
    group = path.relative_to(RULES_DIR).parts[0]
    assert str(rule["id"]).startswith(f"{group}."), (
        f"{_rel(path)}: id {rule['id']!r} should start with {group!r}"
    )


def test_rule_ids_are_unique():
    """Findings cite rule_id forever; two files sharing one is unresolvable."""
    seen: dict[str, str] = {}
    for path in RULE_FILES:
        rid = str(_load(path)["id"])
        assert rid not in seen, f"duplicate rule id {rid!r} in {_rel(path)} and {seen[rid]}"
        seen[rid] = _rel(path)


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_rule_declares_exactly_one_of_expr_or_sequence(path: pathlib.Path):
    rule = _load(path)
    has_expr = "expr" in rule
    has_seq = "sequence" in rule
    assert has_expr != has_seq, (
        f"{_rel(path)}: a rule needs exactly one of `expr` or `sequence` "
        f"(expr={has_expr}, sequence={has_seq})"
    )


def _check_matcher(where: str, matcher) -> None:
    assert isinstance(matcher, dict) and matcher, f"{where}: matcher must be a non-empty mapping"
    unknown = set(matcher) - ALL_MATCHER_KEYS
    assert not unknown, (
        f"{where}: unknown matcher keys {sorted(unknown)}. The loader SKIPS an "
        f"unrecognised key, so a typo here is a rule that silently never fires."
    )
    for key, values in matcher.items():
        assert isinstance(values, list) and values, (
            f"{where}: `{key}` must be a non-empty list (the list ORs)"
        )
        for value in values:
            assert isinstance(value, str), f"{where}: `{key}` value {value!r} must be a string"
        if key in REGEX_KEYS:
            for value in values:
                try:
                    re.compile(value)
                except re.error as exc:
                    pytest.fail(f"{where}: `{key}` pattern {value!r} does not compile: {exc}")
        if key in ("event_type", "not_event_type"):
            bad = set(values) - EVENT_TYPES
            assert not bad, f"{where}: unknown event types {sorted(bad)}"


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_matchers_use_known_keys_and_compilable_patterns(path: pathlib.Path):
    rule = _load(path)
    if "expr" in rule:
        _check_matcher(f"{_rel(path)}:expr", rule["expr"])
    else:
        for i, step in enumerate(rule["sequence"]["steps"]):
            _check_matcher(f"{_rel(path)}:sequence.steps[{i}]", step)


@pytest.mark.parametrize(
    "path", [p for p in RULE_FILES if "sequence" in (_load(p) or {})], ids=_rel
)
def test_sequence_rules_are_well_formed(path: pathlib.Path):
    seq = _load(path)["sequence"]
    assert isinstance(seq, dict), f"{_rel(path)}: sequence must be a mapping"

    assert "within" in seq or "within_events" in seq, (
        f"{_rel(path)}: a sequence needs at least one of `within` / `within_events`. "
        "An unbounded window would stitch a chain out of a whole day's activity."
    )
    if "within" in seq:
        assert _DURATION_RE.match(str(seq["within"])), (
            f"{_rel(path)}: within={seq['within']!r} is not a duration like '30m'"
        )
    if "within_events" in seq:
        assert isinstance(seq["within_events"], int) and seq["within_events"] > 0

    steps = seq.get("steps")
    assert isinstance(steps, list), f"{_rel(path)}: sequence.steps must be a list"
    assert 2 <= len(steps) <= 8, (
        f"{_rel(path)}: a sequence takes 2-8 ordered steps, got {len(steps)}"
    )

    max_matches = seq.get("max_matches", 1)
    assert isinstance(max_matches, int) and 1 <= max_matches <= 16, (
        f"{_rel(path)}: max_matches must be an int in 1..16, got {max_matches!r}"
    )


@pytest.mark.parametrize("path", RULE_FILES, ids=_rel)
def test_no_matcher_step_is_structurally_unmatchable(path: pathlib.Path):
    """A step naming a non-command event type AND a command key can never fire.

    Keys AND together, so `event_type: [network.indicator]` combined with
    `command_name: [...]` is a dead branch: a network indicator carries no
    command to name. This is easy to write and impossible to notice at runtime,
    because a rule that never fires looks exactly like a clean environment.
    """
    rule = _load(path)
    matchers = [rule["expr"]] if "expr" in rule else list(rule["sequence"]["steps"])
    command_keys = {"command_name", "command_matches", "argv_contains"}
    for i, matcher in enumerate(matchers):
        declared = set(matcher.get("event_type") or [])
        if declared and command_keys & set(matcher):
            assert "command.exec" in declared, (
                f"{_rel(path)} matcher[{i}]: event_type={sorted(declared)} is ANDed "
                f"with {sorted(command_keys & set(matcher))}, which only a "
                f"command.exec event carries — this branch can never match."
            )


def test_rules_load_through_the_loader_when_available():
    """Once agov-det-03 lands, the pack must also satisfy the real loader.

    Skipped until then. The point is that this file stops being the only
    definition of the schema the moment there is a second one.
    """
    rules_mod = pytest.importorskip(
        "tools.agent_detect.rules", reason="agov-det-03 loader not merged yet"
    )
    load = getattr(rules_mod, "load_rules", None)
    if load is None:  # pragma: no cover — loader API not final until det-03 merges
        pytest.skip("tools.agent_detect.rules exposes no load_rules()")

    loaded = load(RULES_DIR)
    assert len(loaded) == len(RULE_FILES), (
        f"loader accepted {len(loaded)} of {len(RULE_FILES)} shipped rules — "
        "the loader SKIPS malformed rules, so a shortfall means a rule in this "
        "pack is silently dead."
    )
    for rule in loaded:
        assert getattr(rule, "enforce", False) is False
