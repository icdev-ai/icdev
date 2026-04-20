# Framework Planning Commands (Phase 42)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Framework Planning Commands (Phase 42)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Plan Python | .claude/commands/plan_python.md | Python build plan: Flask/FastAPI, pytest, behave, bandit, pip-audit | $ARGUMENTS | Build plan |
| Plan Java | .claude/commands/plan_java.md | Java build plan: Spring Boot, Cucumber-JVM, checkstyle, SpotBugs | $ARGUMENTS | Build plan |
| Plan Go | .claude/commands/plan_go.md | Go build plan: net/http/Gin, godog, golangci-lint, gosec | $ARGUMENTS | Build plan |
| Plan Rust | .claude/commands/plan_rust.md | Rust build plan: Actix-web, cucumber-rs, clippy, cargo-audit | $ARGUMENTS | Build plan |
| Plan C# | .claude/commands/plan_csharp.md | C# build plan: ASP.NET Core, SpecFlow, SecurityCodeScan | $ARGUMENTS | Build plan |
| Plan TypeScript | .claude/commands/plan_typescript.md | TypeScript build plan: Express, cucumber-js, eslint-security | $ARGUMENTS | Build plan |
| PRD → Plan | tools/planning/prd_to_plan.py | OPT-53 tracer-bullet planner — reads PRD md, optional LLM enrichment, emits vertical-slice phases; lints output for leaked file/function names (adapted from mattpocock/skills/prd-to-plan, MIT) | --prd PATH, --out PATH, --no-llm, --lint, --validate-only, --json | Plan markdown + lint result |

## Kanban Plan Schedulers (tools/scripts/)

Idempotent enqueue scripts — decompose multi-phase plans into atomic Kanban subtasks chained via `depends_on_task_id`. Re-runnable: skips rows whose stable id already exists. All use `get_connection()` (never raw sqlite3).

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FathomDesk News Plan Scheduler | tools/scripts/schedule_fathomdesk_news_plan.py | Enqueues the FathomDesk /news pipeline plan (adn- prefix, 8 phases A–I: DB scaffold, RSS ingestor, classifier, scenario matcher, aggregator, dashboard route, daemon reflexes, INTaaS wiring). Each phase ends with a 5-step exit gate subtask. | `python tools/scripts/schedule_fathomdesk_news_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Phase 7.6 AI Options Plan Scheduler | tools/scripts/schedule_phase_7_6_ai_options_plan.py | Enqueues FathomDesk Phase 7.6 AI-Assisted Options subtasks (intent parser, strategy selector, strike picker, proposal builder, preflight gates, coach engine). | `python tools/scripts/schedule_phase_7_6_ai_options_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Phase 7.7 Prob/Compare Plan Scheduler | tools/scripts/schedule_phase_7_7_prob_compare_plan.py | Enqueues FathomDesk Phase 7.7 Probability & Compare subtasks (Monte Carlo POP, side-by-side compare API). | `python tools/scripts/schedule_phase_7_7_prob_compare_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Phase 7.8 Greeks/Share Plan Scheduler | tools/scripts/schedule_phase_7_8_greeks_share_plan.py | Enqueues FathomDesk Phase 7.8 Greeks Deep Dive subtasks (Black-Scholes pricer, time-T payoff, portfolio Greeks, shareable trade URLs). | `python tools/scripts/schedule_phase_7_8_greeks_share_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Phase 7.9 TA Foundation Plan Scheduler | tools/scripts/schedule_phase_7_9_ta_foundation_plan.py | Enqueues FathomDesk Phase 7.9 technical analysis foundation subtasks. | `python tools/scripts/schedule_phase_7_9_ta_foundation_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Phase 7.10 Traps Plan Scheduler | tools/scripts/schedule_phase_7_10_traps_plan.py | Enqueues FathomDesk Phase 7.10 trap/pattern detection subtasks. | `python tools/scripts/schedule_phase_7_10_traps_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Phase 7.11 News 2 Plan Scheduler | tools/scripts/schedule_phase_7_11_news_2_plan.py | Enqueues FathomDesk Phase 7.11 news pipeline v2 subtasks. | `python tools/scripts/schedule_phase_7_11_news_2_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |
| Enterprise Frontend Plan Scheduler | tools/scripts/schedule_enterprise_frontend_plan.py | Enqueues the Enterprise Frontend plan subtasks. | `python tools/scripts/schedule_enterprise_frontend_plan.py` | `{inserted, skipped_already_present, total_subtasks}` JSON |

