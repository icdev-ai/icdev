from __future__ import annotations

"""Config override utility — parse --opts key=value CLI arguments and merge into YAML config.

Usage pattern (in any ICDEV script that loads config):
    from icdev.tools.llm.config_opts import parse_opts, apply_opts
    opts = parse_opts(sys.argv)  # extracts --opts k=v pairs
    config = apply_opts(yaml.safe_load(...), opts)

--opts syntax:
    --opts providers.kimi.model=kimi-k2          # nested dot-path
    --opts routing.default_function=chat         # top-level key
    --opts providers.kimi.max_tokens=8192        # auto-typed (int detected)
"""

import copy
import os
from typing import Any


def parse_opts(argv: list[str]) -> dict[str, str]:
    """Scan argv for --opts flags and collect key=value pairs.

    Supports both ``--opts key=val`` and ``--opts key=val key2=val2``.
    Stops collecting values when the next flag (starts with ``-``) is found.
    """
    result: dict[str, str] = {}
    i = 0
    while i < len(argv):
        if argv[i] == "--opts":
            i += 1
            while i < len(argv) and not argv[i].startswith("-"):
                token = argv[i]
                if "=" in token:
                    k, _, v = token.partition("=")
                    result[k.strip()] = v
                i += 1
        else:
            i += 1
    return result


def _coerce_value(v: str) -> int | float | bool | str:
    """Try int, then float, then bool, then return str unchanged."""
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    return v


def apply_opts(config: dict, opts: dict[str, str]) -> dict:
    """Deep-merge flat dot-path opts into config and return a new dict.

    Does not mutate the input config.
    Example: apply_opts({}, {"providers.kimi.model": "kimi-k2"})
             -> {"providers": {"kimi": {"model": "kimi-k2"}}}
    """
    result: dict[str, Any] = copy.deepcopy(config)
    for dotpath, raw_value in opts.items():
        keys = dotpath.split(".")
        node = result
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = _coerce_value(raw_value)
    return result


def opts_from_env(prefix: str = "ICDEV_OPTS_") -> dict[str, str]:
    """Read env vars starting with prefix and convert to flat opts dict.

    Converts ``__`` to ``.`` in key names after stripping the prefix.
    Example: ICDEV_OPTS_PROVIDERS__KIMI__MODEL=kimi-k2
             -> {"providers.kimi.model": "kimi-k2"}
    """
    result: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.startswith(prefix):
            dotpath = k[len(prefix):].lower().replace("__", ".")
            result[dotpath] = v
    return result


def merge_config_sources(
    base_config: dict,
    file_opts: dict[str, str] | None = None,
    env_opts: dict[str, str] | None = None,
    cli_opts: dict[str, str] | None = None,
) -> dict:
    """Apply overrides in precedence order: base -> file_opts -> env_opts -> cli_opts."""
    result = copy.deepcopy(base_config)
    for opts in (file_opts, env_opts, cli_opts):
        if opts:
            result = apply_opts(result, opts)
    return result
