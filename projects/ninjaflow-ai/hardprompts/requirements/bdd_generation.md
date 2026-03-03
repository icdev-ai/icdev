# BDD Acceptance Criteria Generation Prompt

> CUI // SP-CTI

Generate BDD (Behavior-Driven Development) acceptance criteria for the given requirement or SAFe item.

## Input
- Item: {{item_json}} (requirement, feature, or story)
- Context: {{session_context}}

## Rules

1. **Format**: Use Gherkin syntax (Given/When/Then)
2. **Coverage**: Generate 2-5 scenarios per item:
   - Happy path (primary success scenario)
   - Error/edge case (invalid input, timeout, unauthorized)
   - Boundary condition (max/min values, empty data)
   - Security scenario (if applicable — unauthorized access, audit logging)
3. **Measurability**: Every Then clause must be objectively verifiable
4. **Avoid Ambiguity**: No subjective language in acceptance criteria
5. **Include Security Scenarios**: For items touching auth, data, or APIs

## Output Format
```gherkin
Feature: {{feature_name}}
  As a {{role}}
  I want to {{action}}
  So that {{benefit}}

  Scenario: {{scenario_name}}
    Given {{precondition}}
    When {{action}}
    Then {{expected_result}}
    And {{additional_verification}}
```
