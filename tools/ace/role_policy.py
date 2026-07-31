# CUI // SP-CTI
"""Capability policy for LLM-generated ACE roles.

``ensure_sme()`` asks a model to invent a domain expert. The model authors WHO
the expert is; this module decides WHAT it may do, from the hand-authored
bundles in ``args/ace/sme_capability_bundles.yaml``.

Two entry points:

``apply_bundle(spec, bundle_name)``
    Stamp a bundle's security fields onto a spec, discarding whatever the model
    proposed for them. Use when writing a generated role.

``validate_generated_role(spec, bundle_name)``
    Return a list of violation strings (empty == clean). Called on write AND on
    load, so a role YAML that was hand-edited after generation — or produced by
    an older, laxer generator — is still caught before it is staffed onto a team.

Design note: the checks below re-derive their limits from ToolRunner's own
constants (``_ALLOWED_PREFIXES``, ``_BLOCKED_RE``) rather than restating them.
A command that this module accepts but ToolRunner would refuse is a latent
surprise at run time; a command this module refuses can never reach ToolRunner
at all. Keeping one source of truth is the point.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import yaml

from icdev._paths import get_data_path
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Security-relevant fields. The model never authors these; apply_bundle always
# overwrites them and validate_generated_role always checks them.
GOVERNED_FIELDS: tuple[str, ...] = (
    "trust_tier",
    "mode",
    "tool_permissions",
    "folder_access",
    "icdev_tools",
    "max_iterations",
)

# Fallback used only when the bundles file is missing or unreadable. Deliberately
# the most restrictive shape possible: a generated role with no bundle config
# should be inert, not unconstrained.
_FALLBACK_BUNDLE: dict[str, Any] = {
    "display_name": "Advisory (fallback)",
    "trust_tier": "red",
    "mode": "steps",
    "tool_permissions": ["Read", "Grep", "Glob"],
    "folder_access": [],
    "icdev_tools": [],
    "max_iterations": 6,
}

_FALLBACK_FORBIDDEN: tuple[str, ...] = (
    "args/ace/roles", "args/ace", ".claude", ".git", "tools", "icdev",
)


def _bundles_path():
    return get_data_path("args") / "ace" / "sme_capability_bundles.yaml"


def load_bundles() -> dict[str, Any]:
    """Load the bundle config, degrading to a restrictive fallback.

    Never raises: a generated role must still be *bounded* when config is
    missing, and failing closed here means falling back to advisory-only.
    """
    try:
        raw = yaml.safe_load(_bundles_path().read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — missing/unparseable config
        logger.warning("role_policy: bundle config unreadable (%s); using fallback", exc)
        return {
            "default_bundle": "advisory",
            "bundles": {"advisory": dict(_FALLBACK_BUNDLE)},
            "forbidden_folder_prefixes": list(_FALLBACK_FORBIDDEN),
        }
    raw.setdefault("default_bundle", "advisory")
    raw.setdefault("bundles", {"advisory": dict(_FALLBACK_BUNDLE)})
    raw.setdefault("forbidden_folder_prefixes", list(_FALLBACK_FORBIDDEN))
    return raw


def get_bundle(bundle_name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Resolve *bundle_name* to ``(name, bundle_dict)``.

    An unknown name falls back to the default bundle rather than raising — an
    unrecognised bundle must not become an unconstrained role.
    """
    cfg = load_bundles()
    bundles = cfg.get("bundles") or {}
    name = (bundle_name or cfg.get("default_bundle") or "advisory").strip()
    if name not in bundles:
        default = cfg.get("default_bundle") or "advisory"
        logger.warning("role_policy: unknown bundle %r; falling back to %r", name, default)
        name = default if default in bundles else next(iter(bundles), "advisory")
    return name, dict(bundles.get(name) or _FALLBACK_BUNDLE)


def apply_bundle(spec: dict[str, Any], bundle_name: str | None = None) -> dict[str, Any]:
    """Return *spec* with every governed field replaced by the bundle's value.

    Mutates and returns the same dict. Anything the model proposed for a
    governed field is dropped, and the drop is logged so it is visible in the
    audit trail rather than silent.
    """
    name, bundle = get_bundle(bundle_name)

    for field in GOVERNED_FIELDS:
        if field not in bundle:
            continue
        proposed = spec.get(field)
        approved = bundle[field]
        if proposed is not None and proposed != approved:
            logger.warning(
                "role_policy: discarding model-proposed %s=%r for role %r (bundle %r allows %r)",
                field, proposed, spec.get("role_id", "?"), name, approved,
            )
        spec[field] = _copy(approved)

    spec["capability_bundle"] = name
    spec["source"] = "sme_generated"
    return spec


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_copy(v) for v in value]
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return value


def _normalise_path(raw: str) -> str:
    """Normalise a folder_access path for prefix comparison.

    Backslashes are folded to forward slashes so a Windows-style entry cannot
    slip past a POSIX prefix check, and ``..`` segments are resolved so
    ``.tmp/ace/../../tools`` does not read as being under ``.tmp/ace``.
    """
    text = str(raw or "").strip().replace("\\", "/").lstrip("/")
    parts: list[str] = []
    for part in PurePosixPath(text).parts:
        if part == "..":
            if parts:
                parts.pop()
            continue
        if part in (".", ""):
            continue
        parts.append(part)
    return "/".join(parts)


