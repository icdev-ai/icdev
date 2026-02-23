# [TEMPLATE: CUI // SP-CTI]
Feature: ICDEV Security Scanning
  As a security engineer
  I want automated security scanning in the pipeline
  So that vulnerabilities are caught before deployment

  Scenario: SAST scan produces structured output
    Given a project directory at "."
    When I run the SAST runner with JSON output
    Then the output should contain severity counts
    And the output should contain scanned file count

  Scenario: Secret detection scans all file types
    Given a project directory at "."
    When I run the secret detector with JSON output
    Then the output should report files scanned
    And the output should report findings count

  Scenario: Dependency audit checks all packages
    Given a project directory at "."
    When I run the dependency auditor with JSON output
    Then the output should list audited packages
    And the output should report vulnerability counts

  Scenario: STIG compliance check
    Given a project with ID "proj-test"
    When I run the STIG checker
    Then the result should report CAT1 findings count
    And the result should report CAT2 findings count
    And the result should report CAT3 findings count
    And CAT1 findings should be 0 for gate pass

  Scenario: Container security scan
    Given a container image "test-image:latest"
    When I run the container scanner
    Then the result should report vulnerability counts by severity
