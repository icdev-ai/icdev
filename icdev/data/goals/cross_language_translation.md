# [TEMPLATE: CUI // SP-CTI]
# Cross-Language Translation — Phase 43

> LLM-assisted cross-language code translation across all 30 directional pairs of 6 first-class languages, with configurable model selection (including local/air-gapped), full ATO compliance preservation, and Dashboard + SaaS Portal visibility.

## Purpose

Translate entire codebases between ICDEV's 6 supported languages (Python, Java, JavaScript/TypeScript, Go, Rust, C#) using a 5-phase hybrid pipeline that combines deterministic extraction/assembly with LLM-assisted translation. Preserves ATO compliance (95% NIST 800-53 control coverage), CUI markings, and audit trail throughout.

## When to Use

- Customer modernizing legacy systems (e.g., Python 2 backend → Go microservice, Java monolith → Rust)
- Cross-language migration as part of 7R "Rearchitect" strategy
- DoD software modernization per FY25-26 plan (cATO, DevSecOps, presumptive reciprocity)
- Test suite translation alongside production code

## Prerequisites

1. Source code accessible at a local path
2. Source and target language identified (must be different, both in VALID_LANGUAGES)
3. LLM provider configured in `args/llm_config.yaml` (or Ollama for air-gapped)
4. ICDEV database initialized (`python tools/db/init_icdev_db.py`)

## Workflow (7 Steps)

### Step 1 — Extract (Deterministic)
**Tool:** `tools/translation/source_extractor.py`

Parse source code into language-agnostic Intermediate Representation (IR) as JSON. Uses Python `ast` module for Python, regex-based extractors for Java/Go/Rust/C#/TypeScript. Detects functions, classes, interfaces, enums, imports, idioms, concurrency patterns, error handling.

```bash
python tools/translation/source_extractor.py \
  --source-path /path/to/source \
  --language python \
  --output-ir source_ir.json \
  --project-id "proj-123" --json
```

### Step 2 — Type-Check (Deterministic)
**Tool:** `tools/translation/type_checker.py`

Validate type-compatibility of function signatures between source/target type systems BEFORE LLM translation (D253, adopted from Amazon Oxidizer). Catches nullable/non-nullable mismatches, generic type parameter differences, trait/interface incompatibilities early.

### Step 3 — Translate (LLM-Assisted)
**Tool:** `tools/translation/code_translator.py`

Chunk-based LLM translation with feature mapping rules (D247), pass@k candidate generation (D254). Walks dependency graph post-order (leaf-first, D244). Mock-and-continue on persistent failure (D256).

```bash
python tools/translation/code_translator.py \
  --ir-file source_ir.json \
  --source-language python \
  --target-language java \
  --output-dir /path/to/output \
  --project-id "proj-123" --candidates 3 --json
```

### Step 4 — Assemble (Deterministic)
**Tool:** `tools/translation/project_assembler.py`

Scaffold target project (pom.xml/go.mod/Cargo.toml/etc.), write translated files to language-conventional paths, apply CUI headers, generate README with provenance.

### Step 5 — Validate + Repair (Deterministic + LLM)
**Tool:** `tools/translation/translation_validator.py`

8-check validation suite:
1. Syntax check (compiler/interpreter)
2. Lint (language-specific linter)
3. Round-trip IR consistency (D248)
4. API surface match (≥90%)
5. Type coverage (≥85%)
6. Complexity analysis (≤30% increase)
7. Compliance (CUI markings present)
8. Feature mapping (D247 rules applied)

On failure, feeds compiler errors back to LLM for targeted repair (D255, max 3 attempts).

### Step 6 — Compliance Bridge (Optional)
**Tool:** `tools/modernization/compliance_bridge.py`

Reuses existing compliance bridge for NIST 800-53 control inheritance. 95% coverage threshold applies. Cascades to FedRAMP/CMMC/800-171 via crosswalk engine.

### Step 7 — Dashboard Review
Navigate to `/translations` in Dashboard or `/portal/translations` in SaaS Portal. Review job status, validation results, unit-level translation status, dependency mappings.

## Full Pipeline (One Command)

```bash
python tools/translation/translation_manager.py \
  --source-path /path/to/source \
  --source-language python \
  --target-language java \
  --output-dir /path/to/output \
  --project-id "proj-123" \
  --validate --json
```

### Pipeline Modes

