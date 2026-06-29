# CUI // SP-CTI
"""Zero-config starter recipe loader — agentcn adaptation.

Loads recipes from args/core_profiles.yaml and returns the 3-command
setup sequence for a given recipe name.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

_ARGS_DIR = Path(__file__).resolve().parents[2] / "args"


def _load_recipes() -> dict[str, Any]:
    import yaml
    cfg_path = _ARGS_DIR / "core_profiles.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("recipes", {})


def list_recipes() -> list[dict[str, Any]]:
    """Return all recipes as a list with name injected."""
    recipes = _load_recipes()
    return [{"name": k, **v} for k, v in recipes.items()]


def get_recipe(name: str) -> dict[str, Any] | None:
    """Return a single recipe by name, or None if not found."""
    return _load_recipes().get(name)


def get_setup_commands(name: str) -> list[str]:
    """Return the 3-command setup sequence for a recipe."""
    recipe = get_recipe(name)
    if not recipe:
        return []
    return recipe.get("setup_commands", [])


def print_recipe_card(name: str) -> None:
    """Print a human-readable recipe card to stdout."""
    recipe = get_recipe(name)
    if not recipe:
        print(f"Recipe '{name}' not found. Available: {', '.join(_load_recipes())}")
        return
    print(f"\n{'='*50}")
    print(f"  {recipe['display_name']} — {name}")
    print(f"{'='*50}")
    print(f"  {recipe['description']}")
    print(f"\n  3-command setup:")
    for i, cmd in enumerate(recipe.get("setup_commands", []), 1):
        print(f"    {i}. {cmd}")
    print(f"\n  Profile: {recipe.get('profile', 'local-dev')}")
    print(f"  LLM:     {recipe.get('recommended_llm', 'claude-sonnet-4-6')}")
    print(f"  Min IL:  {recipe.get('min_il', 'IL2')}")
    print()


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else None
    if name:
        print_recipe_card(name)
    else:
        for r in list_recipes():
            print(f"  {r['name']:12} — {r['display_name']}: {r['description'][:60]}")
