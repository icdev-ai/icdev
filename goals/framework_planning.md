# CUI // SP-CTI
# Framework Planning Goal — Language-Specific Build Commands

## Purpose
Provide framework-specific planning commands that generate comprehensive build plans
tailored to each supported language, including project structure, testing patterns,
STIG-hardened Dockerfiles, CI/CD pipeline stages, and NIST 800-53 control mapping.

## Trigger
- User runs `/plan_python <name>`, `/plan_java <name>`, `/plan_go <name>`,
  `/plan_rust <name>`, `/plan_csharp <name>`, or `/plan_typescript <name>`

## Supported Languages

| Language | Command | Framework | BDD | SAST |
|----------|---------|-----------|-----|------|
| Python | `/plan_python` | Flask/FastAPI | behave | bandit |
| Java | `/plan_java` | Spring Boot | Cucumber-JVM | SpotBugs |
| Go | `/plan_go` | net/http/Gin | godog | gosec |
| Rust | `/plan_rust` | Actix-web | cucumber-rs | cargo-audit |
| C# | `/plan_csharp` | ASP.NET Core | SpecFlow | SecurityCodeScan |
| TypeScript | `/plan_typescript` | Express | cucumber-js | eslint-security |

## What Each Command Generates
1. Project structure template
2. Technology stack recommendations
3. STIG-hardened Dockerfile
4. CI/CD pipeline stage definitions
5. CUI marking injection points
6. Test file structure (unit + BDD + E2E)
7. Security scanning configuration
8. NIST 800-53 control mapping hints

## Workflow
1. User selects language-specific planning command
2. Command generates framework-specific plan
3. User reviews and customizes plan
4. Use `/icdev-init` to scaffold the project
5. Follow ATLAS workflow for implementation
6. TDD cycle: RED → GREEN → REFACTOR

## Context
- `context/languages/language_registry.json` — Language detection profiles
- `context/languages/framework_patterns.json` — Framework-specific patterns

## 12 Leverage Points of Agentic Development
These commands embody the 12 Leverage Points framework:

### In Agent (Core Four)
1. **Context** — CLAUDE.md, goals, context files inform agent decisions
2. **Model** — Bedrock Claude Sonnet/Opus via agent_config.yaml
3. **Prompt** — hardprompts/ templates guide generation
4. **Tools** — tools/ deterministic Python scripts do the work

### Through Agent (Multipliers)
5. **Standard Output** — Structured JSON (--json flag)
6. **Types** — Strong typing (dataclasses, Pydantic, DB schemas)
7. **Docs** — Clear instructions (CLAUDE.md, SKILL.md, goals)
8. **Tests** — Validation (pytest + behave + Playwright + gates)
9. **Architecture** — System design (GOTCHA layers, ATLAS workflow)
10. **Plans** — Implementation blueprints (framework commands)
11. **Templates** — Reusable patterns (context/ JSON, hardprompts/)
12. **Workflows** — Orchestration logic (goals/, CI/CD pipelines)