def validate_generated_role(
    spec: dict[str, Any],
    bundle_name: str | None = None,
) -> list[str]:
    """Return a list of policy violations for *spec* (empty means clean).

    Checks, in order: trust tier, mode, tool_permissions, folder_access
    (forbidden prefixes and bundle scope), and icdev_tools (bundle membership,
    ToolRunner prefix rules, destructive patterns).
    """
    cfg = load_bundles()
    name, bundle = get_bundle(bundle_name or spec.get("capability_bundle"))
    problems: list[str] = []
    role_id = spec.get("role_id", "?")

    # --- trust tier ---------------------------------------------------------
    # "green" is the specific danger: it clears the CoWorkerThread confidence
    # gate, so the role would act before any human sees it.
    tier = str(spec.get("trust_tier", "")).strip().lower()
    if tier != str(bundle.get("trust_tier", "red")).lower():
        problems.append(
            f"trust_tier {tier!r} does not match bundle {name!r} "
            f"({bundle.get('trust_tier')!r})"
        )
    if tier == "green":
        problems.append("generated roles may never hold trust_tier 'green'")

    # --- mode ---------------------------------------------------------------
    mode = str(spec.get("mode", "steps")).strip().lower()
    if mode != str(bundle.get("mode", "steps")).lower():
        problems.append(f"mode {mode!r} does not match bundle {name!r} ({bundle.get('mode')!r})")

    # --- tool_permissions ---------------------------------------------------
    allowed_perms = set(bundle.get("tool_permissions") or [])
    for perm in spec.get("tool_permissions") or []:
        if perm not in allowed_perms:
            problems.append(f"tool_permission {perm!r} not in bundle {name!r}")

    # --- folder_access ------------------------------------------------------
    forbidden = [
        _normalise_path(p) for p in (cfg.get("forbidden_folder_prefixes") or [])
    ]
    allowed_scopes = {
        _normalise_path(e.get("path", "")): str(e.get("mode", "r"))
        for e in (bundle.get("folder_access") or [])
        if isinstance(e, dict)
    }

    for entry in spec.get("folder_access") or []:
        if not isinstance(entry, dict):
            problems.append(f"folder_access entry is not a mapping: {entry!r}")
            continue
        path = _normalise_path(entry.get("path", ""))
        mode = str(entry.get("mode", "r"))
        if not path:
            problems.append("folder_access entry has an empty path")
            continue

        for bad in forbidden:
            if bad and (path == bad or path.startswith(bad + "/")):
                problems.append(
                    f"folder_access {path!r} is under forbidden prefix {bad!r}"
                )
                break

        if not any(
            path == scope or path.startswith(scope + "/")
            for scope in allowed_scopes
            if scope
        ):
            problems.append(f"folder_access {path!r} is outside bundle {name!r} scope")
            continue

        scope_mode = next(
            (m for s, m in allowed_scopes.items()
             if s and (path == s or path.startswith(s + "/"))),
            "r",
        )
        if "w" in mode and "w" not in scope_mode:
            problems.append(
                f"folder_access {path!r} requests mode {mode!r} but bundle {name!r} "
                f"grants only {scope_mode!r}"
            )

    # --- icdev_tools --------------------------------------------------------
    # Re-derive limits from ToolRunner so this module can never be more
    # permissive than the gate that actually runs the command.
    try:
        from icdev.tools.ace.tool_runner import _ALLOWED_PREFIXES, _BLOCKED_RE
    except Exception:  # noqa: BLE001 — keep validation usable if import fails
        _ALLOWED_PREFIXES = ("python tools/", "python -m tools.", "python icdev/", "python -m icdev.")
        _BLOCKED_RE = None

    allowed_tools = set(bundle.get("icdev_tools") or [])
    for cmd in spec.get("icdev_tools") or []:
        text = str(cmd)
        if text not in allowed_tools:
            problems.append(f"icdev_tool {text!r} not in bundle {name!r}")
            continue
        if not any(text.startswith(p) for p in _ALLOWED_PREFIXES):
            problems.append(f"icdev_tool {text!r} does not match ToolRunner allowed prefixes")
        if _BLOCKED_RE is not None and _BLOCKED_RE.search(text):
            problems.append(f"icdev_tool {text!r} matches a destructive pattern")

    if problems:
        logger.warning("role_policy: role %r failed validation: %s", role_id, problems)
    return problems


def assert_valid(spec: dict[str, Any], bundle_name: str | None = None) -> None:
    """Raise ``PermissionError`` if *spec* violates policy."""
    problems = validate_generated_role(spec, bundle_name)
    if problems:
        raise PermissionError(
            f"generated role {spec.get('role_id', '?')!r} violates capability policy: "
            + "; ".join(problems)
        )
