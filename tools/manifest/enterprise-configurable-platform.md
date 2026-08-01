.--- CUI // SP-CTI ---
# Enterprise-Configurable Platform

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Tools and configuration files that make ICDEV™ enterprise-ready via a
registry-driven, template-driven architecture.

## Configuration sources
| File | Description |
|------|-------------|
| `args/component_registry.yaml` | Single source of truth for canvases, child apps, features, and core extensions. |
| `args/core_profiles.yaml` | Enterprise deployment presets: `local-dev`, `air-gap`, `saas-il4`, `il6-secret`. |

## Registry & profile loaders
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Component Registry | `tools/config/component_registry.py` | Loads `args/component_registry.yaml`; provides blueprint resolution, toggle maps, IQE dispatch mapping, nav tree, tenant overrides, completeness validation, and component audit logging. | `get_registry()`, `ComponentRegistry` | Component objects, nav context, IQE map, tenant overrides |
| Core Profile | `tools/config/core_profile.py` | Loads `args/core_profiles.yaml`; provides env-var-aware defaults for storage backend, LLM provider, classification, and air-gap flags. | `load_profiles()`, `get_profile(name)`, `apply_active_profile_env_defaults()` | Profile dict; applied env defaults |

## Template engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Template Engine | `tools/builder/template_engine.py` | Jinja2 file-tree generator for canvases and child apps. | `render_tree(template_dir, out_dir, variables)` | Rendered files + validation results |
| Canvas Template - Minimal | `data/templates/canvases/minimal/` | Baseline canvas skeleton (`__init__.py`, `blueprint.py`, `constants.py`, `page.html`). | `icdev scaffold canvas` | New canvas tree |
| Canvas Template - Info Ops | `data/templates/canvases/info_ops/` | Real-canvas template proving the engine on an existing codebase. | `icdev scaffold canvas --template info_ops` | New canvas tree |
| Child-App Flavor - Minimal | `data/templates/child_apps/minimal/` | Baseline FORGE-compliant child app. | `icdev scaffold child-app --flavor minimal` | Child app tree |
| Child-App Flavor - Compliance | `data/templates/child_apps/compliance/` | Compliance-focused overlay. | `--flavor compliance` | Child app tree |
| Child-App Flavor - AI Lab | `data/templates/child_apps/ai-lab/` | LLM/RAG/experiment overlay. | `--flavor ai-lab` | Child app tree |
| Child-App Flavor - GovCon | `data/templates/child_apps/govcon/` | GovCon capture-to-delivery overlay. | `--flavor govcon` | Child app tree |

## CLI wrappers
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Enable/Disable | `tools/cli/enable.py` | `icdev enable/disable/status/list` derived from registry env flags. | toggle names, `--env-file`, `--json` | Updated `.env` + status JSON |
| Setup TUI | `tools/cli/setup.py` | `icdev setup` — stdlib-only interactive feature-toggle TUI (primary browser-free on/off surface). Registry-driven; groups by kind with live counts, shows env_flag + sub-pages, arrow/SPACE/p/w/q; degrades to a plain numbered menu when not a TTY. Writes `.env` + `log_component_audit` per change. | `--env-file`, `--plain`, `--json` | Updated `.env` + audit events |
| Profile CLI | `tools/cli/profile.py` | `icdev profile list/show/apply` for core profiles. | profile name, `--env-file`, `--dry-run` | Written/previewed env overrides |
| Scaffold CLI | `tools/cli/scaffold.py` | `icdev scaffold canvas` and `icdev scaffold child-app`. | key, `--template`, `--flavor`, `--out` | Scaffolded tree |
| Backfill Manifests | `tools/cli/backfill_manifests.py` | Creates `data/templates/canvases/{key}/manifest.yaml` stubs for all registered canvases that lack one. Makes existing canvases discoverable and diff-able against the template baseline. | `[--dry-run]` `[--key <canvas-key>]` `[--json]` | Created/skipped stubs per canvas |
| CLI Dispatcher | `tools/cli/__main__.py` | Routes `icdev` subcommands to the module-level scripts above. | subcommand + args | stdout / JSON |

## Runtime integration
| Tool | File | Description |
|------|------|-------------|
| Dashboard registration | `tools/dashboard/app.py` | Registers enabled canvases from registry; injects `nav_tree`; attaches RBAC `before_request` guards. |
| Dashboard config | `tools/dashboard/config.py` | Applies active core profile defaults before env vars. |
| Storage backend | `tools/db/storage.py` | Selects backend using profile default (env overrides). |
| LLM router | `tools/llm/router.py` | Uses profile provider default when not overridden. |
| Canvas access / RBAC | `tools/security/canvas_access.py` | `guard_component_access()` enforces `min_il` and explicit grants. |
| Oracle verifiers | `tools/foundry/oracle_verifiers.py` | Derives registered routes from registry instead of parsing source text. |
| Admin Console Blueprint | `tools/admin/blueprint.py` | Flask blueprint for the admin console (`/admin/`). Provides tenant component override CRUD, component audit log viewer, and canvas access grant log. Gated by `ICDEV_ADMIN_CONSOLE_ENABLED=true` and admin role. |

## Database artifacts
| Migration | File | Description |
|-----------|------|-------------|
| 207 | `tools/db/migrations/207_tenant_component_overrides/` | Per-tenant canvas/feature enablement overrides. |
| 208 | `tools/db/migrations/208_component_audit_log/` | Append-only log of enable/disable/profile/override events. |

## Tests
| Test | File | Coverage |
|------|------|----------|
| Component Registry | `tests/test_component_registry.py` | Parity with legacy defs, nav context, tenant overrides, audit logging, completeness gate. |
| Core Profiles | `tests/test_core_profile.py` | Profile loading, air-gap detection, env override precedence. |
| Template Engine | `tests/test_template_engine.py` | Manifest loading, variable resolution, full tree render, flavor rendering, forge validator gate. |
