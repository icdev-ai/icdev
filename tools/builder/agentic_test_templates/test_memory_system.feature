# [TEMPLATE: CUI // SP-CTI]
Feature: Memory System Operations
  Verify the memory system stores and retrieves data correctly

  Scenario: Write and read a memory entry
    When I write a memory entry with type "fact" and content "Test fact"
    Then the entry should be stored in data/memory.db
    And I should be able to read it back

  Scenario: Daily log creation
    When a new session starts
    Then a daily log file should exist for today
    And the log should be in memory/logs/YYYY-MM-DD.md format

  Scenario: Memory search
    Given multiple memory entries exist
    When I search for "test keyword"
    Then matching entries should be returned
    And results should be ordered by relevance

  Scenario: MEMORY.md contains identity
    Given the application is initialized
    When I read memory/MEMORY.md
    Then it should contain the application name
    And it should contain the classification level
    And it should state "CANNOT generate child applications"

  Scenario: Memory entry types
    When I write entries with types "fact", "preference", "event", "insight", "task", "relationship"
    Then each entry should be stored with the correct type
    And each entry should have a timestamp

  Scenario: Memory entry importance levels
    When I write a fact with importance 1
    And I write a fact with importance 10
    Then both entries should be stored
    And the high-importance entry should rank higher in search results

  Scenario: Hybrid search combines keyword and semantic results
    Given multiple memory entries with embeddings exist
    When I perform a hybrid search with query "deployment configuration"
    Then the results should combine keyword and semantic matches
    And the BM25 weight should default to 0.7
    And the semantic weight should default to 0.3

  Scenario: Memory database integrity
    Given the memory database exists at data/memory.db
    Then it should contain the memory_entries table
    And it should contain the daily_logs table
    And it should contain the memory_access_log table
