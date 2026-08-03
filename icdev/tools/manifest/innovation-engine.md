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
| External Repo Seeder | tools/innovation/seed_external_repos.py | Seed external GitHub repos as innovation signals for technology scouting (source_type=external_repo_scouting) | --register-all, --status, --score-all, --json | Registered signal ids + scores |
| Benchmark Comparator | tools/innovation/benchmark_compare.py | xbm-cmp-01. Joins every scout-tracked project to the ICDEV subsystem it benchmarks (the `subsystem:` tag in `context/genesis/competitors.yaml`, with a category fallback for entries predating the tag) and attaches the measured local half — module counts off disk, table row counts off the live DB. Verdict is COMPUTED from code_state (absent/thin/built) × data_state (unfed/populated/not_expected/not_assessed) × outstanding external adaptations; `ahead — no adaptation needed` is a first-class outcome and is produced for compliance_ato, delivery_pipeline and observability. Automates by measurement what docs/research/external-benchmark-map.md and canvas-engine-sweep.md did by hand. Read-only — writes nothing to the DB. An unreachable DB yields `not_assessed`, never `unfed`, so blindness can never manufacture a gap; an unroutable project is reported as `unmapped`, never dropped | --json, --markdown, --subsystem, --catalog, --watchlist, --no-db, --out | Per-project verdicts + per-subsystem assessments + summary |
| Benchmark Subsystem Catalogue | args/benchmark_subsystems.yaml | The ICDEV half of the comparison: per subsystem the module dirs and `min_modules` floor, evidence tables with `data_expected` / `data_floor`, and the external `adaptations` (status open/deferred/done) whose presence decides parity vs ahead. Declares no counts — everything numeric is measured at run time | (data) | YAML config |


## DIC Integration (Epic D — dic-syn-ri)

`stage_discover()` in `innovation_manager.py` accepts an optional `dic_collection_id` parameter.
When provided, it calls `tools/research/source_scanners/dic_scanner.scan_dic_collection()` and
includes the result as a `dic_collection` sub_result — treating DIC RAG chunks as pre-synthesized
signals alongside web scanner output.

```python
from tools.innovation.innovation_manager import stage_discover
result = stage_discover(dic_collection_id="my-collection")
# result["sub_results"]["dic_collection"] = {collection_id, signals: N}
```
