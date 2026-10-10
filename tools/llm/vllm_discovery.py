#!/usr/bin/env python3
# CUI // SP-CTI
"""vLLM discovery: served models, context window, and a PROBED tool-call flag (omx-vllm-02).

The generic ``vllm`` provider in ``args/llm_config.yaml`` used to be declared and
unreachable: no model entry named it, so no routing chain could ever select it.
This module is the read side of the fix. It mirrors the router's Ollama discovery
(``settings.ollama_discovery``) for an OpenAI-compatible vLLM server:

* ``GET {base_url}/models`` lists what the server is actually serving. vLLM adds
  ``max_model_len`` to each entry, which is the REAL context window for that
  deployment -- on a low-VRAM box it is whatever ``--max-model-len`` the operator
  had to choose to fit, not the model card's figure.
* Tool calling is a SERVER flag (``--enable-auto-tool-choice --tool-call-parser``)
  that ``/v1/models`` does not expose. So it is probed ONCE with a 1-token request
  carrying a tool, and the answer is cached per (base_url, model). It is never
  assumed: a probe that cannot reach a verdict reports ``unknown`` and the model is
  registered with ``supports_tools: false``.

Nothing here writes ``llm_config.yaml``; the router registers the results
in memory. Nothing here makes a performance claim either -- it reports what the
server says it serves and whether one tools request was accepted.

Usage:
    python tools/llm/vllm_discovery.py --status [--json]
    icdev llm doctor [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- never
# the import root. Bootstrap it before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.llm.vllm_discovery")

TOOL_SUPPORTED = "supported"
TOOL_UNSUPPORTED = "unsupported"
TOOL_UNKNOWN = "unknown"

#: Status codes that mean "the server understood the request and refused the tools
#: part". vLLM answers 400 when auto tool choice is not enabled; a 422 is the same
#: verdict from a stricter validator. Anything else (401/404/5xx, a timeout) says
#: nothing about tool support and stays UNKNOWN.
_REFUSAL_STATUSES = (400, 422)

_lock = threading.Lock()
#: base_url -> (fetched_at, [{"id", "max_model_len"}])
_models_cache: Dict[str, Tuple[float, List[dict]]] = {}
#: (base_url, model_id) -> (status, evidence). Only DEFINITIVE verdicts are cached.
_tool_probe_cache: Dict[Tuple[str, str], Tuple[str, str]] = {}


def clear_caches() -> None:
    """Forget every cached model list and tool verdict (tests, and a server restart)."""
    with _lock:
        _models_cache.clear()
        _tool_probe_cache.clear()


def _request(url: str, api_key: str, timeout: float, body: Optional[dict] = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - operator-configured URL


def list_served_models(base_url: str, api_key: str = "", timeout: float = 3.0,
                       max_age: float = 0.0) -> List[dict]:
    """Return ``[{"id", "max_model_len"}]`` from ``GET {base_url}/models``.

    Raises on an unreachable server or a malformed body -- the caller decides
    whether that is a debug line (router startup) or a reported finding (doctor).
    ``max_model_len`` is None when the server does not report it (a non-vLLM
    OpenAI-compatible server); the router then leaves the context window unset
    rather than inventing one.
    """
    base = base_url.rstrip("/")
    if max_age > 0:
        with _lock:
            hit = _models_cache.get(base)
        if hit and (time.time() - hit[0]) < max_age:
            return [dict(m) for m in hit[1]]
    with _request(f"{base}/models", api_key, timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    models: List[dict] = []
    for entry in payload.get("data", []) or []:
        model_id = str(entry.get("id", "")).strip()
        if not model_id:
            continue
        mml = entry.get("max_model_len")
        models.append({"id": model_id, "max_model_len": int(mml) if isinstance(mml, int) and mml > 0 else None})
    with _lock:
        _models_cache[base] = (time.time(), [dict(m) for m in models])
    return models


def probe_tool_support(base_url: str, model_id: str, api_key: str = "",
                       timeout: float = 10.0) -> Tuple[str, str]:
    """Ask the server once whether it accepts a tools request for *model_id*.

    Returns ``(status, evidence)``, status one of supported / unsupported / unknown.
    A definitive verdict is cached for the life of the process; ``unknown`` is not,
    so a server that was merely down at startup is asked again next time.
    """
    base = base_url.rstrip("/")
    key = (base, model_id)
    with _lock:
        cached = _tool_probe_cache.get(key)
    if cached:
        return cached

    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "tools": [{
            "type": "function",
            "function": {
                "name": "icdev_tool_probe",
                "description": "Capability probe; never called.",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        "tool_choice": "auto",
    }
    try:
        with _request(f"{base}/chat/completions", api_key, timeout, body) as resp:
            verdict = (TOOL_SUPPORTED, f"HTTP {resp.status}: tools request accepted")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001 - evidence only
            pass
        if exc.code in _REFUSAL_STATUSES:
            verdict = (TOOL_UNSUPPORTED, f"HTTP {exc.code}: {detail}".strip())
        else:
            return TOOL_UNKNOWN, f"HTTP {exc.code}: {detail}".strip()
    except Exception as exc:  # noqa: BLE001 - unreachable/timeout => no verdict
        return TOOL_UNKNOWN, f"{type(exc).__name__}: {exc}"

    with _lock:
        _tool_probe_cache[key] = verdict
    return verdict


def discover(base_url: str, api_key: str = "", *, timeout: float = 3.0,
             probe_tools: bool = True, max_age: float = 0.0) -> List[dict]:
    """Served models enriched with the tool verdict: ``[{id, max_model_len, tool_call, tool_evidence}]``."""
    out = []
    for model in list_served_models(base_url, api_key, timeout=timeout, max_age=max_age):
        status, evidence = (TOOL_UNKNOWN, "probe disabled")
        if probe_tools:
            status, evidence = probe_tool_support(base_url, model["id"], api_key, timeout=max(timeout, 10.0))
        out.append({**model, "tool_call": status, "tool_evidence": evidence})
    return out


# ---------------------------------------------------------------------------
# Status / doctor
# ---------------------------------------------------------------------------
def status_report(config: Optional[dict] = None, timeout: float = 3.0) -> dict:
    """Reachability, served models, max_model_len and tool support per vLLM provider.

    *config* is a loaded ``llm_config`` dict; None loads the router's. Probes every
    provider in ``settings.vllm_discovery.probe_providers`` regardless of
    ``probe_only_if_env`` -- an operator asking for a doctor report wants the answer.
    """
    from tools.llm.cli_bridge.activate import _is_local_only_provider
    from tools.llm.router import _expand_env

    if config is None:
        config = _load_router_config()
    disc = (config.get("settings", {}) or {}).get("vllm_discovery", {}) or {}
    providers = config.get("providers", {}) or {}
    models = config.get("models", {}) or {}
    alias_id = os.environ.get("VLLM_MODEL", "").strip()

    report = {
        "discovery_enabled": bool(disc.get("enabled", False)),
        "vllm_model_env": alias_id or None,
        "providers": [],
    }
    for pname in disc.get("probe_providers", ["vllm"]) or []:
        pcfg = providers.get(pname) or {}
        base_url = _expand_env(pcfg.get("base_url", "")).rstrip("/")
        api_key = os.environ.get(pcfg.get("api_key_env", ""), "") if pcfg.get("api_key_env") else ""
        entry = {
            "provider": pname,
            "declared": bool(pcfg),
            "base_url": base_url or None,
            "locality": pcfg.get("locality"),
            "counts_as_local": _is_local_only_provider(pname, providers) if pcfg else False,
            "reachable": False,
            "error": None,
            "served_models": [],
        }
        if not base_url:
            entry["error"] = "provider has no base_url"
        else:
            try:
                entry["served_models"] = discover(base_url, api_key, timeout=timeout,
                                                  probe_tools=bool(disc.get("probe_tools", True)))
                entry["reachable"] = True
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                entry["error"] = f"{type(exc).__name__}: {exc}"
        served_ids = {m["id"] for m in entry["served_models"]}
        entry["registered_as"] = sorted(f"{pname}:{m}" for m in served_ids)
        report["providers"].append(entry)

    alias = models.get("vllm-local") or {}
    if not alias:
        report["vllm_local"] = {"declared": False}
    else:
        served = {m["id"] for p in report["providers"] if p["provider"] == alias.get("provider")
                  for m in p["served_models"]}
        report["vllm_local"] = {
            "declared": True,
            "model_id": alias_id or None,
            "available": bool(alias_id) and alias_id in served,
            "reason": ("VLLM_MODEL is unset -- vllm-local is skipped in every chain" if not alias_id
                       else "served" if alias_id in served
                       else f"VLLM_MODEL={alias_id!r} is not in the served model list"),
        }
    return report


def _load_router_config() -> dict:
    from tools.llm.cli_bridge.activate import cli_bridge_override, reset_cli_bridge_override
    from tools.llm.router import LLMRouter

    token = cli_bridge_override(False)  # skip the air-gap probe; only providers/models are read
    try:
        return LLMRouter()._config
    finally:
        reset_cli_bridge_override(token)


def _format_text(report: dict) -> str:
    lines = [f"vLLM discovery: {'enabled' if report['discovery_enabled'] else 'disabled'}"]
    for p in report["providers"]:
        state = "reachable" if p["reachable"] else f"UNREACHABLE ({p['error']})"
        lines.append(f"- {p['provider']} @ {p['base_url']} [locality={p['locality']}, "
                     f"local={p['counts_as_local']}]: {state}")
        for m in p["served_models"]:
            lines.append(f"    {p['provider']}:{m['id']}  max_model_len={m['max_model_len']}  "
                         f"tool_call={m['tool_call']}")
    alias = report.get("vllm_local", {})
    if alias.get("declared"):
        lines.append(f"vllm-local: {'available' if alias['available'] else 'unavailable'} -- {alias['reason']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="vLLM endpoint status: reachability, served models, "
                                                 "max_model_len, tool-call support.")
    parser.add_argument("--status", action="store_true", help="probe and report (default action)")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = status_report(timeout=args.timeout)
    print(json.dumps(report, indent=2) if args.json else _format_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
