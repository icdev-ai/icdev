# CUI // SP-CTI
"""Unit tests for tools.observability_canvas.sigma_generator — 5 cases."""

import yaml

from tools.observability_canvas.sigma_generator import (
    generate_rules,
    validate_rule,
)


# ---------------------------------------------------------------------------
# Case 1: generate_rules returns a list of YAML strings
# ---------------------------------------------------------------------------
def test_generate_rules_returns_yaml_strings():
    """(tid, src) pair produces a non-empty list of valid YAML strings."""
    rules = generate_rules([("T1059", "src-os-log")])
    assert isinstance(rules, list)
    assert len(rules) == 1
    assert isinstance(rules[0], str)
    # Must be parseable YAML
    parsed = yaml.safe_load(rules[0])
    assert isinstance(parsed, dict)
    assert parsed.get("title")


# ---------------------------------------------------------------------------
# Case 2: detection block is present and contains a condition
# ---------------------------------------------------------------------------
def test_rule_has_detection_block():
    """Generated rule YAML contains a detection block with a condition key."""
    rules = generate_rules([("T1059", "src-wef"), ("T1078", "src-iam")])
    for raw in rules:
        parsed = yaml.safe_load(raw)
        det = parsed.get("detection")
        assert det is not None, "detection block missing"
        assert "condition" in det, "detection.condition missing"
        assert "selection" in det, "detection.selection missing"


# ---------------------------------------------------------------------------
# Case 3: tags include technique tag and tactic tag
# ---------------------------------------------------------------------------
def test_rule_tags_include_tid_and_tactic():
    """Tags contain attack.<tid> and attack.<tactic>."""
    rules = generate_rules([("T1059", "src-endpoint"), ("T1110", "src-iam")])
    expected_tids = ["attack.t1059", "attack.t1110"]
    for raw, tid_tag in zip(rules, expected_tids):
        parsed = yaml.safe_load(raw)
        tags = parsed.get("tags", [])
        assert any(t == tid_tag for t in tags), f"Expected {tid_tag} in tags: {tags}"
        assert any(t.startswith("attack.") and t != tid_tag for t in tags), \
            f"Expected a tactic tag besides {tid_tag} in tags: {tags}"


# ---------------------------------------------------------------------------
# Case 4: falsepositives stub is a non-empty list
# ---------------------------------------------------------------------------
def test_rule_has_falsepositives_stub():
    """Generated rules always include a falsepositives list with ≥1 entry."""
    for tid, src in [("T1059", "src-os-log"), ("T1566", "src-app-log"), ("T9999", "src-unknown")]:
        rules = generate_rules([(tid, src)])
        parsed = yaml.safe_load(rules[0])
        fps = parsed.get("falsepositives")
        assert isinstance(fps, list), f"falsepositives should be a list for ({tid}, {src})"
        assert len(fps) >= 1, f"falsepositives is empty for ({tid}, {src})"


# ---------------------------------------------------------------------------
# Case 5: sigma-cli validation passes (or is skipped when not installed)
# ---------------------------------------------------------------------------
def test_sigma_cli_validation_passes_or_skips():
    """validate_rule() returns True for well-formed rules (sigma-cli optional)."""
    rules = generate_rules([("T1059", "src-os-log"), ("T1078", "src-cloud-log")])
    for raw in rules:
        result = validate_rule(raw)
        assert result is True, "validate_rule should return True (or skip when sigma-cli absent)"
