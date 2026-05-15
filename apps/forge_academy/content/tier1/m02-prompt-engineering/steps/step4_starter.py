
from __future__ import annotations
"""
Step 4: Structured Output Enforcement
Goal: Build a prompt that reliably extracts structured JSON from unstructured text.
"""

import json
import re


def simulate_llm_extract(prompt: str, raw_text: str) -> str:
    """Mock LLM that extracts structured data. Quality depends on prompt clarity."""
    has_format = "json" in prompt.lower()
    has_schema = "{" in prompt and "}" in prompt
    has_example = "example" in prompt.lower() or '\"' in prompt

    if has_format and has_schema and has_example:
        # Well-structured prompt → clean JSON output
        return '{"name": "ACME Corp", "naics": "541519", "value_usd": 2500000, "status": "award"}'
    elif has_format and has_schema:
        return '{"name": "ACME Corp", "naics": null, "value_usd": 2500000, "status": "award"}'
    elif has_format:
        return '{"name": "ACME Corp"}'
    else:
        # Weak prompt → prose output that breaks JSON parsing
        return "The contract was awarded to ACME Corp under NAICS code 541519 for $2.5M."


def parse_llm_output(raw_output: str) -> dict | None:
    """Extract and parse JSON from LLM response (handles prose wrapper)."""
    # Try direct parse first
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        pass
    # Try to extract JSON block from prose
    match = re.search(r'\{[^{}]+\}', raw_output, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def extract_contract_data(raw_sam_text: str) -> dict | None:
    """TODO: Build a structured-output prompt that extracts contract fields.

    The prompt must cause simulate_llm_extract to return valid JSON with:
    - name (string): company name
    - naics (string): NAICS code
    - value_usd (number): contract value in USD
    - status (string): award|solicitation|modification

    Then parse the output using parse_llm_output() and return the parsed dict.
    Print the raw LLM output and the parsed result.
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    sample = """
    AWARD NOTICE: The Department of Defense has awarded Contract W911QY-26-C-0042
    to ACME Corp (CAGE: 1A2B3) under NAICS 541519 (Information Technology Services)
    for a total obligated amount of $2,500,000. Period of performance: 12 months.
    """
    result = extract_contract_data(sample)
    if result:
        print("Extracted:", json.dumps(result, indent=2))
