# Plan: Make ICDEV™ Enterprise-Ready via Configurable Canvas / Child-App / Core Templates

## 1. Goal

Transform ICDEV from a monolithic, code-first Flask dashboard with hardcoded canvas/app registration into an **enterprise-configurable platform** where:

- Every **canvas** is declared and registered from a template / registry entry, not from inline Python lists.
- Every **child application** is generated from the same template library, not from a copy-and-adapt script.
- The **core** runtime (DB, LLM, auth, workflow, compliance) is selected via profiles/flavors, not hardcoded branches.

This makes ICDEV multi-tenant-aware, air-gap friendly, deployment-profile driven, and maintainable at scale.

## 2. Current State (from codebase analysis)

### 2.1 Hardcoded registration (single points of truth missing)

| Concern | Current Location | Problem |
|---|---|---|
| Canvas blueprints | `tools/dashboard/app.py:142-171` `_CANVAS_DEFS` | Hardcoded `(key, env_flag, module, attr)` tuples. |
| Canvas URL prefixes | `tools/dashboard/app.py:2183-2204` `_CANVAS_ROUTES` | Hardcoded route map; must be kept in sync with blueprints. |
| App modules | `tools/dashboard/app.py:199-204` `_APP_DEFS` | Hardcoded child-app registration. |
| IQE dispatch map | `tools/dashboard/app.py:3416-3458` `_CANVAS_MAP` | Hardcoded per-canvas adapter + collections. |
| CLI toggles | `tools/cli/enable.py:32-53` `TOGGLES` | Duplicates env-flag knowledge from `_CANVAS_DEFS`. |
| Awareness enablement | `args/awareness_enablement_map.yaml` | Duplicates path-glob ↔ flag mappings again. |
| Navigation links | `tools/dashboard/templates/base.html:211-392` | Hardcoded canvas menu; feature flags injected manually. |
| Canvas metadata | `args/canvas_registry.yaml` | Has name/display_name/IL/roles, but is **not** used for registration. |

### 2.2 Child-app generation

- `tools/builder/child_app_generator.py` uses **copy-and-adapt** (`DIRECTORY_TREE`, `CONDITIONAL_DIRS`, string replacements). It is not template-driven.
- `tools/builder/forge_validator.py` checks FORGE layers but does not validate against a canvas/app template schema.

### 2.3 Core runtime

- `tools/dashboard/config.py`, `tools/db/storage.py`, `tools/llm/router.py` load settings from YAML/env, but there is no "core profile" concept that selects an entire runtime flavor (SaaS, air-gap, IL6, edge).

### 2.4 Enterprise gaps

- No single configuration surface for tenants/deployment profiles.
- Navigation, feature flags, and route registration drift apart.
- Adding a canvas requires touching ≥6 files (app.py, base.html, enable.py, iqe adapter, awareness map, CLI toggle list).
- No schema validation for what a "complete canvas" must contain (the 8-point CLAUDE.md gate is manual).

## 3. Interpretations (Karpathy principle #2)

"Enterprise ready + templatize" can be read several ways. The plan below assumes:

1. **Configuration-first, not generation-only**: we want runtime registration from config, not just code-generation templates.
2. **Template = contract**: a canvas template describes the 8 required components (routes, module, constants, DB migration, nav link, IQE adapter, seed queries, path mapping) and is validated by a gate.
3. **Child-app factory**: child apps are produced by composing core templates + selected canvas templates, not by copying the whole parent repo.
4. **Core profiles**: DB/LLM/auth/compliance are selected from named profiles in `args/core_profiles.yaml`.
5. **Backward compatibility**: existing canvases keep working while being migrated into the registry; no big-bang rewrite.

If you want a different emphasis (e.g., multi-tenant SaaS first, or air-gap packaging first), the phase order can change.

## 4. Proposed Architecture

### 4.1 Layer: Unified Component Registry

Create `args/component_registry.yaml` (or extend `args/canvas_registry.yaml`) as the single source of truth for every canvas, child app, and core extension.

```yaml
components:
  - key: dic
    kind: canvas
    display_name: "Document Intelligence Canvas"
    env_flag: ICDEV_DIC_ENABLED
    default_enabled: false
    module: tools.document_intelligence.blueprint
    blueprint_attr: dic_bp
    url_prefix: /document-intelligence
    template_dir: tools/document_intelligence
    min_il: IL4
    default_roles: [researcher, intelligence_analyst, writer]
    nav:
      section: Canvases
      label: Document Intelligence
      links:
        - label: DIC Overview
          href: /document-intelligence/
          style: color:#7ab3f0;
        - label: Collections
          href: /document-intelligence/collections
    iqe:
      adapter_module: tools.iqe.adapters.dic
      collections: [dic.drift_events, dic.regen_queue, dic.ssp_fragments]
    dependencies: []
    completeness:
      template: tools/dashboard/templates/document_intelligence/page.html
      blueprint: true
      constants: tools/document_intelligence/constants.py
      db_migration: tools/document_intelligence/db/migrations
      iqe_adapter: true
      seed_queries: context/iqe/queries/document_intelligence/

  - key: forge_academy
    kind: child_app
    display_name: "FORGE Academy"
    env_flag: ICDEV_FORGE_ACADEMY_ENABLED
    module: apps.forge_academy.blueprint
    blueprint_attr: academy_bp
    template_package: data/templates/child_apps/forge_academy
    # ...
```

