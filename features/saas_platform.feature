# [TEMPLATE: CUI // SP-CTI]
Feature: ICDEV SaaS Multi-Tenancy Platform
  As a SaaS platform administrator
  I want multi-tenant isolation and management
  So that each tenant operates securely within their impact level

  Scenario: Initialize platform database
    Given a fresh SaaS environment
    When I initialize the platform database
    Then the tenants table should exist
    And the users table should exist
    And the api_keys table should exist
    And the subscriptions table should exist

  Scenario: Create a new tenant
    Given the platform database is initialized
    When I create a tenant with name "ACME" and tier "professional" and IL "IL4"
    Then the tenant should be created with status "pending_provision"
    And the tenant should have a unique ID

  Scenario: Provision a tenant
    Given a tenant "ACME" with status "pending_provision"
    When I provision the tenant
    Then the tenant database should be created
    And the tenant status should be "active"

  Scenario: API key authentication
    Given an active tenant with an API key
    When I make a request with header "Authorization: Bearer icdev_testkey"
    Then the request should be authenticated
    And the tenant context should be resolved

  Scenario: Rate limiting by tier
    Given a tenant on the "starter" tier
    When the tenant exceeds 60 requests per minute
    Then subsequent requests should be rate limited

  Scenario: Tenant portal login page
    Given the SaaS portal is configured
    When I request the portal login page
    Then the response status should be 200
    And the page should contain login form elements

  Scenario: Tenant portal dashboard
    Given the SaaS portal is configured
    And a logged-in tenant admin
    When I request the portal dashboard
    Then the response status should be 200

  Scenario: IL5 tenant requires approval
    Given a new tenant requesting IL5 access
    When the tenant is created
    Then the tenant should require ISSO approval before provisioning
