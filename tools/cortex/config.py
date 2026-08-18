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
    # analyst.ask(summarize=True). Absent from this tuple until ctx-trust-01,
    # so assert_airgap_ready() never validated the one Cortex LLM call that was
    # NOT going through the router singleton or passing air-gap exclusions.
    "cortex_summarize",
)

# Fallback skeleton when args/cortex_config.yaml is missing or unreadable.
# The YAML file is the source of truth — keep this minimal, not a mirror.
CORTEX_CONFIG_DEFAULTS: Dict = {
    "search": {
        # sme: 0.0 is a policy floor, not a default to tune — see the note on
        # this key in args/cortex_config.yaml. It must survive an unreadable
        # config file, because "config missing" is exactly when an advisory
        # result would otherwise fall back to the neutral 1.0 and outrank
        # evidence. currency (cef-bck-01) is an ordinary retrieval weight.
        "strategy_weights": {
            "rag": 1.0, "graph": 0.8, "dic": 0.9, "kb": 0.6,
            "currency": 0.7, "external": 0.5, "sme": 0.0,
        },
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
    # cortex.resolve (cef-rsv-01). In-boundary evidentiary rungs only: `external`
    # would make a currency question the trigger for an outbound call, and `sme`
    # is an LLM opinion, which cannot be evidence for a deterministic verdict.
    # This fallback matters more than most — it is what applies when the config
    # file is unreadable, i.e. exactly when a permissive default would quietly
    # widen the rung set.
    "resolve": {
        "backends": ["currency", "rag", "dic", "graph", "kb"],
    },
    "cache": {
        # Off by default — matches shipped args/cortex_config.yaml. See there for
        # the security model (key folds tenant/classification/domain/air_gap).
        "enabled": False,
        "max_entries": 512,
        "operations": [
            "cortex.complete", "cortex.search", "cortex.ask",
            "cortex.classify", "cortex.extract",
        ],
        "ttl_seconds": {
            "default": 300,
            "cortex.complete": 900,
            "cortex.search": 120,
            "cortex.ask": 30,
            "cortex.classify": 600,
            "cortex.extract": 900,
        },
    },
}

# mtime-keyed cache: str(path) -> (mtime, merged config)
_config_cache: Dict[str, tuple] = {}

# Same shape, for the raw args/llm_config.yaml read by airgap_exclusions().
_llm_config_cache: Dict[str, tuple] = {}


class CortexAirgapError(RuntimeError):
    """The LLM routing config cannot serve Cortex in an air-gapped environment."""


# ---------------------------------------------------------------------------
# Behavior config (args/cortex_config.yaml)
# ---------------------------------------------------------------------------
#: Resolved config paths, keyed on the two env vars that can change the answer.
#: See resolve_cortex_config_path() for why this is safe to memoize.
_path_cache: Dict[tuple, Path] = {}


def reset_path_cache() -> None:
    """Drop the resolved-path memo. For tests that move config files around."""
    _path_cache.clear()


def resolve_cortex_config_path() -> Path:
    """Return the cortex_config.yaml every ICDEV component should read.

    Memoized (ctx-perf-01). The uncached form was a real hot-path cost: this
    calls ``resolve_llm_config_path()``, which walks EVERY parent directory
    is_file()-probing for ``args/llm_config.yaml`` with no memo of its own. A
    single governed Cortex call reaches ``load_cortex_config()`` roughly 8-12
    times — ``is_enabled``, ``cacheable``, ``_ttl_for``, ``resolve_fail_closed``
    at three sites, ``_content_grounding_floor`` at two — so the walk was paid
    that many times over, tens of filesystem syscalls per call, whether or not
    the response cache was even on.

    Safe to memoize because the answer depends on exactly two inputs, both keyed
    here: the two env overrides. The directory walk starts from a module-level
    root derived from ``__file__``, NOT from ``os.getcwd()``, so it cannot change
    under a worktree or a test that chdirs. ``reset_path_cache()`` exists for
    tests that genuinely relocate a config file.

    The calls themselves are collapsed separately, by threading ONE snapshot
    through the pipeline (see :func:`load_cortex_config`); the ``stat()`` that
    remains is the invalidation signal and is now paid once per governed call
    rather than once per gate. Budget asserted by
    tests/cortex/test_config_load_budget.py.
    """
    key = (
        os.environ.get(CORTEX_CONFIG_ENV_VAR, "").strip(),
        os.environ.get("ICDEV_LLM_CONFIG", "").strip(),
    )
    cached = _path_cache.get(key)
    if cached is not None:
        return cached

    override = key[0]
    if override:
        resolved = Path(override).expanduser().resolve()
    else:
        resolved = resolve_llm_config_path().parent / CORTEX_CONFIG_FILENAME
    _path_cache[key] = resolved
    return resolved


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

    "Cached" is not free (ctx-perf-01): the mtime is the cache KEY, so every
    call still resolves the path and ``stat()``s it before the memo can answer.
    A governed Cortex call used to reach here six to eight times — three from
    the response cache, two from the grounding gates, one from the fail-closed
    posture, none of them aware the others had just read the same file. The
    readers below therefore take an optional ``config``: a caller that already
    holds a snapshot passes it in and pays nothing. :meth:`GovernancePipeline.wrap`
    and the ``_governed_facade`` wrapper each take one snapshot per call and
    thread it down, which is what keeps an operator's edit visible (the NEXT
    call re-reads) while costing one stat instead of eight.
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


