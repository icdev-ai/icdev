---
mode: agent
description: "Build code using true TDD (RED → GREEN → REFACTOR) with automatic test generation and compliance tracking"
tools:
  - terminal
  - file_search
---

# icdev-build

Implements the full TDD cycle for a feature:
1. **RED** — Generate failing tests from feature description
2. **GREEN** — Generate minimal code to make tests pass
3. **REFACTOR** — Clean up code while keeping tests green
4. All artifacts get CUI markings and NIST 800-53 control mappings

## Steps

1. **Load Project Context**
```bash
!cat args/project_defaults.yaml
```

2. **RED Phase — Write Failing Tests**
Run the equivalent CLI command for write_tests:
- Feature description: `$ARGUMENTS`
- Generates Gherkin BDD feature file (.feature) with scenarios

3. **GREEN Phase — Generate Implementation Code**
Run the equivalent CLI command for generate_code:
- Analyzes failing test files to determine required code
- Generates minimal implementation to make tests pass

4. **REFACTOR Phase — Clean Up**
Run the equivalent CLI command for format:
- Run black + isort (Python) or prettier (JS)
Run the equivalent CLI command for lint:

5. **Compliance Mapping**
Run the equivalent CLI command for control_map:
- Map `code.commit` activity to NIST controls (SA-11, CM-3)

6. **Output Summary**
Display:
- Tests written (count, types)
- Code generated (files, lines)

7. **Output Summary**
Display:
- Tests written (count, types)
- Code generated (files, lines)

## Example
```
#prompt:icdev-build "User authentication with JWT tokens, login/logout endpoints, password hashing with bcrypt"
```