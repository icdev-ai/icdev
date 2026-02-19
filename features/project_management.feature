# CUI // SP-CTI
Feature: ICDEV Project Management
  As a developer or project manager
  I want to create and manage ICDEV projects
  So that I can track software development with compliance

  Scenario: Create a new project
    Given the ICDEV database is initialized
    When I create a project with name "test-app" and type "microservice"
    Then the project should be created successfully
    And the project should have a unique project ID
    And the audit trail should record the creation event

  Scenario: List all projects
    Given the ICDEV database is initialized
    And a project "test-app" exists
    When I list all projects
    Then the project list should include "test-app"

  Scenario: Get project status
    Given the ICDEV database is initialized
    And a project "test-app" exists
    When I request the status of project "test-app"
    Then the status should include compliance information
    And the status should include security scan results

  Scenario: Initialize ICDEV database
    Given a fresh ICDEV environment
    When I run the database initialization
    Then at least 120 tables should be created
    And the audit trail table should exist
    And the projects table should exist
