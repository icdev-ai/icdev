# [TEMPLATE: CUI // SP-CTI]
Feature: ICDEV Testing Pipeline
  As a developer
  I want a comprehensive testing pipeline
  So that code quality is enforced at every stage

  Scenario: Syntax validation catches compile errors
    Given a set of Python source files
    When I run py_compile on each file
    Then all files should compile without errors

  Scenario: Ruff linter enforces code quality
    Given the project root directory
    When I run ruff check
    Then there should be 0 remaining violations

  Scenario: Unit tests pass with pytest
    Given the tests/ directory with test files
    When I run pytest with verbose output
    Then all tests should pass
    And there should be 0 failures
    And there should be 0 errors

  Scenario: Test orchestrator runs full pipeline
    Given a project directory with tests
    When I run the test orchestrator
    Then it should execute health check step
    And it should execute unit test step
    And it should execute security gate step
    And it should report overall pass/fail

  Scenario: Acceptance validator checks plan criteria
    Given a plan file with acceptance criteria
    And test results from a previous run
    When I run the acceptance validator
    Then each criterion should be mapped to evidence
    And the overall gate should pass if all criteria met

  Scenario: E2E test runner discovers test specs
    Given E2E test specs in .claude/commands/e2e/
    When I run the E2E runner with discover flag
    Then it should list available test specifications
