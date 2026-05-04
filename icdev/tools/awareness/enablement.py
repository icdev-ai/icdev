# CUI // SP-CTI
"""Enablement awareness helper for the Internal Awareness Engine.

Purpose: make every awareness subsystem cognitive about which ICDEV
modules / canvases / subsystems are actually enabled in .env. The
component indexer tags nodes with `enabled=true|false`, the health
prober skips disabled components, the drift detector ignores them,
the gap detector excludes them from route-coverage rules, and the
/components-map UI visually dims them.

Why this matters (user requirement 2026-04-11):
  "Not all modules will be enabled (see .env). This process must be
  cognitive about the state of the modules, components, and etc.
  Only monitor when they are enabled. The context of the chat and
  KG must reflect what is enable/disabled."

Design:
  * .env is the single source of truth for *_ENABLED flags.
  * args/awareness_enablement_map.yaml is a declarative mapping from
    component (entity_type + path_glob) to the list of flags that
    must ALL be true for the component to be considered enabled.
  * Unmapped components default to always-enabled (never blocks
    components we don't have an explicit rule for).
  * Flags are cached with mtime invalidation; a fresh .env edit
    causes the next call to reload.
  * A stable signature lets downstream consumers (Genesis reflex)
    detect configuration drift and trigger a full re-index.

Public API:
  load_enablement_flags(env_file=None) -> Dict[str, bool]
  enabled_flags_signature(flags=None) -> str
  load_enablement_map(map_file=None) -> List[Dict]
  is_component_enabled(component_id, properties, flags=None, mapping=None) -> bool

Pure stdlib + optional PyYAML. No LLM, no network, air-gap safe.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_ENV_FILE = BASE_DIR / ".env"
_DEFAULT_ENV_EXAMPLE_FILE = BASE_DIR / ".env.example"
_DEFAULT_MAP_FILE = BASE_DIR / "args" / "awareness_enablement_map.yaml"

# Values considered truthy / falsy for env var parsing.
_TRUTHY = frozenset({"true", "1", "yes", "on", "y", "t"})
_FALSY = frozenset({"false", "0", "no", "off", "n", "f", ""})

# Matches `*_ENABLED=value` lines (case-sensitive on the name side so
# we only pick up ICDEV-style flags and avoid eating random env vars).
_ENABLED_LINE_RE = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*_ENABLED)\s*=\s*(.*?)\s*$"
)

# Module-level caches with mtime invalidation. Populated on first
# successful load; invalidated when the underlying file's mtime
# changes. Only the default paths are cached; explicit overrides
# bypass the cache so tests do not pollute production state.
_FLAGS_CACHE: Dict[str, Any] = {"mtime": None, "flags": None}
_MAP_CACHE: Dict[str, Any] = {"mtime": None, "mapping": None}


def _parse_bool(value: str) -> bool:
    """Parse a shell-style boolean, stripping quotes and whitespace.

    Unknown values default to False (conservative — a misconfigured
    flag should not accidentally enable monitoring of a subsystem).
    """
    v = value.strip().strip('"').strip("'").lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return False


def _parse_enabled_flags_from_text(text: str) -> Dict[str, bool]:
    """Extract ``*_ENABLED`` flags from the contents of a dotenv file."""
    result: Dict[str, bool] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENABLED_LINE_RE.match(line)
        if m:
            result[m.group(1)] = _parse_bool(m.group(2))
    return result


def _read_flags_file(path: Path) -> Dict[str, bool]:
    """Read a single dotenv-style file, returning its ``*_ENABLED`` flags.

    Returns an empty dict on missing / unreadable file.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return _parse_enabled_flags_from_text(text)


def load_enablement_flags(
    env_file: Optional[Path] = None,
    env_example_file: Optional[Path] = None,
) -> Dict[str, bool]:
    """Parse .env (with .env.example as a fallback) and return all
    ``*_ENABLED`` flags as a dict.

    Resolution order (standard dotenv pattern):
        1. Start with flags from .env.example as defaults
        2. Override with flags from .env

    This means an operator's .env can override specific flags but any
    flag they don't explicitly set inherits the .env.example default.
    Without this, unspecified flags would fall to the conservative
    "missing = False" rule which would falsely disable subsystems
    whose defaults in .env.example are ``=true``.

    Args:
        env_file:         override the .env location (for tests).
        env_example_file: override the .env.example location (tests).
                          Pass ``Path("")`` to disable example fallback.

    Missing files return an empty contribution — if BOTH are missing
    the result is ``{}``. Comments and non-matching lines are ignored.
    Cached (mtime-invalidated) only when both override arguments are
    None so test calls do not pollute production state.
    """
    use_cache = env_file is None and env_example_file is None
    env_path = env_file if env_file is not None else _DEFAULT_ENV_FILE

    # Example-file fallback only applies when BOTH overrides are
    # None (production path). If a test passes env_file only, we
    # skip the example fallback so the test is not contaminated by
    # the real .env.example. Tests that WANT both can pass both.
    if env_example_file is not None:
        example_path = env_example_file
    elif env_file is None:
        example_path = _DEFAULT_ENV_EXAMPLE_FILE
    else:
        example_path = Path("")  # non-existent sentinel → empty contribution

    # Compose a cache key from both files' mtimes so either file
    # changing invalidates the cache.
    try:
        env_mtime = env_path.stat().st_mtime if env_path.exists() else None
    except OSError:
        env_mtime = None
    try:
        ex_mtime = example_path.stat().st_mtime if example_path.exists() else None
    except OSError:
        ex_mtime = None
    cache_key = (env_mtime, ex_mtime)

    if use_cache and _FLAGS_CACHE["mtime"] == cache_key and _FLAGS_CACHE["flags"] is not None:
        return dict(_FLAGS_CACHE["flags"])

    # .env.example first (defaults), then .env (overrides)
    flags: Dict[str, bool] = {}
    flags.update(_read_flags_file(example_path))
    flags.update(_read_flags_file(env_path))

    if use_cache:
        _FLAGS_CACHE["mtime"] = cache_key
        _FLAGS_CACHE["flags"] = dict(flags)

    return flags


