---
name: icdev-status
description: Display comprehensive project status including compliance, security, deployment, and agent health
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-status — Project Status Dashboard

## Usage
```
/icdev-status [<project-id>] [--all] [--format table|json|brief]
```

## What This Does
Shows a comprehensive status overview:
1. Project metadata and health
2. Compliance status (SSP, POAM, STIG, SBOM)
3. Security scan results
4. Test results and coverage
5. Deployment status
6. Agent health
7. Recent audit trail entries

## Steps

### 1. List or Select Project
If no project-id given and `--all` flag:
Use the `project_list` MCP tool from icdev-core to list all projects.

If project-id given:
Use the `project_status` MCP tool from icdev-core for full details.

### 2. Display Project Overview
```
╔══════════════════════════════════════╗
║  ICDEV Project Status                ║
║  CUI // SP-CTI                       ║
╠══════════════════════════════════════╣
║  Project:  <name>                    ║
║  ID:       <uuid>                    ║
║  Type:     <webapp|microservice|...> ║
║  Status:   <active|completed|...>    ║
║  Created:  <date>                    ║
╚══════════════════════════════════════╝
```

### 3. Compliance Status
Use the `get_recommendations` MCP tool from icdev-knowledge (if available):
```
Compliance Score: XX%
  SSP:      Generated ✓ / Missing ✗
  POAM:     X open items
  STIG:     X CAT1 / Y CAT2 / Z CAT3
  SBOM:     Current ✓ / Stale ✗
  Controls: X/Y mapped
```

### 4. Security Status
```
Security Gate: PASS / FAIL
  SAST:       X findings (H/M/L)
  Dependencies: X CVEs
  Secrets:    X detected
  Container:  X issues
```

### 5. Test Status
```
Tests: PASS / FAIL
  Unit:     X passed, Y failed
  BDD:      X scenarios passed
  Coverage: XX%
```

### 6. Deployment Status
```
Deployments:
  Staging:    v1.2.3 (deployed 2024-01-15)
  Production: v1.2.2 (deployed 2024-01-10)
  Pipeline:   #456 running...
```

### 7. Agent Health
Use the `agent_status` MCP tool from icdev-core:
```
Agents: X/8 healthy
  Orchestrator: ● healthy
  Builder:      ● healthy
  Compliance:   ○ offline
  ...
```

### 8. Recent Audit Trail
```
Recent Activity:
  2024-01-15 10:30 - security.scan completed
  2024-01-15 10:25 - test.execute passed
  2024-01-15 10:20 - code.commit by developer
```

## Example
```
/icdev-status abc123-uuid --format table
/icdev-status --all --format brief
```

## Error Handling
- If project not found: show available projects
- If database unavailable: report connection error
- If agent health check fails: show last known status
