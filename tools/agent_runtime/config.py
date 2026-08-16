# CUI // SP-CTI
"""Declarative configuration for the standalone agent runtime (SAG) — hgx-cfg-01.

Before this module the runtime's behaviour lived in roughly a dozen environment
variables spread across nine files, with no place to see them together and no
way to ship a configured default. This adds one file — ``args/agent_runtime.yaml``
— and one loader. It does **not** take anything away:

    explicit call argument  >  environment variable  >  YAML  >  built-in default

The environment stays authoritative. Every accessor here takes the env var name
it defers to and reads it *first*; the YAML value is consulted only when the env
var is absent or unparseable. An operator who exports ``ICDEV_SAG_GOALS=0`` in a
systemd unit must not have it quietly reversed by a file they did not know to
look at, so the layer is inserted *below* the environment rather than above it.

Permission postures (hcx-post-01)
---------------------------------
``args/permission_postures.yaml`` names a *combination* of the safety knobs —
sandbox mode, approval mode, command-approval mode, mutation gate — so a run can
say which posture it was under instead of leaving four independent variables to
be read individually. It extends the chain at the bottom:

    explicit call argument  >  env var  >  agent_runtime.yaml  >  posture  >  default

A posture is a DEFAULT-SETTER. It supplies a value only where neither an
environment variable nor an explicit ``agent_runtime.yaml`` key already did, for
exactly the reason above: naming a posture must not reverse an intent an
operator stated at a higher layer. Selecting the posture is itself layered —
explicit argument, then ``ICDEV_PERMISSION_POSTURE``, then the file's ``default:``
— and a posture flagged ``requires_explicit_selection`` is reachable only from
the first two, so no file that ships with the repo can widen the blast radius.

Design notes
------------
- **LLM-agnostic.** The only model-ish key in the schema is ``llm_function`` — a
  routing function resolved by ``LLMRouter`` against ``args/llm_config.yaml``.
  There is no ``model:`` key: a model id in YAML pins a vendor exactly as hard
  as a model id in Python.
- **OS-agnostic.** The file is read with ``encoding="utf-8"`` and ``newline=""``
  (no newline translation), the repo root is resolved from ``__file__`` rather
  than ``os.getcwd()`` so a worktree or a service started from ``/`` behaves the
  same, and both the checkout layout (``<root>/args/``) and the wheel layout
  (``icdev/data/args/``) are probed.
- **Never raises.** A missing, unreadable or malformed config file degrades to
  the built-in defaults with a debug log. Configuration is not a hard dependency
  of starting an agent.

Usage::

    from tools.agent_runtime.config import load_config
    cfg = load_config()
    cfg.flag("subsystems.standing_goals.enabled", env="ICDEV_SAG_GOALS", default=True)

CLI::

    python -m tools.agent_runtime.config --json     # resolved values + overrides
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.config")

#: Basename of the config file, in ``args/`` (checkout) or ``data/args/`` (wheel).
CONFIG_FILENAME = "agent_runtime.yaml"

#: Absolute path override, for tests and for operators running from a packaged
#: install with a site-local config.
ENV_CONFIG_PATH = "ICDEV_AGENT_RUNTIME_CONFIG"

#: Master enable flag, shared with the ``sag`` entry in component_registry.yaml.
ENV_ENABLED = "ICDEV_SAG_ENABLED"

#: Basename of the posture file, found the same way as :data:`CONFIG_FILENAME`.
POSTURES_FILENAME = "permission_postures.yaml"

#: Absolute path override for the posture file (tests, site-local installs).
ENV_POSTURES_PATH = "ICDEV_PERMISSION_POSTURES_CONFIG"

#: Names the posture in force. Beaten only by an explicit call argument.
ENV_POSTURE = "ICDEV_PERMISSION_POSTURE"

_TRUTHY = ("1", "true", "yes", "on")
_FALSEY = ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------
def _find_config_path(
    filename: str = CONFIG_FILENAME, env_override: str = ENV_CONFIG_PATH
) -> Path | None:
    """Locate ``filename`` from this file's location, not the cwd.

    Walks upward from this module probing both layouts at every level: the
    checkout (``<parent>/args/``) and the installed wheel, where the FORGE data
    layer lands under ``icdev/data/args/``. Returns ``None`` when no file exists
    — an absent config is not an error, it means "use the built-in defaults".

    Both this file and ``permission_postures.yaml`` are found this way, so the
    packaged mirror wins for a wheel and the checkout copy for a worktree, for
    both files together rather than one each.
    """
    override = (os.environ.get(env_override) or "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None

    here = Path(__file__).resolve()
    for parent in here.parents:
        for rel in (("args",), ("data", "args")):
            candidate = parent.joinpath(*rel, filename)
            if candidate.is_file():
                return candidate
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    """Parse the config file. Returns ``{}`` on any failure (never raises)."""
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001 — pyyaml is optional at runtime
        logger.debug("agent_runtime.config: pyyaml unavailable (%s)", exc)
        return {}
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            raw = fh.read()
    except Exception as exc:  # noqa: BLE001 — an unreadable config is not fatal
        logger.debug("agent_runtime.config: cannot read %s: %s", path, exc)
        return {}
    try:
        # lstrip the BOM a Windows editor may have prepended; PyYAML treats it
        # as content and fails the parse.
        data = yaml.safe_load(raw.lstrip("﻿")) or {}
    except Exception as exc:  # noqa: BLE001 — a malformed config falls back
        logger.warning("agent_runtime.config: %s is not valid YAML: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------
def _as_bool(value: Any) -> bool | None:
    """Coerce a YAML/env scalar to bool, or ``None`` when it is not a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUTHY:
            return True
        if token in _FALSEY:
            return False
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Permission postures (hcx-post-01)
# ---------------------------------------------------------------------------
#: The posture in force when nothing selects one. Its values are exactly what
#: this platform shipped before postures existed, so the layer is a rename of
#: the status quo rather than a change to it.
DEFAULT_POSTURE = "workspace-write"

