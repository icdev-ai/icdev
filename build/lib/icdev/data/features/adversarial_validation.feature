# CUI // SP-CTI
Feature: Adversarial Data Validation Pipeline for OSINT Sources
  As a predictive analysis engineer
  I want OSINT data screened for bias, deepfakes, and adversarial manipulation
  So that only trustworthy data enters the predictive engine

  Background:
    Given the adversarial validation pipeline is initialized

  @bias @red
  Scenario: Single-source bias detection blocks concentrated propaganda
    Given a batch of OSINT signals from a concentrated source
    When 75% or more signals originate from the same source
    Then the pipeline flags a bias violation
    And the batch is blocked from the predictive engine
    And the dominant source is recorded in the validation report

  @bias @red
  Scenario: Sentiment skew detection blocks emotionally manipulated data
    Given a batch of OSINT signals with text content
    When the aggregate sentiment skew score exceeds 0.5
    Then the pipeline flags a bias violation
    And the batch is blocked from the predictive engine

  @deepfake @red
  Scenario: Missing camera metadata triggers deepfake alert
    Given an OSINT media file with image mime type
    When the file lacks EXIF make or model metadata
    And the file size is below 10 KB
    Then the pipeline flags a deepfake violation
    And the batch is blocked from the predictive engine
    And the anomaly flags include "missing_camera_metadata"

  @deepfake @red
  Scenario: Hash mismatch against reference triggers deepfake alert
    Given an OSINT media file with a known source URL
    When the computed SHA256 hash differs from the reference hash
    Then the pipeline flags a deepfake violation
    And the anomaly flags include "hash_mismatch"

  @manipulation @red
  Scenario: Character substitution attack detected in text
    Given an OSINT signal containing free-text description
    When the text contains leet-style character substitutions
    Then the pipeline flags a manipulation violation
    And the perturbation score exceeds the default threshold
    And the batch is blocked from the predictive engine

  @manipulation @red
  Scenario: Prompt injection attempt detected in text
    Given an OSINT signal containing free-text description
    When the text contains prompt injection keywords
    Then the pipeline flags a manipulation violation
    And the injection score exceeds the default threshold
    And the batch is blocked from the predictive engine

  @pipeline @green
  Scenario: Clean data passes all validation gates
    Given a batch of OSINT signals from diverse reputable sources
    And a media file with complete metadata and matching hash
    And all text is free of adversarial patterns
    When the pipeline validates the batch
    Then the validation result is valid
    And no block reasons are recorded
    And the report is JSON serializable

  @pipeline @green
  Scenario: Partial data without media or signals still passes
    Given a batch with only OSINT signals and no media
    When the pipeline validates the batch
    Then the validation result is valid
    And no block reasons are recorded
