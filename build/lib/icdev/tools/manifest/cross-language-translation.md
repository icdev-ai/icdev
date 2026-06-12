# Cross-Language Translation (Phase 43 — D242-D256)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Cross-Language Translation (Phase 43 — D242-D256)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Source Extractor | tools/translation/source_extractor.py | Phase 1: AST/regex → language-agnostic IR (JSON). Per-language extractors (Python AST, Java/Go/Rust/C#/TS regex). Detects concurrency, error handling, idioms, framework annotations | --source-path, --language, --output-ir, --project-id, --json | IR JSON |
| Type Checker | tools/translation/type_checker.py | Phase 2: Validate type-compatibility of function signatures between source/target type systems (D253, Amazon Oxidizer) | --ir-file, --source-language, --target-language, --json | Compatibility report |
| Code Translator | tools/translation/code_translator.py | Phase 3: LLM-assisted chunk translation with feature mapping rules (D247), pass@k candidates (D254). Post-order dependency traversal (D244). Mock-and-continue on failure (D256) | --ir-file, --source-language, --target-language, --output-dir, --candidates, --json | Translated units JSON |
| Project Assembler | tools/translation/project_assembler.py | Phase 4: Scaffold target project (pom.xml/go.mod/Cargo.toml/etc.), write translated files, apply CUI headers, generate build file | --translated-file, --source-language, --target-language, --output-dir, --json | Project files |
| Translation Validator | tools/translation/translation_validator.py | Phase 5: 8-check validation (syntax, lint, round-trip IR, API surface, type coverage, complexity, compliance, feature mapping). Compiler-feedback repair loop (D255) | --ir-file, --translated-file, --source-language, --target-language, --json | Validation report |
| Translation Manager | tools/translation/translation_manager.py | Full pipeline orchestrator. Supports --extract-only, --translate-only, --validate-only, --dry-run, --compliance-bridge, --candidates k | --source-path, --source-language, --target-language, --output-dir, --project-id, --json | Pipeline result |
| Test Translator | tools/translation/test_translator.py | Translate test files between frameworks (pytest↔JUnit↔testing↔cargo_test↔xUnit↔Jest). BDD .feature files preserved; step definitions translated (D250) | --source-test-dir, --source-language, --target-language, --output-dir, --ir-file, --json | Translated tests |
| Dependency Mapper | tools/translation/dependency_mapper.py | Map cross-language package equivalents from declarative JSON table (D246). LLM suggestion for unknowns (advisory only) | --source-language, --target-language, --imports, --json | Mapped dependencies |
| Feature Map Loader | tools/translation/feature_map.py | Load and apply 3-part feature mapping rules (D247): syntactic pattern → NL description → static validation | (library) | Feature rules |

