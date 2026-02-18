# CUI // SP-CTI
Feature: A2A Task Lifecycle
  As an agent orchestrator
  I want to verify the A2A task lifecycle works correctly
  So that agents can collaborate on tasks

  Scenario: Task submission and completion
    Given agent "orchestrator" is running
    And agent "builder" is registered
    When the orchestrator submits a task to builder
    Then the task should be created with status "pending"
    And builder should receive the task
    When builder completes the task
    Then the task status should be "completed"
    And the result should be recorded in a2a_tasks table

  Scenario: Task timeout handling
    Given a task with timeout of 5 seconds
    When the target agent does not respond
    Then the task should be marked as "timed_out"
    And an alert should be generated

  Scenario: Audit trail for A2A tasks
    Given a completed A2A task
    Then the audit trail should contain the task submission event
    And the audit trail should contain the task completion event

  Scenario: Task cancellation
    Given agent "orchestrator" is running
    And a task is in "pending" status
    When the orchestrator cancels the task
    Then the task status should be "cancelled"
    And the cancellation should be recorded in audit trail

  Scenario: Task retry on transient failure
    Given agent "builder" is running
    And a task fails with a transient error
    When the retry policy allows retries
    Then the task should be retried up to the configured maximum
    And each retry attempt should be logged

  Scenario: Multi-agent task routing
    Given agents "architect", "builder", and "compliance" are running
    When the orchestrator dispatches tasks to each agent
    Then each agent should receive only its assigned tasks
    And no task should be delivered to the wrong agent

  Scenario: Task result persistence
    Given a completed A2A task with result data
    Then the result should be stored in the database
    And the result should be retrievable by task ID
    And the result should include execution duration
