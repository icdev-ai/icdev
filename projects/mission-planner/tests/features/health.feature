Feature: Health Check
  As a system administrator
  I want to verify the application is running
  So that I can confirm the deployment is successful

  Scenario: Health endpoint returns OK
    Given the application is running
    When I request the health endpoint
    Then I should receive a 200 status code
    And the response should contain "healthy"
