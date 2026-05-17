Feature: Adversarial Data Validation Pipeline
  As an intelligence analyst
  I want OSINT signals screened for bias, deepfakes, and adversarial manipulation
  So that corrupted data does not poison the predictive analysis engine

  Background:
    Given the adversarial validator module is available

  Scenario: Social media post flagged for bias
    Given a social media signal with biased content
    When the signal is validated by the pipeline
    Then the result should be flagged
    And the issue category should include "bias"

  Scenario: Satellite imagery with suspicious metadata flagged as deepfake
    Given a satellite imagery signal with inconsistent metadata
    When the signal is validated by the pipeline
    Then the result should be flagged
    And the issue category should include "deepfake"

  Scenario: Adversarial text manipulation detected
    Given a text signal containing homoglyphs and invisible characters
    When the signal is validated by the pipeline
    Then the result should be flagged
    And the issue category should include "manipulation"

  Scenario: Clean signal passes validation
    Given a clean OSINT signal
    When the signal is validated by the pipeline
    Then the result should not be flagged
    And the status should be "clean"

  Scenario: Batch validation produces correct counts
    Given a batch of 3 signals with mixed quality
    When the batch is validated
    Then the total count should be 3
    And the flagged count should be 2
    And the clean count should be 1

  Scenario: Audit trail is append-only
    Given a validated signal result
    When the audit event is generated
    Then the audit event should contain a timestamp
    And the audit event should contain the signal id
    And the audit event should contain detected issues

  Scenario: Gate blocks signals with critical findings
    Given a social media signal with severely biased content
    When the signal is validated with gate mode enabled
    Then the validation gate should fail
