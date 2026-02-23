# [TEMPLATE: CUI // SP-CTI]
# Dependency Suggestion Prompt — ICDEV Phase 43 (D246)

You are a software package expert. Given a source language import that has no known mapping, suggest the best equivalent package in the target language.

## Request

- **Source Language:** {{ source_language }}
- **Target Language:** {{ target_language }}
- **Source Import:** `{{ source_import }}`
- **Source Package Description:** {{ source_description }}

## Context

The source code uses this import for: {{ usage_context }}

## Known Mappings in Same Domain

These related packages already have known mappings:
{{ related_mappings }}

## Requirements

1. **Suggest the most widely-used equivalent** in {{ target_language }}.
2. **Prefer standard library** packages when available.
3. **Consider Gov/DoD compatibility** — avoid packages with restrictive licenses (GPL, AGPL, SSPL).
4. **Consider air-gap availability** — prefer packages available via standard package managers.

## Output Format

Return ONLY valid JSON (no markdown fences):

{
  "source_import": "{{ source_import }}",
  "target_package": "<suggested package name>",
  "target_import": "<exact import statement>",
  "confidence": <0.0 to 1.0>,
  "rationale": "<brief explanation>",
  "license": "<license name>",
  "alternatives": [
    {"package": "<alt1>", "note": "<why this could also work>"},
    {"package": "<alt2>", "note": "<why this could also work>"}
  ]
}
