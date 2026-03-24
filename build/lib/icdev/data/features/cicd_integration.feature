# [TEMPLATE: CUI // SP-CTI]
Feature: ICDEV CI/CD Integration
  As a DevOps engineer
  I want automated CI/CD pipeline integration
  So that ICDEV workflows trigger from GitHub and GitLab events

  Scenario: GitHub webhook triggers SDLC workflow
    Given the webhook server is running
    When a GitHub issue is created with "/icdev_sdlc" in the body
    Then the full SDLC pipeline should be triggered

  Scenario: GitLab webhook triggers build workflow
    Given the webhook server is running
    When a GitLab issue has tag "{{icdev: build}}"
    Then the build workflow should be triggered

  Scenario: Poll trigger detects new issues
    Given the poll trigger is configured
    When a new issue is created with ICDEV workflow command
    Then the poll trigger should detect it within 20 seconds

  Scenario: SDLC pipeline runs all phases
    Given a valid issue number
    When I run the full SDLC pipeline
    Then it should execute plan phase
    And it should execute build phase
    And it should execute test phase
    And it should execute review phase

  Scenario: Pipeline generates IaC artifacts
    Given a project with infrastructure requirements
    When I run the infrastructure generators
    Then Terraform files should be generated
    And Kubernetes manifests should be generated
    And the pipeline YAML should be generated

  Scenario: Bot loop prevention
    Given a webhook event from an ICDEV bot comment
    When the webhook processes the event
    Then the event should be ignored
    And no workflow should be triggered
