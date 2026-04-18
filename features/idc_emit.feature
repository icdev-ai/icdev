# CUI // SP-CTI
Feature: IDC IaC Emit Page
  As a DevSecOps engineer
  I want a web UI to generate IaC from an infrastructure design
  So that I can emit Terraform, Ansible, Pulumi, or Helm output without leaving the dashboard

  # NIST 800-53: SA-11 (developer security testing), CM-3 (configuration change control)

  Scenario: Emit page loads with CUI banner
    Given the dashboard Flask app is configured
    When I request "/infra/emit"
    Then the response status should be 200
    And the page should contain "CUI"

  Scenario: Emit page contains IaC target field
    Given the dashboard Flask app is configured
    When I request "/infra/emit"
    Then the response status should be 200
    And the page should contain "target"

  Scenario: Emit page contains CSP field
    Given the dashboard Flask app is configured
    When I request "/infra/emit"
    Then the response status should be 200
    And the page should contain "csp"

  Scenario: Emit page contains project field
    Given the dashboard Flask app is configured
    When I request "/infra/emit"
    Then the response status should be 200
    And the page should contain "project"

  Scenario: POST /infra/emit/run with missing project returns 400
    Given the dashboard Flask app is configured
    When I POST to "/infra/emit/run" with JSON '{"target":"terraform","csp":"aws"}'
    Then the response status should be 400

  Scenario: POST /infra/emit/run with missing target returns 400
    Given the dashboard Flask app is configured
    When I POST to "/infra/emit/run" with JSON '{"project":"{}","csp":"aws"}'
    Then the response status should be 400

  Scenario: POST /infra/emit/run with missing CSP returns 400
    Given the dashboard Flask app is configured
    When I POST to "/infra/emit/run" with JSON '{"project":"{}","target":"terraform"}'
    Then the response status should be 400

  Scenario: POST /infra/emit/run with valid terraform/aws payload returns files JSON
    Given the dashboard Flask app is configured
    When I POST to "/infra/emit/run" with JSON '{"project":"{\"nodes\":[{\"id\":\"v1\",\"type\":\"aws-vpc\",\"label\":\"vpc\",\"metadata\":{\"cidr_block\":\"10.0.0.0/16\"}}],\"edges\":[]}","target":"terraform","csp":"aws"}'
    Then the response status should be 200
    And the response should be valid JSON
    And the response JSON should contain key "files"
    And the response JSON should contain key "target"
    And the response JSON should contain key "csp"

  Scenario: POST /infra/emit/run with unsupported CSP/target combo returns 422
    Given the dashboard Flask app is configured
    When I POST to "/infra/emit/run" with JSON '{"project":"{}","target":"pulumi","csp":"gcp"}'
    Then the response status should be 422