| Flag | Behavior |
|------|----------|
| `--dry-run` | Extract IR + type-check only, no LLM calls |
| `--extract-only` | Phase 1 only, output IR file |
| `--translate-only` | Skip validation |
| `--validate-only` | Run Phase 5 on existing translated code |
| `--translate-tests` | Include test file translation (D250) |
| `--compliance-bridge` | Run compliance bridge after validation |
| `--candidates k` | Generate k translation candidates (pass@k, D254) |

## Security Gates

| Gate | Condition | Type |
|------|-----------|------|
| Syntax errors in output | Any syntax/compile error | Blocking |
| API surface below 90% | Less than 90% of public APIs preserved | Blocking |
| Compliance coverage below 95% | Less than 95% NIST control inheritance | Blocking |
| Secrets detected | Any secrets in translated code | Blocking |
| CUI markings missing | Any file without CUI header | Blocking |
| Round-trip similarity low | Below 80% structural match | Warning |
| Type coverage low | Below 85% type mapping coverage | Warning |
| Complexity increase high | Over 30% LOC increase | Warning |
| Unmapped dependencies | Dependencies without target equivalents | Warning |
| Stub functions present | Mock-and-continue units in output | Warning |
| Lint issues | Linter warnings in translated code | Warning |

## Architecture Decisions

- **D242:** Hybrid 5-phase pipeline — deterministic extraction + type-checking + LLM translation + deterministic assembly + validate-repair loop
- **D243:** IR pivot — source code extracted into language-agnostic JSON IR before translation
- **D244:** Post-order dependency graph traversal at function/class granularity — translate leaf nodes first
- **D245:** Non-destructive output (extends D18) — translation output to separate directory
- **D246:** Declarative dependency mapping tables (D26 pattern) — `context/translation/dependency_mappings.json`
- **D247:** 3-part feature mapping rules (Amazon Oxidizer) — syntactic pattern + NL description + static validation check
- **D248:** Round-trip IR consistency check — re-parse translated output, compare to source IR
- **D249:** Translation compliance bridge — reuses `compliance_bridge.py` for NIST 800-53 control inheritance
- **D250:** Test translation as separate tool — `test_translator.py` with framework-specific assertion mapping
- **D251:** Translation DB tables follow existing `migration_plans`/`migration_tasks` pattern
- **D252:** Dashboard/Portal pages follow existing page patterns (stat-grid, table-container, charts.js)
- **D253:** Type-compatibility pre-check (Amazon Oxidizer) — validate function signatures before LLM translation
- **D254:** Pass@k candidate generation (Google) — generate k translations, select best
- **D255:** Compiler-feedback repair loop (Google/CoTran) — max 3 repair attempts per unit
- **D256:** Mock-and-continue (Amazon Oxidizer) — generate type-compatible stub on persistent failure

## Edge Cases

- **Empty source directory** — Return error with helpful message
- **Single file project** — Works fine, creates minimal project scaffold
- **Circular dependencies** — DFS handles cycles via visited set
- **Very large functions (>500 lines)** — Split at method boundaries for classes
- **No LLM available** — `--dry-run` mode works without LLM for IR extraction + type-checking
- **All units fail translation** — Job marked as `failed`, all units mocked, dashboard shows full failure state
- **Mixed language source** — Only files matching `--source-language` are extracted
- **BDD test files** — `.feature` files copied unchanged; only step definitions translated

## Cross-Phase Integration

| Phase | Integration |
|-------|-------------|
| Phase 13-16 (RICOAS) | Intake engine detects translation requirements; gap detector flags missing target language expertise |
| Phase 15 (Simulation) | "What if we translate X to Y?" simulation scenario |
| Phase 17 (Compliance) | Crosswalk engine cascades translation compliance to FedRAMP/CMMC/800-171 |
| Phase 18 (MBSE) | `translates_to` digital thread link type |
| Phase 19 (Modernization) | Translation = "Rearchitect" in 7R taxonomy; compliance bridge reused |
| Phase 22 (Marketplace) | Feature mapping rules publishable as marketplace assets |
| Phase 24-25 (DevSecOps/ZTA) | SAST + secret detection on translated output |
| Phase 29 (Monitoring) | Translation jobs emit heartbeat events |
| Phase 35 (Innovation) | Translation metrics feed innovation engine |
| Phase 36 (Evolutionary) | Translation capability tracked as genome dimension |
| Phase 37 (ATLAS) | Prompt injection scanning on translation prompts |
| Phase 38 (Cloud) | Multi-cloud LLM selection via router |
| Phase 39 (Observability) | Translation events logged to audit trail |