#: Marks a posture that ``args/permission_postures.yaml``'s own ``default:`` key
#: may not select. Widening the blast radius takes an explicit human act — an
#: exported variable or a call argument — never a line in a shipped file.
POSTURE_EXPLICIT_KEY = "requires_explicit_selection"

#: Built-in postures, so the YAML file is documentation rather than a
#: load-bearing dependency: deleting it leaves both postures available and the
#: default unchanged. Every key here is read by a module — ``sandbox`` by
#: ``tools/agents/adapters/codex_cli.py``, the rest by the properties below.
BUILTIN_POSTURES: dict[str, dict[str, Any]] = {
    "workspace-write": {
        "sandbox": "workspace-write",
        "approval_mode": "manual",
        "command_approval_mode": "enforce",
        "allow_mutation": False,
    },
    "danger-full-access": {
        POSTURE_EXPLICIT_KEY: True,
        "sandbox": "danger-full-access",
        "approval_mode": "off",
        "command_approval_mode": "off",
        "allow_mutation": True,
    },
}

#: How a posture name was chosen, reported by :meth:`AgentRuntimeConfig.describe`.
POSTURE_SOURCE_ARGUMENT = "argument"
POSTURE_SOURCE_ENV = "env"
POSTURE_SOURCE_FILE = "file"
POSTURE_SOURCE_BUILTIN = "builtin"


