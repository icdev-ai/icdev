<!-- CUI // SP-CTI -->
# Enterprise-Configurable Platform

> Phase 5/6 feature documentation for the enterprise-configurable-templates initiative.
> Replaces hardcoded canvas, child-app, and core registration with a
> configuration-first, template-driven architecture.

## Summary

ICDEV™ now registers canvases, child apps, features, and core extensions from
a single YAML registry instead of scattered Python lists. This makes the platform:

- **Multi-tenant-aware**: per-tenant component enablement overrides live in the
  database and fall back to environment defaults.
- **Air-gap friendly**: core profiles (`local-dev`, `air-gap`, `saas-il4`,
  `il6-secret`) select backend, LLM provider, auth, and classification defaults.
- **Template-driven**: new canvases are scaffolded from `data/templates/canvases/`
  and child apps from `data/templates/child_apps/<flavor>/`.
- **RBAC-aware**: every component declares `min_il` and `default_roles`; the
  dashboard guards canvas routes with existing `canvas_access` primitives.
- **Auditable**: every `icdev enable/disable`, `icdev profile apply`, and tenant
  override is written to the append-only `component_audit_log` table.

## Single source of truth: `args/component_registry.yaml`

Every runtime consumer reads from the same registry:

| Consumer | What it reads |
|----------|---------------|
| `tools/dashboard/app.py` | enabled canvases → blueprints + url_prefixes |
| `tools/cli/enable.py` | CLI toggle names → env flags |
| `tools/dashboard/templates/base.html` | `nav_tree` sections/links |
| `tools/iqe/adapters/*.py` | adapter module + collections per canvas |
| `tools/security/canvas_access.py` | `min_il` + `default_roles` |
| `tools/foundry/oracle_verifiers.py` | registered routes instead of parsing source text |

A minimal canvas entry looks like:

```yaml
components:
  - key: dic
    kind: canvas
    display_name: "Document Intelligence Canvas"
    cli_name: dic
    env_flag: ICDEV_DIC_ENABLED
    extra_env_flags: []
    default_enabled: false
    module: tools.document_intelligence.blueprint
    blueprint_attr: dic_bp
    url_prefix: /document-intelligence
    min_il: IL4
    default_roles: [researcher, intelligence_analyst, writer]
    nav:
      section: Canvases
      label: Document Intelligence
      links:
        - label: DIC Overview
          href: /document-intelligence/
    iqe:
      adapter_module: tools.iqe.adapters.dic
      collections: [dic.drift_events, dic.regen_queue]
    completeness:
      blueprint: true
      constants: tools/document_intelligence/constants.py
      db_migration: tools/document_intelligence/db/migrations
      iqe_adapter: true
      nav_link: /document-intelligence/
      page_template: tools/dashboard/templates/document_intelligence/page.html
      seed_queries: context/iqe/queries/dic/
      route: /document_intelligence/
      tests: tests/test_dic_canvas.py
```

## Registry loader: `tools/config/component_registry.py`

```python
from tools.config.component_registry import get_registry, ComponentRegistry

registry = get_registry()
for comp in registry.iter_enabled(kind="canvas"):
    bp = comp.get_blueprint()
    app.register_blueprint(bp, url_prefix=comp.url_prefix)
```

Key APIs:

- `iter_canvases()`, `iter_child_apps()`, `iter_features()`, `iter_core_extensions()`
- `iter_enabled(kind=...)` — env-flag aware
- `is_enabled_for_tenant(key, tenant_id)` — env flag + DB override
- `set_tenant_component_override(tenant_id, key, enabled, updated_by)`
- `clear_tenant_component_override(tenant_id, key)`
- `get_nav_context()` — grouped nav tree for templates
- `get_iqe_mapping()` — `{canvas_key: (adapter_module, collections)}`
- `get_cli_toggles()`, `get_cli_descriptions()`
- `validate_canvas_completeness(key)` — 8-point gate
- `get_owner(key)`, `list_owned()`, `list_unowned()`, `get_ownership_map()`,
  `get_ownership_summary()` — ownership (see below)

## Ownership: `owner`, `owner_contact`, `on_call`

Three **optional** per-component fields answer the first question any Internal
Developer Portal has to answer — *who owns this?*

```yaml
- key: dic
  kind: canvas
  # ...
  owner: "Document Intelligence Team"    # team or individual ACCOUNTABLE
  owner_contact: dic-team@example.mil    # email, chat handle, list, or URL
  on_call: dic-primary                   # rotation / escalation handle
```

`owner` is **not** `default_roles`. `default_roles` is an RBAC access list — who
may *use* the canvas. `owner` is who answers for it when it breaks.

They are optional by design: making one required would fail the *whole* registry
load for every entry that has no owner yet. A component that omits `owner`, or
carries a placeholder (`TBD`, `unassigned`, `none`, …), is reported as **unowned**
by `list_unowned()` / `get_ownership_summary()` — never attributed to a fallback
team, because a wrong owner routes an incident to nobody while reading as
answered.

Full rationale, API reference, and the deliberately-zero backfill decision:
[idp-cat-01-component-ownership.md](idp-cat-01-component-ownership.md).

## Core profiles: `args/core_profiles.yaml`

Profiles select a whole runtime flavor. Activate one with:

```bash
icdev profile apply air-gap
```

Profiles include:

- `local-dev` — SQLite allowed, cloud LLMs allowed, all canvases opt-in.
- `air-gap` — local PostgreSQL or SQLite, Ollama-only LLM routing, cloud
  canvases disabled.
- `saas-il4` — PostgreSQL, tenant RLS, SSO, CUI default.
- `il6-secret` — SIPR-only, SECRET default, PKI auth, no cloud dependencies.

The active profile is read from `ICDEV_CORE_PROFILE`; values are applied as env
overrides only when the corresponding env flag is not already set.

