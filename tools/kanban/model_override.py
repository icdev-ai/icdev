# CUI // SP-CTI
"""Pick the model the kanban runner builds with. Cross-process, live, no restart.

The case this exists for: you build with Claude, you hit token exhaustion at 2am, and
you want the runner to carry on with Kimi or a local Ollama model — without editing YAML,
restarting the scheduler, or losing the board.

## Selecting a non-Claude model actually means it

The executor chain (ICDEV_KANBAN_EXECUTOR_CHAIN) picks a TIER: claude_cli, then gitlab,
then ollama_local. A model override is a different axis, and getting the interaction
wrong would be the worst kind of control — the one that looks like it worked.

If you select ``kimi-k2`` and the runner still dispatches the Claude CLI (which cannot
serve it), the dropdown has done nothing and the board gives you no sign of that. So
selecting a model whose provider the Claude CLI cannot run **removes claude_cli from the
chain for that dispatch**, and the task goes to the LLM executor with that model. The
selector's promise is "build with this", not "prefer this".

A Claude model (provider anthropic / claude-cli) is passed straight to the CLI as
``--model <model_id>``.

## The names are the ones in args/llm_config.yaml

``available()`` reads the same `models:` block the router uses, so the dropdown cannot
offer a model the system does not actually have — and a model renamed in config
disappears from the dropdown rather than becoming a selection that silently fails at
dispatch time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
_FLAG = Path(
    os.environ.get("KANBAN_MODEL_FLAG", str(_ROOT / "data" / "kanban_model_override.json"))
)

# Providers the Claude Code CLI can actually serve. Anything else must NOT be dispatched
# through it — see the module docstring.
CLI_PROVIDERS = ("anthropic", "claude-cli", "claude_cli")


def _config_path() -> Path:
    try:
        from tools.llm.config_path import resolve_llm_config_path

        return Path(resolve_llm_config_path())
    except Exception:  # noqa: BLE001
        return _ROOT / "args" / "llm_config.yaml"


def available() -> List[Dict[str, Any]]:
    """Every model the router knows about, as {name, provider, model_id, cli_capable}."""
    import yaml

    try:
        cfg = yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []

    out: List[Dict[str, Any]] = []
    for name, spec in (cfg.get("models") or {}).items():
        spec = spec or {}
        provider = str(spec.get("provider") or "")
        out.append({
            "name": name,
            "provider": provider,
            "model_id": spec.get("model_id") or spec.get("model") or "",
            # Whether the Claude Code CLI can run it. False => selecting it drops
            # claude_cli from the executor chain.
            "cli_capable": provider in CLI_PROVIDERS,
        })
    return sorted(out, key=lambda m: (not m["cli_capable"], m["name"]))


def _read() -> Dict[str, Any]:
    if not _FLAG.exists():
        return {}
    try:
        data = json.loads(_FLAG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def selected() -> Optional[str]:
    """The logical model name the runner must build with, or None for the default.

    Never raises. Falls back to None (config-driven routing) on any error: an
    unreadable override must not stop the runner from building at all.
    """
    try:
        name = (_read().get("model") or "").strip()
        return name or None
    except Exception:  # noqa: BLE001
        return None


def spec() -> Optional[Dict[str, Any]]:
    """The full record for the selected model, or None. Unknown name => None.

    A name that is no longer in llm_config resolves to None rather than being passed
    to a provider that will reject it at dispatch time — a stale override degrades to
    the default, loudly in the status payload, instead of failing every task.
    """
    name = selected()
    if not name:
        return None
    return next((m for m in available() if m["name"] == name), None)


def status() -> Dict[str, Any]:
    name = selected()
    resolved = spec()
    return {
        "model": name,
        "resolved": resolved,
        "stale": bool(name and resolved is None),
        "since": _read().get("since"),
        "actor": _read().get("actor"),
    }


def set_model(name: Optional[str], actor: str = "dashboard") -> Dict[str, Any]:
    """Select a model, or pass a falsy name to clear back to config-driven routing."""
    name = (name or "").strip()
    if not name:
        try:
            _FLAG.unlink()
        except FileNotFoundError:
            pass
        return status()

    if not any(m["name"] == name for m in available()):
        raise ValueError(
            f"unknown model {name!r}. The dropdown is built from the `models:` block of "
            f"llm_config.yaml, so a name that is not there is one the router cannot "
            f"serve — selecting it would fail at dispatch, not here."
        )

    _FLAG.parent.mkdir(parents=True, exist_ok=True)
    _FLAG.write_text(
        json.dumps(
            {"model": name, "since": datetime.now(timezone.utc).isoformat(), "actor": actor},
            indent=2,
        ),
        encoding="utf-8",
    )
    return status()
