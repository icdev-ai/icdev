# Mission m-aie-01 — Coding Harnesses
# Step 3: render instruction files for three harnesses from ONE project description.
#
# Claude Code reads CLAUDE.md, Codex CLI reads AGENTS.md, Cursor reads
# .cursor/rules/*.mdc. Write one function that renders all three so they can never
# disagree with each other.

PROJECT = {
    "name": "ledger-api",
    "summary": "Internal REST API for the finance team's ledger.",
    "stack": ["Python 3.12", "FastAPI", "PostgreSQL"],
    "commands": {
        "test": "pytest -q",
        "lint": "ruff check .",
        "run": "uvicorn app.main:app --reload",
    },
    "conventions": [
        "Type-hint every public function",
        "Use pathlib.Path, never string paths",
        "Every new endpoint ships with a test",
    ],
    "boundaries": [
        "Never commit directly to main",
        "Never edit files under migrations/ by hand",
        "Never read or print values from .env",
    ],
}

SECTIONS = ["Project Overview", "Commands", "Conventions", "Boundaries"]

HARNESS_FILES = {
    "claude_code": "CLAUDE.md",
    "codex_cli": "AGENTS.md",
    "cursor": ".cursor/rules/project.mdc",
}


def render_body(project: dict) -> str:
    """The markdown every harness shares: one '## ' section per entry in SECTIONS."""
    # TODO: build the four sections from `project`:
    #   ## Project Overview -> name, summary and stack
    #   ## Commands         -> every command, e.g. "- test: `pytest -q`"
    #   ## Conventions      -> one bullet per convention
    #   ## Boundaries       -> one bullet per boundary
    return ""


def build_instruction_files(project: dict) -> dict:
    """Return {path: content} for CLAUDE.md, AGENTS.md and .cursor/rules/project.mdc."""
    # TODO: render the body once, then wrap it per harness. The Cursor file must open
    # with YAML frontmatter:
    #   ---
    #   description: <one line about the project>
    #   alwaysApply: true
    #   ---
    return {}


files = build_instruction_files(PROJECT)
for path, content in files.items():
    print(f"===== {path} ({len(content)} chars)")
    print(content)