### 4.2 Layer: Registry Loader (`tools/config/component_registry.py`)

- Loads `args/component_registry.yaml` once at startup.
- Provides:
  - `get_canvases()`, `get_child_apps()`, `get_core_profiles()`
  - `is_enabled(key)` — reads env flag + default.
  - `get_blueprint(key)` — dynamic import + factory call.
  - `get_iqe_mapping()` — returns the `{canvas: (adapter, collections)}` dict.
  - `get_nav_tree()` — returns navigation sections/links filtered by enabled components.

### 4.3 Layer: Template Engine (`tools/builder/template_engine.py`)

A deterministic engine that materializes a component from a template:

- **Canvas templates** live in `data/templates/canvases/<key>/`:
  - `blueprint.py.j2`, `constants.py.j2`, `page.html.j2`, `migration.sql.j2`, `iqe_adapter.py.j2`, `seed_queries.yaml`, `manifest.yaml`.
- **Child-app templates** live in `data/templates/child_apps/<flavor>/`:
  - `CLAUDE.md.j2`, `.env.template.j2`, `goals/manifest.md.j2`, selected canvas list, core profile.
- **Core profile templates** live in `data/templates/core_profiles/<profile>/`:
  - `db.yaml`, `llm.yaml`, `auth.yaml`, `compliance.yaml`.

Engine operations:
- `scaffold_canvas(key, target_dir, variables)` — generate a new canvas from template.
- `scaffold_child_app(name, flavor, selected_canvases, core_profile, target_dir)` — generate a child app.
- `apply_core_profile(profile_name)` — validate and overlay profile config onto `args/`.
- `validate_component(key)` — run the 8-point completeness gate.

### 4.4 Layer: Refactored Dashboard Registration

Replace inline lists in `tools/dashboard/app.py` with:

```python
from tools.config.component_registry import ComponentRegistry
_registry = ComponentRegistry()

# Design canvases + child apps + core extensions register uniformly
for comp in _registry.iter_enabled():
    bp = comp.get_blueprint()
    app.register_blueprint(bp, url_prefix=comp.url_prefix)

# Navigation injected from registry
@app.context_processor
def inject_nav():
    return _registry.get_nav_context()

# IQE dispatch uses registry mapping
@app.route("/api/iqe/dispatch", methods=["POST"])
def iqe_dispatch():
    canvas_map = _registry.get_iqe_mapping()
    # ... existing logic, now data-driven
```

### 4.5 Layer: Template-driven CLI

`tools/cli/enable.py` derives `TOGGLES` from `ComponentRegistry` instead of a hardcoded dict:

```python
_registry = ComponentRegistry()
TOGGLES = {c.key: [c.env_flag] for c in _registry.iter_all() if c.env_flag}
DESCRIPTIONS = {c.key: c.display_name for c in _registry.iter_all()}
```

Add:
- `icdev scaffold canvas <key> --target-dir ...`
- `icdev scaffold child-app <name> --flavor <flavor> --canvases ...`
- `icdev apply-profile <profile>`
- `icdev validate component <key>`
- `icdev list --kind canvas|child_app|core_profile`

### 4.6 Layer: Enterprise Profiles

Create `args/core_profiles.yaml`:

```yaml
profiles:
  local-dev:
    storage_backend: sqlite
    llm_provider: openai
    auth: local
    classification_default: CUI
    airgap: false

  air-gap:
    storage_backend: sqlite
    llm_provider: ollama
    auth: local
    classification_default: CUI
    airgap: true
    disabled_canvases: [govcon, research, pulse, genesis]

  saas-il4:
    storage_backend: postgresql
    llm_provider: bedrock
    auth: sso
    classification_default: CUI
    tenant_rls: true

  il6-secret:
    storage_backend: postgresql
    llm_provider: air_gap_local
    auth: sso_pki
    classification_default: SECRET
    tenant_rls: true
    sipr_only: true
```

`tools/dashboard/config.py` and `tools/db/storage.py` load the active profile via `ICDEV_CORE_PROFILE` and apply the nested settings.

### 4.7 Layer: Coherence & Validation Gates

Extend `tools/workflow/coherence_checker.py` with:

