Feature: Proposals and GovCon Workflow
  As a proposal team member
  I want to manage proposals, opportunities, and contracts
  So that I can track capture progress end-to-end

  Background:
    Given the ICDEV dashboard is running at http://localhost:5050

  Scenario: Capture Manager views GovCon pipeline
    Given I navigate to "http://localhost:5050/govcon"
    Then the response status should be 200
    And the page should display an opportunities table or pipeline view

  Scenario: Proposal Manager views proposal list
    Given I navigate to "http://localhost:5050/proposals"
    Then the response status should be 200
    And the page should display proposal opportunity cards or a list

  Scenario: Reviewer accesses reviews dashboard
    Given I navigate to "http://localhost:5050/proposals/reviews-dashboard"
    Then the response status should be 200
    And the page should display review score cards with pass/fail statistics

  Scenario: Contract Officer views CPMP dashboard
    Given I navigate to "http://localhost:5050/cpmp"
    Then the response status should be 200
    And the page should display active contract cards

  Scenario: COR accesses COR portal
    Given I navigate to "http://localhost:5050/cpmp/cor"
    Then the response status should be 200
    And the page should display COR assignments or deliverable tracking

  Scenario: GovCon requirements page accessible
    Given I navigate to "http://localhost:5050/govcon/requirements"
    Then the response status should be 200

  Scenario: GovCon capabilities page accessible
    Given I navigate to "http://localhost:5050/govcon/capabilities"
    Then the response status should be 200
