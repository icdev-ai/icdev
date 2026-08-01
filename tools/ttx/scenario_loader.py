# CUI // SP-CTI
"""TTX Engine — scenario YAML loader and validator."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from pathlib import Path
from typing import Any

import yaml

log = get_logger(__name__)

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent.parent / "scenarios"


def _resolve(pack_dir: Path, rel_path: str) -> Path:
    return (pack_dir / rel_path).resolve()


def load_scenario(slug: str) -> dict[str, Any]:
    """Load and fully resolve a scenario pack by slug.

    Returns the parsed scenario dict with all inject/persona/rubric
    sub-files merged inline under their respective keys.
    Raises FileNotFoundError or ValueError on problems.
    """
    pack_dir = _SCENARIOS_DIR / slug
    master = pack_dir / "scenario.yaml"
    if not master.exists():
        raise FileNotFoundError(f"Scenario pack not found: {pack_dir}")

    with master.open(encoding="utf-8") as fh:
        scenario: dict = yaml.safe_load(fh)

    scenario.setdefault("slug", slug)

    # Resolve inject body files inline
    for inject in scenario.get("injects", []):
        if "body_path" in inject:
            bp = _resolve(pack_dir, inject["body_path"])
            if bp.exists():
                with bp.open(encoding="utf-8") as fh:
                    sub = yaml.safe_load(fh) or {}
                inject.update({k: v for k, v in sub.items() if k not in inject})
            inject.pop("body_path", None)

        # Resolve rubric inline
        scoring = inject.get("scoring", {})
        if isinstance(scoring.get("rubric"), str):
            rp = _resolve(pack_dir, scoring["rubric"])
            if rp.exists():
                with rp.open(encoding="utf-8") as fh:
                    scoring["rubric"] = yaml.safe_load(fh)

        # Normalize legacy dict-of-dicts rubric dimensions to the canonical
        # dimensions-LIST format ([{id, weight, prompt, ...}]) so ai_scorer can
        # consume every pack uniformly. This is the SINGLE conversion point —
        # ai_scorer.judge_response only validates shape, it does not convert.
        if isinstance(scoring.get("rubric"), dict):
            normalize_rubric_dimensions(scoring["rubric"])

    # Resolve persona templates inline
    for role in scenario.get("roles", []):
        if "persona_template" in role:
            pp = _resolve(pack_dir, role["persona_template"])
            if pp.exists():
                with pp.open(encoding="utf-8") as fh:
                    role["persona_data"] = yaml.safe_load(fh) or {}
            role.pop("persona_template", None)

    _validate(scenario)
    return scenario


def normalize_rubric_dimensions(rubric: dict) -> dict:
    """Convert a legacy dict-of-dicts rubric to the canonical dimensions-LIST
    format, in place.

    Legacy shape (forge_ascent / hunt_the_fleet / meridian era)::

        dimensions:
          bug_identification: {weight: 0.25, prompt: "..."}
          fix_correctness:    {weight: 0.40, prompt: "..."}

    Canonical shape consumed by ``tools/ttx/ai_scorer``::

        dimensions:
          - {id: bug_identification, weight: 0.25, prompt: "..."}
          - {id: fix_correctness,    weight: 0.40, prompt: "..."}

    The mapping key becomes the dimension ``id`` and every other key (weight,
    prompt, max_pts, ...) is preserved verbatim. No-op when ``dimensions`` is
    already a list or is absent, so it is safe to call unconditionally.
    """
    if not isinstance(rubric, dict):
        return rubric
    dims = rubric.get("dimensions")
    if isinstance(dims, dict):
        rubric["dimensions"] = [
            {**(spec if isinstance(spec, dict) else {"value": spec}), "id": dim_id}
            for dim_id, spec in dims.items()
        ]
    return rubric


def list_scenario_slugs() -> list[str]:
    """Return slugs for all available scenario packs on disk."""
    if not _SCENARIOS_DIR.exists():
        return []
    return sorted(
        d.name for d in _SCENARIOS_DIR.iterdir()
        if d.is_dir() and (d / "scenario.yaml").exists()
    )


def _validate(scenario: dict) -> None:
    required = ("name", "session_mode", "injects")
    for field in required:
        if field not in scenario:
            raise ValueError(f"Scenario missing required field: {field}")
    mode = scenario["session_mode"]
    if mode not in ("live", "async"):
        raise ValueError(f"session_mode must be 'live' or 'async', got: {mode!r}")
    for i, inj in enumerate(scenario["injects"]):
        if "id" not in inj or "title" not in inj:
            raise ValueError(f"Inject [{i}] missing 'id' or 'title'")
