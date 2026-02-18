# CUI // SP-CTI
Feature: Agent Card Discovery and Validation
  As a system administrator
  I want to verify all agent cards are valid
  So that A2A protocol discovery works correctly

  Scenario: All agents publish valid agent cards
    Given the application has {agent_count} configured agents
    When I query each agent's /.well-known/agent.json endpoint
    Then each response should contain a valid agent card
    And each card should have name, description, url, and skills
    And each card should specify mutual-tls authentication

  Scenario: Agent cards match configuration
    Given the agent configuration from args/agent_config.yaml
    When I compare agent cards to the configuration
    Then each agent's port should match the configuration
    And each agent's name should match the configuration

  Scenario: No duplicate agent ports
    Given the full list of agent ports
    Then all ports should be unique
    And no port should conflict with known system ports

  Scenario: Agent card schema compliance
    Given the A2A protocol specification
    When I validate each agent card against the schema
    Then each card should have a valid JSON structure
    And each card should include version information
    And each card should list supported protocols

  Scenario: Agent card endpoint accessibility
    Given all agents are deployed
    When I request /.well-known/agent.json from each agent
    Then each endpoint should respond within 5 seconds
    And each endpoint should return Content-Type application/json
    And no endpoint should return a 5xx error
