# Phase 43 — Cross-Language Translation

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 43 |
| Title | Cross-Language Translation — LLM-Assisted Hybrid Pipeline |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 38 (Cloud-Agnostic Architecture), Phase 19 (App Modernization), Phase 17 (ATO Acceleration) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

DoD and government organizations face a persistent challenge in legacy software modernization: migrating codebases from one programming language to another while preserving functionality, compliance posture, and ATO status. The FY25-26 DoD software modernization plan emphasizes cATO, DevSecOps, and presumptive reciprocity — all of which require maintaining compliance continuity through language migrations. Manual translation is error-prone, expensive, and does not scale.

ICDEV already supports 6 first-class languages (Python, Java, Go, Rust, C#, TypeScript) with full toolchain coverage (scaffold, lint, format, SAST, dep audit, BDD, code gen). However, translating between these 30 directional language pairs requires more than simple LLM prompting. LLMs excel at translating small code snippets but struggle with project-level translation: maintaining consistent naming, resolving cross-file dependencies, mapping language-specific idioms, preserving type safety, and ensuring the translated output compiles and passes tests.

The GOTCHA principle — that LLMs are probabilistic while business logic must be deterministic — dictates a hybrid approach. Deterministic extraction parses source code into a language-agnostic Intermediate Representation (IR). Deterministic type-checking validates signature compatibility before translation. LLM-assisted translation handles the creative mapping between language idioms. Deterministic assembly scaffolds the target project. A validate-and-repair loop uses compiler feedback to fix translation errors. This 5-phase pipeline maximizes translation quality while maintaining auditability and compliance traceability.

---

## 2. Goals

1. Translate entire codebases between ICDEV's 6 supported languages (30 directional pairs) using a 5-phase hybrid pipeline
2. Preserve ATO compliance through the translation with 95% NIST 800-53 control coverage via the compliance bridge
3. Generate multiple translation candidates per unit (pass@k) and select the best, improving translation quality
4. Handle persistent translation failures gracefully via mock-and-continue, generating type-compatible stubs that allow dependent code to translate
5. Repair translation errors automatically via compiler-feedback loops (max 3 attempts per unit)
6. Translate test suites alongside production code, with framework-specific assertion mapping and BDD feature file preservation
7. Track all translation units, dependency mappings, and validation results in the database for traceability
8. Provide dashboard and SaaS portal visibility into translation job status, unit-level progress, and validation results

---

## 3. Architecture

```
Source Code (/path/to/source)
         │
    Phase 1: EXTRACT (deterministic)
    source_extractor.py → Language-Agnostic IR (JSON)
         │
    Phase 2: TYPE-CHECK (deterministic)
    type_checker.py → Signature compatibility validation
         │
    Phase 3: TRANSLATE (LLM-assisted)
    code_translator.py → Post-order dependency traversal
         │                  ├── pass@k candidates (D254)
         │                  ├── mock-and-continue (D256)
         │                  └── feature mapping rules (D247)
         │
    Phase 4: ASSEMBLE (deterministic)
    project_assembler.py → Target project scaffold
         │                   ├── pom.xml / go.mod / Cargo.toml
         │                   ├── CUI headers applied
         │                   └── README with provenance
         │
    Phase 5: VALIDATE + REPAIR (deterministic + LLM)
    translation_validator.py → 8-check validation
         │                       ├── Syntax (compiler)
         │                       ├── Lint (language linter)
         │                       ├── Round-trip IR (D248)
         │                       ├── API surface (>=90%)
         │                       ├── Type coverage (>=85%)
         │                       ├── Complexity (<=30% increase)
         │                       ├── Compliance (CUI markings)
         │                       └── Feature mapping (D247)
         │
    On failure: Compiler-feedback repair (D255, max 3 attempts)
         │
    Phase 6: COMPLIANCE BRIDGE (optional)
    compliance_bridge.py → NIST 800-53 control inheritance (95%)
```

The pipeline processes source code through 5 phases. Phase 1 extracts an IR using Python `ast` for Python and regex-based extractors for other languages. Phase 2 validates type-system compatibility before invoking the LLM. Phase 3 translates code units in post-order dependency graph traversal (leaf nodes first), generating k candidates per unit and selecting the best. Phase 4 assembles the target project with language-conventional structure. Phase 5 validates the output with 8 checks and feeds compiler errors back to the LLM for repair. An optional Phase 6 runs the compliance bridge for ATO preservation.

---

## 4. Requirements

### 4.1 Extraction

#### REQ-43-001: Language-Agnostic IR
The system SHALL extract source code into a JSON Intermediate Representation containing functions, classes, interfaces, enums, imports, idioms, concurrency patterns, and error handling constructs.

#### REQ-43-002: Dependency Graph
The system SHALL build a dependency graph at function/class granularity and translate in post-order (leaf nodes first) to ensure dependencies are resolved before dependents.

### 4.2 Translation

#### REQ-43-003: Pass@k Candidate Generation
The system SHALL generate k translation candidates per unit (default k=3 for cloud, k=1 for air-gapped) with varied prompts and select the best based on validation scores.

#### REQ-43-004: Mock-and-Continue
When a translation unit persistently fails after max repair attempts, the system SHALL generate a type-compatible mock/stub and continue translating dependent units.

#### REQ-43-005: Feature Mapping Rules
The system SHALL apply 3-part feature mapping rules (syntactic pattern, natural language description, static validation check) for language-specific idiom translation.

#### REQ-43-006: Non-Destructive Output
Translation output SHALL be written to a separate directory; source code SHALL never be modified.

### 4.3 Validation

#### REQ-43-007: 8-Check Validation Suite
The system SHALL validate translated output with: syntax check, lint, round-trip IR consistency, API surface match (>=90%), type coverage (>=85%), complexity analysis (<=30% increase), compliance (CUI markings), and feature mapping verification.

#### REQ-43-008: Compiler-Feedback Repair
On validation failure, the system SHALL feed compiler errors back to the LLM for targeted repair, with a maximum of 3 repair attempts per unit.

### 4.4 Compliance

#### REQ-43-009: Compliance Bridge
The system SHALL reuse the existing compliance bridge for NIST 800-53 control inheritance with a 95% coverage threshold, cascading to FedRAMP/CMMC/800-171 via the crosswalk engine.

#### REQ-43-010: CUI Marking Preservation
All translated files SHALL include CUI headers appropriate to the target language's comment syntax.

### 4.5 Test Translation

#### REQ-43-011: Test Suite Translation
The system SHALL translate test suites alongside production code with framework-specific assertion mapping (pytest to JUnit, behave to Cucumber-JVM, etc.).

#### REQ-43-012: BDD Feature Preservation
BDD `.feature` files SHALL be copied unchanged; only step definition implementations SHALL be translated.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `translation_jobs` | Job-level tracking — source/target language, project ID, status, phase progress, timestamps, validation summary |
| `translation_units` | Unit-level tracking — function/class name, translation status, candidate count, selected candidate, mock flag, repair attempts |
| `translation_dependency_mappings` | Cross-language dependency equivalents — source package, target package, mapping confidence, manual override flag |
| `translation_validations` | Validation results per unit — 8 check results, scores, repair history, final status |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/translation/translation_manager.py` | Full pipeline orchestrator — runs all 5 phases with configurable modes (dry-run, extract-only, translate-only, validate-only) |
| `tools/translation/source_extractor.py` | Phase 1 — parse source code into language-agnostic IR JSON |
| `tools/translation/type_checker.py` | Phase 2 — validate type-system compatibility between source and target |
| `tools/translation/code_translator.py` | Phase 3 — LLM-assisted chunk-based translation with pass@k and mock-and-continue |
| `tools/translation/project_assembler.py` | Phase 4 — scaffold target project with language conventions and CUI headers |
| `tools/translation/translation_validator.py` | Phase 5 — 8-check validation suite with compiler-feedback repair loop |
| `tools/translation/dependency_mapper.py` | Cross-language dependency lookup from declarative mapping tables |
| `tools/translation/test_translator.py` | Test suite translation with framework-specific assertion mapping |
| `tools/modernization/compliance_bridge.py` | Phase 6 — NIST 800-53 control inheritance for ATO preservation (reused) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D242 | Hybrid 5-phase pipeline (deterministic + LLM) | Consistent with GOTCHA: LLMs probabilistic, business logic deterministic |
| D243 | IR pivot — language-agnostic JSON IR | Enables chunk-based translation, round-trip validation, progress tracking |
| D244 | Post-order dependency graph traversal | Translate leaf nodes first ensures dependencies resolved before dependents |
| D245 | Non-destructive output (extends D18) | Source never modified; output to separate directory |
| D246 | Declarative dependency mapping tables (D26 pattern) | Cross-language package equivalents without code changes |
| D247 | 3-part feature mapping rules (Amazon Oxidizer) | Syntactic pattern + NL description + static validation per language pair |
| D248 | Round-trip IR consistency check | Re-parse translated output into IR, compare structurally to source IR |
| D249 | Translation compliance bridge reuses existing tool | 95% NIST 800-53 control coverage threshold; cascades via crosswalk |
| D250 | Test translation as separate tool | Framework-specific assertion mapping; BDD features preserved |
| D253 | Type-compatibility pre-check (Amazon Oxidizer) | Validate signatures before LLM translation; catch mismatches early |
| D254 | Pass@k candidate generation (Google) | Generate k candidates, select best; default k=3 cloud, k=1 air-gapped |
| D255 | Compiler-feedback repair loop (Google/CoTran) | Feed compiler errors to LLM for targeted repair; max 3 attempts |
| D256 | Mock-and-continue (Amazon Oxidizer) | Type-compatible stub on persistent failure; unblocks dependents |

---

## 8. Security Gate

**Translation Gate:**
- **Blocking:** Syntax errors in output, API surface below 90%, compliance coverage below 95%, secrets detected in translated code, CUI markings missing
- **Warning:** Round-trip similarity below 80%, type coverage below 85%, complexity increase over 30%, unmapped dependencies, stub functions present, lint issues

---

## 9. Commands

```bash
# Full pipeline
python tools/translation/translation_manager.py \
  --source-path /path/to/source --source-language python --target-language java \
  --output-dir /path/to/output --project-id "proj-123" --validate --json

# Dry run (no LLM calls)
python tools/translation/translation_manager.py \
  --source-path /path --source-language python --target-language java \
  --output-dir /path --project-id "proj-123" --dry-run --json

# Extract IR only
python tools/translation/source_extractor.py \
  --source-path /path --language python --output-ir ir.json --project-id "proj-123" --json

# Translate with pass@k candidates
python tools/translation/code_translator.py \
  --ir-file ir.json --source-language python --target-language go \
  --output-dir /path --candidates 3 --json

# Dependency lookup
python tools/translation/dependency_mapper.py \
  --source-language python --target-language go --imports "flask,requests" --json

# Translate tests
python tools/translation/test_translator.py \
  --source-test-dir /path/tests --source-language python --target-language java \
  --output-dir /path/output/tests --ir-file ir.json --json

# Configuration
# args/translation_config.yaml — 30 language pairs, extraction, translation, repair, validation thresholds
# context/translation/dependency_mappings.json — Cross-language package equivalents
```
