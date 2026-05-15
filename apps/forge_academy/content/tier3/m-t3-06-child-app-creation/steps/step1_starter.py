
"""
Tier 3 Mission 6: Child App Creation
Goal: Implement AppManifest — the structured spec that drives child app scaffolding.

Every ICDEV child app passes through:
  1. AppManifest.validate() → structural gate
  2. child_app_generator.py → scaffold files from manifest
  3. forge_validator.py --gate → 7-component gate check
"""

import re
from dataclasses import dataclass, field

# ── Valid Canvases ─────────────────────────────────────────────────────────────

VALID_CANVASES = {"NDC", "SDC", "PDC", "BDC", "DDC", "ODC", "IDC"}

SLUG_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')


# ── AppManifest ───────────────────────────────────────────────────────────────

@dataclass
class AppManifest:
    """Structured specification for an ICDEV child app."""

    app_name: str
    app_slug: str
    canvas: str
    description: str
    routes: list = field(default_factory=list)
    db_tables: list = field(default_factory=list)
    author: str = "forge_academy"

    def validate(self) -> tuple[bool, list[str]]:
        """TODO: Validate the manifest against forge_validator requirements.

        Check (in order, collect ALL issues):
        1. app_name not empty → issue: "app_name cannot be empty"
        2. app_slug matches SLUG_PATTERN → issue: f"app_slug '{self.app_slug}' is invalid — use lowercase letters, digits, underscores only"
        3. canvas in VALID_CANVASES → issue: f"canvas '{self.canvas}' is not valid — must be one of {sorted(VALID_CANVASES)}"
        4. len(self.routes) >= 1 → issue: "at least one route is required"
        5. any route starts with "/" → issue: "all routes must start with '/'"
           (only add this if routes exist but none start with "/")
        6. len(self.db_tables) >= 1 → issue: "at least one DB table is required"

        Return (True, []) if no issues.
        Return (False, issues_list) if any issues.
        """
        # YOUR CODE HERE
        pass

    def to_dict(self) -> dict:
        """TODO: Return a serializable dict representation of the manifest.

        Return:
        {
            "app_name": self.app_name,
            "app_slug": self.app_slug,
            "canvas": self.canvas,
            "description": self.description,
            "routes": self.routes,
            "db_tables": self.db_tables,
            "author": self.author,
        }
        """
        # YOUR CODE HERE
        pass


# ── File Tree Generator ───────────────────────────────────────────────────────

def generate_file_tree(app_slug: str) -> list[str]:
    """TODO: Return the expected file paths for a child app.

    Return this list (with app_slug substituted):
      - f"apps/{app_slug}/__init__.py"
      - f"apps/{app_slug}/blueprint.py"
      - f"apps/{app_slug}/{app_slug}.py"
      - f"apps/{app_slug}/constants.py"
      - f"apps/{app_slug}/migrations"           (directory, not file)
      - f"tools/dashboard/templates/{app_slug}/page.html"
      - f"icdev/tools/dashboard/templates/{app_slug}/page.html"
    """
    # YOUR CODE HERE
    pass


# ── Completeness Checker ──────────────────────────────────────────────────────

def check_completeness(manifest: AppManifest) -> dict:
    """TODO: Score a manifest for completeness (0–5 quality signals).

    Quality signals (each worth 1 point):
    1. description has ≥ 10 characters
    2. len(routes) >= 2 (has at least an index + one API route)
    3. len(db_tables) >= 1
    4. author is not empty
    5. canvas is in VALID_CANVASES

    Return:
    {
        "score": int (0–5),
        "max_score": 5,
        "complete": score == 5,
        "signals": {
            "has_description": bool,
            "has_multiple_routes": bool,
            "has_db_tables": bool,
            "has_author": bool,
            "valid_canvas": bool,
        },
    }
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    print("=== AppManifest Builder ===\n")

    # Valid manifest
    m = AppManifest(
        app_name="Status Board",
        app_slug="statusboard",
        canvas="ODC",
        description="Real-time operational status display for mission tracking",
        routes=["/", "/api/status", "/api/items"],
        db_tables=["statusboard_items", "statusboard_events"],
        author="forge_academy",
    )

    valid, issues = m.validate()
    print(f"Valid: {valid}, issues: {issues}")
    print(f"Dict: {m.to_dict()}")
    print(f"File tree: {generate_file_tree('statusboard')}")
    print(f"Completeness: {check_completeness(m)}")

    # Invalid slug
    m_bad = AppManifest("My App", "My App", "ODC", "desc", ["/"], ["t"])
    valid2, issues2 = m_bad.validate()
    print(f"\nBad slug — Valid: {valid2}, issues: {issues2}")
