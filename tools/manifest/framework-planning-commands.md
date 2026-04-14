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

