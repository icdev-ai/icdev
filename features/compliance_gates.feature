# CUI // SP-CTI
Feature: ICDEV Compliance Gates
  As a compliance officer (ISSO)
  I want security gates enforced in the SDLC pipeline
  So that all deliverables meet NIST 800-53 and CUI requirements

  Scenario: CUI markings are present on all Python files
    Given a project with Python source files
    When I check for CUI markings
    Then every Python file should contain "CUI // SP-CTI"

  Scenario: SAST scan blocks on critical findings
    Given a project directory with Python source files
    When I run the SAST security scan
    Then the result should report 0 critical findings
    And the result should report 0 high findings

  Scenario: Secret detection blocks on leaked secrets
    Given a project directory with source files
    When I run the secret detector
    Then the result should report 0 secrets detected

  Scenario: Dependency audit blocks on critical vulnerabilities
    Given a project with a requirements file
    When I run the dependency auditor
    Then the result should report 0 critical vulnerabilities
    And the result should report 0 high vulnerabilities

  Scenario: SBOM generation succeeds
    Given a project directory with dependencies
    When I generate the SBOM
    Then the SBOM should be created successfully
    And the SBOM should list all components

  Scenario: NIST 800-53 control mapping
    Given a project with ID "proj-test"
    When I map activity "code.commit" to NIST controls
    Then the mapping should include at least one control
    And the crosswalk should cascade to mapped frameworks

  Scenario: Multi-regime gate evaluation
    Given a project with ID "proj-test"
    And the project has applicable compliance frameworks
    When I run the multi-regime gate
    Then all applicable frameworks should be assessed
    And the gate result should be reported
