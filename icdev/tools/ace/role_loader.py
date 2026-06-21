"""ACE role loader — loads *.yaml role definitions with in-memory cache and hot-reload."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ROLES_DIR = Path(__file__).parent.parent.parent.parent / "args" / "ace" / "roles"
_REQUIRED_FIELDS = {"role_id", "steps", "trust_tier", "tool_permissions"}
_CACHE_TTL = 60  # seconds


class RoleNotFoundError(KeyError):
    """Raised when get_role() is called with an unknown role_id."""


@dataclass
class RoleStep:
    """A single step in a role definition.

    Supports both plain string steps (name only) and structured steps with a
    tool, params, and an optional condition expression.
    """
    """A single step in a role definition — supports both plain names and structured dicts."""

    name: str
    tool: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    condition: str | None = None  # e.g. "$e2e_result.failed_count > 0"

    @classmethod
    def from_raw(cls, raw: str | dict[str, Any]) -> "RoleStep":
        """Parse a step from a YAML value — either a plain string or a dict."""
        if isinstance(raw, str):
            return cls(name=raw)
        name = raw.get("name", "")
        if not name:
            raise ValueError(f"Structured step missing 'name': {raw!r}")
        return cls(
            name=name,
            tool=str(raw.get("tool", "")),
            params=dict(raw.get("params") or {}),
            condition=raw.get("condition") or None,
        )
    condition: str | None = None

    @classmethod
    def from_raw(cls, raw: "str | dict[str, Any]") -> "RoleStep":
        if isinstance(raw, str):
            return cls(name=raw)
        if isinstance(raw, dict):
            if "name" not in raw:
                raise ValueError(f"Structured step missing 'name' field: {raw!r}")
            return cls(
                name=raw["name"],
                tool=raw.get("tool", ""),
                params=dict(raw.get("params") or {}),
                condition=raw.get("condition"),
            )
        raise TypeError(f"Expected str or dict for step, got {type(raw)!r}")


@dataclass
class RoleTemplate:
    role_id: str
    display_name: str
    description: str
    version: str
    trust_tier: str
    default_count: int
    max_instances: int
    steps: list[RoleStep]
    communication: dict[str, Any]
    llm_function: str
    tool_permissions: list[str]
    genesis_reflex: str
    # Extended fields (optional — absent in legacy role YAMLs)
    canvas: str = ""
    personality: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Expose listen_topics at top level for dispatcher hot-path
        if not hasattr(self, "_listen_topics_cache"):
            object.__setattr__(
                self, "_listen_topics_cache",
                list(self.communication.get("listen_topics") or [])
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleTemplate":
        missing = _REQUIRED_FIELDS - data.keys()
        if missing:
            raise ValueError(f"Role YAML missing required fields: {sorted(missing)}")
        steps = [RoleStep.from_raw(s) for s in data["steps"]]
        return cls(
            role_id=data["role_id"],
            display_name=data.get("display_name", data["role_id"]),
            description=data.get("description", ""),
            version=str(data.get("version", "1.0")),
            trust_tier=data["trust_tier"],
            default_count=int(data.get("default_count", 1)),
            max_instances=int(data.get("max_instances", 1)),
            steps=steps,
            communication=dict(data.get("communication", {})),
            llm_function=data.get("llm_function", ""),
            tool_permissions=list(data["tool_permissions"]),
            genesis_reflex=data.get("genesis_reflex", ""),
            canvas=data.get("canvas", ""),
            personality=dict(data.get("personality") or {}),
        )


class RoleLoader:
    """Loads ACE role definitions from YAML files with optional hot-reload."""

    def __init__(self, roles_dir: Path | None = None, hot_reload: bool = True) -> None:
        self._roles_dir = Path(roles_dir) if roles_dir else _ROLES_DIR
        self._hot_reload = hot_reload
        self._cache: dict[str, RoleTemplate] = {}
        self._loaded_at: float = 0.0
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_role(self, role_id: str) -> RoleTemplate:
        """Return a RoleTemplate by role_id, refreshing the cache if stale."""
        self._maybe_reload()
        if role_id not in self._cache:
            raise RoleNotFoundError(f"Unknown role: {role_id!r}")
        return self._cache[role_id]

    def list_roles(self) -> list[RoleTemplate]:
        """Return all loaded RoleTemplates sorted by role_id."""
        self._maybe_reload()
        return sorted(self._cache.values(), key=lambda r: r.role_id)

    def reload(self) -> int:
        """Force a full reload from disk; returns the count of roles loaded."""
        return self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_reload(self) -> None:
        if self._hot_reload and (time.monotonic() - self._loaded_at) > _CACHE_TTL:
            self._load()

    def _load(self) -> int:
        cache: dict[str, RoleTemplate] = {}
        for path in sorted(self._roles_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                role = RoleTemplate.from_dict(data)
                cache[role.role_id] = role
            except Exception as exc:  # noqa: BLE001
                import warnings
                warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)
        self._cache = cache
        self._loaded_at = time.monotonic()
        return len(cache)
