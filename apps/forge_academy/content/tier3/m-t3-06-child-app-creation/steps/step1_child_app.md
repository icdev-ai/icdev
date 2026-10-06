---
ontology_id: icdev:mission:m-t3-06-child-app-creation:step:1
step_class: icdev:Lesson
---

# Child App Creation

"Child app" means two different things in ICDEV, and a Tier 3 engineer needs to know which one they are building:

1. **An in-dashboard child app** — a Flask blueprint under `apps/<key>/` (FORGE Academy itself is
   `apps/forge_academy/`), declared with `kind: child_app` in `args/component_registry.yaml`.
   Scaffold one from a template with:

   ```bash
   icdev scaffold child-app my_lab --display-name "My Lab" --flavor ai-lab --canvases dic,slides
   ```

2. **A generated standalone child app** — a mini-ICDEV clone with its own FORGE layers (goals/,
   tools/, args/, context/, hardprompts/), agents, memory and CI/CD, produced from a blueprint JSON and
   then gated by the FORGE validator. The repo rule is: always use the generator, then the gate:

   ```bash
   python tools/builder/child_app_generator.py --blueprint bp.json --project-path <parent_dir> --name my-app --json
   python tools/builder/forge_validator.py --project-dir <parent_dir>/my-app --gate
   ```

   `forge_validator.py --gate` exits non-zero if any check fails: the six FORGE layers, CLAUDE.md,
   memory, DB init, ANVIL workflow, grounding modules, coherence checker, DB patterns, and (FORGE-13)
   the 8-component completeness gate for every canvas the child declares.

In this mission you'll implement `AppManifest` — a **simplified teaching model** of the structured
spec that drives scaffolding. It is not a class in the repo; the real generator reads a blueprint
JSON, and the real scaffold takes `--canvases` as registry keys (lowercase, e.g. `dic`). The
exercise uses the seven Design Canvas codes from T3-05 (`NDC`, `SDC`, `PDC`, `BDC`, `DDC`, `ODC`, `IDC`).

## Child App Anatomy (exercise model, in-dashboard flavor)

```
apps/<app_slug>/
├── __init__.py          # Package marker
├── blueprint.py         # Flask blueprint with all routes
├── <app_slug>.py        # Main logic module
├── constants.py         # App-level constants
└── migrations/          # DB migration SQL files

tools/dashboard/templates/<app_slug>/
└── page.html            # Main template

icdev/tools/dashboard/templates/<app_slug>/
└── page.html            # icdev/ package mirror
```

## AppManifest Structure

The manifest drives the scaffolding:

```python
manifest = AppManifest(
    app_name="Status Board",
    app_slug="statusboard",
    canvas="ODC",
    description="Real-time operational status display",
    routes=["/", "/api/status"],
    db_tables=["statusboard_items"],
    author="forge_academy",
)
manifest.to_dict()
# → {"app_slug": "statusboard", "canvas": "ODC", "routes": [...], ...}
```

## Validation Rules

Before scaffolding, your `AppManifest.validate()` checks:

1. `app_slug` matches `^[a-z][a-z0-9_]*$` (lowercase, alphanumeric + underscore, starts with letter)
2. `canvas` is one of the canvas codes in `VALID_CANVASES`
3. At least one route must start with "/"
4. At least one DB table defined
5. `app_name` is not empty

## What You'll Build

- `AppManifest` class with `validate()` and `to_dict()`
- `generate_file_tree()` — returns list of expected file paths for the app
- `check_completeness()` — scores the manifest on 5 quality signals

## Success Criteria

- `AppManifest.validate()` returns `(True, [])` for valid manifests
- `AppManifest.validate()` returns `(False, [issue, ...])` for invalid manifests
- `generate_file_tree()` returns all expected paths including icdev/ mirror
- Slug validation correctly rejects uppercase, spaces, special chars
- `to_dict()` returns serializable dict with all manifest fields
