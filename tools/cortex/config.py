# CUI // SP-CTI
"""Cortex behavior config (args/cortex_config.yaml) + air-gap routing invariant.

Kept separate from ``api.py`` on purpose: ``api.py`` is guaranteed free of
provider/model references (tests/cortex/test_api_core.py::
test_no_model_id_literals_in_module), so this module is the one place in the
Cortex package that knows what the local provider tier looks like.

Two responsibilities:

1. ``load_cortex_config()`` — the search/governance/analyst behavior knobs
   later Cortex modules consume instead of hardcoding. Resolution follows the
   single-source rule from PR #139: the file lives next to whichever
   ``args/llm_config.yaml`` ``resolve_llm_config_path()`` picks, overridable
   via ``$ICDEV_CORTEX_CONFIG``.

2. ``assert_airgap_ready()`` — the day-one invariant that every logical
   routing function the facade uses keeps a local (no-API-key ollama tier)
   fallback in its ``args/llm_config.yaml`` chain, plus the per-request
   ``airgap_exclusions()`` used to force local-only resolution when
   ``ICDEV_AIRGAP=1`` or ``CortexContext.air_gap`` is set.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is an ICDEV requirement
    yaml = None

from tools.logging.icdev_logger import get_logger

# Single resolution point for args/llm_config.yaml (PR #139).
try:
    from tools.llm.config_path import resolve_llm_config_path
except ImportError:  # packaged-only install
    from icdev.tools.llm.config_path import resolve_llm_config_path

logger = get_logger("icdev.cortex.config")

AIRGAP_ENV_VAR = "ICDEV_AIRGAP"
CORTEX_CONFIG_ENV_VAR = "ICDEV_CORTEX_CONFIG"
CORTEX_CONFIG_FILENAME = "cortex_config.yaml"

# The local provider tier: an `ollama`-type provider with NO api_key_env.
# api_key_env presence is what distinguishes the local daemon from the cloud
# variant of the same provider type (ollama vs ollama_cloud).
_LOCAL_PROVIDER_TYPE = "ollama"

# Every logical routing function the Cortex facade passes to LLMRouter.invoke.
# tests/cortex/test_airgap_assertion.py pins this against the constants in
# api.py so the two cannot drift.
CORTEX_ROUTING_FUNCTIONS = (
    "cortex_complete",
    "cortex_classify",
    "cortex_extract",
    "cortex_search_rewrite",
    "cortex_analyst",
)

# Fallback skeleton when args/cortex_config.yaml is missing or unreadable.
# The YAML file is the source of truth — keep this minimal, not a mirror.
CORTEX_CONFIG_DEFAULTS: Dict = {
    "search": {
        "strategy_weights": {"rag": 1.0, "graph": 0.8, "dic": 0.9, "kb": 0.6},
        "rrf_k": 60,
        "crag_threshold": 0.55,
        "timeouts": {"default": 10.0},
    },
    "governance": {
        # Fallback matches the shipped args/cortex_config.yaml: fail-open by
        # default (preserves the platform's actual behavior; operators opt into
        # fail-closed by setting governance.fail_closed: true).
        "fail_closed": False,
        "skip_grounding_for_plain_complete": True,
    },
    "analyst": {
        "nlq_fallback_enabled": True,
    },
    "cache": {
        # Off by default — matches shipped args/cortex_config.yaml. See there for
        # the security model (key folds tenant/classification/domain/air_gap).
        "enabled": False,
        "max_entries": 512,
        "operations": ["cortex.complete", "cortex.search", "cortex.ask"],
        "ttl_seconds": {
            "default": 300,
            "cortex.complete": 900,
            "cortex.search": 120,
            "cortex.ask": 30,
        },
    },
}

# mtime-keyed cache: str(path) -> (mtime, merged config)
_config_cache: Dict[str, tuple] = {}


class CortexAirgapError(RuntimeError):
    """The LLM routing config cannot serve Cortex in an air-gapped environment."""


# ---------------------------------------------------------------------------
# Behavior config (args/cortex_config.yaml)
# ---------------------------------------------------------------------------
def resolve_cortex_config_path() -> Path:
    """Return the cortex_config.yaml every ICDEV component should read."""
    override = os.environ.get(CORTEX_CONFIG_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return resolve_llm_config_path().parent / CORTEX_CONFIG_FILENAME


def _load_yaml(path: Path) -> Dict:
    if yaml is None or not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("cortex: unreadable YAML at %s (%s)", path, exc)
        return {}


def _deep_merge(base: Dict, override: Dict) -> Dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_cortex_config(config_path=None, refresh: bool = False) -> Dict:
    """Load args/cortex_config.yaml deep-merged over CORTEX_CONFIG_DEFAULTS.

    Cached per path+mtime so hot paths (search fan-out) can call freely;
    ``refresh=True`` bypasses the cache.
    """
    path = Path(config_path) if config_path else resolve_cortex_config_path()
    key = str(path)
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    if not refresh:
        cached = _config_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    config = _deep_merge(CORTEX_CONFIG_DEFAULTS, _load_yaml(path))
    _config_cache[key] = (mtime, config)
    return config


def resolve_fail_closed(ctx=None, config_path=None) -> bool:
    """Effective fail-closed posture for a Cortex call.

    An explicit ``ctx.fail_closed`` (True or False) always wins. When it is
    None — the default, meaning "use the platform policy" — fall back to
    ``governance.fail_closed`` in ``args/cortex_config.yaml``. This is what
    makes that config key live: previously the pipeline read only the raw
    (always-defaulted-False) ``ctx.fail_closed`` and the config was dead.
    """
    explicit = getattr(ctx, "fail_closed", None)
    if explicit is not None:
        return bool(explicit)
    cfg = load_cortex_config(config_path)
    return bool((cfg.get("governance") or {}).get("fail_closed", False))


# ---------------------------------------------------------------------------
# Air-gap invariant
# ---------------------------------------------------------------------------
def _load_llm_config(config_path=None) -> Dict:
    path = Path(config_path) if config_path else resolve_llm_config_path()
    return _load_yaml(path)


def _is_local_model(model_cfg: Optional[Dict], providers: Dict) -> bool:
    """True when the model resolves through the local (no-API-key) tier."""
    provider_name = (model_cfg or {}).get("provider", "")
    provider_cfg = providers.get(provider_name) or {}
    return provider_cfg.get("type") == _LOCAL_PROVIDER_TYPE and not provider_cfg.get("api_key_env")


def airgap_active(ctx=None) -> bool:
    """True when ICDEV_AIRGAP is set or the CortexContext requests air-gap."""
    if os.environ.get(AIRGAP_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(ctx is not None and getattr(ctx, "air_gap", False))


def airgap_exclusions(ctx=None, config_path=None) -> Optional[List[str]]:
    """model_ids to pass as LLMRouter.invoke(exclude_model_ids=...) offline.

    Returns None when air-gap is not active. When active, returns every
    model_id in the LLM config that only resolves through a non-local
    provider, so chain-walking skips straight to the local tier. A model_id
    also served by a local entry is never excluded.
    """
    if not airgap_active(ctx):
        return None
    config = _load_llm_config(config_path)
    providers = config.get("providers") or {}
    local_ids, remote_ids = set(), set()
    for name, model_cfg in (config.get("models") or {}).items():
        model_id = (model_cfg or {}).get("model_id", name)
        if _is_local_model(model_cfg, providers):
            local_ids.add(model_id)
        else:
            remote_ids.add(model_id)
    return sorted(remote_ids - local_ids)


def assert_airgap_ready(config_path=None) -> None:
    """Fail at import, not at first offline call (ctx-core-03 invariant).

    Verifies every routing function in CORTEX_ROUTING_FUNCTIONS has at least
    one local-tier model in its args/llm_config.yaml chain, raising
    CortexAirgapError listing every missing entry. A config with no routing:
    section at all (stub/minimal test environments, or PyYAML unavailable)
    is logged and skipped — there is nothing meaningful to validate.
    """
    path = Path(config_path) if config_path else resolve_llm_config_path()
    config = _load_yaml(path)
    routing = config.get("routing") or {}
    if not routing:
        logger.warning(
            "cortex: air-gap readiness not verified — no routing section at %s", path
        )
        return

    providers = config.get("providers") or {}
    models = config.get("models") or {}
    missing = []
    for function in CORTEX_ROUTING_FUNCTIONS:
        chain = (routing.get(function) or {}).get("chain") or []
        if not chain:
            missing.append(f"  - {function}: no routing entry (or empty chain)")
        elif not any(_is_local_model(models.get(name), providers) for name in chain):
            missing.append(
                f"  - {function}: chain {chain} has no local {_LOCAL_PROVIDER_TYPE}-tier model"
            )

    if missing:
        raise CortexAirgapError(
            "Cortex air-gap invariant violated in {path}:\n{missing}\n"
            "Every cortex_* routing chain must keep at least one model whose "
            "provider is a local '{tier}' provider (type: {tier}, no "
            "api_key_env) so the facade still resolves when ICDEV_AIRGAP=1 or "
            "CortexContext.air_gap is set. Fix: add a local-tier model (e.g. "
            "one of the *-local entries) to each chain listed above, under "
            "routing: in {path}.".format(
                path=path, missing="\n".join(missing), tier=_LOCAL_PROVIDER_TYPE
            )
        )