def enabled_flags_signature(flags: Optional[Dict[str, bool]] = None) -> str:
    """Return a stable SHA256 hash of the enablement flag set.

    Used by downstream consumers (e.g., the Genesis awareness reflex)
    to detect when .env has changed since the last full index, so
    they can trigger a re-index with the new enablement state.

    The hash is stable across Python versions because we serialize
    via json.dumps with sorted keys and a deterministic separator.
    """
    if flags is None:
        flags = load_enablement_flags()
    serialized = json.dumps(flags, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_enablement_map(
    map_file: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Load ``args/awareness_enablement_map.yaml``.

    The YAML schema is::

        mappings:
          - entity_type: canvas_module
            path_glob: "tools/network_canvas/**"
            requires: [ICDEV_NETWORK_ENABLED, ICDEV_NDC_ENABLED]
          - ...

    Returns a list of validated mapping dicts with keys
    ``entity_type``, ``path_glob``, ``requires``. Returns an empty
    list on missing file, malformed YAML, missing pyyaml, or an
    unexpected top-level shape — the is_component_enabled() default
    (always-on) applies in that case.
    """
    path = map_file if map_file is not None else _DEFAULT_MAP_FILE
    if not path.exists():
        return []

    use_cache = map_file is None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []

    if use_cache and _MAP_CACHE["mtime"] == mtime and _MAP_CACHE["mapping"] is not None:
        return [dict(e) for e in _MAP_CACHE["mapping"]]

    try:
        import yaml  # type: ignore
    except ImportError:
        return []

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    mappings = raw.get("mappings", []) if isinstance(raw, dict) else []
    if not isinstance(mappings, list):
        return []

    cleaned: List[Dict[str, Any]] = []
    for entry in mappings:
        if not isinstance(entry, dict):
            continue
        et = entry.get("entity_type")
        pg = entry.get("path_glob")
        req = entry.get("requires", [])
        if et is None or pg is None:
            continue
        if not isinstance(req, list):
            continue
        cleaned.append(
            {
                "entity_type": str(et),
                "path_glob": str(pg),
                "requires": [str(r) for r in req],
            }
        )

    if use_cache:
        _MAP_CACHE["mtime"] = mtime
        _MAP_CACHE["mapping"] = [dict(e) for e in cleaned]

    return cleaned


def is_component_enabled(
    component_id: str,
    properties: Dict[str, Any],
    flags: Optional[Dict[str, bool]] = None,
    mapping: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Determine whether a component should be monitored.

    Args:
        component_id: the kg_nodes.id (unused for now but reserved
                      for future per-component overrides).
        properties:   the kg_nodes.properties_json dict for this
                      component. Must contain ``entity_type`` and
                      ideally ``file_path`` for path-glob matching.
        flags:        override the flag dict (for tests). None loads
                      from .env via the default cache.
        mapping:      override the mapping list (for tests). None
                      loads from args/awareness_enablement_map.yaml.

    Resolution:
        1. Find all mapping entries whose ``entity_type`` matches
           AND whose ``path_glob`` matches the component's file_path.
        2. Collect the union of required flags across matching rules.
        3. Return True iff every required flag is True in the flag set.
        4. If NO mapping rule matches, return True (default-on). We
           don't know what gates the component so we assume it's
           always enabled — callers should add a mapping rule to
           explicitly disable-by-default if that's not desired.

    All comparison is case-sensitive for entity_type and flag names;
    path_glob matching uses fnmatch with forward-slash paths.
    """
    if flags is None:
        flags = load_enablement_flags()
    if mapping is None:
        mapping = load_enablement_map()

    entity_type = str(properties.get("entity_type") or "")
    file_path = str(properties.get("file_path") or "").replace("\\", "/")

    required_flags: set = set()
    matched = False
    for entry in mapping:
        if entry.get("entity_type") != entity_type:
            continue
        if not fnmatch.fnmatch(file_path, str(entry.get("path_glob", ""))):
            continue
        matched = True
        for flag_name in entry.get("requires", []):
            required_flags.add(str(flag_name))

    if not matched:
        # No rule gates this component → always enabled
        return True

    for flag_name in required_flags:
        if not flags.get(flag_name, False):
            return False
    return True


def _reset_caches_for_test() -> None:
    """Clear the module-level caches. Intended for test isolation —
    normal callers should never need this.
    """
    _FLAGS_CACHE["mtime"] = None
    _FLAGS_CACHE["flags"] = None
    _MAP_CACHE["mtime"] = None
    _MAP_CACHE["mapping"] = None
