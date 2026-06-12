# Builder (TDD)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Builder (TDD)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Code Generator [DEPRECATED] | tools/builder/code_generator.py | Generate code from specifications (Python, Java, Go, Rust, C#, TypeScript) | --project, --spec, --language | Generated file paths |
| Test Writer [DEPRECATED] | tools/builder/test_writer.py | Generate BDD tests — Gherkin + language-specific step defs (6 languages) | --project, --requirement, --language | Feature file paths |
| Scaffolder | tools/builder/scaffolder.py | Project scaffolding from templates (6 languages) | --project, --type | Directory tree |
| Scaffolder Extended | tools/builder/scaffolder_extended.py | Java, Go, Rust, C#, TypeScript scaffold functions | (imported by scaffolder.py) | — |
| Language Support | tools/builder/language_support.py | Unified language registry, detection, CUI headers, dep file finder | --detect, --list, --profile | Language profiles |
| Linter | tools/builder/linter.py | Multi-language linting (flake8, eslint, checkstyle, golangci-lint, clippy, dotnet) | --project, --fix | Lint report |
| Formatter | tools/builder/formatter.py | Multi-language formatting (black, prettier, gofmt, rustfmt, dotnet-format) | --project | Formatted files |
| Agentic Fitness | tools/builder/agentic_fitness.py | Assess component fitness for agentic architecture (6-dimension scoring) | --spec, --project-id, --json | Fitness scorecard |
| App Blueprint | tools/builder/app_blueprint.py | Generate deployment blueprint from fitness scorecard | --fitness-scorecard, --user-decisions, --app-name, --json | Blueprint JSON |
| Framework Detector | tools/builder/framework_detector.py | Zero-config language/framework detection from source — pre-populates FORGE blueprint (language, framework, deploy_template with Terraform+STIG baselines+CUI markings, capabilities) | --source-path, --blueprint, --merge, --gate, --json | Detection result + optional merged blueprint |
| Child App Generator | tools/builder/child_app_generator.py | Generate mini-ICDEV™ clone child applications (16-step pipeline); supports --source-path --auto-detect for zero-config build detection | --blueprint, --project-path, --name, --source-path, --auto-detect, --json | Generated app path |
| Claude MD Generator | tools/builder/claude_md_generator.py | Generate dynamic CLAUDE.md for child apps (Jinja2) | --blueprint, --output, --json | CLAUDE.md path |
| Goal Adapter | tools/builder/goal_adapter.py | Copy and adapt ICDEV™ goals for child applications | --source-goals, --output, --app-name, --json | Adapted goal paths |
| DB Init Generator | tools/builder/db_init_generator.py | Generate standalone DB init scripts for child apps | --blueprint, --output, --app-name, --json | DB init script path |
| Dev Profile Manager | tools/builder/dev_profile_manager.py | 5-layer cascade dev profiles (Platform→Tenant→Program→Project→User) with version immutability, role-based locks, LLM injection (D183-D188) | --scope, --scope-id, --create, --get, --update, --resolve, --lock, --inject, --diff, --rollback, --json | Profile + cascade |
| Profile Detector | tools/builder/profile_detector.py | Auto-detect dev profile from repo analysis or natural language text (D185 advisory-only) | --repo-path, --text, --json | Detected dimensions |
| Profile MD Generator | tools/builder/profile_md_generator.py | Generate PROFILE.md from resolved dev profile via Jinja2 (D186) | --scope, --scope-id, --output, --store, --json | PROFILE.md path |
| FORGE Validator | tools/builder/forge_validator.py | Validate FORGE framework compliance for child apps (6 layers + 4 meta checks) | --project-dir, --json, --human, --gate | Validation report |
| Agentic Test: A2A Callback | tools/builder/agentic_test_templates/test_a2a_callback.py | Template test for A2A callback verification in child apps | (pytest template) | Test results |
| Agentic Test: Agent Health | tools/builder/agentic_test_templates/test_agent_health.py | Template test for agent health endpoint verification in child apps | (pytest template) | Test results |

