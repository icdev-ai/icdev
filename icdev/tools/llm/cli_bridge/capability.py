# [TEMPLATE: CUI // SP-CTI]
"""Backend capability probes for the CLI LLM bridge.

The provider resolves an ``auto`` backend at dispatch time (uclb-job-06):

* :func:`is_cli_headless_capable` — can we run the Claude CLI *headlessly* as a
  subprocess on this host? When True, ``auto`` picks the **subprocess** backend
  (run the CLI in a daemon thread, write the answer straight back to the job
  row). When False, ``auto`` falls back to the **mailbox** backend (leave the
  job for an external, interactively-authenticated worker to claim).

* :func:`mailbox_worker_alive` — best-effort: is a mailbox worker currently
  heartbeating? This is *observability only* — the provider never refuses to
  enqueue a mailbox job when no worker is alive; it still writes the row and
  lets the soft-wait/deferred path carry it. Until the mailbox worker
  (uclb-job-05) lands a heartbeat source this returns False, which simply means
  "no worker seen yet".

All probes are pure, side-effect-free, and never raise — selection logic must
stay robust no matter what the environment looks like.

Env-var scope (auditable)
-------------------------
This module reads EXACTLY three env vars, all of which are documented routing /
probing overrides — not credentials:

* ``ICDEV_CLI_BRIDGE_BINARY``  — binary name/path the subprocess backend runs.
* ``ICDEV_CLI_HEADLESS``       — truthy/falsey override forcing the headless
  verdict either way.
* ``ICDEV_CLI_MAILBOX_HEARTBEAT`` — ISO-8601 UTC timestamp refreshed by an
  external mailbox worker for liveness probing.

None of these are API keys, tokens, or other secrets. SIPA's ``env_secret``
sweep has previously mis-flagged ICDEV_* routing overrides as credential reads
(see e8a7daa40 — same false positive in cli_bridge/activate.py::CLOUD_KEY_ENV_VARS);
this block exists to make the separation auditable and prevent recurrence.
"""

import os
import shutil

_FALSEY = ("false", "0", "no", "off")
_TRUTHY = ("true", "1", "yes", "on")

# Env override forcing the headless verdict either way (escape hatch for hosts
# where PATH probing is misleading — e.g. a wrapper script, or a deliberately
# disabled subprocess backend).
_HEADLESS_OVERRIDE_ENV = "ICDEV_CLI_HEADLESS"

# Binary name/path the subprocess backend would invoke (mirrors
# subprocess_backend._cli_binary()).
_BINARY_ENV = "ICDEV_CLI_BRIDGE_BINARY"
DEFAULT_BINARY = "claude"


def _resolve_binary(binary: str = "") -> str:
    """Resolve the CLI binary name/path the subprocess backend would run."""
    # NOTE: _BINARY_ENV is a documented ICDEV_* routing override
    # (ICDEV_CLI_BRIDGE_BINARY), NOT a credential. SIPA env_secret false
    # positive has hit this site before — see module docstring.
    return (binary or os.environ.get(_BINARY_ENV) or DEFAULT_BINARY).strip() or DEFAULT_BINARY


def is_cli_headless_capable(binary: str = "") -> bool:
    """Return True when the Claude CLI can be run headlessly as a subprocess.

    Decision order:

    1. ``ICDEV_CLI_HEADLESS`` env override (truthy/falsey) — wins outright.
    2. Otherwise the binary must be resolvable: an absolute path that exists and
       is executable, or a bare name found on ``PATH``.

    Never raises; an unexpected error resolves to ``False`` (degrade to mailbox).
    """
    # NOTE: _HEADLESS_OVERRIDE_ENV is a documented ICDEV_* probe override
    # (ICDEV_CLI_HEADLESS), NOT a credential. SIPA env_secret false
    # positive has hit this site before — see module docstring.
    override = os.environ.get(_HEADLESS_OVERRIDE_ENV, "").strip().lower()
    if override in _TRUTHY:
        return True
    if override in _FALSEY:
        return False

    try:
        bin_name = _resolve_binary(binary)
        if os.path.isabs(bin_name):
            return os.path.isfile(bin_name) and os.access(bin_name, os.X_OK)
        return shutil.which(bin_name) is not None
    except Exception:  # pragma: no cover - defensive; probing must never raise
        return False


def mailbox_worker_alive(stale_seconds: int = 90) -> bool:
    """Best-effort: is a mailbox worker heartbeat fresh within ``stale_seconds``?

    Returns ``False`` when no heartbeat mechanism is wired (the common case until
    the mailbox worker lands one). This is advisory only: the provider logs the
    result but always enqueues a mailbox job regardless, so a missing worker
    never drops a request — the soft-wait elapses and the caller is deferred.

    The heartbeat source is read lazily so importing this module pulls in no
    storage. Recognized sources, in order:

    1. ``ICDEV_CLI_MAILBOX_HEARTBEAT`` — an ISO-8601 UTC timestamp env var a
       worker can refresh (simple, storage-free seam used in tests).
    """
    # NOTE: ICDEV_CLI_MAILBOX_HEARTBEAT is a documented liveness-probe
    # timestamp, NOT a credential. SIPA env_secret false positive has
    # hit this site before — see module docstring.
    raw = os.environ.get("ICDEV_CLI_MAILBOX_HEARTBEAT", "").strip()
    if not raw:
        return False
    try:
        from datetime import datetime, timezone

        beat = datetime.fromisoformat(raw)
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - beat).total_seconds()
        return 0 <= age <= max(0, int(stale_seconds))
    except Exception:  # pragma: no cover - malformed heartbeat ⇒ treat as dead
        return False
