#!/usr/bin/env python3
# CUI // SP-CTI
"""Codebase Assistant configuration (Phase 69 — D-CA-4, D-CA-8, D-CA-9).

Maps dashboard URL prefixes to codebase module directories for auto-scoping.
Defines security exclusions for the codebase indexer.

Usage:
    from tools.dashboard.assistant_config import scope_for_path, ROUTE_MODULE_MAP
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Route → Module mapping (D-CA-4)
# URL path prefix → codebase directory (relative to BASE_DIR)
# ---------------------------------------------------------------------------

ROUTE_MODULE_MAP: dict[str, str] = {
    "/pulse": "tools/pulse/",
    "/genesis": "tools/genesis/",
    "/forge-studio": "tools/forge_studio/",
    "/research": "tools/research/",
    "/autoresearch": "tools/research/",
    "/govcon": "tools/govcon/",
    "/proposals": "tools/govcon/",
    "/cpmp": "tools/govcon/",
    "/finetune": "tools/finetune/",
    "/chat": "tools/dashboard/",
    "/knowledge-search": "tools/rag/",
    "/code-quality": "tools/knowledge/",
    "/agents": "tools/agents/",
    "/orchestration": "tools/orchestration/",
    "/monitoring": "tools/monitoring/",
    "/cicd": "tools/cicd/",
    "/oscal": "tools/compliance/",
    "/ai-transparency": "tools/ai_transparency/",
    "/ai-accountability": "tools/ai_accountability/",
    "/dev-profiles": "tools/builder/",
    "/phases": "tools/project/",
    "/translations": "tools/translate/",
    "/diagrams": "tools/diagrams/",
    "/projects": "tools/project/",
    "/activity": "tools/project/",
    "/events": "tools/project/",
    "/batch": "tools/batch/",
    "/review-board": "tools/review/",
    "/proposal-genesis": "tools/proposal_genesis/",
    "/filesync": "tools/filesync/",
    "/wizard": "tools/project/",
    "/strategos": "tools/strategos/",
    "/studio": "tools/studio/",
    "/network": "tools/network/",
    "/migration-intel": "tools/migration/",
}


def scope_for_path(url_path: str) -> str | None:
    """Resolve a dashboard URL path to a codebase module directory.

    Returns the module directory string (e.g. 'tools/pulse/') or None
    if no specific scope matches (global scope).
    """
    if not url_path:
        return None
    # Normalize
    path = url_path.rstrip("/").lower()
    # Try exact match first, then prefix match
    for prefix, module in sorted(ROUTE_MODULE_MAP.items(), key=lambda x: -len(x[0])):
        if path == prefix or path.startswith(prefix + "/"):
            return module
    return None


def files_in_scope(scope: str) -> list[str]:
    """List Python/MD/YAML files within a scope directory.

    Args:
        scope: Relative directory path (e.g. 'tools/pulse/')

    Returns:
        List of relative file paths within the scope.
    """
    scope_dir = BASE_DIR / scope
    if not scope_dir.is_dir():
        return []

    result = []
    for ext in ("*.py", "*.md", "*.yaml", "*.yml", "*.html", "*.json"):
        for f in scope_dir.rglob(ext):
            rel = f.relative_to(BASE_DIR)
            result.append(str(rel).replace("\\", "/"))
    return sorted(result)


# ---------------------------------------------------------------------------
# Security Exclusions (D-CA-9)
# Files/patterns that must NEVER be indexed.
# ---------------------------------------------------------------------------

SECURITY_EXCLUSIONS: list[str] = [
    ".env",
    ".env.local",
    ".env.production",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "credentials*",
    "secrets*",
    "*_secret*",
    "*_token*",
    "*.sqlite",
    "*.db",
]

EXCLUDED_DIRS: set[str] = {
    ".git",
    "__pycache__",
    "node_modules",
    ".tmp",
    "data",
    "venv",
    ".venv",
    "env",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "playwright",
    "dist",
    "build",
    "*.egg-info",
}


def is_excluded_file(file_path: Path) -> bool:
    """Check if a file should be excluded from indexing."""
    name = file_path.name.lower()

    # Check exact name matches
    for pattern in SECURITY_EXCLUSIONS:
        if "*" not in pattern:
            if name == pattern.lower():
                return True
        else:
            # Simple glob: *prefix or suffix*
            import fnmatch

            if fnmatch.fnmatch(name, pattern.lower()):
                return True

    # Check directory exclusions
    for part in file_path.parts:
        if part.lower() in EXCLUDED_DIRS:
            return True
        for excl in EXCLUDED_DIRS:
            if "*" in excl:
                import fnmatch

                if fnmatch.fnmatch(part.lower(), excl.lower()):
                    return True

    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if "--json" in sys.argv:
        print(
            json.dumps(
                {
                    "route_module_map": ROUTE_MODULE_MAP,
                    "security_exclusions": SECURITY_EXCLUSIONS,
                    "excluded_dirs": sorted(EXCLUDED_DIRS),
                },
                indent=2,
            )
        )
    else:
        print("ROUTE_MODULE_MAP:")
        for route, module in sorted(ROUTE_MODULE_MAP.items()):
            print(f"  {route:30s} → {module}")
        print(f"\nSecurity exclusions: {len(SECURITY_EXCLUSIONS)}")
        print(f"Excluded dirs: {len(EXCLUDED_DIRS)}")
