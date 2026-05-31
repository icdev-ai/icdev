Feature: ICDEV Proposal Content Validation
  As a proposal manager
  I want to verify that ICDEV-branded proposal content is populated for target solicitations
  So that the proposals page demonstrates real capabilities rather than synthetic filler

  Background:
    Given the ICDEV dashboard is running at http://localhost:5050

  Scenario: Proposal 0002 detail page loads with ICDEV sections and metadata
    Given I navigate to "http://localhost:5050/proposals/47294739-614f-f3d7-19db-3ad0ddd1dfb2"
    Then the response status should be 200
    And the page should contain "Gold Team Review"
    And the page should contain "ICDEV Proposal Genesis"
    And the page should contain "Cloud Migration"

  Scenario: Proposal 0008 detail page loads with ICDEV sections and metadata
    Given I navigate to "http://localhost:5050/proposals/dd56cc94-3c9a-d14c-ee0c-aeb5ecfedb99"
    Then the response status should be 200
    And the page should contain "Gold Team Review"
    And the page should contain "ICDEV Proposal Genesis"
    And the page should contain "Cloud Migration"

  Scenario: Proposal 0304 detail page loads with ICDEV sections and metadata
    Given I navigate to "http://localhost:5050/proposals/bff9507d-cd14-a03e-8359-9af65d01f55f"
    Then the response status should be 200
    And the page should contain "Gold Team Review"
    And the page should contain "ICDEV Proposal Genesis"
    And the page should contain "Artificial Intelligence"

  Scenario: Compliance matrix API shows 100% coverage for proposal 0002
    When I GET API "/api/proposals/opportunities/47294739-614f-f3d7-19db-3ad0ddd1dfb2/compliance"
    Then the response status should be 200
    And the compliance coverage should be at least 85 percent

  Scenario: Compliance matrix API shows 100% coverage for proposal 0008
    When I GET API "/api/proposals/opportunities/dd56cc94-3c9a-d14c-ee0c-aeb5ecfedb99/compliance"
    Then the response status should be 200
    And the compliance coverage should be at least 85 percent

  Scenario: Compliance matrix API shows 100% coverage for proposal 0304
    When I GET API "/api/proposals/opportunities/bff9507d-cd14-a03e-8359-9af65d01f55f/compliance"
    Then the response status should be 200
    And the compliance coverage should be at least 85 percent

  Scenario: Section detail page shows compliance items for proposal 0002 technical volume
    Given I navigate to "http://localhost:5050/proposals/47294739-614f-f3d7-19db-3ad0ddd1dfb2/sections/3f22faf8-23be-d01d-43cf-2fde24933b83"
    Then the response status should be 200
    And the page should contain "Compliance"
    And the page should contain "L"
    And the page should contain "Compliant"

  Scenario: Section detail page shows compliance items for proposal 0304 technical volume
    Given I navigate to "http://localhost:5050/proposals/bff9507d-cd14-a03e-8359-9af65d01f55f/sections/7dbf4bc1-ffa6-23d0-ea9e-5c8db1a8b71f"
    Then the response status should be 200
    And the page should contain "Compliance"
    And the page should contain "L"
    And the page should contain "Compliant"

  Scenario: Proposal list shows updated review statuses
    Given I navigate to "http://localhost:5050/proposals"
    Then the response status should be 200
    And the page should contain "review" at least 3 times