- `check_component_registry()` — registry is loadable, every referenced module exists, every template file exists.
- `check_canvas_completeness(key)` — enforce the 8-point gate from config.
- `check_nav_sync()` — navigation links in base.html match registry nav entries.
- `check_iqe_map_sync()` — iqe_dispatch map matches registry.
- `check_toggle_sync()` — CLI toggle dict matches registry env flags.

## 5. Phased Implementation

### Phase 0: Foundation & Safety (1-2 days) ✅ COMPLETED

1. Create `args/component_registry.yaml` from existing `_CANVAS_DEFS`, `_APP_DEFS`, `_CANVAS_ROUTES`, and `_CANVAS_MAP`.
2. Create `tools/config/component_registry.py` loader with 100% backward-compatible API.
3. Add unit tests that assert the loader reproduces today's hardcoded lists exactly.
4. **Do not change app.py yet.** Only add the new files + tests.
5. Run `pytest tests/` and `ruff check .`.

### Phase 1: Dashboard Registration from Registry (2-3 days) ✅ COMPLETED

1. Refactor `tools/dashboard/app.py`:
   - Replace `_CANVAS_DEFS` loop with `ComponentRegistry` iteration.
   - Replace `_APP_DEFS` loop with registry iteration.
   - Replace `_CANVAS_ROUTES` with registry `url_prefix`.
   - Keep GovCon isolated registration as-is (it is opt-in and parent-only).
2. Refactor `iqe_dispatch()` to build `_CANVAS_MAP` from registry.
3. Add `nav_tree` and `component_registry` to the existing `inject_cui` context processor so templates can build nav from the registry.
4. Update `tools/cli/enable.py` to derive toggles from registry.
5. Kept `args/awareness_enablement_map.yaml` as-is for now; removing duplicates requires first teaching `tools/awareness/enablement.py` to fall back to the registry.
6. Updated `tools/foundry/oracle_verifiers.py` to derive registered routes from the registry instead of parsing `_CANVAS_DEFS` source text.
7. Added `tools/config/component_registry.py` to `tools/manifest/dashboard.md`.
8. Test: dashboard starts, all previously enabled canvases register at the same URLs; `pytest tests/test_component_registry.py tests/cli/test_icdev_enable.py` passes.

### Phase 2: Template Engine for Canvases (3-4 days) ✅ COMPLETED

1. ✅ Design YAML schema for canvas templates (`data/templates/canvases/<name>/manifest.yaml`).
2. ✅ Create `tools/builder/template_engine.py` with:
   - Jinja2-based file generation.
   - Variable substitution (`{{ key }}`, `{{ display_name }}`, `{{ module_path }}`) with nested defaults.
   - file_exists and python_syntax validators (8-point completeness validator in follow-up).
3. ✅ Create `data/templates/canvases/minimal/` as the first template — generates `__init__.py`, `blueprint.py`, `constants.py`, and `page.html`.
4. ✅ Add `icdev scaffold canvas` subcommand via `tools/cli/scaffold.py`.
5. ✅ Add `tests/test_template_engine.py` covering manifest loading, variable resolution, full tree render, and skip-existing.
6. ✅ Add `icdev profile` CLI (`tools/cli/profile.py`) with `list`, `show`, `apply`.
7. ✅ Add `tests/test_core_profile.py` covering profile loading, air-gap detection, cloud allowance, and env override precedence.
8. ✅ Convert one existing small canvas (`info_ops`) into the new template format to prove the engine on a real codebase.
9. ✅ Add tests for generated canvas passing `forge_validator.py --gate`.
10. ✅ Add 8-point completeness validator integration.

### Phase 3: Child-App Factory (3-4 days) ✅ COMPLETED

1. ✅ Built `data/templates/child_apps/<flavor>/` skeletons:
   - `minimal` — full FORGE-compliant child-app baseline.
   - `compliance` — overlays `args/security_gates.yaml` and a compliance-focused blueprint.
   - `ai-lab` — overlays `args/llm_config.yaml` and an LLM/RAG/experiment blueprint.
   - `govcon` — overlays a GovCon capture-to-delivery blueprint.
   - All flavors render independently and score ≥0.929 on `forge_validator` (excluding coherence).
2. ✅ Refactored `tools/builder/child_app_generator.py` to compose child apps from template flavors via `template_engine.py`.
   - Default path renders the selected flavor over the legacy-generated baseline.
   - `--legacy` flag keeps the original copy-and-adapt path untouched.
   - `--template` and `--flavor` CLI args select the template source.
3. ✅ Added tests in `tests/test_template_engine.py` covering flavor rendering, `_resolve_template_dir`, `_build_template_variables`, and `_overlay_template`.
4. ⬜ Update `forge_validator.py` with explicit child-app template schema checks (deferred to Phase 6 hardening).
5. ⬜ `icdev scaffold child-app` subcommand already scaffolded in `tools/cli/scaffold.py`; wire to generator in Phase 6.

