---
mode: agent
description: "Display comprehensive project status including compliance, security, deployment, and agent health"
tools:
  - terminal
  - file_search
---

# icdev-status

Shows a comprehensive status overview:
1. Project metadata and health
2. Compliance status (SSP, POAM, STIG, SBOM)
3. Security scan results
4. Test results and coverage
5. Deployment status
6. Agent health
7. Recent audit trail entries

## Steps

1. **Load Session Context (Auto-Detect)**
```bash
python tools/project/session_context_builder.py --format markdown
```

2. **List or Select Project**
If no project-id given and `--all` flag:
Run the equivalent CLI command for project_list to list all projects.
If project-id given:

3. **Display Project Overview**
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

4. **Compliance Status**
```
Compliance Score: XX%
  SSP:      Generated ✓ / Missing ✗
  POAM:     X open items
  STIG:     X CAT1 / Y CAT2 / Z CAT3
  SBOM:     Current ✓ / Stale ✗
  Controls: X/Y mapped
```

5. **Security Status**
```
Security Gate: PASS / FAIL
  SAST:       X findings (H/M/L)
  Dependencies: X CVEs
  Secrets:    X detected
  Container:  X issues
```

6. **Test Status**
```
Tests: PASS / FAIL
  Unit:     X passed, Y failed
  BDD:      X scenarios passed
  Coverage: XX%
```

7. **Deployment Status**
```
Deployments:
  Staging:    v1.2.3 (deployed 2024-01-15)
  Production: v1.2.2 (deployed 2024-01-10)
  Pipeline:   #456 running...
```

8. **Agent Health**
```
Agents: X/8 healthy
  Orchestrator: ● healthy
  Builder:      ● healthy
  Compliance:   ○ offline
  ...
```

9. **Recent Audit Trail**
```
Recent Activity:
  2024-01-15 10:30 - security.scan completed
  2024-01-15 10:25 - test.execute passed
  2024-01-15 10:20 - code.commit by developer
```

10. **Recent Audit Trail**
```
Recent Activity:
  2024-01-15 10:30 - security.scan completed
  2024-01-15 10:25 - test.execute passed
  2024-01-15 10:20 - code.commit by developer
```

## Example
```
#prompt:icdev-status abc123-uuid --format table
#prompt:icdev-status --all --format brief
```