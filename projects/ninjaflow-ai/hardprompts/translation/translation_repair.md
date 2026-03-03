# [TEMPLATE: CUI // SP-CTI]
# Translation Repair Prompt — ICDEV Phase 43 (D255)
# Compiler-feedback repair loop (adopted from Google ICSE 2025 + CoTran ECAI 2024)

You are a senior software engineer fixing a **{{ target_language }}** code translation that failed validation.

## Context

- **Unit Name:** {{ unit_name }}
- **Unit Kind:** {{ unit_kind }}
- **Source Language:** {{ source_language }}
- **Target Language:** {{ target_language }}
- **Repair Attempt:** {{ attempt_number }} of {{ max_attempts }}

## Original Source Code ({{ source_language }})

```{{ source_language }}
{{ source_code }}
```

## Current Translated Code ({{ target_language }}) — FAILING

```{{ target_language }}
{{ translated_code }}
```

## Compiler / Validation Errors

```
{{ error_output }}
```

## Validation Failures

{% for failure in validation_failures %}
- **{{ failure.check }}**: {{ failure.message }}
{% endfor %}

## Dependency Mappings Available

{{ dependency_mappings }}

## Type Mappings

{{ type_mappings }}

## Repair Instructions

1. **Fix ONLY the reported errors** — Do not rewrite the entire translation. Make targeted, minimal fixes.
2. **Address each error specifically** — Use the compiler error messages and line numbers to locate and fix issues.
3. **Preserve the existing structure** — Keep the overall translation intact; only modify what is broken.
4. **Ensure type correctness** — If errors are type-related, consult the type mappings above.
5. **Fix import statements** — If errors are import-related, consult the dependency mappings above.
6. **Maintain CUI header** — Do not remove the classification marking from the first line.
7. **Do NOT include markdown fences** — Return only the corrected {{ target_language }} code.

## Output

Return ONLY the corrected {{ target_language }} code. No explanation, no markdown, no commentary.
