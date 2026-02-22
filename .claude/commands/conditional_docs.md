# Conditional Documentation Guide

This prompt helps you determine what documentation to read based on the specific changes you need to make. Review the conditions below and read ONLY the relevant documentation before proceeding — avoid excessive reading.

## Instructions

- Review the task you've been asked to perform
- Check each documentation path below
- For each path, evaluate if ANY of the listed conditions apply to your task
- Only read the documentation if a condition matches — do not over-read

## Conditional Documentation

### Core Framework
- `CLAUDE.md`
  - Conditions:
    - First time understanding the project
    - Need to find a tool, command, or workflow
    - Checking architecture decisions (D1-D241)

- `goals/manifest.md`
  - Conditions:
    - Starting any workflow (build, test, deploy, comply, etc.)
    - Need to understand what a goal does before executing

- `tools/manifest.md`
  - Conditions:
    - Writing a new tool script
    - Need to check if a tool already exists

### Compliance & Security
- `goals/compliance_workflow.md`
  - Conditions:
    - Generating SSP, POAM, STIG, SBOM artifacts
    - Working with CUI markings or classification

- `goals/universal_compliance.md`
  - Conditions:
    - Working with non-NIST frameworks (HIPAA, PCI DSS, CJIS, SOC 2, ISO 27001, HITRUST)
    - Adding a new compliance framework
    - Working with the crosswalk engine

- `goals/security_scan.md`
  - Conditions:
    - Running SAST, dependency audit, secret detection, container scan
    - Working with security gates

- `goals/atlas_integration.md`
  - Conditions:
    - Working with AI security (prompt injection, ATLAS, OWASP LLM Top 10)
    - Modifying AI telemetry or AI BOM

### Build & Test
- `goals/tdd_workflow.md`
  - Conditions:
    - Writing tests or implementation code
    - Running the RED→GREEN→REFACTOR cycle

- `goals/integration_testing.md`
  - Conditions:
    - Running pytest, behave, or Playwright E2E tests
    - Modifying the test pipeline
    - Working with security/compliance gates

- `.claude/commands/prepare_app.md`
  - Conditions:
    - About to run E2E tests
    - About to run the review command
    - Need to reset the database and restart the dashboard

### Infrastructure & Deployment
- `goals/deploy_workflow.md`
  - Conditions:
    - Generating Terraform, Ansible, K8s manifests
    - Working with CI/CD pipelines

- `goals/cloud_agnostic.md`
  - Conditions:
    - Working with multi-cloud (AWS, Azure, GCP, OCI, IBM)
    - Modifying cloud provider abstractions
    - Working with region validation or CSP monitoring

- `goals/zero_trust_architecture.md`
  - Conditions:
    - Working with ZTA pillars, service mesh, network segmentation
    - Modifying PDP/PEP configurations

### Requirements & MBSE
- `goals/requirements_intake.md`
  - Conditions:
    - Running conversational intake sessions
    - Working with gap detection, readiness scoring, SAFe decomposition

- `goals/mbse_integration.md`
  - Conditions:
    - Importing SysML/XMI or DOORS NG/ReqIF files
    - Working with the digital thread

### Dashboard & SaaS
- `goals/dashboard.md`
  - Conditions:
    - Modifying dashboard templates, routes, or static assets
    - Adding new dashboard pages

- `goals/saas_multi_tenancy.md`
  - Conditions:
    - Working with tenants, API gateway, or MCP Streamable HTTP
    - Modifying authentication or RBAC

### Marketplace & Innovation
- `goals/marketplace.md`
  - Conditions:
    - Publishing, installing, or searching marketplace assets
    - Working with the 7-gate security pipeline

- `goals/innovation_engine.md`
  - Conditions:
    - Running web scanning, signal scoring, or trend detection
    - Working with the innovation pipeline

### CI/CD & Agents
- `goals/cicd_integration.md`
  - Conditions:
    - Working with GitHub/GitLab webhooks or polling
    - Modifying workflow commands (`/icdev_plan`, `/icdev_build`, etc.)

- `goals/multi_agent_orchestration.md`
  - Conditions:
    - Working with Bedrock multi-agent DAG workflows
    - Modifying agent authority, mailbox, or collaboration patterns

### Slash Commands
- `.claude/commands/classify_workflow.md`
  - Conditions:
    - Adding or removing ICDEV workflow commands
    - Modifying the CI/CD workflow extraction

- `.claude/commands/classify_issue.md`
  - Conditions:
    - Modifying how issues are classified as bug/feature/chore/patch
