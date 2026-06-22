# ICDEV™ Innovation Engine — Stage Reference

## Stage 1: Discover Signals
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

## Stage 1b: Introspective Analysis (Air-Gap Safe)
```bash
# Mine internal telemetry for self-improvement opportunities
python tools/innovation/introspective_analyzer.py --analyze --all --json

# Specific analysis types
python tools/innovation/introspective_analyzer.py --analyze --type gate_failures --json
python tools/innovation/introspective_analyzer.py --analyze --type unused_tools --json
python tools/innovation/introspective_analyzer.py --analyze --type slow_pipelines --json
python tools/innovation/introspective_analyzer.py --analyze --type failed_self_heals --json
```

## Stage 1c: Competitive Intelligence
```bash
# Scan all competitors
python tools/innovation/competitive_intel.py --scan --all --json

# Gap analysis
python tools/innovation/competitive_intel.py --gap-analysis --json

# Competitive report
python tools/innovation/competitive_intel.py --report --json
```

## Stage 1d: Standards Monitoring
```bash
# Check all standards bodies
python tools/innovation/standards_monitor.py --check --all --json

# Check specific body
python tools/innovation/standards_monitor.py --check --body nist --json
python tools/innovation/standards_monitor.py --check --body cisa --json
```

## Stage 2: Score Signals
```bash
# Score all new signals
python tools/innovation/signal_ranker.py --score-all --json

# Score specific signal
python tools/innovation/signal_ranker.py --score --signal-id "sig-xxx" --json

# View top-scored signals
python tools/innovation/signal_ranker.py --top --limit 20 --min-score 0.5 --json
```

## Stage 3: Triage (Compliance Gate)
```bash
# Triage all scored signals
python tools/innovation/triage_engine.py --triage-all --json

# Triage specific signal
python tools/innovation/triage_engine.py --triage --signal-id "sig-xxx" --json

# Triage summary
python tools/innovation/triage_engine.py --summary --json
```

## Stage 3b: Trend Detection
```bash
# Detect emerging trends
python tools/innovation/trend_detector.py --detect --days 30 --min-signals 3 --json

# Trend report
python tools/innovation/trend_detector.py --report --json
```

## Stage 4: Solution Generation
```bash
# Generate specs for all approved signals
python tools/innovation/solution_generator.py --generate-all --json

# Generate for specific signal
python tools/innovation/solution_generator.py --generate --signal-id "sig-xxx" --json

# List generated solutions
python tools/innovation/solution_generator.py --list --status generated --json
```

## Status & Monitoring
```bash
# Engine status overview
python tools/innovation/innovation_manager.py --status --json

# Full pipeline report
python tools/innovation/innovation_manager.py --pipeline-report --json

# Scan history
python tools/innovation/web_scanner.py --history --days 7 --json
```

## Daemon Mode (Continuous)
```bash
# Run as continuous daemon
python tools/innovation/innovation_manager.py --daemon --json
```

## Feedback Calibration
```bash
# Recalibrate scoring weights from marketplace feedback
python tools/innovation/signal_ranker.py --calibrate --json
```
