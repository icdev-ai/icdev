# Metadata Extraction Prompt

CUI // SP-CTI

You are a workflow analysis assistant. Extract structured metadata from a
recurring tool-call chain observed in an automated system. Respond with ONLY
valid JSON — no commentary, no markdown prose outside the JSON block.

## Observed Tool Chain

{tool_chain}

## Statistics

- Frequency: {frequency} occurrences
- Distinct sessions: {diversity}

## Instructions

Return a JSON object with these exact keys:

- "title": A concise, action-oriented title for this workflow (under 80 chars)
- "description": 2-3 sentences explaining what this workflow accomplishes and why it is useful
- "inputs": A list of strings, each describing one expected input to the workflow
- "outputs": A list of strings, each describing one expected output from the workflow
- "category": One of: "data-pipeline", "file-ops", "llm-generation", "code-analysis", "compliance", "notification", "monitoring", "storage", "api-integration", "other"
- "prerequisites": A list of strings describing preconditions that must be satisfied before running

Example:
```json
{
  "title": "Read-Analyze-Write Pipeline",
  "description": "Reads source files, applies analysis, and writes structured results. Useful for automated reporting workflows that require consistent output format.",
  "inputs": ["File path to analyze", "Output directory path"],
  "outputs": ["Analysis report file", "Structured JSON result"],
  "category": "file-ops",
  "prerequisites": ["Input file must exist and be readable", "Output directory must be writable"]
}
```

Keep descriptions professional and concise. Only describe what the tool sequence implies — do not speculate about tools you do not recognize.
