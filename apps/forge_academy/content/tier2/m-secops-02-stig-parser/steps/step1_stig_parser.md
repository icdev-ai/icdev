# STIG Parser — Extract Controls from XCCDF Benchmarks

STIG benchmarks are distributed as XCCDF XML files — machine-readable but dense. Before you can scan for compliance, you need to parse these benchmarks into structured control objects. In this mission you'll build a STIG parser that extracts control metadata from XCCDF content.

## What You'll Build

A `STIGParser` that processes XCCDF benchmark content and extracts control metadata:

```python
parser = STIGParser()
result = parser.parse(xccdf_content)
# → {"controls": [...], "count": N, "benchmark_title": "..."}
```

## XCCDF Structure (simplified)

Real XCCDF files are complex XML. For this mission, we use a simplified tagged format:

```
<Rule id="V-220706" severity="medium">
  <title>The system must enforce MFA for all privileged access.</title>
  <fix id="F-220706r1">
    Configure Duo Security MFA for all accounts with administrator roles.
  </fix>
  <check>
    Verify MFA is configured for all admin accounts.
    If not configured: FINDING
  </check>
</Rule>
```

## Parsing Requirements

For each Rule block:
- Extract `id` (V-NNNNNN format)
- Extract `severity` → map to CAT: "high"→"CAT I", "medium"→"CAT II", "low"→"CAT III"
- Extract `<title>` text content
- Extract `<fix>` text content (remediation guidance)
- Determine `finding_keyword`: True if "FINDING" appears in `<check>` text

## Success Criteria

- `extract_rules()` finds all `<Rule>` blocks using regex
- `parse_rule()` extracts id, severity, title, fix text, finding_keyword from a single rule block
- `map_severity()` correctly maps high/medium/low → CAT I/II/III
- `STIGParser.parse()` returns structured result with all controls
