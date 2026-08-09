"""ACE role loader — loads *.yaml role definitions with in-memory cache and hot-reload."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from icdev._paths import get_data_path
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_ROLES_DIR = get_data_path("args") / "ace" / "roles"
_REQUIRED_FIELDS = {"role_id", "steps", "trust_tier", "tool_permissions"}
_CACHE_TTL = 60  # seconds


class RoleNotFoundError(KeyError):
    """Raised when get_role() is called with an unknown role_id."""


# ---------------------------------------------------------------------------
# Shared parse cache
# ---------------------------------------------------------------------------
#
# RoleLoader.__init__ calls _load(), which parsed every yaml in the roles dir
# (88 files as of writing). The TTL cache lives on the instance, so the seven
# call sites that construct RoleLoader() inline re-parsed the whole directory
# every time and got no benefit from it — a single /coworker request was doing
# 174 yaml parses.
#
# Caching the PARSE (keyed on a stat signature of the directory) rather than
# sharing a loader instance keeps RoleLoader's public API and per-instance
# semantics exactly as they were, including the monkeypatch seams the ACE test
# suite relies on. Hot-reload still works: touching a role yaml changes the
# signature and forces a re-parse.

_PARSE_CACHE: dict[str, tuple[tuple, dict[str, "RoleTemplate"]]] = {}
_PARSE_CACHE_LOCK = threading.Lock()


def _dir_signature(roles_dir: Path) -> tuple:
    """Cheap identity of the roles dir: (name, mtime, size) per yaml file.

    Stat-ing the directory costs microseconds against ~500ms to parse it.
    """
    sig = []
    for path in sorted(roles_dir.glob("*.yaml")):
        try:
            st = path.stat()
            sig.append((path.name, st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((path.name, None, None))
    return tuple(sig)


def _parse_roles_dir(roles_dir: Path) -> dict[str, "RoleTemplate"]:
    """Parse every role yaml in ``roles_dir``, reusing the last parse if unchanged.

    Returns a fresh dict each call so a caller mutating its own cache cannot
    corrupt the shared one.
    """
    key = str(roles_dir)
    sig = _dir_signature(roles_dir)
    hit = _PARSE_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return dict(hit[1])

    cache: dict[str, RoleTemplate] = {}
    for path in sorted(roles_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            role = RoleTemplate.from_dict(data)
            cache[role.role_id] = role
        except Exception as exc:  # noqa: BLE001
            import warnings
            # A skipped role is always a defect, and a role skipped for a trust
            # policy violation is a security-relevant one — warnings.warn alone
            # is invisible in a daemon, so log it too.
            logger.error("role_loader: skipping %s — %s", path.name, exc)
            warnings.warn(f"Skipping {path.name}: {exc}", stacklevel=2)

    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE[key] = (sig, dict(cache))
    return cache


def reset_role_parse_cache() -> None:
    """Drop the shared parse cache. For tests and explicit reload hooks."""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


@dataclass
class RoleStep:
    """A single step in a role definition — supports both plain names and structured dicts."""

    name: str
    tool: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    condition: str | None = None

    @classmethod
    def from_raw(cls, raw: str | dict[str, Any]) -> "RoleStep":
        if isinstance(raw, str):
            return cls(name=raw)
        if isinstance(raw, dict):
            name = raw.get("name") or raw.get("id")
            if not name:
                raise ValueError(f"Structured step missing 'name' or 'id' field: {raw!r}")
            return cls(
                name=str(name),
                tool=raw.get("tool", ""),
                params=dict(raw.get("params") or raw.get("args") or {}),
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
    # Phase 2: filesystem + routine access scope (absent in legacy roles → empty)
    folder_access: list[dict[str, Any]] = field(default_factory=list)
    icdev_tools: list[str] = field(default_factory=list)
    # Agent mode: when mode=="agent" the co-worker runs an agentic LLM loop
    # (icdev.tools.llm.agent_loop) that re-prompts the LLM after each tool call
    # until it calls `done`, instead of the fixed steps list. Defaults preserve
    # the legacy deterministic step-list behaviour.
    mode: str = "steps"
    agent_tools: list[str] = field(default_factory=list)
    max_iterations: int = 12
    # Real-time rubric gating (opt-in). When set to "pipeline" an agent-mode run
    # is routed through run_agent_loop_with_rubric with the delivery-pipeline
    # grader (build -> gates -> revise). Absent/empty/None = off (plain loop).
    rubric: str = ""

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

        # A role must not declare a tool its own trust_tier is forbidden to
        # call. Without this, the contradiction only appeared at run time as a
        # permission-denied string on every single call — which is how qa_agent
        # shipped yellow-tier with `run_tool` and could not do its job at all.
        # Raising here means _parse_roles_dir skips the role with a named
        # reason: an unusable role never gets staffed onto a team.
        from icdev.tools.ace.tool_trust_policy import assert_role_tools_valid

        assert_role_tools_valid(
            data.get("role_id", "?"),
            data.get("trust_tier"),
            data.get("agent_tools"),
            data.get("mode", "steps"),
        )

        return cls(
            role_id=data["role_id"],
            display_name=data.get("display_name", data["role_id"]),
            description=data.get("description", ""),
            version=str(data.get("version", "1.0")),
            trust_tier=data["trust_tier"],
            default_count=int(data.get("default_count", 1)),
            max_instances=int(data.get("max_instances", 1)),
            steps=[RoleStep.from_raw(s) for s in data["steps"]],
            communication=dict(data.get("communication", {})),
            llm_function=data.get("llm_function", ""),
            tool_permissions=list(data["tool_permissions"]),
            genesis_reflex=data.get("genesis_reflex", ""),
            canvas=data.get("canvas", ""),
            personality=dict(data.get("personality") or {}),
            folder_access=list(data.get("folder_access") or []),
            icdev_tools=list(data.get("icdev_tools") or []),
            mode=str(data.get("mode", "steps")),
            agent_tools=list(data.get("agent_tools") or []),
            max_iterations=int(data.get("max_iterations", 12)),
            rubric=str(data.get("rubric") or ""),
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
        cache = _parse_roles_dir(self._roles_dir)
        self._cache = cache
        self._loaded_at = time.monotonic()
        return len(cache)
