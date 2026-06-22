---
name: icdev-innovate
description: "Run the ICDEV™ Innovation Engine for autonomous self-improvement through web intelligence and competitive monitoring. Use when triggering an innovation or self-improvement cycle."
context: fork
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# ICDEV™ Innovation Engine

CUI // SP-CTI

## Overview

The Innovation Engine enables ICDEV™ to continuously and autonomously improve itself by discovering developer pain points, CVEs, compliance changes, and competitive gaps — then generating solutions through the existing ANVIL build pipeline with full compliance triage.

Use this skill when you want to:
- Discover new innovation opportunities from the web or internal telemetry
- Score and prioritize discovered signals
- Triage signals through compliance gates before acting on them
- Generate solution specifications from approved signals
- Run the full autonomous improvement pipeline
- Monitor competitors, standards bodies, or internal health

## Before Starting

1. Read `goals/innovation_engine.md` for full workflow
2. Read `args/innovation_config.yaml` for configuration
3. Ensure database initialized: `python tools/db/init_icdev_db.py`
4. Ensure migration applied: `python tools/db/migrate.py --up`

## Available Operations

### Full Pipeline (Recommended)
Run the complete DISCOVER → SCORE → TRIAGE → GENERATE pipeline:
```bash
python tools/innovation/innovation_manager.py --run --json
```

See [REFERENCE.md](REFERENCE.md) for individual stage commands (discover, score, triage, generate, monitor, daemon).

## Workflow Decision Tree

1. **User wants to discover innovation opportunities** → Run `--run` (full pipeline)
2. **User wants to check internal health** → Run introspective analysis
3. **User wants to compare against competitors** → Run competitive gap analysis
4. **User wants compliance framework updates** → Run standards monitoring
5. **User wants to see what's been found** → Run `--status` or `--pipeline-report`
6. **User wants continuous improvement** → Run `--daemon` for background operation

## Innovation Pipeline Stages

```
Web Sources ──┐
Introspective ├─► DISCOVER ──► SCORE ──► TRIAGE ──► GENERATE ──► BUILD ──► PUBLISH
Competitive ──┤                                                     │
Standards ────┘                                               (ANVIL/TDD)
```

## Security Gates

1. **License Check** — No GPL/AGPL/SSPL (copyleft risk for Gov/DoD)
2. **Boundary Impact** — RED items blocked from auto-generation
3. **Compliance Alignment** — Must not weaken existing compliance posture
4. **FORGE Fit** — Must map to Goal/Tool/Arg/Context/HardPrompt
5. **Duplicate Detection** — Content hash dedup (similarity > 0.85)
6. **Budget Cap** — Max 10 auto-solutions per PI
7. **All existing ICDEV™ security gates** — SAST, deps, secrets, CUI, STIG

## Error Handling

- If web scan fails for a source → continues with other sources, logs error
- If database tables missing → returns error with migration instructions
- If air-gapped → skips web sources, runs introspective analysis only
- If rate limited → backs off, retries on next cycle
- If budget exceeded → logs signal for next PI, skips generation
