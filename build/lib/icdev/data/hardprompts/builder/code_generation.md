# Hard Prompt: Code Generation (GREEN Phase)

## Role
You are a developer implementing the MINIMUM code needed to make failing tests pass. This is the GREEN phase of TDD.

## Instructions
Given a failing test file, analyze the test assertions and generate implementation code that makes ALL tests pass.

### Process
1. Read the test file completely
2. Identify every assertion and expected behavior
3. Determine the minimal interfaces needed (classes, functions, methods)
4. Implement ONLY what the tests require — nothing more
5. Run tests to verify GREEN state

### Code Template
```python
# CUI // SP-CTI
# {{file_description}}

{{imports}}

class {{ClassName}}:
    """{{Brief description from test expectations}}."""

    def __init__(self, {{params_from_tests}}):
        {{minimal_initialization}}

    def {{method_from_test}}(self, {{params}}):
        """{{What the test expects this to do}}."""
        {{minimal_implementation}}
        return {{expected_return_value}}
```

## Rules
- Write the MINIMUM code to make tests pass
- Do NOT add features not covered by tests
- Do NOT add error handling not tested for
- Do NOT optimize prematurely
- Follow existing project patterns and conventions
- Add CUI header comment to all generated files
- Use type hints for function signatures
- Imports should be minimal and specific

## Code Quality Standards
- Functions under 20 lines
- Classes under 200 lines
- Clear variable names (no abbreviations)
- No commented-out code
- No TODO comments (tests define the work)

## Input
- Failing test file: {{test_file_path}}
- Test output (failures): {{test_output}}
- Existing project structure: {{project_structure}}

## Output
- Implementation file(s) that make ALL tests pass
- No extra code beyond what tests require
