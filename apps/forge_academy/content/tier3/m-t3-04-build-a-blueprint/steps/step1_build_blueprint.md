---
ontology_id: icdev:mission:m-t3-04-build-a-blueprint:step:1
step_class: icdev:Lesson
---

# Build a Blueprint

Every ICDEV child app is a Flask Blueprint. A blueprint packages its routes, templates, and DB logic into a self-contained module that registers into the main dashboard with zero coupling. In this mission you'll implement the core pieces of a blueprint module.

## The ICDEV Blueprint Pattern

```python
# apps/myapp/blueprint.py
from flask import Blueprint, render_template
from apps.myapp.db import get_records

bp = Blueprint("myapp", __name__, url_prefix="/myapp")

@bp.route("/")
def index():
    records = get_records()
    return render_template("myapp/index.html", records=records)

@bp.route("/api/records")
def api_records():
    return {"records": get_records(), "count": len(get_records())}
```

## Registration Pattern

Blueprints register in `tools/dashboard/app.py` via try/except (fail-silent):

```python
try:
    from apps.myapp.blueprint import bp as myapp_bp
    app.register_blueprint(myapp_bp)
except Exception as e:
    print(f"myapp blueprint failed: {e}")
```

## The 7-Component Gate

Every ICDEV dashboard page ships with ALL 7 components together:

| # | Component | Example |
|---|-----------|---------|
| 1 | Template | `templates/myapp/page.html` |
| 2 | Route | `@bp.route("/")` in `blueprint.py` |
| 3 | Backing module | `apps/myapp/module.py` |
| 4 | Constants | `apps/myapp/constants.py` |
| 5 | DB migration | SQL `CREATE TABLE IF NOT EXISTS` |
| 6 | Nav link | Link from parent navigation |
| 7 | icdev/ mirror | Copy to `icdev/` package |

## What You'll Build

A `BlueprintSpec` validator that checks a blueprint module against the 7-component gate:

```python
spec = BlueprintSpec("statusboard")
result = spec.validate()
# → {"valid": True, "score": 7, "components": {...}}
```

## Success Criteria

- `BlueprintSpec.__init__()` accepts an app_name and sets component paths
- `check_components()` returns a dict of component_name → True/False
- `validate()` returns score (0–7), valid (True if score==7), and per-component status
- Mock file system input works correctly
- Score reflects exactly how many of the 7 components are present
