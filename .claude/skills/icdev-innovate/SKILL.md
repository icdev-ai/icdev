---
name: icdev-innovate
description: "Run the ICDEV Innovation Engine — autonomous self-improvement through web intelligence, introspective analysis, competitive monitoring, and standards tracking."
context: fork
allowed-tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task", "TodoWrite"]
---

# ICDEV Innovation Engine

CUI // SP-CTI

## Overview

The Innovation Engine enables ICDEV to continuously and autonomously improve itself by discovering developer pain points, CVEs, compliance changes, and competitive gaps — then generating solutions through the existing ATLAS build pipeline with full compliance triage.

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

### Individual Stages

#### Stage 1: Discover Signals
```bash
# Scan all web sources
python tools/innovation/web_scanner.py --scan --all --json

# Scan specific source
python tools/innovation/web_scanner.py --scan --source github --json
python tools/innovation/web_scanner.py --scan --source cve_databases --json
python tools/innovation/web_scanner.py --scan --source stackoverflow --json

# List configured sources
python tools/innovation/web_scanner.py --list-sources --json
```

#### Stage 1b: Introspective Analysis (Air-Gap Safe)
```bash
# Mine internal telemetry for self-improvement opportunities
python tools/innovation/introspective_analyzer.py --analyze --all --json

# Specific analysis types
python tools/innovation/introspective_analyzer.py --analyze --type gate_failures --json
python tools/innovation/introspective_analyzer.py --analyze --type unused_tools --json
python tools/innovation/introspective_analyzer.py --analyze --type slow_pipelines --json
python tools/innovation/introspective_analyzer.py --analyze --type failed_self_heals --json
```

#### Stage 1c: Competitive Intelligence
```bash
# Scan all competitors
python tools/innovation/competitive_intel.py --scan --all --json

# Gap analysis
python tools/innovation/competitive_intel.py --gap-analysis --json

# Competitive report
python tools/innovation/competitive_intel.py --report --json
```

#### Stage 1d: Standards Monitoring
```bash
# Check all standards bodies
python tools/innovation/standards_monitor.py --check --all --json

# Check specific body
python tools/innovation/standards_monitor.py --check --body nist --json
python tools/innovation/standards_monitor.py --check --body cisa --json
```

#### Stage 2: Score Signals
```bash
# Score all new signals
python tools/innovation/signal_ranker.py --score-all --json

# Score specific signal
python tools/innovation/signal_ranker.py --score --signal-id "sig-xxx" --json

# View top-scored signals
python tools/innovation/signal_ranker.py --top --limit 20 --min-score 0.5 --json
```

#### Stage 3: Triage (Compliance Gate)
```bash
# Triage all scored signals
python tools/innovation/triage_engine.py --triage-all --json

# Triage specific signal
python tools/innovation/triage_engine.py --triage --signal-id "sig-xxx" --json

# Triage summary
python tools/innovation/triage_engine.py --summary --json
```

#### Stage 3b: Trend Detection
```bash
# Detect emerging trends
python tools/innovation/trend_detector.py --detect --days 30 --min-signals 3 --json

# Trend report
python tools/innovation/trend_detector.py --report --json
```

#### Stage 4: Solution Generation
```bash
# Generate specs for all approved signals
python tools/innovation/solution_generator.py --generate-all --json

# Generate for specific signal
python tools/innovation/solution_generator.py --generate --signal-id "sig-xxx" --json

# List generated solutions
python tools/innovation/solution_generator.py --list --status generated --json
```

### Status & Monitoring
```bash
# Engine status overview
python tools/innovation/innovation_manager.py --status --json

# Full pipeline report
python tools/innovation/innovation_manager.py --pipeline-report --json

# Scan history
python tools/innovation/web_scanner.py --history --days 7 --json
```

### Daemon Mode (Continuous)
```bash
# Run as continuous daemon
python tools/innovation/innovation_manager.py --daemon --json
```

### Feedback Calibration
```bash
# Recalibrate scoring weights from marketplace feedback
python tools/innovation/signal_ranker.py --calibrate --json
```

## Workflow Decision Tree

1. **User wants to discover innovation opportunities** → Run `--discover` or `--run` (full pipeline)
2. **User wants to check internal health** → Run `--introspect` for internal telemetry mining
3. **User wants to compare against competitors** → Run `--competitive` for gap analysis
4. **User wants compliance framework updates** → Run `--standards` for NIST/CISA/DoD changes
5. **User wants to see what's been found** → Run `--status` or `--pipeline-report`
6. **User wants continuous improvement** → Run `--daemon` for background operation

## Innovation Pipeline Stages

```
Web Sources ──┐
Introspective ├─► DISCOVER ──► SCORE ──► TRIAGE ──► GENERATE ──► BUILD ──► PUBLISH
Competitive ──┤                                                     │          │
Standards ────┘                                               (ATLAS/TDD) (Marketplace)
                                                                              │
                                           CALIBRATE ◄── MEASURE ◄── FEEDBACK ┘
```

## Security Gates

1. **License Check** — No GPL/AGPL/SSPL (copyleft risk for Gov/DoD)
2. **Boundary Impact** — RED items blocked from auto-generation
3. **Compliance Alignment** — Must not weaken existing compliance posture
4. **GOTCHA Fit** — Must map to Goal/Tool/Arg/Context/HardPrompt
5. **Duplicate Detection** — Content hash dedup (similarity > 0.85)
6. **Budget Cap** — Max 10 auto-solutions per PI
7. **All existing ICDEV security gates** — SAST, deps, secrets, CUI, STIG

## Error Handling

- If web scan fails for a source → continues with other sources, logs error
- If database tables missing → returns error with migration instructions
- If air-gapped → skips web sources, runs introspective analysis only
- If rate limited → backs off, retries on next cycle
- If budget exceeded → logs signal for next PI, skips generation