def cortex_config(config=None, config_path=None) -> Dict:
    """A caller's already-loaded snapshot, or a fresh (mtime-memoized) load.

    The one place the "did someone upstream already read this?" question is
    answered, so every reader below is a two-line function instead of a
    conditional repeated eight times.
    """
    return config if config is not None else load_cortex_config(config_path)


def resolve_fail_closed(ctx=None, config_path=None, config=None) -> bool:
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
    cfg = cortex_config(config, config_path)
    return bool((cfg.get("governance") or {}).get("fail_closed", False))


def resolve_strategy_weights(search_cfg=None, config_path=None, config=None) -> Dict[str, float]:
    """Per-backend fusion weights for RRF (``search.strategy_weights``).

    Consumed by ``search_service._rrf_fuse``, which scores each item
    ``sum(weight / (rrf_k + rank))`` over the backends that returned it —
    the formula args/cortex_config.yaml has always documented. A backend with
    no entry weighs 1.0 (neutral); an unparseable or negative entry is clamped
    to 0.0 so a typo demotes that backend rather than inverting the ordering.

    ``search_cfg`` lets a caller that already loaded the ``search`` section
    pass it in instead of paying a second (cached) config read.
    """
    if search_cfg is None:
        search_cfg = (cortex_config(config, config_path) or {}).get("search") or {}
    raw = search_cfg.get("strategy_weights") or {}
    weights: Dict[str, float] = {}
    if isinstance(raw, dict):
        for backend, value in raw.items():
            try:
                weights[str(backend)] = max(0.0, float(value))
            except (TypeError, ValueError):
                logger.warning(
                    "cortex: non-numeric search.strategy_weights[%r]=%r — using 0.0",
                    backend, value,
                )
                weights[str(backend)] = 0.0
    return weights


def nlq_fallback_enabled(config_path=None, config=None) -> bool:
    """Whether ``analyst.ask()`` may fall back from IQE to the LLM NL->SQL path.

    ``analyst.nlq_fallback_enabled: false`` is a POLICY switch: it stops
    ``mode="auto"`` from silently degrading into LLM-generated SQL when IQE
    cannot resolve/translate/authorize the question. It deliberately does NOT
    govern an explicit ``mode="nlq"`` call — that is a caller opting in by
    name, not a fallback.
    """
    cfg = cortex_config(config, config_path)
    return bool((cfg.get("analyst") or {}).get("nlq_fallback_enabled", True))


def skip_grounding_for_plain_complete(config_path=None, config=None) -> bool:
    """Whether a non-retrieval call skips the two grounding gates.

    Default True: a plain ``complete()`` is free-form drafting with no evidence
    set, so both grounding gates record ``skip``. Set
    ``governance.skip_grounding_for_plain_complete: false`` to run them anyway —
    the citation gate then validates against an EMPTY allowed set (a plain
    completion injects no sources, so any ``[source: N]`` tag it emits is
    fabricated by construction) and the content gate runs the placeholder scan.
    """
    cfg = cortex_config(config, config_path)
    return bool(
        (cfg.get("governance") or {}).get("skip_grounding_for_plain_complete", True)
    )


# ---------------------------------------------------------------------------
# Air-gap invariant
# ---------------------------------------------------------------------------
def _load_llm_config(config_path=None, refresh: bool = False) -> Dict:
    """Load args/llm_config.yaml, cached per path+mtime.

    Same cache discipline as ``load_cortex_config``: this is on the air-gap hot
    path (``airgap_exclusions`` runs on every ``_invoke`` when ICDEV_AIRGAP=1),
    so an uncached ``yaml.safe_load`` of the whole LLM config would be paid per
    LLM call. ``refresh=True`` bypasses the cache.
    """
    path = Path(config_path) if config_path else resolve_llm_config_path()
    key = str(path)
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    if not refresh:
        cached = _llm_config_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    config = _load_yaml(path)
    _llm_config_cache[key] = (mtime, config)
    return config


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

    Reads through ``_load_llm_config`` rather than ``_load_yaml`` (ctx-perf-01):
    this runs at MODULE IMPORT of ``tools.cortex.api``, and a raw parse there
    both re-read args/llm_config.yaml in a process that is about to read it
    again for routing, and left the memo cold for the first ``airgap_exclusions``
    call. Same file, same mtime key — so the invariant is unchanged, it just
    populates the cache instead of bypassing it.
    """
    path = Path(config_path) if config_path else resolve_llm_config_path()
    config = _load_llm_config(path)
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
