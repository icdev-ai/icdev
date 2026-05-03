# CUI // SP-CTI
"""TTX Scenario Loader — discovers and loads YAML/JSON scenario packs."""
from __future__ import annotations

import json
from pathlib import Path

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent.parent / "args" / "ttx_scenarios"


def list_scenario_slugs() -> list[str]:
    if not _SCENARIOS_DIR.exists():
        return []
    slugs = []
    for f in sorted(_SCENARIOS_DIR.iterdir()):
        if f.suffix in (".yaml", ".yml", ".json") and not f.name.startswith("_"):
            slugs.append(f.stem)
    return slugs


def load_scenario(slug: str) -> dict:
    if not _SCENARIOS_DIR.exists():
        raise FileNotFoundError(f"Scenarios directory not found: {_SCENARIOS_DIR}")
    for ext in (".yaml", ".yml", ".json"):
        path = _SCENARIOS_DIR / f"{slug}{ext}"
        if path.exists():
            if ext == ".json":
                return json.loads(path.read_text(encoding="utf-8"))
            try:
                import yaml
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except ImportError:
                import json as _j
                raise FileNotFoundError(f"PyYAML not installed; cannot load {path}")
    raise FileNotFoundError(f"Scenario '{slug}' not found in {_SCENARIOS_DIR}")
