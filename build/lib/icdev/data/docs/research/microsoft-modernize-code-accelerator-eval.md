# Research: Microsoft Modernize-Your-Code Solution Accelerator
<!-- CUI // SP-CTI -->

**Date:** 2026-04-10  
**Task:** task-937ed5fb75  
**Source:** research_challenges session rsess-7518c1c1e901 (score 0.51)  
**Purpose:** Evaluate as partner integration for Phase 43 cross-language translation and SQL migration in `modernization_workflow`

---

## Repository

- **GitHub:** `github.com/microsoft/Modernize-your-code-solution-accelerator`
- **Version:** v1.7.0 (released 2026-04-08, actively maintained)
- **License:** MIT (Microsoft Corporation) — zero legal friction, fork/extend freely
- **Primary Language:** Python (FastAPI backend) + TypeScript/React (frontend)

---

## What It Does

A 5-agent sequential pipeline for SQL query migration, shipped as a web app with REST API. The default and only production-ready migration path is **Informix → T-SQL (SQL Server)**. Other pairs are theoretically possible via parameter substitution but require substantial manual engineering.

### 5-Agent Pipeline

```
Source SQL file (Blob Storage)
    ↓
[1] MigratorAgent    — Generates 3 candidate T-SQL translations (GPT-4o)
    ↓
[2] PickerAgent      — Selects the best semantic candidate (GPT-4o)
    ↓
[3] SyntaxCheckerAgent — C# tsqlParser binary (TSql150Parser), outputs errors
    ├── No errors → jump to SemanticVerifier
    └── Has errors →
        ↓
[4] FixerAgent       — Repairs syntax errors (GPT-4o, up to 5 retries)
    ↓
[5] SemanticVerifierAgent — Confirms semantic equivalence source↔migrated (GPT-4o)
```