## CLI commands

```bash
# Toggle canvases/features atomically
icdev enable dic
icdev disable network pipeline
icdev status
icdev list

# Core profiles
icdev profile list
icdev profile show air-gap
icdev profile apply air-gap

# Scaffolding
icdev scaffold canvas my_canvas --display-name "My Canvas" --flavor minimal --out /tmp/my_canvas
icdev scaffold child-app my_lab --display-name "My Lab" --flavor ai-lab --canvases dic,slides
```

## Navigation templatization

`tools/dashboard/templates/base.html` no longer hardcodes the Canvases dropdown.
It renders `nav_tree.sections` from the registry:

```jinja2
{% set _canvases = nav_tree.sections.get('Canvases', {}).get('groups', []) %}
{% for group in _canvases %}
  {% if group.items | selectattr('enabled') | list | length > 0 %}
    <li class="nav-section-label">{{ group.label }}</li>
    {% for comp in group.items if comp.enabled %}
      {% for link in comp.links if link.enabled | default(true) %}
        <li><a href="{{ link.href }}">{{ link.label }}</a></li>
      {% endfor %}
    {% endfor %}
  {% endif %}
{% endfor %}
```

## RBAC integration

`tools/security/canvas_access.py` exposes `guard_component_access(key, min_il)`.
`tools/dashboard/app.py` attaches it as a `before_request` guard for every
registered canvas blueprint. The guard:

- Is **fail-closed by default** (cnr-plat-03): an unauthenticated canvas request
  is redirected to login (browser) or `401`'d (API/JSON). Set
  `ICDEV_CANVAS_ACCESS_OPEN=true` (dev) or `ICDEV_AUTH_BYPASS` (test/CI) to allow
  unauthenticated access. The legacy `ICDEV_ENFORCE_CANVAS_ACCESS` still works
  when set explicitly (`true`=enforce, `false`=open) and takes precedence, but is
  no longer required to turn enforcement on.
- Enforces that the user's impact level is ≥ the canvas `min_il`.
- Requires an explicit canvas access grant (user, role, or group). An
  authenticated principal without a tenant (e.g. a platform admin) is allowed
  through the tenant-scoped grant checks.

Default tenant grants are seeded from `ComponentRegistry.default_roles`.

## Tenant overrides

Enterprise tenants can override env-based enablement per component via the
`tenant_component_overrides` table (migration 207). A missing row falls back to
environment/default settings. Override writes are audited.

## Audit logging

Append-only `component_audit_log` (migration 208) records:

| Event type | Trigger |
|------------|---------|
| `enable` / `disable` | `icdev enable` / `icdev disable` changing flags |
| `profile_apply` | `icdev profile apply <name>` writing to `.env` |
| `tenant_override_set` | `ComponentRegistry.set_tenant_component_override()` |
| `tenant_override_clear` | `ComponentRegistry.clear_tenant_component_override()` |

Failures are logged at debug level and swallowed so audit problems cannot break
configuration changes.

## Template engine

`tools/builder/template_engine.py` materializes components from Jinja2 file trees:

- Canvas templates: `data/templates/canvases/_base_/` and `data/templates/canvases/minimal/`
- Child-app flavors: `data/templates/child_apps/minimal/`, `compliance/`, `ai-lab/`, `govcon/`
- Core profile templates: `data/templates/core_profiles/`

It supports nested variable defaults, `{% if include_feature %}...{% endif %}`
conditionals, skip-existing files, and post-render Python syntax validation.

## Migration guide for canvas authors

To add a new canvas without editing Python lists:

1. Create the module under `tools/<my_canvas>/` with `blueprint.py`, `constants.py`,
   `db/init_db.py` (or migrations), and `page.html`.
2. Create an IQE adapter at `tools/iqe/adapters/<my_canvas>.py` and ≥3 seed queries
   in `context/iqe/queries/<my_canvas>/`.
3. Add a registry entry to `args/component_registry.yaml` with all `completeness`
   fields.
4. (Optional) add a template under `data/templates/canvases/<my_canvas>/` for
   `icdev scaffold canvas`.
5. Run `icdev enable <cli_name>` to test; run `pytest tests/test_component_registry.py`
   to lock in parity.

No changes to `tools/dashboard/app.py`, `tools/cli/enable.py`, or
`tools/dashboard/templates/base.html` are required.

## Coherence gates

`tools/workflow/coherence_checker.py` validates:

- `check_component_registry()` — registry loads, all referenced modules importable.
- `check_canvas_completeness(key)` — 8-point gate from `completeness` block.
- `check_nav_sync()` — base.html nav matches registry.
- `check_iqe_map_sync()` — IQE dispatch map matches registry.
- `check_toggle_sync()` — CLI toggles match registry env flags.
- `check_profile_sync()` — active profile resolves and env overrides are valid.

Run the full gate with:

```bash
python tools/workflow/coherence_checker.py --all --gate
```

## Security & compliance notes

- `component_audit_log` is append-only and protected by the pre-tool-use hook; do
  not `UPDATE` or `DELETE` rows.
- Profile changes only apply unset env flags; existing secrets and backend choices
  are never overwritten silently.
- Tenant overrides respect RLS through `tools.db.storage.get_connection()`.
- Air-gap profile disables cloud LLM providers and cloud-dependent canvases by env
  override, not by runtime branch.

## Testing

- `pytest tests/test_component_registry.py` — registry parity + tenant overrides + audit log.
- `pytest tests/test_core_profile.py` — profile loading and env precedence.
- `pytest tests/test_template_engine.py` — template rendering and validation.
- `python tools/testing/claude_dir_validator.py --json` — append-only table sync.
- `python tools/workflow/coherence_checker.py --all --gate` — enterprise coherence.
