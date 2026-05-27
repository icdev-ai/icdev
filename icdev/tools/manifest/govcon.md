# GovCon (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## GovCon (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AI Clause Compliance | tools/govcon/ai_clause_compliance.py | AI-specific FAR/DFARS clause compliance checker | --json, --gate | Compliance results |
| Bayesian Bid Scorer | tools/govcon/bayesian_bid_scorer.py | Bayesian bid/no-bid scoring engine | --json | Bid scores |
| Capability Enricher | tools/govcon/capability_enricher.py | Enrich capability mappings with evidence | --json | Enriched mappings |
| Capture AI Blueprint | tools/govcon/capture_ai_blueprint.py | AI-assisted capture management blueprint | --json | Blueprint data |
| CMMC Validator | tools/govcon/cmmc_validator.py | CMMC compliance validator for proposals | --json, --gate | Validation results |
| Color Review Simulator | tools/govcon/color_review_simulator.py | Shipley color team review simulator | --json | Review results |
| Compliance Matrix Builder | tools/govcon/compliance_matrix_builder.py | L/M/N compliance matrix builder | --json | Compliance matrix |
| IDIQ Factory | tools/govcon/idiq_factory.py | IDIQ/BPA task order factory | --json | Task orders |
| LCAT Mapper | tools/govcon/lcat_mapper.py | Labor category (LCAT) mapping engine | --json | LCAT mappings |
| Opportunity Lifecycle | tools/govcon/opportunity_lifecycle.py | Opportunity lifecycle state machine | --json | Lifecycle state |
| Program Bridge | tools/govcon/program_bridge.py | Bridge proposals to program execution | --json | Bridge results |
| Proposal Quality Evaluator | tools/govcon/proposal_quality_evaluator.py | Multi-dimension proposal quality scoring | --json | Quality scores |
| Rate Benchmarker | tools/govcon/rate_benchmarker.py | Labor rate benchmarking against market data | --json | Benchmark results |
| Reflex Sandbox | tools/govcon/reflex_sandbox.py | Proposal Genesis reflex testing sandbox | --json | Sandbox results |
| Shipley Mapper | tools/govcon/shipley_mapper.py | Map proposal phases to Shipley process | --json | Phase mappings |
| Talent Intelligence | tools/govcon/talent_intelligence.py | Talent pipeline intelligence for proposals | --json | Talent data |
| Teaming Hub | tools/govcon/teaming_hub.py | Teaming partner discovery and management | --json | Partner data |
| Win Theme Manager | tools/govcon/win_theme_manager.py | Win theme and discriminator management | --json | Theme data |
| Synthetic Proposal Generator | tools/govcon/synthetic_proposal_generator.py | 50 fictional GovCon proposals (5 archetypes × 10); seed=42; no real data | (library) generate(count, seed) | list[{opportunity, volumes, sections}] |
| Demo Ingest Orchestrator | tools/govcon/demo_ingest.py | Seed DB + RAG index + KG bridge + 450 FT Q&A pairs + optional fine-tuning | --run/--dry-run, --train, --json | Pipeline results |
| Seed GovCon Proposals | tools/db/seeds/seed_govcon_proposals.py | Seeds 50 proposals + 150 volumes + 150 sections; idempotent via created_by=synthetic_demo | --dry-run, --json | Inserted counts |