@dataclasses.dataclass(frozen=True)
class PostureSet:
    """Resolved ``args/permission_postures.yaml``.

    Attributes:
        data: The parsed mapping (``{}`` when no file was found).
        path: The file it came from, or ``None`` when built-ins are in force.
    """

    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    path: Path | None = None

    @property
    def postures(self) -> dict[str, dict[str, Any]]:
        """Built-in postures, key-wise overlaid by the file's declarations.

        Overlaying rather than replacing means a partially-declared posture
        keeps the keys it did not mention, so a half-written file cannot blank a
        safety knob into "unset". The one key the file may not relax is
        :data:`POSTURE_EXPLICIT_KEY` on a *built-in* posture: re-declaring
        ``danger-full-access`` without it would restore exactly the "a file
        selected the dangerous posture" case the flag exists to prevent. A
        posture the file invents owns its own flag.
        """
        merged = {name: dict(values) for name, values in BUILTIN_POSTURES.items()}
        declared = self.data.get("postures")
        if isinstance(declared, dict):
            for name, values in declared.items():
                if not isinstance(values, dict):
                    logger.warning(
                        "permission_postures: posture %r is not a mapping; ignored", name
                    )
                    continue
                merged.setdefault(str(name), {}).update(values)
                if BUILTIN_POSTURES.get(str(name), {}).get(POSTURE_EXPLICIT_KEY):
                    merged[str(name)][POSTURE_EXPLICIT_KEY] = True
        return merged

    def requires_explicit_selection(self, name: str) -> bool:
        return bool(self.postures.get(name, {}).get(POSTURE_EXPLICIT_KEY))

    def resolve_name(self, explicit: str | None = None) -> tuple[str, str]:
        """Return ``(posture_name, source)``.

        ``explicit`` → :data:`ENV_POSTURE` → the file's ``default:`` →
        :data:`DEFAULT_POSTURE`. An unknown name is discarded (with a warning)
        and resolution continues to the next layer rather than resolving to
        nothing — the same discipline ``text(choices=...)`` applies, for the
        same reason: a typo must not silently disable a gate.

        The file layer additionally may not name a posture flagged
        :data:`POSTURE_EXPLICIT_KEY`. The two layers above it may, because both
        are acts a person performed.
        """
        known = self.postures
        candidates = (
            (POSTURE_SOURCE_ARGUMENT, explicit, True),
            (POSTURE_SOURCE_ENV, os.environ.get(ENV_POSTURE), True),
            (POSTURE_SOURCE_FILE, self.data.get("default"), False),
        )
        for source, raw, may_be_explicit_only in candidates:
            if raw is None:
                continue
            name = str(raw).strip()
            if not name:
                continue
            if name not in known:
                logger.warning(
                    "permission_postures: %s names unknown posture %r (known: %s); "
                    "falling through",
                    source,
                    name,
                    ", ".join(sorted(known)),
                )
                continue
            if not may_be_explicit_only and self.requires_explicit_selection(name):
                logger.warning(
                    "permission_postures: posture %r requires an explicit selection "
                    "(%s=%s or a call argument) and cannot be chosen by the file's "
                    "default: key; using %r",
                    name,
                    ENV_POSTURE,
                    name,
                    DEFAULT_POSTURE,
                )
                continue
            return name, source
        return DEFAULT_POSTURE, POSTURE_SOURCE_BUILTIN

    def values(self, explicit: str | None = None) -> dict[str, Any]:
        """The knob values of the posture in force."""
        name, _ = self.resolve_name(explicit)
        return self.postures.get(name, {})


