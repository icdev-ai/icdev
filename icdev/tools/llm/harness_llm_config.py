#!/usr/bin/env python3
# CUI // SP-CTI
"""ONE vLLM endpoint for ICDEV's router AND the harnesses it dispatches (omx-vllm-04).

``VLLM_BASE_URL`` already points ICDEV's own router at a vLLM server (omx-vllm-01/02).
Before this module every harness the runner launches -- opencode, Pi, Codex --
needed the same endpoint configured BY HAND, in three different file formats.
This module writes those three configs from the one endpoint, using the models
the server actually serves (``vllm_discovery``), and gives the adapters the
routed model to pass on their command line.

What gets written (``--write``; the default is a dry run):

* opencode -- ``provider.<name>`` in ``opencode.json`` (project file with
  ``--project``, else ``~/.config/opencode/opencode.json``): a custom
  ``@ai-sdk/openai-compatible`` provider with ``options.baseURL`` and one entry
  per served model (``limit.context`` = the server's ``max_model_len``,
  ``tool_call`` = the probed flag). Every other key survives.
* Pi -- ``providers.<name>`` in ``$PI_CODING_AGENT_DIR/models.json`` (default
  ``~/.pi/agent/models.json``), the shape omx-spike-01 measured.
* Codex -- a ``[model_providers.<name>]`` table inside a marked block of
  ``$CODEX_HOME/config.toml`` (default ``~/.codex/config.toml``). Only the block
  is replaced; a hand-written table of the same name is a CONFLICT, reported and
  never overwritten.

No API key is ever written: Pi gets the env var NAME, Codex gets ``env_key``.

Claude Code is deliberately NOT pointed at vLLM (Anthropic API shape).

Opt-in, and the warning
-----------------------
The target boxes have low-end GPUs: only a small quantized model fits under
vLLM's KV-cache preallocation, and agentic harnesses degrade badly without tool
calling. So generating configs does not switch any harness's default model,
and an adapter passes a routed vLLM model ONLY when ``ICDEV_HARNESS_LLM=vllm``
AND the router's choice for the task's ``llm_function`` is a vLLM model. A
served model whose tool probe was not ``supported`` produces a warning in both
places. No performance claim is made anywhere here.

Usage:
    icdev llm harnesses [--llm auto|vllm|none] [--write] [--project DIR] [--json]
    python tools/llm/harness_llm_config.py --llm vllm --write
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Run by path, sys.path[0] is this file's own directory -- bootstrap the import root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.llm import vllm_discovery  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.llm.harness_llm_config")

HARNESSES = ("opencode", "pi", "codex")
OPENCODE_SCHEMA = "https://opencode.ai/config.json"
#: Opt-in switch for passing a routed vLLM model to a harness (see module doc).
ENV_HARNESS_LLM = "ICDEV_HARNESS_LLM"
#: Codex wire protocol for the provider: vLLM's chat-completions endpoint.
CODEX_WIRE_API = "chat"

_CODEX_BEGIN = "# >>> icdev vllm (omx-vllm-04): generated from VLLM_BASE_URL -- re-run, do not edit >>>"
_CODEX_END = "# <<< icdev vllm <<<"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
def resolve_endpoint(config: Optional[dict] = None, provider: str = "vllm") -> dict:
    """``{provider, base_url, api_key_env, api_key_set, env_set}`` from llm_config."""
    from tools.llm.router import _expand_env

    if config is None:
        config = vllm_discovery._load_router_config()
    pcfg = (config.get("providers", {}) or {}).get(provider) or {}
    key_env = str(pcfg.get("api_key_env") or "")
    return {
        "provider": provider,
        "base_url": _expand_env(pcfg.get("base_url", "")).rstrip("/") or None,
        "api_key_env": key_env or None,
        "api_key_set": bool(key_env and os.environ.get(key_env, "").strip()),
        "env_set": bool(os.environ.get("VLLM_BASE_URL", "").strip()),
    }


def tool_call_warnings(provider: str, models: List[dict]) -> List[str]:
    """One warning per served model whose tool-call probe was not ``supported``."""
    out = []
    for m in models:
        status = m.get("tool_call", vllm_discovery.TOOL_UNKNOWN)
        if status != vllm_discovery.TOOL_SUPPORTED:
            out.append(
                f"WARNING: {provider}/{m['id']} tool calling is {status} "
                f"({m.get('tool_evidence') or 'no probe'}). Agentic harnesses "
                "(opencode/pi/codex) degrade badly without it; start vLLM with "
                "--enable-auto-tool-choice --tool-call-parser <parser>, or keep the "
                "harness on a tool-capable model."
            )
    return out


# ---------------------------------------------------------------------------
# Renderers -- pure functions over (existing content, endpoint, served models)
# ---------------------------------------------------------------------------
def render_opencode(existing: Optional[dict], provider: str, base_url: str,
                    api_key_env: Optional[str], models: List[dict]) -> dict:
    """``opencode.json`` with ``provider.<provider>`` replaced; other keys survive."""
    config = dict(existing) if isinstance(existing, dict) else {}
    config.setdefault("$schema", OPENCODE_SCHEMA)
    providers = dict(config.get("provider") or {})
    options: Dict[str, str] = {"baseURL": base_url}
    if api_key_env:
        options["apiKey"] = "{env:%s}" % api_key_env
    entries = {}
    for m in models:
        entry: dict = {"name": f"{m['id']} (vLLM)",
                       "tool_call": m.get("tool_call") == vllm_discovery.TOOL_SUPPORTED}
        if m.get("max_model_len"):
            entry["limit"] = {"context": m["max_model_len"],
                              "output": min(4096, m["max_model_len"])}
        entries[m["id"]] = entry
    providers[provider] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "vLLM (ICDEV, VLLM_BASE_URL)",
        "options": options,
        "models": entries,
    }
    config["provider"] = providers
    return config


def render_pi(existing: Optional[dict], provider: str, base_url: str,
              api_key_env: Optional[str], models: List[dict]) -> dict:
    """Pi ``models.json`` with ``providers.<provider>`` replaced; others survive."""
    config = dict(existing) if isinstance(existing, dict) else {}
    providers = dict(config.get("providers") or {})
    entries = []
    for m in models:
        entry: dict = {"id": m["id"], "input": ["text"]}
        if m.get("max_model_len"):
            entry["contextWindow"] = m["max_model_len"]
            entry["maxTokens"] = min(4096, m["max_model_len"])
        entries.append(entry)
    providers[provider] = {
        "api": "openai-completions",
        # Pi resolves apiKey as an env var NAME first; a keyless vLLM still
        # needs a non-empty value, and "EMPTY" is vLLM's documented placeholder.
        "apiKey": api_key_env or "EMPTY",
        "baseUrl": base_url,
        "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False},
        "models": entries,
    }
    config["providers"] = providers
    return config


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def render_codex(existing: str, provider: str, base_url: str,
                 api_key_env: Optional[str]) -> str:
    """``config.toml`` with the managed ``[model_providers.<provider>]`` block.

    Raises:
        ValueError: the file already declares that table OUTSIDE the managed
            block -- writing a second one would make the TOML invalid.
    """
    text = existing or ""
    pattern = re.compile(re.escape(_CODEX_BEGIN) + r".*?" + re.escape(_CODEX_END) + r"\n?", re.S)
    unmanaged = pattern.sub("", text)
    header = re.compile(r"^\s*\[model_providers\.%s\]\s*$" % re.escape(provider), re.M)
    if header.search(unmanaged):
        raise ValueError(
            f"[model_providers.{provider}] is already declared by hand in this "
            "config.toml; remove it (or rename it) and re-run"
        )
    lines = [
        _CODEX_BEGIN,
        f"[model_providers.{provider}]",
        f"name = {_toml_str('vLLM (ICDEV, VLLM_BASE_URL)')}",
        f"base_url = {_toml_str(base_url)}",
        f"wire_api = {_toml_str(CODEX_WIRE_API)}",
    ]
    if api_key_env:
        lines.append(f"env_key = {_toml_str(api_key_env)}")
    lines.append(_CODEX_END)
    block = "\n".join(lines) + "\n"
    if pattern.search(text):
        return pattern.sub(lambda _m: block, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return f"{text}\n{block}" if text else block


# ---------------------------------------------------------------------------
# Paths + plan
# ---------------------------------------------------------------------------
def config_paths(home: Path, project: Optional[Path] = None) -> Dict[str, Path]:
    """Where each harness reads its provider config on this host."""
    pi_dir = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    return {
        "opencode": (Path(project) / "opencode.json" if project
                     else Path(home) / ".config" / "opencode" / "opencode.json"),
        "pi": (Path(pi_dir) if pi_dir else Path(home) / ".pi" / "agent") / "models.json",
        "codex": (Path(codex_home) if codex_home else Path(home) / ".codex") / "config.toml",
    }


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def plan_harness_configs(endpoint: dict, models: List[dict], *, home: Path,
                         project: Optional[Path] = None) -> List[dict]:
    """``[{harness, path, content, error}]`` -- what would be written, nothing written."""
    provider, base_url, key_env = endpoint["provider"], endpoint["base_url"], endpoint["api_key_env"]
    # An env var that is not set is not handed to a harness: Codex refuses to
    # start when env_key names an unset variable.
    key_env = key_env if endpoint.get("api_key_set") else None
    plan = []
    for harness, path in config_paths(home, project).items():
        item = {"harness": harness, "path": str(path), "content": None, "error": None}
        try:
            if harness == "opencode":
                item["content"] = json.dumps(
                    render_opencode(_read_json(path), provider, base_url, key_env, models), indent=2) + "\n"
            elif harness == "pi":
                item["content"] = json.dumps(
                    render_pi(_read_json(path), provider, base_url, key_env, models), indent=2) + "\n"
            else:
                existing = path.read_text(encoding="utf-8") if path.is_file() else ""
                item["content"] = render_codex(existing, provider, base_url, key_env)
        except (OSError, ValueError) as exc:
            item["error"] = str(exc)
        plan.append(item)
    return plan


def configure_harnesses(llm: str = "auto", *, write: bool = False, home: Optional[Path] = None,
                        project: Optional[Path] = None, timeout: float = 3.0,
                        config: Optional[dict] = None) -> dict:
    """Point opencode, Pi and Codex at the one vLLM endpoint.

    ``llm``: ``vllm`` (required -- an unreachable endpoint is an error),
    ``auto`` (only when ``VLLM_BASE_URL`` is set AND reachable; otherwise a
    no-op) or ``none``. This is the seam ``icdev omarchy setup --llm`` calls.
    """
    report: dict = {"llm": llm, "configured": False, "written": [], "files": [],
                    "warnings": [], "error": None,
                    "opt_in": f"set {ENV_HARNESS_LLM}=vllm in .env to have the runner "
                              "pass the routed vLLM model to opencode/pi/codex"}
    if llm == "none":
        report["reason"] = "--llm none"
        return report
    endpoint = resolve_endpoint(config)
    report["endpoint"] = endpoint
    if llm == "auto" and not endpoint["env_set"]:
        report["reason"] = "VLLM_BASE_URL is unset -- harness configs left alone"
        return report
    if not endpoint["base_url"]:
        report["error"] = f"provider {endpoint['provider']!r} has no base_url in llm_config"
        return report
    api_key = os.environ.get(endpoint["api_key_env"] or "", "") if endpoint["api_key_env"] else ""
    try:
        models = vllm_discovery.discover(endpoint["base_url"], api_key, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - reported
        msg = f"vLLM endpoint {endpoint['base_url']} unreachable: {type(exc).__name__}: {exc}"
        if llm == "auto":
            report["reason"] = msg + " -- harness configs left alone"
        else:
            report["error"] = msg
        return report
    if not models:
        report["error"] = f"vLLM endpoint {endpoint['base_url']} serves no models"
        return report

    report["models"] = models
    report["warnings"] = tool_call_warnings(endpoint["provider"], models)
    plan = plan_harness_configs(endpoint, models, home=Path(home) if home else Path.home(),
                                project=Path(project) if project else None)
    report["files"] = plan
    if write:
        for item in plan:
            if item["error"]:
                continue
            path = Path(item["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(item["content"])
            report["written"].append(item["path"])
    report["configured"] = not any(i["error"] for i in plan)
    return report


# ---------------------------------------------------------------------------
# Adapter side: the routed model on the harness command line
# ---------------------------------------------------------------------------
def routed_harness_model(harness: str, llm_function: str, router=None) -> Optional[dict]:
    """The router's choice for *llm_function*, IF it is a vLLM model and opted in.

    Returns ``{"argv": [...], "model": "<provider>/<served>", "warning": str|None}``
    or None (not opted in, router chose a non-vLLM model, or nothing routable).
    """
    if os.environ.get(ENV_HARNESS_LLM, "").strip().lower() != "vllm" or not llm_function:
        return None
    if harness not in HARNESSES:
        return None
    if router is None:
        from tools.llm.router import LLMRouter

        router = LLMRouter()
    _provider, model_id, cfg = router.get_provider_for_function(llm_function)
    disc = ((router._config.get("settings", {}) or {}).get("vllm_discovery", {}) or {})
    vllm_providers = set(disc.get("probe_providers") or ["vllm"])
    pname = (cfg or {}).get("provider", "")
    if pname not in vllm_providers or not model_id:
        return None
    qualified = f"{pname}/{model_id}"
    if harness == "codex":
        argv = ["--model", str(model_id), "-c", f"model_provider={_toml_str(pname)}"]
    else:
        argv = ["--model", qualified]
    warning = None
    if not cfg.get("supports_tools"):
        warning = (f"WARNING: {harness} is being pointed at {qualified}, whose tool calling is "
                   f"{cfg.get('tool_call_probe', 'unverified')}; agentic harnesses degrade badly "
                   f"without it. Unset {ENV_HARNESS_LLM} to keep the harness's own model.")
    return {"argv": argv, "model": qualified, "warning": warning}


def routed_model_argv(harness: str, metadata: Optional[dict]) -> List[str]:
    """``--model ...`` tokens for an adapter, or [] -- never raises into a dispatch."""
    llm_function = (metadata or {}).get("llm_function")
    if not llm_function:
        return []
    try:
        routed = routed_harness_model(harness, str(llm_function))
    except Exception as exc:  # noqa: BLE001 - routing must not break a dispatch
        logger.warning("harness model routing failed for %s/%s: %s", harness, llm_function, exc)
        return []
    if not routed:
        return []
    if routed["warning"]:
        logger.warning(routed["warning"])
        print(routed["warning"], file=sys.stderr)
    logger.info("%s: routed %s -> %s", harness, llm_function, routed["model"])
    return list(routed["argv"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _format_text(report: dict) -> str:
    if report.get("error"):
        return f"ERROR: {report['error']}"
    if not report.get("files"):
        return f"harness configs: nothing to do ({report.get('reason', '')})"
    ep = report["endpoint"]
    lines = [f"vLLM endpoint {ep['base_url']} -> opencode, pi, codex "
             f"({len(report['models'])} served model(s))"]
    for item in report["files"]:
        state = (f"ERROR {item['error']}" if item["error"]
                 else "written" if item["path"] in report["written"] else "would write (dry run)")
        lines.append(f"- {item['harness']}: {item['path']} [{state}]")
    lines += report["warnings"]
    lines.append(f"Opt-in: {report['opt_in']}.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Point opencode, Pi and Codex at ICDEV's vLLM endpoint.")
    parser.add_argument("--llm", choices=("auto", "vllm", "none"), default="auto",
                        help="auto: only when VLLM_BASE_URL is set and reachable (default)")
    parser.add_argument("--write", action="store_true", help="write the files (default: dry run)")
    parser.add_argument("--project", help="write opencode's PROJECT opencode.json here instead of the user one")
    parser.add_argument("--home", help="home directory to write under (default: ~)")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = configure_harnesses(args.llm, write=args.write, project=args.project,
                                 home=Path(args.home) if args.home else None, timeout=args.timeout)
    if args.json:
        out = dict(report)
        out["files"] = [{k: v for k, v in f.items() if k != "content"} for f in report["files"]]
        print(json.dumps(out, indent=2))
    else:
        print(_format_text(report))
    for warning in report["warnings"] if args.json else []:
        print(warning, file=sys.stderr)
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
