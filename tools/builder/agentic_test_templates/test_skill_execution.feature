# CUI // SP-CTI
Feature: Agent Skill Execution
  Test that each agent's registered skills execute correctly

  Scenario Outline: Execute agent skill
    Given agent "<agent>" is running
    And skill "<skill>" is registered
    When I invoke skill "<skill>" with valid parameters
    Then the skill should return a successful result
    And the execution should be logged

    Examples:
      | agent        | skill          |
      | orchestrator | task-dispatch  |
      | architect    | system-design  |
      | builder      | code-generate  |
      | builder      | tdd-cycle      |
      | compliance   | ssp-generate   |
      | compliance   | stig-check     |
      | security     | sast-scan      |
      | security     | secret-detect  |
      | knowledge    | pattern-detect |
      | monitor      | health-check   |
      | infra        | terraform-plan |
      | mbse         | import-xmi     |

  Scenario: Skill execution with invalid parameters
    Given agent "builder" is running
    And skill "code-generate" is registered
    When I invoke skill "code-generate" with invalid parameters
    Then the skill should return an error result
    And the error should include a descriptive message
    And the execution should be logged with error status

  Scenario: Skill execution timeout
    Given agent "builder" is running
    And skill "code-generate" is registered
    When I invoke skill "code-generate" and it exceeds the timeout
    Then the skill execution should be terminated
    And the timeout should be recorded in the audit trail
