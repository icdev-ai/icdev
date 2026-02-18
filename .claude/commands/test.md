# ICDEV Application Validation Test Suite

Execute comprehensive validation tests for ICDEV-built projects, returning results in standardized JSON format for automated processing.

## Purpose

Proactively identify and fix issues in the application before they impact users or deployment gates. This runs:
- Python syntax validation (py_compile)
- Code quality checks (Ruff linter)
- Backend unit tests (pytest)
- BDD scenario tests (behave)
- Security scan validation (bandit)

## Variables

TEST_COMMAND_TIMEOUT: 5 minutes
PROJECT_DIR: The project directory under test (auto-detected from cwd)

## Instructions

- Execute each test in the sequence provided below
- Capture the result (passed/failed) and any error messages
- IMPORTANT: Return ONLY the JSON array with test results
  - Do not include any additional text, explanations, or markdown formatting
  - We'll immediately run JSON.parse() on the output, so make sure it's valid JSON
- If a test passes, omit the error field
- If a test fails, include the error message in the error field
- Execute all tests even if some fail (except: if a test fails, stop and return results thus far)
- Error Handling:
  - If a command returns non-zero exit code, mark as failed and stop processing tests
  - Capture stderr output for error field
  - Timeout commands after `TEST_COMMAND_TIMEOUT`
- Always run `pwd` before each test to ensure you're in the correct directory

## Test Execution Sequence

### Syntax & Quality

1. **Python Syntax Check**
   - Command: `python -m py_compile src/*.py` (adjust path to project's source)
   - test_name: "python_syntax_check"
   - test_purpose: "Validates Python syntax by compiling source files to bytecode, catching syntax errors like missing colons, invalid indentation, or malformed statements"

2. **Code Quality Check (Ruff)**
   - Command: `ruff check src/` (or `python -m ruff check src/` if installed via pip)
   - test_name: "code_quality_ruff"
   - test_purpose: "Validates Python code quality using Ruff — identifies unused imports, style violations, security issues, and potential bugs (replaces flake8+isort+black)"

### Backend Tests

3. **Unit Tests (pytest)**
   - Command: `python -m pytest tests/ -v --tb=short`
   - test_name: "unit_tests_pytest"
   - test_purpose: "Validates all backend functionality including business logic, API endpoints, data processing, and error handling"

4. **BDD Tests (behave)**
   - Command: `python -m behave features/ --format json --no-capture` (skip if no features/ dir)
   - test_name: "bdd_tests_behave"
   - test_purpose: "Validates business requirements through Gherkin scenarios, ensuring user stories are correctly implemented"

### Security

5. **SAST Security Scan (Bandit)**
   - Command: `python -m bandit -r src/ -f json --severity-level medium`
   - test_name: "security_sast_bandit"
   - test_purpose: "Static application security testing — identifies common vulnerabilities like SQL injection, XSS, hardcoded secrets, and insecure function calls"

6. **Secret Detection**
   - Command: `python tools/security/secret_detector.py --project-dir .`
   - test_name: "secret_detection"
   - test_purpose: "Scans all files for leaked secrets, API keys, passwords, and tokens using regex patterns and detect-secrets"

## Report

- IMPORTANT: Return results exclusively as a JSON array based on the `Output Structure` section below.
- Sort the JSON array with failed tests (passed: false) at the top
- Include all tests in the output, both passed and failed
- The execution_command field should contain the exact command that can be run to reproduce the test

### Output Structure

```json
[
  {
    "test_name": "string",
    "passed": boolean,
    "execution_command": "string",
    "test_purpose": "string",
    "error": "optional string"
  }
]
```

### Example Output

```json
[
  {
    "test_name": "code_quality_ruff",
    "passed": false,
    "execution_command": "ruff check src/",
    "test_purpose": "Validates Python code quality using Ruff",
    "error": "src/app.py:42:1: F401 `os` imported but unused"
  },
  {
    "test_name": "python_syntax_check",
    "passed": true,
    "execution_command": "python -m py_compile src/app.py",
    "test_purpose": "Validates Python syntax by compiling source files to bytecode"
  }
]
```
