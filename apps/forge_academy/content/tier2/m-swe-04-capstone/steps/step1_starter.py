
"""
Tier 2 SWE/Architect Capstone: Multi-Agent System Designer
Goal: Wire ScaffoldGenerator + MCPToolRegistry into a complete
      system design and scaffolding pipeline.

This capstone integrates everything from SWE-01, 02, and 03:
  - MCPToolRegistry (tool registration) → ScaffoldGenerator (file generation) →
  - SystemDesigner (integration report + readiness check)
"""

import re
import inspect
from dataclasses import dataclass, field

# ── Provided Stubs (simplified versions of prior missions) ────────────────────

class MCPToolRegistry:
    """MCP tool registry (simplified from SWE-02)."""
    def __init__(self):
        self._tools = {}

    def tool(self):
        def decorator(func):
            schema = self._generate_schema(func)
            self._tools[func.__name__] = {"func": func, "schema": schema}
            return func
        return decorator

    def _generate_schema(self, func):
        sig = inspect.signature(func)
        params = {}
        for name, param in sig.parameters.items():
            ann = param.annotation
            if ann == inspect.Parameter.empty:
                type_str = "any"
            elif ann == str:
                type_str = "string"
            elif ann == int:
                type_str = "integer"
            elif ann == float:
                type_str = "number"
            elif ann == bool:
                type_str = "boolean"
            else:
                type_str = "any"
            params[name] = {"type": type_str}
        return {"name": func.__name__, "parameters": params}

    def list_tools(self):
        return list(self._tools.keys())

    def call(self, tool_name, **kwargs):
        if tool_name not in self._tools:
            return {"error": f"Tool '{tool_name}' not found"}
        try:
            return {"result": self._tools[tool_name]["func"](**kwargs)}
        except Exception as e:
            return {"error": str(e)}


@dataclass
class ScaffoldSpec:
    app_name: str
    app_slug: str
    canvas: str
    description: str = ""
    extra_routes: list = field(default_factory=list)


INIT_TEMPLATE = '"""{{ app_name }} — ICDEV child app."""\n'

BLUEPRINT_TEMPLATE = """from flask import Blueprint
bp = Blueprint("{{ app_slug }}", __name__, url_prefix="/{{ app_slug }}")
@bp.route("/")
def index():
    return "{{ app_name }}"
"""

CONSTANTS_TEMPLATE = """APP_NAME = "{{ app_name }}"
APP_SLUG = "{{ app_slug }}"
CANVAS = "{{ canvas }}"
"""

VALID_CANVASES = {"NDC", "SDC", "PDC", "BDC", "DDC", "ODC", "IDC"}


# ── Step 1: Template Renderer ─────────────────────────────────────────────────

def render_template_str(template: str, context: dict) -> str:
    """TODO: Replace {{ key }} placeholders in template with context values.

    For each key in context, replace all occurrences of "{{ key }}" with str(value).
    Return the rendered string.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: DesignValidator ───────────────────────────────────────────────────

def validate_spec(spec: ScaffoldSpec) -> tuple[bool, list[str]]:
    """TODO: Validate a ScaffoldSpec.

    Checks (collect all issues):
    1. spec.app_name not empty → issue: "app_name is required"
    2. spec.app_slug matches r'^[a-z][a-z0-9_]*$' → issue: f"app_slug '{spec.app_slug}' is invalid"
    3. spec.canvas in VALID_CANVASES → issue: f"canvas '{spec.canvas}' is not valid"

    Return (True, []) if valid, (False, issues) otherwise.
    """
    # YOUR CODE HERE
    pass


def generate_scaffold_files(spec: ScaffoldSpec) -> dict[str, str]:
    """TODO: Generate scaffold files for a spec.

    Build context dict from spec (app_name, app_slug, canvas).
    Return dict mapping path → rendered content for:
    - f"apps/{slug}/__init__.py"   → INIT_TEMPLATE
    - f"apps/{slug}/blueprint.py" → BLUEPRINT_TEMPLATE
    - f"apps/{slug}/constants.py" → CONSTANTS_TEMPLATE
    """
    # YOUR CODE HERE
    pass


# ── Step 3: SystemDesigner ────────────────────────────────────────────────────

class SystemDesigner:
    """Integrates spec validation, scaffold generation, and tool registration."""

    def __init__(self):
        self.registry = MCPToolRegistry()

    def register_app_tools(self, spec: ScaffoldSpec) -> list[str]:
        """TODO: Register two MCP tools for the app using the registry.

        1. Define and register a tool named f"{spec.app_slug}_get_records"
           that takes: system_id (str) → returns {"records": [], "system": system_id}
        2. Define and register a tool named f"{spec.app_slug}_get_status"
           that takes: component (str) → returns {"status": "ok", "component": component}

        Return list of registered tool names.
        """
        # YOUR CODE HERE
        pass

    def design(self, spec: ScaffoldSpec) -> dict:
        """TODO: Run the full system design pipeline.

        1. validate_spec(spec) → (valid, issues)
        2. If valid:
           a. generate_scaffold_files(spec) → files
           b. self.register_app_tools(spec) → tools
        3. Return:
        {
            "app_name": spec.app_name,
            "app_slug": spec.app_slug,
            "canvas": spec.canvas,
            "valid": valid,
            "issues": issues,
            "files_generated": len(files) if valid else 0,
            "file_paths": sorted(files.keys()) if valid else [],
            "tools_registered": tools if valid else [],
            "ready": valid and len(files) > 0 and len(tools) > 0,
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    designer = SystemDesigner()
    spec = ScaffoldSpec(
        app_name="Threat Monitor",
        app_slug="threatmon",
        canvas="SDC",
        description="Security threat monitoring dashboard",
    )
    result = designer.design(spec)
    print(f"App: {result['app_name']} ({result['app_slug']})")
    print(f"Valid: {result['valid']}, Ready: {result['ready']}")
    print(f"Files: {result['files_generated']}")
    print(f"Tools: {result['tools_registered']}")
    if result["issues"]:
        print(f"Issues: {result['issues']}")