# ---------------------------------------------------------------------------
# The config object
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class AgentRuntimeConfig:
    """Resolved ``args/agent_runtime.yaml``, with env-first accessors.

    Attributes:
        data: The parsed mapping (``{}`` when no file was found).
        path: The file it came from, or ``None`` when defaults are in force.
    """

    data: dict[str, Any] = dataclasses.field(default_factory=dict)
    path: Path | None = None
    #: Resolve the posture layer as if this posture were in force, instead of
    #: consulting ``ICDEV_PERMISSION_POSTURE`` and the file's ``default:``
    #: (hcx-post-02). ``dataclasses.replace(cfg, posture_override=name)`` then
    #: answers "what would the knobs be under *that* posture" through the whole
    #: existing precedence chain rather than through a second copy of it — which
    #: is what lets :mod:`tools.agent_runtime.posture_selection` compute a
    #: before/after delta without mutating the process to find out.
    posture_override: str | None = None

    # -- raw lookup ---------------------------------------------------------

    def get(self, dotted: str, default: Any = None) -> Any:
        """Return the value at a dotted path (``subsystems.approval.mode``).

        A missing intermediate key, or an intermediate that is not a mapping,
        yields ``default`` rather than raising — a partial config file is a
        legitimate thing for an operator to write.
        """
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    # -- posture ------------------------------------------------------------

    @property
    def posture_set(self) -> PostureSet:
        """The loaded ``args/permission_postures.yaml`` (process-cached)."""
        return load_postures()

    @property
    def posture_name(self) -> str:
        return self.posture_set.resolve_name(self.posture_override)[0]

    def _posture_value(self, key: str | None) -> Any:
        """The posture's value for ``key``, or ``None`` when it declares none."""
        if not key:
            return None
        return self.posture_set.values(self.posture_override).get(key)

    # -- env-first accessors ------------------------------------------------
    #
    # Each takes the env var it defers to. The env var is read FIRST; the YAML
    # value is a fallback for when it is unset. Unparseable env values fall
    # through to YAML too, so a typo does not silently flip a safety toggle.
    #
    # ``posture_key`` names this knob in the selected permission posture. It is
    # consulted LAST, below the YAML file: a posture sets defaults, it does not
    # overrule a value somebody wrote down for this specific knob.

    def flag(
        self,
        dotted: str,
        *,
        env: str | None = None,
        posture_key: str | None = None,
        default: bool = True,
    ) -> bool:
        """Resolve a boolean: ``env`` → YAML → posture → ``default``."""
        if env:
            raw = os.environ.get(env)
            if raw is not None and str(raw).strip():
                coerced = _as_bool(raw)
                if coerced is not None:
                    return coerced
                logger.debug(
                    "agent_runtime.config: %s=%r is not a boolean; using config", env, raw
                )
        coerced = _as_bool(self.get(dotted))
        if coerced is None:
            coerced = _as_bool(self._posture_value(posture_key))
        return default if coerced is None else coerced

    def integer(
        self,
        dotted: str,
        *,
        env: str | None = None,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int | None:
        """Resolve an int: ``env`` → YAML at ``dotted`` → ``default``, clamped.

        ``None`` is a meaningful value here (``max_total_tokens: null`` means
        "uncapped"), so it is returned rather than coerced to zero.
        """
        resolved: int | None = None
        if env:
            raw = os.environ.get(env)
            if raw is not None and str(raw).strip():
                resolved = _as_int(raw)
                if resolved is None:
                    logger.debug(
                        "agent_runtime.config: %s=%r is not an int; using config",
                        env,
                        raw,
                    )
        if resolved is None:
            resolved = _as_int(self.get(dotted))
        if resolved is None:
            return default
        if minimum is not None:
            resolved = max(minimum, resolved)
        if maximum is not None:
            resolved = min(maximum, resolved)
        return resolved

    def number(
        self, dotted: str, *, env: str | None = None, default: float | None = None
    ) -> float | None:
        """Resolve a float: ``env`` → YAML at ``dotted`` → ``default``."""
        resolved: float | None = None
        if env:
            raw = os.environ.get(env)
            if raw is not None and str(raw).strip():
                resolved = _as_float(raw)
        if resolved is None:
            resolved = _as_float(self.get(dotted))
        return default if resolved is None else resolved

    def text(
        self,
        dotted: str,
        *,
        env: str | None = None,
        posture_key: str | None = None,
        default: str = "",
        choices: "tuple[str, ...] | None" = None,
    ) -> str:
        """Resolve a string: ``env`` → YAML → posture → ``default``.

        When ``choices`` is given, a value outside it is discarded (with a debug
        log) and resolution continues to the next layer — a mistyped approval
        mode must not silently disable the approval gate. That applies to the
        posture layer too: a posture cannot smuggle in a mode the property does
        not accept.
        """
        candidates: list[tuple[str, Any]] = []
        if env:
            candidates.append((env, os.environ.get(env)))
        candidates.append((dotted, self.get(dotted)))
        if posture_key:
            candidates.append((f"posture.{posture_key}", self._posture_value(posture_key)))
        for source, raw in candidates:
            if raw is None:
                continue
            token = str(raw).strip()
            if not token:
                continue
            if choices is not None and token.lower() not in choices:
                logger.debug(
                    "agent_runtime.config: %s=%r not in %s; falling through",
                    source,
                    token,
                    choices,
                )
                continue
            return token.lower() if choices is not None else token
        return default

    # -- named settings -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Master switch (``ICDEV_SAG_ENABLED`` → ``enabled:`` → True).

        Shared with the ``sag`` component in ``args/component_registry.yaml``,
        so ``icdev enable sag`` / ``icdev disable sag`` flips exactly this.
        """
        return self.flag("enabled", env=ENV_ENABLED, default=True)

    def subsystem_enabled(self, name: str, *, env: str, default: bool = True) -> bool:
        """Resolve ``subsystems.<name>.enabled`` with ``env`` winning."""
        return self.flag(f"subsystems.{name}.enabled", env=env, default=default)

    def describe(self) -> dict[str, Any]:
        """A JSON-able view: where the config came from and what resolved.

        Reports which env vars are actually overriding the file, because "the
        config says X but the agent does Y" is otherwise a ten-minute hunt.
        """
        # Local imports: these modules import this one.
        from tools.agent_runtime.goal_context import ENV_DISABLE as GOALS_ENV
        from tools.agent_runtime.goal_context import goal_limit
        from tools.agent_runtime.project_context import ENV_DISABLE as PROJECT_ENV
        from tools.agent_runtime.project_context import (
            ENV_DISABLE_STATE as PROJECT_STATE_ENV,
        )

        posture_name, posture_source = self.posture_set.resolve_name(
            self.posture_override
        )
        resolved = {
            "enabled": self.enabled,
            "posture": {
                "name": posture_name,
                "source": posture_source,
                "values": self.posture_set.postures.get(posture_name, {}),
            },
            "runtime": {
                "llm_function": self.llm_function,
                "max_iterations": self.max_iterations,
                "max_total_tokens": self.max_total_tokens,
                "max_cost_usd": self.max_cost_usd,
            },
            "subsystems": {
                "project_context": {
                    "enabled": self.subsystem_enabled(
                        "project_context", env=PROJECT_ENV
                    ),
                    "include_project_state": self.flag(
                        "subsystems.project_context.include_project_state",
                        env=PROJECT_STATE_ENV,
                    ),
                },
                "standing_goals": {
                    "enabled": self.subsystem_enabled("standing_goals", env=GOALS_ENV),
                    "limit": goal_limit(),
                },
                "profile_memory": {
                    "enabled": self.subsystem_enabled(
                        "profile_memory", env=ENV_PROFILE_MEMORY
                    )
                },
                "skill_proposals": {
                    "enabled": self.subsystem_enabled(
                        "skill_proposals", env=ENV_SKILL_PROPOSALS, default=False
                    )
                },
                "approval": {
                    "mode": self.approval_mode,
                    "risk_function": self.risk_function,
                    "command_mode": self.command_approval_mode,
                },
                "sandbox": {"mode": self.sandbox_mode},
                "mutation": {"allow": self.allow_mutation},
                "delegation": {"child_can_delegate": self.child_can_delegate},
                "toolsets": {"bundle_path": self.toolset_bundle_path},
                "wake": {"max_sleep_seconds": self.max_sleep_seconds},
            },
        }
        env_overrides = {
            name: os.environ[name]
            for name in sorted(OVERRIDE_ENV_VARS)
            if name in os.environ
        }
        return {
            "config_path": str(self.path) if self.path else None,
            "config_found": self.path is not None,
            "postures_path": (
                str(self.posture_set.path) if self.posture_set.path else None
            ),
            "env_overrides": env_overrides,
            "resolved": resolved,
        }

    # -- runtime knobs (read by AgentRuntime.__init__) ----------------------

    @property
    def llm_function(self) -> str:
        return self.text(
            "runtime.llm_function",
            env="ICDEV_SAG_LLM_FUNCTION",
            default=DEFAULT_LLM_FUNCTION,
        )

    @property
    def max_iterations(self) -> int:
        value = self.integer(
            "runtime.max_iterations",
            env="ICDEV_SAG_MAX_ITERATIONS",
            default=DEFAULT_MAX_ITERATIONS,
            minimum=1,
        )
        return DEFAULT_MAX_ITERATIONS if value is None else value

    @property
    def max_total_tokens(self) -> int | None:
        return self.integer(
            "runtime.max_total_tokens", env="ICDEV_SAG_MAX_TOTAL_TOKENS", minimum=1
        )

    @property
    def max_cost_usd(self) -> float | None:
        return self.number("runtime.max_cost_usd", env="ICDEV_SAG_MAX_COST_USD")

    @property
    def approval_mode(self) -> str:
        """``manual`` | ``smart`` | ``off`` — the sag-safe-01 mutation gate."""
        return self.text(
            "subsystems.approval.mode",
            env="ICDEV_SAG_APPROVAL_MODE",
            posture_key="approval_mode",
            default="manual",
            choices=("manual", "smart", "off"),
        )

    @property
    def sandbox_mode(self) -> str:
        """Sandbox confinement passed to an agent CLI that supports one.

        Read by ``tools/agents/adapters/codex_cli.py`` as the layer beneath
        ``ICDEV_CODEX_SANDBOX``. Deliberately unconstrained by ``choices``: the
        accepted mode names belong to the vendor CLI, not to ICDEV, and a list
        here would go stale and start discarding modes that work.
        """
        return self.text(
            "subsystems.sandbox.mode",
            env="ICDEV_SAG_SANDBOX_MODE",
            posture_key="sandbox",
            default="",
        )

    @property
    def risk_function(self) -> str:
        """Routing function used for `smart` risk judgment (never a model id)."""
        return self.text(
            "subsystems.approval.risk_function",
            env="ICDEV_SAG_RISK_FUNCTION",
            default="summarization",
        )

    @property
    def command_approval_mode(self) -> str:
        """``enforce`` | ``dry_run`` | ``off`` — the approval_gate command tier."""
        return self.text(
            "subsystems.approval.command_mode",
            env="ICDEV_AGENT_APPROVAL_MODE",
            posture_key="command_approval_mode",
            default="enforce",
            choices=("enforce", "dry_run", "off"),
        )

    @property
    def allow_mutation(self) -> bool:
        """Whether the fail-closed default gate lets a mutating tool through.

        ``tools.agent_runtime.dispatch.mutation_allowed`` is the public reader
        and delegates here; this is the same resolution it always performed,
        moved onto the config object so a caller holding a
        :attr:`posture_override` copy resolves all four posture-governed knobs
        the same way. It used to be the one knob a caller could not ask *this*
        object about, which would have made hcx-post-02's before/after delta
        reach for the process-wide config for a quarter of its answer.

        Default ``False``, so the gate stays fail-closed when both files are
        missing, empty or malformed.
        """
        return self.flag(
            "subsystems.mutation.allow",
            env="ICDEV_SAG_ALLOW_MUTATION",
            posture_key="allow_mutation",
            default=False,
        )

    @property
    def child_can_delegate(self) -> bool:
        return self.flag(
            "subsystems.delegation.child_can_delegate",
            env="ICDEV_SAG_CAN_DELEGATE",
            default=False,
        )

    @property
    def toolset_bundle_path(self) -> str:
        return self.text("subsystems.toolsets.bundle_path", env="ICDEV_AGENT_TOOLSETS")

    @property
    def max_sleep_seconds(self) -> int:
        """Ceiling on a sleep an agent schedules for ITSELF (agov-wake-02).

        Read by ``tools/agent_runtime/wake_tools.py``. Unlike the other integer
        knobs this never returns ``None`` and never returns a non-positive
        value: at every layer a missing, unparseable or ``<= 0`` setting
        resolves to :data:`DEFAULT_MAX_SLEEP_SECONDS`, because a cap that a typo
        can switch off is not a cap.
        """
        value = self.integer(
            "subsystems.wake.max_sleep_seconds",
            env="ICDEV_SAG_MAX_SLEEP_SECONDS",
            default=DEFAULT_MAX_SLEEP_SECONDS,
        )
        return value if value and value > 0 else DEFAULT_MAX_SLEEP_SECONDS


# Built-in defaults, kept here so the YAML file is documentation rather than a
# load-bearing dependency: deleting it changes nothing about how the agent runs.
DEFAULT_LLM_FUNCTION = "code_generation"
DEFAULT_MAX_ITERATIONS = 12

#: Ceiling on an agent-scheduled sleep (agov-wake-02). A day: long enough for the
#: overnight case, short enough that a mistake surfaces within one working day.
DEFAULT_MAX_SLEEP_SECONDS = 86_400

#: Env vars that override this file — plus the two that locate and select a
#: permission posture. Reported by ``--json`` so an operator can see at a glance
#: why the file is not winning.
OVERRIDE_ENV_VARS = (
    ENV_CONFIG_PATH,
    ENV_POSTURES_PATH,
    ENV_POSTURE,
    ENV_ENABLED,
    "ICDEV_SAG_LLM_FUNCTION",
    "ICDEV_SAG_MAX_ITERATIONS",
    "ICDEV_SAG_MAX_TOTAL_TOKENS",
    "ICDEV_SAG_MAX_COST_USD",
    "ICDEV_SAG_PROJECT_CONTEXT",
    "ICDEV_SAG_PROJECT_STATE",
    "ICDEV_SAG_GOALS",
    "ICDEV_SAG_GOAL_LIMIT",
    "ICDEV_SAG_PROFILE_MEMORY",
    "ICDEV_SAG_SKILL_PROPOSALS",
    "ICDEV_SAG_APPROVAL_MODE",
    "ICDEV_SAG_RISK_FUNCTION",
    "ICDEV_SAG_SANDBOX_MODE",
    "ICDEV_AGENT_APPROVAL_MODE",
    "ICDEV_SAG_ALLOW_MUTATION",
    "ICDEV_SAG_CAN_DELEGATE",
    "ICDEV_AGENT_TOOLSETS",
    "ICDEV_SAG_MAX_SLEEP_SECONDS",
)

#: Env names owned by this module rather than by a subsystem module.
ENV_PROFILE_MEMORY = "ICDEV_SAG_PROFILE_MEMORY"
ENV_SKILL_PROPOSALS = "ICDEV_SAG_SKILL_PROPOSALS"


# ---------------------------------------------------------------------------
# Cached loader
# ---------------------------------------------------------------------------
_LOCK = threading.Lock()
_CACHE: AgentRuntimeConfig | None = None
_POSTURE_CACHE: PostureSet | None = None


def load_postures(
    path: "Path | str | None" = None, *, refresh: bool = False
) -> PostureSet:
    """Return the process-wide :class:`PostureSet` (cached like the config).

    Same contract as :func:`load_config`: the file is parsed once, the
    environment is not cached, and an absent or malformed file degrades to
    :data:`BUILTIN_POSTURES` rather than raising.
    """
    global _POSTURE_CACHE
    if path is not None:
        candidate = Path(path)
        return PostureSet(
            data=_read_yaml(candidate) if candidate.is_file() else {},
            path=candidate if candidate.is_file() else None,
        )
    with _LOCK:
        if _POSTURE_CACHE is None or refresh:
            found = _find_config_path(POSTURES_FILENAME, ENV_POSTURES_PATH)
            _POSTURE_CACHE = PostureSet(
                data=_read_yaml(found) if found else {}, path=found
            )
        return _POSTURE_CACHE


def load_config(
    path: "Path | str | None" = None, *, refresh: bool = False
) -> AgentRuntimeConfig:
    """Return the process-wide :class:`AgentRuntimeConfig` (cached).

    The file is parsed once — it is read on every turn boundary through the
    subsystem accessors, and re-reading it there would put disk I/O in the hot
    path. Environment variables are *not* cached: they are read at every
    access, so ``os.environ`` changes (a test's ``monkeypatch``, an operator's
    ``/set``) take effect immediately.

    Args:
        path: Explicit config file. Bypasses the search and the cache.
        refresh: Re-read the default location, discarding the cache.
    """
    global _CACHE
    if path is not None:
        candidate = Path(path)
        return AgentRuntimeConfig(
            data=_read_yaml(candidate) if candidate.is_file() else {},
            path=candidate if candidate.is_file() else None,
        )
    with _LOCK:
        if _CACHE is None or refresh:
            found = _find_config_path()
            _CACHE = AgentRuntimeConfig(
                data=_read_yaml(found) if found else {}, path=found
            )
        return _CACHE


def reset_cache() -> None:
    """Drop both cached files (tests; ``ICDEV_*_CONFIG`` path changes).

    Postures are cleared alongside the config deliberately: they resolve
    together, so a test that repoints one and not the other would assert against
    a half-refreshed view.
    """
    global _CACHE, _POSTURE_CACHE
    with _LOCK:
        _CACHE = None
        _POSTURE_CACHE = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Show the resolved standalone-agent runtime configuration "
            "(args/agent_runtime.yaml plus the env vars overriding it)."
        )
    )
    parser.add_argument("--config", help="Read this config file instead of the default.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    reset_cache()
    cfg = load_config(args.config, refresh=True) if args.config else load_config(refresh=True)
    report = cfg.describe()

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    posture = report["resolved"]["posture"]
    print(f"config file : {report['config_path'] or '(none — built-in defaults)'}")
    print(f"postures    : {report['postures_path'] or '(none — built-in postures)'}")
    print(f"posture     : {posture['name']} (selected by: {posture['source']})")
    print(f"enabled     : {report['resolved']['enabled']}")
    print("runtime     :")
    for key, value in sorted(report["resolved"]["runtime"].items()):
        print(f"  {key:<18} {value}")
    print("subsystems  :")
    for name, block in sorted(report["resolved"]["subsystems"].items()):
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(block.items()))
        print(f"  {name:<18} {rendered}")
    if report["env_overrides"]:
        print("env overrides (these win over the file):")
        for name, value in sorted(report["env_overrides"].items()):
            print(f"  {name}={value}")
    else:
        print("env overrides: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
