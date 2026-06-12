# Innovation Engine (Phase 35 — D199-D208)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Innovation Engine (Phase 35 — D199-D208)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Web Scanner | tools/innovation/web_scanner.py | Scan GitHub, NVD, Stack Overflow, HN for innovation signals | --scan, --source, --all, --list-sources, --history, --json | Signals + storage results |
| Signal Ranker | tools/innovation/signal_ranker.py | 5-dimension weighted innovation scoring (D21 pattern) | --score, --score-all, --top, --calibrate, --json | Scores + breakdowns |
| Trend Detector | tools/innovation/trend_detector.py | Cross-signal pattern detection via keyword co-occurrence (D207) | --detect, --report, --velocity, --json | Trends + velocity |
| Triage Engine | tools/innovation/triage_engine.py | 5-stage compliance-first triage pipeline (classify, FORGE fit, boundary, compliance, dedup/license) | --triage, --triage-all, --summary, --json | Triage outcomes |
| Solution Generator | tools/innovation/solution_generator.py | Auto-generate solution specs from approved signals (D208) | --generate, --generate-all, --list, --status, --json | Solution specs |
| Innovation Manager | tools/innovation/innovation_manager.py | Main orchestrator + daemon mode for full pipeline | --run, --discover, --score, --triage, --generate, --daemon, --status, --json | Pipeline results |
| Introspective Analyzer | tools/innovation/introspective_analyzer.py | Internal telemetry mining (D203) — gate failures, unused tools, slow pipelines, knowledge gaps | --analyze, --type, --all, --json | Analysis findings |
| Competitive Intel | tools/innovation/competitive_intel.py | Competitor feature monitoring (D205) — gap analysis against ICDEV™ capabilities | --scan, --gap-analysis, --report, --json | Competitive gaps |
| Standards Monitor | tools/innovation/standards_monitor.py | Standards body change tracking (D204) — NIST, CISA, DoD, FedRAMP, ISO | --check, --body, --report, --assess, --json | Standards updates |
| Innovation Config | args/innovation_config.yaml | Configuration: sources, scoring weights, triage rules, scheduling, competitive intel, standards monitoring | (data) | YAML config |
| Kanban Promoter | tools/innovation/kanban_promoter.py | Promote approved/suggested innovation signals into kanban_tasks (status=suggested) with source_prediction_id provenance (OPT-60) | --triage-result, --limit, --min-innovation-score, --dry-run, --list, --promote-id, --json | Summary JSON + inserted task ids |
| Innovation Promoter Config | args/innovation_promoter.yaml | Config for kanban_promoter: triage states, score gate, priority thresholds | (data) | YAML config |

