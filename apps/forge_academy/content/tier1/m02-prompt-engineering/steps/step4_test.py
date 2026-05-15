
# Auto-grader for M02 Step 4: Structured Output Enforcement

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

sample = """
AWARD NOTICE: The Department of Defense has awarded Contract W911QY-26-C-0042
to ACME Corp (CAGE: 1A2B3) under NAICS 541519 (Information Technology Services)
for a total obligated amount of $2,500,000. Period of performance: 12 months.
"""

try:
    result = extract_contract_data(sample)
    if result:
        print(f"Name: {result.get('name')}")
        print(f"NAICS: {result.get('naics')}")
        print(f"Value: {result.get('value_usd')}")
        print(f"Status: {result.get('status')}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

assert result is not None, "extract_contract_data() returned None — implement the function"
assert isinstance(result, dict), f"Must return a dict, got {type(result)}"

assert "name" in result, "Output must include 'name' field"
assert "naics" in result, "Output must include 'naics' field — add the field to your JSON schema in the prompt"
assert "value_usd" in result, "Output must include 'value_usd' field"
assert "status" in result, "Output must include 'status' field"

assert result.get("naics") is not None, \
    "NAICS field is null — add an example to your prompt showing NAICS extraction"

name = result.get("name", "")
assert "acme" in name.lower() or "corp" in name.lower(), \
    f"Company name not extracted correctly: {name}"

assert len(output) > 10, "Print the extracted data"

print(f"PASS: Structured output enforced. All 4 fields extracted. NAICS: {result.get('naics')}")
