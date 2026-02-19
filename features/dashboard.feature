# CUI // SP-CTI
Feature: ICDEV Dashboard
  As a user (PM, developer, ISSO, or CO)
  I want a web dashboard for project management
  So that I can view project status, compliance, and security at a glance

  Scenario: Dashboard home page loads
    Given the dashboard Flask app is configured
    When I request the home page "/"
    Then the response status should be 200
    And the page should contain navigation elements

  Scenario: Projects list page loads
    Given the dashboard Flask app is configured
    When I request "/projects"
    Then the response status should be 200

  Scenario: Agents page loads
    Given the dashboard Flask app is configured
    When I request "/agents"
    Then the response status should be 200

  Scenario: Monitoring page loads
    Given the dashboard Flask app is configured
    When I request "/monitoring"
    Then the response status should be 200

  Scenario: Events page loads with SSE support
    Given the dashboard Flask app is configured
    When I request "/events"
    Then the response status should be 200

  Scenario: Query page loads for NLQ
    Given the dashboard Flask app is configured
    When I request "/query"
    Then the response status should be 200

  Scenario: Wizard page loads
    Given the dashboard Flask app is configured
    When I request "/wizard"
    Then the response status should be 200

  Scenario: Diagrams page loads
    Given the dashboard Flask app is configured
    When I request "/diagrams"
    Then the response status should be 200

  Scenario: Batch operations page loads
    Given the dashboard Flask app is configured
    When I request "/batch"
    Then the response status should be 200

  Scenario: CI/CD page loads
    Given the dashboard Flask app is configured
    When I request "/cicd"
    Then the response status should be 200

  Scenario: Quick paths page loads
    Given the dashboard Flask app is configured
    When I request "/quick-paths"
    Then the response status should be 200

  Scenario: Role-based view filtering
    Given the dashboard Flask app is configured
    When I request "/projects?role=pm"
    Then the response status should be 200
    And the page should reflect PM role context

  Scenario: API events endpoint returns JSON
    Given the dashboard Flask app is configured
    When I request "/api/events"
    Then the response should be valid JSON
