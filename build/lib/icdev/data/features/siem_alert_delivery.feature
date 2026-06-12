# CUI // SP-CTI
Feature: SIEM Alert Delivery
  As a Security Operations Analyst
  I want alerts delivered to the downstream SIEM within 5 seconds
  So that security events are correlated and acted upon without unacceptable delay

  Scenario: Alerts must be delivered to downstream SIEM within 5 seconds
    Given the system is operational and the user is authenticated
    When an alert is forwarded to the downstream SIEM endpoint
    Then the SIEM alert forwarder module exists at "tools/siem_alert_forwarder.py"
    And the SLA constant equals 5 seconds
    And a test alert is delivered within the SLA
    And the delivery is recorded in the audit log
    And the system behaves as specified and the requirement is satisfied

  Scenario: SIEM delivery failure is recorded when endpoint is unreachable
    Given the system is operational and the user is authenticated
    When an alert is forwarded to an unreachable SIEM endpoint
    Then delivery fails gracefully
    And the failure is recorded in the audit log with sla_met=false
