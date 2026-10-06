---
ontology_id: icdev:mission:m-t3-04-build-a-blueprint:step:1
step_class: icdev:Lesson
---

# Build a Blueprint

Every ICDEV dashboard surface — each canvas (`tools/<canvas>/blueprint.py`) and each in-dashboard child app (`apps/<app>/blueprint.py`, e.g. `apps/forge_academy/blueprint.py`) — is a Flask Blueprint. A blueprint packages its routes, templates, and DB logic into a self-contained module that the main dashboard registers. In this mission you'll implement the core pieces of a blueprint completeness checker.

## The ICDEV Blueprint Pattern

```python
# apps/myapp/blueprint.py
from flask import Blueprint, render_template
from apps.myapp.db import get_records

bp = Blueprint("myapp", __name__)

@bp.route("/")
def index():
    records = get_records()
    return render_template("myapp/page.html", records=records)

@bp.route("/api/records")
def api_records():
    return {"records": get_records(), "count": len(get_records())}
```

## Registration Pattern — the component registry

You do **not** edit `tools/dashboard/app.py` to register a blueprint. Canvases, child apps,
features and core extensions are declared in `args/component_registry.yaml`, loaded by
`tools/config/component_registry.py`, and the dashboard derives blueprint registration, nav links
and the `icdev enable/disable` toggle from that entry. The real FORGE Academy entry looks like this
(trimmed):

```yaml
- key: forge_academy
  kind: child_app
  display_name: FORGE Academy
  env_flag: ICDEV_FORGE_ACADEMY_ENABLED
  default_enabled: false
  module: apps.forge_academy.blueprint
  blueprint_attr: academy_bp
  url_prefix: /academy
  nav:
    section: Platforms
    label: FORGE Academy
    links:
      - label: Dashboard
        href: /academy
```

A new app can be scaffolded from a template with
`icdev scaffold child-app <key> --display-name "Name"` (or `icdev scaffold canvas ...` for a canvas).

## The Completeness Gate

The repo's rule for a new dashboard page (CLAUDE.md, "New dashboard page completeness gate") lists
**8** components that must ship together: template, `icdev/` template mirror, `@bp.route`, backing
module, constants, DB migration, nav/parent link, and **IQE integration** (query adapter + widget +
seed queries). This exercise checks the first **7** — the IQE wiring is several files across the
dashboard and is left out to keep the checker small:

| # | Component | Exercise path |
|---|-----------|---------|
| 1 | Template | `tools/dashboard/templates/<app>/page.html` |
| 2 | Route | `@bp.route(...)` in `apps/<app>/blueprint.py` |
| 3 | Backing module | `apps/<app>/<app>.py` |
| 4 | Constants | `apps/<app>/constants.py` |
| 5 | DB migration | `apps/<app>/migrations` (in the real repo: `python tools/db/migrate.py --create "<name>"`, never a hand-numbered migration) |
| 6 | Nav link | Link from parent navigation (registry `nav:` block) |
| 7 | icdev/ mirror | `icdev/tools/dashboard/templates/<app>/page.html` |

> **Sandbox note:** the exercise has no repo on disk, so `BlueprintSpec(..., use_mock=True)` checks
> paths against the in-memory `MOCK_FS` set instead of the filesystem.

## What You'll Build

A `BlueprintSpec` validator that checks a blueprint module against those 7 components:

```python
spec = BlueprintSpec("statusboard", use_mock=True)
result = spec.validate()
# → {"valid": True, "score": 7, "components": {...}}
```

## Success Criteria

- `BlueprintSpec.__init__()` accepts an app_name and sets component paths
- `check_components()` returns a dict of component_name → True/False
- `validate()` returns score (0–7), valid (True if score==7), and per-component status
- Mock file system input works correctly
- Score reflects exactly how many of the 7 components are present
