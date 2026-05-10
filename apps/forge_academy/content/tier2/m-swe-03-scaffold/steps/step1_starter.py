"""
Tier 2 SWE/Architect Mission 3: System Scaffold Generator
Goal: Build a scaffold generator that produces a complete ICDEV child app
      directory structure and starter file contents from a spec.

Good scaffolding accelerates development by 3-5× — the architect's most
high-leverage contribution is getting the skeleton right before anyone writes logic.
"""

from dataclasses import dataclass, field

# ── File Templates ────────────────────────────────────────────────────────────

INIT_TEMPLATE = '"""{{ app_name }} — ICDEV child app."""\n'

BLUEPRINT_TEMPLATE = """from flask import Blueprint, render_template
from apps.{{ app_slug }}.db import get_records

bp = Blueprint("{{ app_slug }}", __name__, url_prefix="/{{ app_slug }}")

@bp.route("/")
def index():
    records = get_records()
    return render_template("{{ app_slug }}/page.html", records=records)

@bp.route("/api/records")
def api_records():
    from apps.{{ app_slug }}.db import get_records
    return {"records": get_records(), "count": len(get_records())}
"""

DB_TEMPLATE = """from tools.db.storage import get_connection

TABLE = "{{ app_slug }}_items"

def get_records():
    conn = get_connection()
    return [dict(r) for r in conn.execute(f"SELECT * FROM {TABLE} LIMIT 100").fetchall()]
"""

CONSTANTS_TEMPLATE = """# {{ app_name }} constants
APP_NAME = "{{ app_name }}"
APP_SLUG = "{{ app_slug }}"
CANVAS = "{{ canvas }}"
"""

MIGRATION_TEMPLATE = """-- Migration: {{ app_slug }} initial schema
CREATE TABLE IF NOT EXISTS {{ app_slug }}_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


# ── Step 1: Template Renderer ─────────────────────────────────────────────────

def render_template_str(template: str, context: dict) -> str:
    """TODO: Render a template string by replacing {{ key }} placeholders.

    For each key in context, replace all occurrences of "{{ key }}" in template
    with str(context[key]).

    Return the rendered string.
    Do not use Jinja2 — implement simple string replacement.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: ScaffoldSpec ──────────────────────────────────────────────────────

@dataclass
class ScaffoldSpec:
    """Specification for an ICDEV child app scaffold."""
    app_name: str
    app_slug: str
    canvas: str
    description: str = ""
    extra_routes: list = field(default_factory=list)


# ── Step 3: ScaffoldGenerator ─────────────────────────────────────────────────

class ScaffoldGenerator:
    """Generates ICDEV child app file structure from a ScaffoldSpec."""

    def generate_files(self, spec: ScaffoldSpec) -> dict[str, str]:
        """TODO: Generate file contents for all scaffold files.

        Build a context dict from spec:
        {"app_name": spec.app_name, "app_slug": spec.app_slug, "canvas": spec.canvas}

        Then render each template:
        Return a dict mapping relative path → rendered file content:
        {
            f"apps/{slug}/__init__.py":   render(INIT_TEMPLATE, context),
            f"apps/{slug}/blueprint.py":  render(BLUEPRINT_TEMPLATE, context),
            f"apps/{slug}/db.py":         render(DB_TEMPLATE, context),
            f"apps/{slug}/constants.py":  render(CONSTANTS_TEMPLATE, context),
            f"apps/{slug}/migrations/001_initial.sql": render(MIGRATION_TEMPLATE, context),
        }
        """
        # YOUR CODE HERE
        pass

    def get_file_list(self, spec: ScaffoldSpec) -> list[str]:
        """TODO: Return the list of files that will be generated.

        Call self.generate_files(spec) and return sorted(files.keys()).
        """
        # YOUR CODE HERE
        pass

    def validate_spec(self, spec: ScaffoldSpec) -> tuple[bool, list[str]]:
        """TODO: Validate a ScaffoldSpec before generating.

        Checks (collect all issues):
        1. app_name not empty → issue: "app_name is required"
        2. app_slug matches r'^[a-z][a-z0-9_]*$' → issue: f"app_slug '{spec.app_slug}' is invalid"
        3. canvas in VALID_CANVASES → issue: f"canvas '{spec.canvas}' is not valid"

        Return (True, []) if valid, (False, issues) otherwise.
        """
        import re
        VALID_CANVASES = {"NDC","SDC","PDC","BDC","DDC","ODC","IDC"}
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    gen = ScaffoldGenerator()
    spec = ScaffoldSpec(
        app_name="Mission Dashboard",
        app_slug="missiondash",
        canvas="ODC",
        description="Real-time mission tracking",
    )

    valid, issues = gen.validate_spec(spec)
    print(f"Spec valid: {valid}, issues: {issues}")

    files = gen.generate_files(spec)
    print(f"\nGenerated {len(files)} files:")
    for path, content in files.items():
        print(f"  {path} ({len(content)} chars)")

    print(f"\n__init__.py preview:\n{files[f'apps/missiondash/__init__.py']}")
    print(f"\nconstants.py preview:\n{files[f'apps/missiondash/constants.py']}")
