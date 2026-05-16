# [TEMPLATE: CUI // SP-CTI]
# NIST 800-53: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)
Feature: JISE Portal Data Retrieval
  As a Joint Intelligence Support Element portal consumer
  I want to retrieve intelligence data via a REST API endpoint
  So that JISE systems can consume structured, current intelligence records

  Background:
    Given the JISE portal API is initialized for testing

  Scenario: Successful retrieval of JISE portal data
    When I send a GET request to "/api/v1/jise/portal-data"
    Then the response status code is 200
    And the response body contains a "data" key
    And the response body contains a "count" key
    And each record in "data" contains "id", "source", "classification", "timestamp"

  Scenario: Response is valid JSON consumable by JISE portal
    When I send a GET request to "/api/v1/jise/portal-data"
    Then the response content type is "application/json"
    And the response body is valid JSON

  Scenario: Filtering by classification level
    When I send a GET request to "/api/v1/jise/portal-data?classification=CUI"
    Then the response status code is 200
    And all records in "data" have classification equal to "CUI"

  Scenario: Empty result set returns structured response
    When I send a GET request to "/api/v1/jise/portal-data?source=nonexistent"
    Then the response status code is 200
    And the response body contains a "data" key
    And the "data" field is an empty list

  Scenario: Invalid filter parameter returns 400
    When I send a GET request to "/api/v1/jise/portal-data?limit=notanumber"
    Then the response status code is 400
    And the response body contains an "error" key
