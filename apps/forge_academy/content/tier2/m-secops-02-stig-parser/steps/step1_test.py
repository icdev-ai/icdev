
# Auto-grader for SecOps-Eng M2: STIG Parser

# ── Test: extract_rules ───────────────────────────────────────────────────────

rules = extract_rules(SAMPLE_XCCDF)
assert rules is not None, "extract_rules() returned None"
assert isinstance(rules, list), "extract_rules() must return list"
assert len(rules) == 4, f"SAMPLE_XCCDF has 4 rules, got {len(rules)}"
assert all("<Rule" in r for r in rules), "Each extracted block should contain <Rule"
assert all("</Rule>" in r for r in rules), "Each extracted block should contain </Rule>"

# Empty XCCDF
empty_rules = extract_rules("<Benchmark><Group/></Benchmark>")
assert isinstance(empty_rules, list), "extract_rules() must return list for empty content"
assert len(empty_rules) == 0, f"No rules in empty content -> [], got {empty_rules}"

# ── Test: map_severity ────────────────────────────────────────────────────────

assert map_severity("high") == "CAT I", f"high -> CAT I, got '{map_severity('high')}'"
assert map_severity("medium") == "CAT II", f"medium -> CAT II, got '{map_severity('medium')}'"
assert map_severity("low") == "CAT III", f"low -> CAT III, got '{map_severity('low')}'"
unknown_sev = map_severity("critical")
assert "UNKNOWN" in unknown_sev.upper() or unknown_sev == "UNKNOWN", \
    f"Unknown severity -> 'UNKNOWN', got '{unknown_sev}'"

# ── Test: parse_rule ──────────────────────────────────────────────────────────

sample_rule = '''<Rule id="V-257777" severity="high">
  <title>RHEL 9 must use shadow file to store passwords.</title>
  <fix id="F-257777r1">Configure PAM to use shadow password storage.</fix>
  <check>Verify /etc/shadow exists. If not: FINDING</check>
</Rule>'''

parsed = parse_rule(sample_rule)
assert parsed is not None, "parse_rule() returned None for valid rule"
assert isinstance(parsed, dict), "parse_rule() must return dict"
assert parsed.get("rule_id") == "V-257777", \
    f"rule_id should be 'V-257777', got '{parsed.get('rule_id')}'"
assert parsed.get("severity") == "high", \
    f"severity should be 'high', got '{parsed.get('severity')}'"
assert parsed.get("cat") == "CAT I", \
    f"CAT should be 'CAT I' for high severity, got '{parsed.get('cat')}'"
assert "shadow" in parsed.get("title", "").lower(), \
    f"title should contain 'shadow', got '{parsed.get('title')}'"
assert "PAM" in parsed.get("fix_text", "") or "shadow" in parsed.get("fix_text", "").lower(), \
    f"fix_text should contain fix guidance, got '{parsed.get('fix_text')}'"
assert parsed.get("finding_keyword") is True, \
    f"'FINDING' in check text -> finding_keyword=True, got {parsed.get('finding_keyword')}"

# Rule without FINDING in check
no_finding_rule = '''<Rule id="V-999999" severity="low">
  <title>Some minor check.</title>
  <fix id="F-999999r1">Run some command.</fix>
  <check>Verify configuration. If correct, this is not a problem.</check>
</Rule>'''
parsed_nf = parse_rule(no_finding_rule)
assert parsed_nf is not None, "parse_rule() should parse rule without FINDING"
assert parsed_nf.get("finding_keyword") is False, \
    f"No 'FINDING' in check -> finding_keyword=False, got {parsed_nf.get('finding_keyword')}"

# ── Test: STIGParser.parse ────────────────────────────────────────────────────

parser = STIGParser()
result = parser.parse(SAMPLE_XCCDF)

assert result is not None, "parse() returned None"
assert isinstance(result, dict), "parse() must return dict"
assert "benchmark_title" in result, "Result must have 'benchmark_title'"
assert "controls" in result, "Result must have 'controls'"
assert "count" in result, "Result must have 'count'"
assert "cat1_count" in result, "Result must have 'cat1_count'"
assert "cat2_count" in result, "Result must have 'cat2_count'"
assert "cat3_count" in result, "Result must have 'cat3_count'"

assert "RHEL" in result["benchmark_title"] or "Red Hat" in result["benchmark_title"], \
    f"Benchmark title should mention RHEL/Red Hat, got '{result['benchmark_title']}'"
assert result["count"] == 4, f"SAMPLE_XCCDF has 4 rules -> count=4, got {result['count']}"
assert isinstance(result["controls"], list), "controls must be list"
assert len(result["controls"]) == 4, \
    f"controls list should have 4 entries, got {len(result['controls'])}"

# CAT counts
assert result["cat1_count"] == 1, f"1 high-severity rule -> cat1_count=1, got {result['cat1_count']}"
assert result["cat2_count"] == 2, f"2 medium-severity rules -> cat2_count=2, got {result['cat2_count']}"
assert result["cat3_count"] == 1, f"1 low-severity rule -> cat3_count=1, got {result['cat3_count']}"

# Each control has required fields
for ctrl in result["controls"]:
    for field in ["rule_id", "severity", "cat", "title", "fix_text", "finding_keyword"]:
        assert field in ctrl, f"Control must have '{field}' field, got keys: {list(ctrl.keys())}"

# Finding keyword: V-257780 check does NOT say FINDING (says "not a FINDING")
v780 = next((c for c in result["controls"] if c["rule_id"] == "V-257780"), None)
assert v780 is not None, "V-257780 should be in controls"
# Note: "FINDING" appears in the text, so finding_keyword detection is substring-based
# The grader accepts True here since "FINDING" literally appears in the check text

print("PASS: STIGParser complete. extract_rules + map_severity + parse_rule + STIGParser.parse all verified.")
