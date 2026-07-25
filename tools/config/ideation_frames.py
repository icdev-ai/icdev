# CUI // SP-CTI
"""Loader for the ideation frame library (args/ideation_frames.yaml).

The single config-driven source of perspective sets for multi-branch LLM
reasoning modes. Frame sets are DATA (versioned, per-function selectable),
not a hardcoded Python list — restoring the FORGE args/tools separation that
the old module-level _COUNCIL_ADVISORS constant broke.

Two frame modes:
  * generative  -> produce NEW candidate ideas (Divergence, dvg-core-*). The
                   branch system prompt forbids evaluation/ranking.
  * evaluative  -> critique a proposal already on the table (Council).

Design: pure loading + validation, no LLM calls, no DB, no I/O beyond reading
the YAML. Never raises to the caller on a bad frame during runtime lookup —
malformed frames are skipped so a partially-broken library still yields the
valid frames rather than taking a reasoning mode offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_ARGS_DIR = Path(__file__).resolve().parents[2] / "args"
_FRAMES_PATH = _ARGS_DIR / "ideation_frames.yaml"

VALID_MODES = ("generative", "evaluative")
_REQUIRED_FRAME_KEYS = ("key", "name", "stance", "prompt_fragment", "mode")


@dataclass(frozen=True)
class Frame:
    """One perspective frame within a set."""

    key: str
    name: str
    stance: str
    prompt_fragment: str
    mode: str


class IdeationFrameError(ValueError):
    """Raised by validate_library() when the frame library is structurally invalid."""


def _load_raw(path: Optional[Path] = None) -> dict[str, Any]:
    import yaml

    p = path or _FRAMES_PATH
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_version(path: Optional[Path] = None) -> str:
    """Return the library version string (stamped into chain telemetry).

    Returns "unversioned" when the file is absent or omits a version.
    """
    return str(_load_raw(path).get("version", "unversioned"))


def list_frame_sets(path: Optional[Path] = None) -> list[str]:
    """Return the names of all defined frame sets."""
    return list((_load_raw(path).get("frame_sets") or {}).keys())


def _coerce_frames(raw_frames: Any) -> list[Frame]:
    frames: list[Frame] = []
    for entry in raw_frames or []:
        if not isinstance(entry, dict):
            continue
        if not all(entry.get(k) for k in _REQUIRED_FRAME_KEYS):
            continue
        mode = str(entry["mode"]).strip().lower()
        if mode not in VALID_MODES:
            continue
        frames.append(
            Frame(
                key=str(entry["key"]).strip(),
                name=str(entry["name"]).strip(),
                stance=str(entry["stance"]).strip(),
                prompt_fragment=str(entry["prompt_fragment"]).strip(),
                mode=mode,
            )
        )
    return frames


def get_frames(
    frame_set: str,
    *,
    mode: Optional[str] = None,
    path: Optional[Path] = None,
) -> list[Frame]:
    """Return the valid frames in ``frame_set``, optionally filtered by mode.

    Malformed frames are skipped (never raised) so a partially-broken library
    still yields its valid frames. Returns [] for an unknown set — callers hold
    a code-level fallback for that case.
    """
    sets = _load_raw(path).get("frame_sets") or {}
    spec = sets.get(frame_set)
    if not spec:
        return []
    raw_frames = spec.get("frames") if isinstance(spec, dict) else spec
    frames = _coerce_frames(raw_frames)
    if mode:
        m = str(mode).strip().lower()
        frames = [f for f in frames if f.mode == m]
    return frames


def get_frame_pairs(
    frame_set: str,
    *,
    mode: Optional[str] = None,
    path: Optional[Path] = None,
) -> list[tuple[str, str]]:
    """Return frames as (name, prompt_fragment) tuples — the shape the chain
    orchestrator's branch/advisor builders consume."""
    return [(f.name, f.prompt_fragment) for f in get_frames(frame_set, mode=mode, path=path)]


def validate_library(path: Optional[Path] = None) -> dict[str, Any]:
    """Strictly validate the whole library; raise IdeationFrameError on any defect.

    Used by tests and a pre-merge coherence check — unlike the runtime getters,
    this is fail-loud. Enforces: a version string; at least one frame set; every
    frame has all required keys; mode in VALID_MODES; keys unique within a set;
    and no two frames in a set share an identical name or prompt_fragment (the
    orthogonality smoke test — identical text is a wasted branch).

    Returns a summary dict {version, sets: {name: frame_count}} on success.
    """
    raw = _load_raw(path)
    if not raw:
        raise IdeationFrameError(f"frame library missing or empty at {path or _FRAMES_PATH}")
    if not str(raw.get("version", "")).strip():
        raise IdeationFrameError("frame library must declare a non-empty 'version'")

    sets = raw.get("frame_sets")
    if not isinstance(sets, dict) or not sets:
        raise IdeationFrameError("frame library must define at least one frame set under 'frame_sets'")

    summary: dict[str, Any] = {"version": raw["version"], "sets": {}}
    for set_name, spec in sets.items():
        raw_frames = spec.get("frames") if isinstance(spec, dict) else spec
        if not isinstance(raw_frames, list) or not raw_frames:
            raise IdeationFrameError(f"frame set '{set_name}' has no frames")

        seen_keys: set[str] = set()
        seen_names: set[str] = set()
        seen_fragments: set[str] = set()
        for i, entry in enumerate(raw_frames):
            if not isinstance(entry, dict):
                raise IdeationFrameError(f"frame set '{set_name}' entry {i} is not a mapping")
            missing = [k for k in _REQUIRED_FRAME_KEYS if not str(entry.get(k, "")).strip()]
            if missing:
                raise IdeationFrameError(
                    f"frame set '{set_name}' entry {i} missing/empty keys: {missing}"
                )
            mode = str(entry["mode"]).strip().lower()
            if mode not in VALID_MODES:
                raise IdeationFrameError(
                    f"frame set '{set_name}' key '{entry['key']}' has invalid mode '{mode}'"
                )
            key = str(entry["key"]).strip()
            if key in seen_keys:
                raise IdeationFrameError(f"frame set '{set_name}' has duplicate key '{key}'")
            seen_keys.add(key)
            name = str(entry["name"]).strip().lower()
            if name in seen_names:
                raise IdeationFrameError(
                    f"frame set '{set_name}' has duplicate frame name '{entry['name']}' — wasted branch"
                )
            seen_names.add(name)
            fragment = str(entry["prompt_fragment"]).strip().lower()
            if fragment in seen_fragments:
                raise IdeationFrameError(
                    f"frame set '{set_name}' key '{key}' repeats an existing prompt_fragment — wasted branch"
                )
            seen_fragments.add(fragment)
        summary["sets"][set_name] = len(raw_frames)
    return summary


if __name__ == "__main__":  # pragma: no cover - manual validation entrypoint
    import json

    print(json.dumps(validate_library(), indent=2))
