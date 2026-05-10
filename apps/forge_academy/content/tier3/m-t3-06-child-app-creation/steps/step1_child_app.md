# Child App Creation

ICDEV child apps are self-contained Flask blueprints that extend the core platform. Every child app must pass the **forge_validator gate** before it ships. In this mission you'll implement the `AppManifest` builder — the structured spec that child_app_generator.py uses to scaffold a new app.

## Child App Anatomy

A complete child app has these pieces, validated by `forge_validator.py --gate`:

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

Before scaffolding, `AppManifest.validate()` checks:
1. `app_slug` matches `^[a-z][a-z0-9_]*$` (lowercase, alphanumeric + underscore, starts with letter)
2. `canvas` is one of the 7 valid canvas codes
3. At least one route must start with "/"
4. At least one DB table defined
5. `app_name` is not empty

## What You'll Build

- `AppManifest` class with `validate()` and `to_dict()`
- `generate_file_tree()` — returns list of expected file paths for the app
- `check_completeness()` — scores the manifest against forge_validator requirements

## Success Criteria

- `AppManifest.validate()` returns `(True, [])` for valid manifests
- `AppManifest.validate()` returns `(False, [issue, ...])` for invalid manifests
- `generate_file_tree()` returns all expected paths including icdev/ mirror
- Slug validation correctly rejects uppercase, spaces, special chars
- `to_dict()` returns serializable dict with all manifest fields
