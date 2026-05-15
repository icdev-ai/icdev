
from __future__ import annotations
"""
Tier 3 Mission 1: FORGE Framework Deep Dive
Goal: Build a GoalTracer that reads ICDEV source files and extracts the execution graph.
"""

from pathlib import Path

# ── ICDEV Project Root Detection ──────────────────────────────────────────────

def find_project_root() -> Path:
    """Locate the ICDEV project root by finding the CLAUDE.md file."""
    current = Path(__file__).resolve()
    for parent in [current, *current.parents]:
        if (parent / "CLAUDE.md").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT = find_project_root()
GOALS_DIR = PROJECT_ROOT / "goals"
TOOLS_DIR = PROJECT_ROOT / "tools"


# ── Sample Goal Content (fallback if goals/ don't exist) ──────────────────────

SAMPLE_GOALS = {
    "compliance_scan": """
# Compliance Scan Goal
# Tools: tools/compliance/scanner.py, tools/db/storage.py
# Args: args/compliance_config.yaml
# Output: DB table audit_findings

Run a STIG compliance scan against the target system.
Uses: compliance/scanner.py to parse STIG XCCDF, storage.py to persist findings.
""",
    "memory_write": """
# Memory Write Goal
# Tools: tools/memory/memory_write.py
# Args: args/memory_config.yaml
# Output: DB table memory_entries, file memory/logs/

Write an event to the ICDEV memory system.
Uses: memory/memory_write.py to persist. Output stored in memory_entries table.
""",
}


# ── Step 1: Goal Parser ───────────────────────────────────────────────────────

def parse_goal_file(goal_content: str) -> dict:
    """TODO: Extract metadata from a goal file's content.

    Look for:
    1. Tool references: lines containing "Tools:" or "tools/" paths
       - Extract any "tools/..." paths using regex: r'tools/[\w/]+\.py'
    2. Args file: lines containing "Args:" followed by an args/ path
       - Extract: r'args/[\w_/]+\.yaml'
    3. Output type: if "DB table" appears → "db"; if "file" appears → "file"; else "unknown"

    Return:
    {
        "tools": [list of tool paths found],
        "args_file": "args/..." or None,
        "output_type": "db" | "file" | "unknown",
    }
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Tool Resolver ─────────────────────────────────────────────────────

def resolve_tools(tool_paths: list[str], tools_dir: Path) -> list[dict]:
    """TODO: Check which tool paths actually exist in the project.

    For each tool_path in tool_paths:
    1. Build the full path: tools_dir.parent / tool_path
    2. Check if it exists: Path.exists()
    3. Return a list of dicts:
       {"path": tool_path, "exists": True/False, "name": filename without .py}

    Return the list even if no tools exist (for tracing purposes).
    """
    # YOUR CODE HERE
    pass


# ── Step 3: GoalTracer ────────────────────────────────────────────────────────

class GoalTracer:
    """Reads ICDEV goal files and extracts the execution graph."""

    def __init__(self, goals_dir: Path = GOALS_DIR, tools_dir: Path = TOOLS_DIR):
        self.goals_dir = goals_dir
        self.tools_dir = tools_dir

    def _load_goal(self, goal_name: str) -> str | None:
        """Load goal file content. Try goals_dir first, then SAMPLE_GOALS fallback."""
        # Try actual file
        for ext in [".md", ".yaml", ".yml", ""]:
            path = self.goals_dir / f"{goal_name}{ext}"
            if path.exists():
                return path.read_text(encoding="utf-8")
        # Fallback to sample
        return SAMPLE_GOALS.get(goal_name)

    def trace(self, goal_name: str) -> dict:
        """TODO: Trace a goal and return its execution graph.

        1. Load the goal content via _load_goal(goal_name)
        2. If not found, return {"error": f"Goal '{goal_name}' not found"}
        3. Call parse_goal_file(content) to extract metadata
        4. Call resolve_tools(parsed["tools"], self.tools_dir) to check tool paths
        5. Return:
           {
               "goal": goal_name,
               "tools": resolved_tools,  # list of {path, exists, name}
               "args_file": parsed["args_file"],
               "output_type": parsed["output_type"],
               "tool_count": len(resolved_tools),
           }
        """
        # YOUR CODE HERE
        pass

    def trace_multiple(self, goal_names: list[str]) -> list[dict]:
        """TODO: Trace multiple goals, returning a list of trace results.

        Call self.trace(name) for each name in goal_names.
        Skip goals that return an error (don't include errors in the result list).
        Return the list of successful trace results.
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    tracer = GoalTracer()

    print("=== FORGE Goal Tracer ===")
    for goal_name in ["compliance_scan", "memory_write"]:
        result = tracer.trace(goal_name)
        print(f"\nGoal: {result.get('goal')}")
        print(f"  Tools: {result.get('tool_count')} found")
        for t in result.get("tools", []):
            status = "EXISTS" if t["exists"] else "missing"
            print(f"    [{status}] {t['path']}")
        print(f"  Args: {result.get('args_file')}")
        print(f"  Output: {result.get('output_type')}")

    print("\n=== Multi-Goal Trace ===")
    batch = tracer.trace_multiple(["compliance_scan", "memory_write", "nonexistent_goal"])
    print(f"Successfully traced: {[r['goal'] for r in batch]}")
