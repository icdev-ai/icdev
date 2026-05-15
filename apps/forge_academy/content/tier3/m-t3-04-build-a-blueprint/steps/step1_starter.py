
"""
Tier 3 Mission 4: Build a Blueprint
Goal: Implement a BlueprintSpec validator that checks a blueprint module
      against ICDEV's 7-component gate.

The 7 components every ICDEV dashboard page must have:
  1. template       — tools/dashboard/templates/<app>/page.html
  2. route          — @bp.route in apps/<app>/blueprint.py
  3. backing_module — apps/<app>/<app>.py (main logic module)
  4. constants      — apps/<app>/constants.py
  5. migration      — DB migration entry (apps/<app>/migrations/ or inline)
  6. nav_link       — page reachable from navigation
  7. icdev_mirror   — icdev/tools/dashboard/templates/<app>/page.html
"""

from pathlib import Path

# ── Component Definitions ─────────────────────────────────────────────────────

COMPONENT_NAMES = [
    "template",
    "route",
    "backing_module",
    "constants",
    "migration",
    "nav_link",
    "icdev_mirror",
]

# Mock filesystem — files that "exist" for testing purposes
# Real implementation would use Path.exists()
MOCK_FS: set[str] = set()


def mock_exists(path: str) -> bool:
    """Check if a path exists in the mock filesystem (for testing)."""
    return path in MOCK_FS


# ── Step 1: BlueprintSpec ─────────────────────────────────────────────────────

class BlueprintSpec:
    """Validates a blueprint module against ICDEV's 7-component gate."""

    def __init__(self, app_name: str, use_mock: bool = False):
        """TODO: Initialize the spec for a given app name.

        Store app_name and use_mock.
        Compute the expected paths for each component:
          - template:       "tools/dashboard/templates/{app_name}/page.html"
          - blueprint_file: "apps/{app_name}/blueprint.py"
          - backing_module: "apps/{app_name}/{app_name}.py"
          - constants_file: "apps/{app_name}/constants.py"
          - migrations_dir: "apps/{app_name}/migrations"
          - icdev_mirror:   "icdev/tools/dashboard/templates/{app_name}/page.html"

        Store these as instance attributes.
        """
        # YOUR CODE HERE
        pass

    def _exists(self, path: str) -> bool:
        """Check if path exists (mock or real filesystem)."""
        if self.use_mock:
            return mock_exists(path)
        return Path(path).exists()

    def check_components(self) -> dict:
        """TODO: Check all 7 components and return their status.

        For each component, check if the expected file/dir exists.
        Component checks:
          1. "template":       self.template exists
          2. "route":          self.blueprint_file exists
          3. "backing_module": self.backing_module exists
          4. "constants":      self.constants_file exists
          5. "migration":      self.migrations_dir exists
          6. "nav_link":       Always True (can't auto-check nav links — assume present)
          7. "icdev_mirror":   self.icdev_mirror exists

        Return:
        {
            "template": True/False,
            "route": True/False,
            "backing_module": True/False,
            "constants": True/False,
            "migration": True/False,
            "nav_link": True,       ← always True (manual check required)
            "icdev_mirror": True/False,
        }
        """
        # YOUR CODE HERE
        pass

    def validate(self) -> dict:
        """TODO: Validate the blueprint against the 7-component gate.

        1. Call check_components() → components dict
        2. Count how many are True → score
        3. Return:
        {
            "app_name": self.app_name,
            "score": score,        ← int 0–7
            "max_score": 7,
            "valid": score == 7,   ← True only when all 7 present
            "components": components,
            "missing": [name for name, present in components.items() if not present],
        }
        """
        # YOUR CODE HERE
        pass


# ── Step 2: Route Extractor ───────────────────────────────────────────────────

def extract_routes(blueprint_content: str) -> list[str]:
    """TODO: Extract route paths from blueprint Python source.

    Find all @bp.route("...") decorators.
    Pattern: r'@bp\.route\(["\']([^"\']+)["\']'

    Return list of route path strings (e.g. ["/", "/api/records"]).
    Return empty list if no routes found.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Migration Checker ─────────────────────────────────────────────────

def check_migration_sql(sql: str) -> dict:
    """TODO: Check if a migration SQL string follows ICDEV conventions.

    Checks:
    1. "if_not_exists": True if "IF NOT EXISTS" in sql (case-insensitive)
    2. "has_id": True if "id " or "id," or "id\n" appears (primary key presence)
    3. "has_created_at": True if "created_at" in sql
    4. "valid": True if all three checks pass

    Return:
    {
        "if_not_exists": bool,
        "has_id": bool,
        "has_created_at": bool,
        "valid": bool,
    }
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    print("=== Blueprint Spec Validator ===\n")

    # Mock test — add files to MOCK_FS
    app = "statusboard"
    MOCK_FS.update([
        f"tools/dashboard/templates/{app}/page.html",
        f"apps/{app}/blueprint.py",
        f"apps/{app}/{app}.py",
        f"apps/{app}/constants.py",
        f"apps/{app}/migrations",
        f"icdev/tools/dashboard/templates/{app}/page.html",
    ])

    spec = BlueprintSpec(app, use_mock=True)
    result = spec.validate()
    print(f"App: {result['app_name']}")
    print(f"Score: {result['score']}/{result['max_score']}")
    print(f"Valid: {result['valid']}")
    print(f"Missing: {result['missing']}")

    # Route extraction
    bp_code = '''
@bp.route("/")
def index(): pass

@bp.route("/api/records")
def api_records(): pass
'''
    routes = extract_routes(bp_code)
    print(f"\nRoutes found: {routes}")

    # Migration check
    good_sql = "CREATE TABLE IF NOT EXISTS statusboard_items (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT);"
    check = check_migration_sql(good_sql)
    print(f"\nMigration check: {check}")
