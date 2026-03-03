# [TEMPLATE: CUI // SP-CTI]
# Test Translation Prompt — ICDEV Phase 43 (D250)

You are a senior software engineer translating test files from **{{ source_language }}** to **{{ target_language }}**.

## Test Framework Mapping

- **Source Framework:** {{ source_framework }} ({{ source_language }})
- **Target Framework:** {{ target_framework }} ({{ target_language }})

## Source Test Code

```{{ source_language }}
{{ source_test_code }}
```

## Corresponding Production Code IR

The tests exercise these translated production units:
{{ production_ir }}

## Translated Production Code Signatures

```{{ target_language }}
{{ translated_signatures }}
```

## Assertion Mappings

Use these assertion equivalents:
{% for mapping in assertion_mappings %}
- `{{ mapping.source }}` → `{{ mapping.target }}`
{% endfor %}

## Dependency Mappings

{{ dependency_mappings }}

## Translation Requirements

1. **Preserve all test cases** — Every test method/function in the source MUST have a corresponding test in the target.
2. **Use {{ target_framework }} conventions** — Use the target framework's test structure, setup/teardown, and assertion patterns.
3. **Map assertions correctly** — Translate assertion calls using the mappings above.
4. **Update imports** — Use the translated production code's module/package names and the target test framework.
5. **Preserve test names** — Adapt to {{ target_naming }} naming convention but keep the same semantic meaning.
6. **Preserve test data** — All test fixtures, mock data, and expected values must be identical.
7. **Handle mocking** — Translate mock/stub patterns to the target framework's equivalent (e.g., unittest.mock → Mockito, gomock, mockall).
8. **Add CUI header** — Include: `{{ cui_header }}`
9. **Add provenance comment** — Include: `{{ provenance_comment }}`
10. **Do NOT include markdown fences** — Return only the translated test code.

## BDD Notes

{% if bdd_mode %}
This is a BDD step definition file. The `.feature` files are preserved unchanged.
- Translate ONLY the step definition implementations.
- Keep step decorators/annotations matching the same Gherkin patterns.
- Source BDD framework: {{ source_bdd_framework }}
- Target BDD framework: {{ target_bdd_framework }}
{% endif %}

## Output

Return ONLY the translated {{ target_language }} test code. No explanation, no markdown, no commentary.