### Phase 4: Core Profiles (2-3 days) ✅ COMPLETED

1. ✅ Create `args/core_profiles.yaml`.
2. ✅ Create `tools/config/core_profile.py` loader.
3. ✅ Add `icdev profile` CLI and `tests/test_core_profile.py`.
4. ✅ Refactor `tools/dashboard/config.py` to apply active profile defaults.
5. ✅ Refactor `tools/db/storage.py` backend selection to use profile (with env overrides).
6. ✅ Refactor `tools/llm/router.py` to use profile provider defaults.
7. ✅ Add tests for profile-driven router defaults (`tests/test_llm_router_profile.py`).
8. ⬜ Add profile validation to coherence checker (Phase 6).

### Phase 5: Navigation Templatization & RBAC (2-3 days)

1. Replace hardcoded `base.html` canvas menu with a loop over `nav_tree` from registry.
2. Add per-canvas `min_il` and `default_roles` enforcement using existing `require_role` / `canvas_access` decorators.
3. Add `tenant_features` concept so enterprise tenants can enable/disable canvases via DB flag in addition to env flag.
4. Add audit logging for enable/disable/profile changes.

### Phase 6: Enterprise Hardening & Documentation (2-3 days)

1. Add `docs/features/enterprise-configurable-platform.md`.
2. Update `CLAUDE.md` with the new architecture and CLI commands.
3. Update `tools/manifest.md` / relevant shards.
4. Run full test matrix: unit, e2e, air-gap simulation, child-app generation, forge_validator gate.
5. Add migration guide for existing canvas authors.

## 6. Files to Create / Modify

### New files

- `args/component_registry.yaml`
- `args/core_profiles.yaml`
- `tools/config/component_registry.py`
- `tools/config/core_profile.py`
- `tools/builder/template_engine.py`
- `tools/builder/canvas_template_schema.yaml`
- `tools/builder/child_app_template_schema.yaml`
- `data/templates/canvases/_base_/` (base canvas template)
- `data/templates/child_apps/minimal/`
- `tests/test_component_registry.py`
- `tests/test_template_engine.py`
- `tests/test_core_profiles.py`
- `docs/features/enterprise-configurable-platform.md`

### Modified files

- `tools/dashboard/app.py` (registration, iqe_dispatch, nav context)
- `tools/dashboard/templates/base.html` (nav loop)
- `tools/cli/enable.py` (derive toggles)
- `tools/cli/__main__.py` (new subcommands)
- `tools/builder/child_app_generator.py` (use template engine)
- `tools/builder/forge_validator.py` (template schema checks)
- `tools/dashboard/config.py` (profile defaults)
- `tools/db/storage.py` (profile backend)
- `tools/llm/router.py` (profile provider)
- `tools/workflow/coherence_checker.py` (new checks)
- `args/awareness_enablement_map.yaml` (deduplicate)
- `CLAUDE.md` (new guardrails + commands)

## 7. Success Criteria

Before implementation starts, these are the acceptance checks:

1. A new canvas can be added by editing **only** `args/component_registry.yaml` (and creating the actual module/templates). No Python list edits.
2. `icdev enable dic` works and is derived from registry.
3. `icdev status --json` reports all canvases from registry.
4. Dashboard starts with the same URL map as before after Phase 1.
5. `icdev scaffold canvas test_canvas --target-dir /tmp/test_canvas` produces a directory passing `forge_validator.py --gate`.
6. `icdev scaffold child-app my_lab --flavor ai --canvases dic,slides` produces a working child app.
7. `python tools/workflow/coherence_checker.py --all --gate` passes.
8. Air-gap profile disables cloud canvases without code changes.
9. All existing tests pass; new tests cover registry, template engine, and profiles.

## 8. Open Questions

1. **Scope priority**: Should we start with Phase 1 (registry-driven dashboard) or jump to a specific pain point (e.g., child-app factory or core profiles)?
2. **Template format**: Jinja2 file trees, cookiecutter-style, or a single YAML descriptor with embedded snippets?
3. **Multi-tenancy**: Should tenant-level canvas enablement live in DB or stay env-only for this phase?
4. **Air-gap first**: Which cloud-dependent components must be auto-disabled in the `air-gap` profile beyond the current `_AIRGAP_DISABLED_ROUTES`?
5. **Child-app flavors**: What are the 2-3 most important child-app flavors to ship first?

## 9. First Step (if approved)

Land Phase 0: create `args/component_registry.yaml` + `tools/config/component_registry.py` + tests, without touching runtime code. This gives us a verifiable, backward-compatible single source of truth and establishes the schema for everything that follows.
