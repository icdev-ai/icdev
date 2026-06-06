# CUI // SP-CTI
Feature: Centralized API Blueprint Registration
  As an ICDEV™ dashboard maintainer
  I want all API blueprints registered through a single function in api/__init__.py
  So that app.py stays thin and blueprints are mounted consistently under /api/v1/

  Background:
    Given the ICDEV™ dashboard application is initialized for testing

  Scenario: register_api_blueprints function exists and is callable
    When I import register_api_blueprints from tools.dashboard.api
    Then the function should be callable

  Scenario: All core blueprints are registered after calling register_api_blueprints
    When I call register_api_blueprints on a test Flask app
    Then the app should have a route matching "/api/v1/projects"
    And the app should have a route matching "/api/v1/kanban"
    And the app should have a route matching "/api/v1/agents"
    And the app should have a route matching "/api/v1/compliance"

  Scenario: Legacy /api/* alias routes still work after registration
    When I call register_api_blueprints on a test Flask app
    Then a GET request to "/api/projects" should return HTTP 200 or 404 not 500
    And a GET request to "/api/kanban" should return HTTP 200 or 404 not 500

  Scenario: app.py create_app delegates blueprint registration
    When I inspect the create_app function in tools.dashboard.app
    Then it should call register_api_blueprints
    And it should not contain inline register_blueprint calls for the core API set

  Scenario: SRE blueprint is registered through the centralized function
    When I call register_api_blueprints on a test Flask app
    Then the app should have registered the sre_api blueprint or logged a warning

  Scenario: Blueprints are mounted with /api/v1/ prefix
    When I call register_api_blueprints on a test Flask app
    Then the registered blueprints should include routes prefixed with "/api/v1/"
