# GovCon (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## GovCon (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| AI Clause Compliance | tools/govcon/ai_clause_compliance.py | AI-specific FAR/DFARS clause compliance checker | --json, --gate | Compliance results |
| FAR/DFARS Verifier | tools/govcon/far_dfars_verifier.py | Per-initiative FAR/DFARS procurement compliance verifier: detects applicable parts, lists required documentation, applies pass/warn/fail gate | --opportunity-id, --solicitation-text, --provided-doc, --save, --gate, --list-clauses, --export, --format, --json | Verification report (JSON or Markdown) |
| Clause Risk Engine | tools/govcon/clause_risk_engine.py | Deterministic-first contract clause risk scoring (crx-gov-02): regex clause/indicator catalog in `args/govcon/clause_risk_rules.yaml` + toxic-combination risk rules (FFP+unbounded scope, unlimited liability, aggressive LD) → severity/rationale/mitigation, each citing its FAR/DFARS source. Optional LLM narrative is GATED behind the deterministic pass and only explains, never scores. Surfaced on the GovCon pipeline view via `POST /api/govcon/opportunities/<id>/clause-risk`. | --text, --text-file, --opportunity-id, --assist, --persist, --list-rules, --export, --format, --json | Clause risk report (JSON or Markdown) |
| Procurement Quote vs IGCE | tools/govcon/procurement_quote_compare.py | Captures IGCE line items + vendor quotes, runs side-by-side comparison, rollup, and pass/warn/fail gate | --create-procurement, --add-igce-line, --add-quote, --add-bom-line, --compare, --summary, --gate, --list-*, --json | Variance report with line-by-line and per-vendor summaries; 9-field BOM capture (Vendor, Item, Qty, Estimate, Quotation, Expiration, POC, Description, Notes) |
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
| BOM Generator | tools/govcon/bom_generator.py | Aggregates IGCE + vendor quotes into a Bill of Materials rollup by initiative tier and equipment category; exports CSV (utf-8-sig) and XLSX (multi-sheet: BOM Lines, Summary, By Category) suitable for direct submission to the procurement office or contracting officer | --bom, --procurement, --tier, --fiscal-year, --category, --format json\|csv\|xlsx, --output PATH, --json | Rollup dict, CSV bytes, or XLSX bytes |
| PM Skills Bridge | tools/govcon/pm_skills_bridge.py | Bridges the product_manager ACE role (phuryn/pm-skills) with GovCon canvas tools: SWOT analysis, ICP profiling, and battlecard generation via agent_product_manager LLM function | (library) analyze_swot(context, conn), build_icp(context, conn), generate_battlecard(context, conn) | Structured SWOT dict, ICP dict, or battlecard dict; graceful empty-dict fallback if LLM unavailable |
| Past-Performance Suggester | tools/govcon/past_performance_suggester.py | Composes EXISTING CPMP contracts + CPARS + win/loss data into ranked past-performance reference SUGGESTIONS for a proposal/RFI opportunity (human selects; never auto-inserted). Similarity via shared embedding provider with deterministic offline fallback; every suggestion carries validated `[source: cpmp_contracts:<id>]` citations via tools/quality/citation_grounding.py. Read-only; table-absent tolerant | --requirements, --title, --agency, --naics, --top-k, --json / (library) suggest_references(opportunity, top_k, conn) | {suggestions[], count, method, auto_inserted:False} |

| Compliance Matrix Ingest (rmf-rfp-01) | tools/govcon/compliance_matrix_builder.py | `build_from_parsed(opp_id, parsed, section_text, conn=)` turns solicitation_parser output (Section L instructions, Section M factors/subfactors/weights, basis of award) plus raw L/M/C bodies into rows of **proposal_compliance_matrix, the ONE matrix** (pg_compliance_matrix folded in and dropped by migration 20260903185253; vocabulary in `compliance_matrix_schema.py`). `ingest_solicitation(path, opp_id)` parses the file first. Consumed by POST /api/proposals/opportunities/<id>/compliance/batch (`parsed` / `section_text` payload) and POST /rfp/upload (`opportunity_id`). | --ingest <file> --opportunity-id <id> --json | {extracted:{L,M,C}, created, duplicates, total_in_matrix} |
