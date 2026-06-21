# [TEMPLATE: CUI // SP-CTI]
Feature: Document Intelligence Canvas (DIC)
  As a user
  I want to manage documents, search them, review AI-generated drafts,
  track freshness, explore knowledge-graph gaps, and capture institutional knowledge
  So that the organization retains accurate, current, and well-governed documentation

  Background:
    Given the DIC blueprint is configured

  # ── Core Pages ────────────────────────────────────────────────────────────────

  Scenario: DIC index page loads
    When I request DIC page "/document-intelligence/"
    Then the DIC response status should be 200
    And the page should contain "Document Intelligence"

  Scenario: Collections page loads
    When I request DIC page "/document-intelligence/collections"
    Then the DIC response status should be 200

  Scenario: Search page loads
    When I request DIC page "/document-intelligence/search"
    Then the DIC response status should be 200

  Scenario: Review page loads
    When I request DIC page "/document-intelligence/review"
    Then the DIC response status should be 200

  Scenario: Generate page loads
    When I request DIC page "/document-intelligence/generate"
    Then the DIC response status should be 200

  Scenario: Templates page loads
    When I request DIC page "/document-intelligence/templates"
    Then the DIC response status should be 200

  # ── New Pages (from dic-discovery.md gaps) ─────────────────────────────────

  Scenario: Freshness heatmap page loads
    When I request DIC page "/document-intelligence/freshness"
    Then the DIC response status should be 200
    And the page should contain "Freshness Heatmap"

  Scenario: Explorer page loads
    When I request DIC page "/document-intelligence/explorer"
    Then the DIC response status should be 200
    And the page should contain "Buried-Bodies Explorer"

  Scenario: Handoff page loads
    When I request DIC page "/document-intelligence/handoff"
    Then the DIC response status should be 200
    And the page should contain "Knowledge Handoff"

  # ── APIs ────────────────────────────────────────────────────────────────────

  Scenario: API collections list returns JSON
    When I GET DIC API "/document-intelligence/api/collections"
    Then the DIC response status should be 200
    And the DIC JSON should contain key "collections"

  Scenario: API KG explore returns JSON
    When I POST DIC API "/document-intelligence/api/kg-explore" with body
      """
      {"mode": "entities", "limit": 5}
      """
    Then the DIC response status should be 200
    And the DIC JSON should contain key "entities"

  Scenario: API explorer refresh returns JSON
    When I POST DIC API "/document-intelligence/api/explorer/refresh" with body "{}"
    Then the DIC response status should be 200
    And the DIC JSON should contain key "findings"

  Scenario: API freshness heatmap returns JSON
    When I GET DIC API "/document-intelligence/api/freshness/heatmap"
    Then the DIC response status should be 200
    And the DIC JSON should contain key "heatmap"

  Scenario: API freshness scan returns JSON
    When I POST DIC API "/document-intelligence/api/freshness/scan" with body
      """
      {"collection_id": "default"}
      """
    Then the DIC response status should be 200
    And the DIC JSON should contain key "scan_id"

  # ── Role Enforcement ────────────────────────────────────────────────────────

  Scenario: Template instantiation requires editor role
    Given I am a DIC viewer
    When I POST DIC API "/document-intelligence/api/templates/acoic/instantiate" with body
      """
      {"collection_id": "default"}
      """
    Then the DIC response status should be 403

  Scenario: Section approve requires reviewer role
    Given I am a DIC editor
    When I POST DIC API "/document-intelligence/api/sections/fake/revise" with body "{}"
    Then the DIC response status should be 403
