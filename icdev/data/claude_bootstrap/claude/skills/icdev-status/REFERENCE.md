# icdev-status — Step-by-Step Reference

## Steps

### 1. Load Session Context (Auto-Detect)
```bash
python tools/project/session_context_builder.py --format markdown
```
If the current directory contains `icdev.yaml` or is a registered ICDEV™ project, this outputs:
- Project metadata (name, type, language, impact level, classification, ATO status)
- Compliance posture (frameworks, SSP, POAMs, STIG findings, controls, cATO readiness)
- Dev profile summary (language, style, testing, crypto standards)
- Recent activity (last 5 audit trail entries)
- Active intake sessions
- Suggested next actions

Display this context to the user. If `setup_needed` is true, suggest `/icdev-init`.

### 2. List or Select Project
If no project-id given and `--all` flag:
Run the CLI command or use MCP tool `project_list` MCP tool from icdev-core to list all projects.

If project-id given:
Run the CLI command or use MCP tool `project_status` MCP tool from icdev-core for full details.

### 3. Display Project Overview
```
╔══════════════════════════════════════╗
║  ICDEV™ Project Status                ║
║  CUI // SP-CTI                       ║
╠══════════════════════════════════════╣
║  Project:  <name>                    ║
║  ID:       <uuid>                    ║
║  Type:     <webapp|microservice|...> ║
║  Status:   <active|completed|...>    ║
║  Created:  <date>                    ║
╚══════════════════════════════════════╝
```

### 4. Compliance Status
Run the CLI command or use MCP tool `get_recommendations` MCP tool from icdev-knowledge (if available):
```
Compliance Score: XX%
  SSP:      Generated ✓ / Missing ✗
  POAM:     X open items
  STIG:     X CAT1 / Y CAT2 / Z CAT3
  SBOM:     Current ✓ / Stale ✗
  Controls: X/Y mapped
```

### 5. Security Status
```
Security Gate: PASS / FAIL
  SAST:       X findings (H/M/L)
  Dependencies: X CVEs
  Secrets:    X detected
  Container:  X issues
```

### 6. Test Status
```
Tests: PASS / FAIL
  Unit:     X passed, Y failed
  BDD:      X scenarios passed
  Coverage: XX%
```

### 7. Deployment Status
```
Deployments:
  Staging:    v1.2.3 (deployed 2024-01-15)
  Production: v1.2.2 (deployed 2024-01-10)
  Pipeline:   #456 running...
```

### 8. Agent Health
Run the CLI command or use MCP tool `agent_status` MCP tool from icdev-core:
```
Agents: X/8 healthy
  Orchestrator: ● healthy
  Builder:      ● healthy
  Compliance:   ○ offline
  ...
```

### 9. Recent Audit Trail
```
Recent Activity:
  2024-01-15 10:30 - security.scan completed
  2024-01-15 10:25 - test.execute passed
  2024-01-15 10:20 - code.commit by developer
```

### Recall memory progressively — index, then timeline, then detail by id (xrv-mem-03)
Never pay full-row cost for a recall you have not yet decided you want. Three
layers of `tools/memory/hybrid_search.py`, each reporting `approx_tokens` for
what it returned (the MCP tool `search_knowledge` takes the same `layer`,
`ids`, `since`, `until` parameters; without `layer` it is the knowledge-pattern
search it has always been):

1. **index** — the top-K headlines for a query, ~40 tokens a row:
   ```bash
   python tools/memory/hybrid_search.py --query "helm chart rollout" --layer index --json
   ```
   Each row is `{id, ts, type, headline, score}`. Read the headlines, pick ids.
2. **timeline** — what happened WHEN: index rows inside a window, in
   chronological order, interleaved with the session activity feed (audit
   trail and hook events, the dashboard's own `/api/activity/feed` query):
   ```bash
   python tools/memory/hybrid_search.py --layer timeline --since 2026-09-11T00:00:00 --json
   python tools/memory/hybrid_search.py --layer timeline --query "rollout" --session-id <id> --json
   ```
   `activity.status` is `unmeasurable` when the activity tables could not be
   read — that is not an idle session. The window defaults to the last 24h
   and says so under `window.basis`.
3. **detail** — the full rows for the ids you chose, and only those:
   ```bash
   python tools/memory/hybrid_search.py --layer detail --ids 28184,28183 --json
   ```
   An id that does not exist is named under `missing_ids`; a row your
   clearance withholds is named under `withheld_ids`. Neither is silent.

The default (no `--layer`) is unchanged: the ranked rows with full content.