### REST API Surface

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/upload` | Upload `.sql` file to a batch |
| POST | `/api/start-processing` | Trigger batch (`batch_id`, `translate_from`, `translate_to`) |
| GET | `/api/download/{batch_id}` | Download translated files as ZIP |
| GET | `/api/batch-story/{batch_id}` | Full batch + file status |
| GET | `/api/batch-summary/{batch_id}` | Batch summary |
| GET | `/api/batch-history` | Paginated batch history |
| WS | `/api/socket/{batch_id}` | Real-time per-file status stream |
| GET | `/health` | Health check |

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.x, FastAPI, uvicorn |
| Agent Orchestration | Semantic Kernel v1.39.4 (`semantic-kernel[azure]`) |
| Agent Hosting | Azure AI Foundry (Azure AI Projects SDK v1.0.0) |
| LLM | Azure OpenAI — GPT-4o |
| Storage | Azure Cosmos DB (metadata) + Azure Blob Storage (files) |
| Syntax Validation | C# .NET binary (`tsqlParser`, `TSql150Parser`) — T-SQL only |
| SQL Parsing | `sqlglot`, `sqlparse` (in requirements, not core to pipeline) |
| Auth | Azure AD (MSAL) — all endpoints require AD tokens |
| Observability | OpenTelemetry + Azure Monitor / Application Insights |
| Deployment | Azure Developer CLI (azd), Azure Container Apps, Docker |

---

## Integration Fit Analysis

### Fit — Strong

| Factor | Detail |
|--------|--------|
| License | MIT — free to fork, adapt, embed |
| Language | Pure Python backend; aligns with ICDEV Python toolchain |
| Extractable core | `sql_agents/` is callable directly: `process_batch_async()`, `convert_script()` |
| Prompt templates | Fully parameterized with `{{$source}}` / `{{$target}}` — dialect-agnostic by design |
| Pipeline design | The 5-agent Migrator→Picker→SyntaxChecker→Fixer→SemanticVerifier pattern is reusable |
| `sqlglot` present | Already in repo's `requirements.txt`; drop-in multi-dialect syntax validator |

### Fit — Weak

| Factor | Severity | Detail |
|--------|----------|--------|
| Hard Azure dependency | High | Requires Azure AI Foundry, Azure OpenAI, Cosmos DB, Blob Storage — no local mode |
| Single dialect out-of-box | High | Informix→T-SQL only; other pairs need manual prompt tuning + new syntax validator |
| T-SQL-only syntax checker | High | C# `tsqlParser` binary only validates T-SQL; invalid for PostgreSQL, Oracle, etc. |
| Air-gap incompatible | High | All calls go to Azure cloud; no Ollama/local LLM path built in |
| Azure AD auth | Medium | No API key option — token-gated; adds auth complexity to pipeline integration |
| No batch parallelism | Medium | Files processed sequentially in `process_batch_async` |
| LLM-only translation | Medium | No AST/rule-based rewriter; accuracy fully dependent on GPT-4o, human review needed |
| Cost unpredictability | Low | ACR daily fee + per-token costs across 5 agents × 3 candidates per file |

### ICDEV's Existing Coverage vs. This Accelerator

| Capability | ICDEV (tools/modernization/) | MS Accelerator |
|-----------|------------------------------|----------------|
| SQL DDL migration | `db_migration_planner.py` — Oracle/MSSQL/DB2/Sybase→PostgreSQL | Informix→T-SQL only |
| DB type mappings | `context/modernization/db_type_mappings.json` — 5 source DBs | Not applicable |
| Multi-dialect support | 5 source dialects to PostgreSQL | 1 dialect pair |
| Air-gap / Ollama | Yes (LLMRouter) | No (Azure-only) |
| Semantic verification | Not explicitly present | Yes (SemanticVerifierAgent) |
| Syntax validation | Not explicitly present | Yes (T-SQL only via C# binary) |
| Multi-language code translation | Phase 43 (`tools/translation/`) — 6 languages | Not applicable |
| Pass@k candidates | Yes (k=3 cloud, k=1 air-gap) | Yes (3 candidates in MigratorAgent) |
| Repair loop | Yes (max 3 attempts with compiler feedback) | Yes (max 5 retries with SyntaxChecker feedback) |

---

## Recommendation

**Do NOT adopt the accelerator as-is.** ICDEV already has a more capable SQL migration foundation (`db_migration_planner.py` with 5 source dialects to PostgreSQL) and a Phase 43 code translation pipeline with pass@k and repair loops.

### Adopt These Specific Patterns (Port, Don't Integrate)

1. **SemanticVerifierAgent pattern** — ICDEV's SQL migration lacks explicit semantic equivalence verification after translation. Port this agent's prompt logic into `db_migration_planner.py` or a new `sql_semantic_verifier.py` tool. Use ICDEV's `LLMRouter` instead of `AzureAIAgent`.

2. **PickerAgent pattern** — The concept of generating N candidates and then having a dedicated picker agent select the best one is distinct from pass@k scoring in Phase 43. This "jury selection" approach is worth adding to `db_migration_planner.py` for stored procedure / function translations.

3. **Prompt templates** — The Migrator, Picker, Fixer, and SemanticVerifier prompt templates are dialect-parameterized and high quality. Copy them verbatim into `context/modernization/sql_agent_prompts/` and adapt for ICDEV's supported DB pairs.

4. **Replace C# syntax checker with `sqlglot`** — `sqlglot.parse(sql, dialect="postgres")` provides multi-dialect syntax validation in pure Python. This covers all 5 of ICDEV's source/target dialects.

### What to Skip

- Do not adopt Azure AI Foundry, Cosmos DB, or Blob Storage dependencies.
- Do not adopt the Semantic Kernel `AgentGroupChat` orchestration — use ICDEV's existing agent framework.
- Do not replace the existing `db_migration_planner.py` — extend it.

### Suggested Phase 43 Enhancement (Optional)

Add a `sql_translation_verifier.py` tool under `tools/modernization/` that wraps the 5-agent pattern using ICDEV's `LLMRouter`:
- Step 1: Generate 3 candidate translations (existing pass@k in `db_migration_planner.py`)
- Step 2: Pick best candidate (new — port PickerAgent prompt)
- Step 3: Validate syntax via `sqlglot.parse()` (new — replaces C# binary)
- Step 4: Fix syntax errors with LLM (new — port FixerAgent prompt)
- Step 5: Verify semantic equivalence (new — port SemanticVerifierAgent prompt)

This brings ICDEV's SQL migration to parity with the MS accelerator's quality bar while keeping the multi-dialect, air-gap-compatible, Azure-free design.

---

## Files Referenced

| File | Relevance |
|------|-----------|
| `tools/modernization/db_migration_planner.py` | Existing SQL migration tool — extend, don't replace |
| `context/modernization/db_type_mappings.json` | DB type mapping context — already covers 5 source dialects |
| `tools/translation/code_translator.py` | Pass@k pattern — adapt for SQL |
| `tools/translation/translation_validator.py` | Repair loop — adapt for SQL |
| `args/translation_config.yaml` | Pass@k config (k=3/k=1) |
| `docs/features/phase-43-cross-language-translation.md` | Phase 43 architecture reference |
| `goals/modernization_workflow.md` | 12-step modernization workflow |
