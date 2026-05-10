# [TEMPLATE: CUI // SP-CTI]
Feature: ICDEV Requirements Intake (RICOAS)
  As a requirements analyst
  I want AI-driven conversational intake
  So that customer requirements are structured, validated, and decomposed

  Scenario: Create a new intake session
    Given the ICDEV database is initialized
    When I create an intake session for customer "Jane Smith" at org "DoD PEO" with IL "IL5"
    Then the session should be created with a unique ID
    And the session status should be "gathering"

  Scenario: Process a conversational turn
    Given an active intake session
    When I send message "We need a mission planning tool with map integration"
    Then the response should contain follow-up questions
    And requirements should be extracted from the message

  Scenario: Upload and extract from a document
    Given an active intake session
    When I upload a SOW document
    And I extract requirements from the document
    Then shall/must/should statements should be identified
    And extracted requirements should be linked to the session

  Scenario: Gap detection identifies missing requirements
    Given an intake session with extracted requirements
    When I run gap detection with security and compliance checks
    Then gaps should be identified with severity levels
    And NIST control gaps should be flagged

  Scenario: Readiness scoring evaluates completeness
    Given an intake session with requirements
    When I calculate the readiness score
    Then the score should cover 5 dimensions
    And the overall score should be between 0.0 and 1.0

  Scenario: SAFe decomposition creates work items
    Given an intake session with readiness score above 0.7
    When I decompose requirements at story level with BDD generation
    Then epics, capabilities, features, and stories should be created
    And each story should have acceptance criteria
