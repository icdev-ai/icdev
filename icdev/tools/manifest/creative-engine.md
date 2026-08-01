# Creative Engine (Phase 58)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Creative Engine (Phase 58)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Creative Engine | tools/creative/creative_engine.py | Customer-centric feature opportunity discovery (D351-D360) | --run, --discover, --scan, --extract, --score, --rank, --generate, --json | Pipeline results |
| Competitor Discoverer | tools/creative/competitor_discoverer.py | Auto-discover competitors from category pages (D353) | --discover, --list, --confirm, --json | Competitor records |
| Gap Scorer | tools/creative/gap_scorer.py | 3-dimension composite scoring (D355) | --score-all, --top, --gaps, --json | Scored gaps |
| Pain Extractor | tools/creative/pain_extractor.py | Deterministic keyword-based pain point extraction (D354) | --extract-all, --json | Pain points |
| Spec Generator | tools/creative/spec_generator.py | Template-based feature spec generation (D356) | --generate-all, --list, --json | Feature specs |
| Trend Tracker | tools/creative/trend_tracker.py | Velocity/acceleration trend detection | --detect, --report, --json | Trend data |
| Source Scanner | tools/creative/source_scanner.py | Scan customer pain points from G2, Capterra, TrustRadius, Reddit, GitHub Issues, Product Hunt, and GovCon blogs; store normalized signals | --scan, --source, --all, --list-sources, --history, --days, --json | Signal records |
| Competitor Repo Seeder | tools/creative/seed_competitor_repos.py | Seed external repo pain points as creative_pain_points (status=new) for gap_scorer to score; maps scouted repos to ICDEV feature gaps | --seed-all, --status, --json | Seeded pain point ids |
| Divergence Benchmark | tools/creative/divergence_benchmark.py | Measure divergence vs single-shot ideation on real ICDEV functions (dvg-bench-01): breadth / novelty / trap detection at fixed model + token cost. Air-gap => status "unmeasured" (never requires live models to merge). Recommend-only — flips no default. Tasks: args/creative/divergence_benchmark_tasks.yaml; results: data/divergence/ | --run, --dry-run, --json, --out, --tasks | Benchmark report + advisory recommendation |
| Divergence Branch | tools/creative/divergence_branch.py | Opt-in generative divergence branch (dvg-wire-01) for stage_generate: fan out over the generative frame set for the top-ranked pain point, run the critic pass, carry surviving clusters + trace_id into the spec. OFF by default (`creative_config.yaml divergence.enabled`); degrades to the deterministic template path | (library; called by creative_engine.stage_generate) | Divergence context (clusters, trace_id, spec section) |

