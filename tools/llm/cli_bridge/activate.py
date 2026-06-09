# [TEMPLATE: CUI // SP-CTI]
"""CLI bridge auto-enable + routing-chain rewrite.

Decides whether the local Claude Code CLI provider (``claude-cli``) should
front the LLM routing chains, and if so prepends it to every function's chain.

Enable logic
------------
``should_enable()`` is True when the environment is air-gapped OR there is no
usable cloud API key (env var or BYOK). The router consults
``cli_bridge_enabled()`` which layers the context-scoped override
(see :func:`cli_bridge_override`) on top of ``should_enable()``:

- Override ``True``  → force-enable for this context only.
- Override ``False`` → force-disable (bypass) for this context only.
- Override ``None``  → fall back to ``should_enable()`` auto-detection.

This mirrors :func:`tools.airgap.config_patcher._rewrite_routing_chains` but
only ensures ``claude-cli`` is first in each chain (it does not strip the
existing cloud/local models — they remain as fallbacks).
"""

import copy
import os
from contextvars import ContextVar
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.activate")

# Model name registered for the CLI provider in args/llm_config.yaml.
CLI_MODEL_NAME = "claude-cli"

# Request-/context-scoped override of the CLI bridge enable state.
#
# ``None``  → no override; fall back to ``should_enable()`` auto-detection.
# ``True``  → force-enable the bridge for this context only.
# ``False`` → force-disable (bypass) the bridge for this context only.
#
# A ContextVar (not thread-local) so the override is isolated per asyncio task
# AND per thread, and so that resetting via the returned token restores the
# exact previous value even under nesting. The dashboard seeds this in a Flask
# ``before_request`` hook so every ``LLMRouter.invoke()`` during a single page
# request honors a per-page toggle, then resets it in ``teardown_request`` so
# the state never leaks into the next request served by the same worker thread.
_cli_bridge_override: ContextVar[Optional[bool]] = ContextVar(
    "icdev_cli_bridge_override", default=None
)


def cli_bridge_override(value: Optional[bool]):
    """Set the context-scoped CLI bridge override and return a reset token.

    Args:
        value: ``True`` force-enable, ``False`` force-disable (bypass),
            ``None`` clear the override (defer to auto-detect).

    Returns:
        A ``contextvars.Token`` to pass to :func:`reset_cli_bridge_override`.
    """
    return _cli_bridge_override.set(value)


def reset_cli_bridge_override(token) -> None:
    """Restore the override to its prior value using a token from :func:`cli_bridge_override`."""
    try:
        _cli_bridge_override.reset(token)
    except (ValueError, LookupError) as exc:  # pragma: no cover - defensive
        # Token created in a different context (e.g. crossed a thread boundary).
        # Fall back to clearing so we never leave a stale override behind.
        logger.debug("override token reset failed, clearing instead: %s", exc)
        _cli_bridge_override.set(None)


def get_cli_bridge_override() -> Optional[bool]:
    """Return the current context-scoped override (``None`` if unset)."""
    return _cli_bridge_override.get()


def apply_cli_bridge_override(chain, model_name: str = CLI_MODEL_NAME):
    """Apply the context-scoped override to a single routing chain.

    Called at invoke time (per request) so a per-page toggle takes effect even
    though the base routing config was rewritten once at router construction.

    - override ``None``  → chain returned unchanged.
    - override ``True``  → ensure ``model_name`` is first (force-enable).
    - override ``False`` → strip ``model_name`` from the chain (force-disable).

    Returns a new list; the input ``chain`` is never mutated.
    """
    override = _cli_bridge_override.get()
    if override is None:
        return list(chain)
    if override:
        if chain and chain[0] == model_name:
            return list(chain)
        return [model_name] + [m for m in chain if m != model_name]
    return [m for m in chain if m != model_name]

# Cloud LLM credentials checked in the environment.
CLOUD_KEY_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "IBM_CLOUD_API_KEY",
)

# Providers probed via BYOK key resolution.
_BYOK_PROVIDERS = ("anthropic", "openai", "google", "azure", "ibm", "bedrock")


def _has_cloud_key() -> bool:
    """Return True if any usable cloud LLM key is available (env or BYOK).

    Checks the well-known cloud key env vars first, then falls back to
    ``tools.dashboard.byok.resolve_api_key`` for any provider. BYOK is
    imported lazily; an ImportError (or any failure) is treated as "no key".
    """
    for var in CLOUD_KEY_ENV_VARS:
        if os.environ.get(var, "").strip():
            return True

    try:
        from tools.dashboard.byok import resolve_api_key
    except ImportError:
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("BYOK import failed, treating as no key: %s", exc)
        return False

    for provider in _BYOK_PROVIDERS:
        try:
            api_key, _source = resolve_api_key("", provider)
        except Exception:
            continue
        if api_key:
            return True

    return False


def should_enable() -> bool:
    """Auto-enable when air-gapped OR no cloud key is configured."""
    try:
        from tools.airgap import is_airgap

        if is_airgap():
            return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("is_airgap() check failed: %s", exc)

    return not _has_cloud_key()


def cli_bridge_enabled() -> bool:
    """Resolve the effective enable state.

    Precedence: context override (set via :func:`cli_bridge_override`) >
    ``should_enable()`` auto-detection.
    """
    override = _cli_bridge_override.get()
    if override is not None:
        return override

    return should_enable()


def prepend_cli_to_chains(config: dict, model_name: str = CLI_MODEL_NAME) -> dict:
    """Return a copy of ``config`` with ``model_name`` first in every chain.

    The original ``config`` is left untouched (operates on a deep copy).
    Idempotent: if a chain already starts with ``model_name`` it is left as-is;
    any later occurrence is de-duplicated so the model never appears twice.
    """
    new_config = copy.deepcopy(config)
    routing = new_config.get("routing", {})
    if not isinstance(routing, dict):
        return new_config

    for route in routing.values():
        if not isinstance(route, dict):
            continue
        chain = route.get("chain")
        if not isinstance(chain, list):
            continue
        if chain and chain[0] == model_name:
            continue  # already first — don't double-prepend
        deduped = [m for m in chain if m != model_name]
        route["chain"] = [model_name] + deduped

    return new_config


def maybe_activate(config: dict) -> dict:
    """Router hook: prepend the CLI provider to chains when enabled.

    Returns the (possibly rewritten) config. On any error the original config
    is returned unchanged so routing is never disrupted.
    """
    try:
        if not cli_bridge_enabled():
            return config
        patched = prepend_cli_to_chains(config)
        logger.info(
            "CLI bridge ACTIVE — '%s' prepended to %d routing chains",
            CLI_MODEL_NAME,
            len(patched.get("routing", {})),
        )
        return patched
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("CLI bridge activation skipped (error): %s", exc)
        return config


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    result = {
        "airgap_or_no_cloud_key": should_enable(),
        "has_cloud_key": _has_cloud_key(),
        "cli_bridge_enabled": cli_bridge_enabled(),
    }
    print(json.dumps(result, indent=2))
    sys.exit(0)
