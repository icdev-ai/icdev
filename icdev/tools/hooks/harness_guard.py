#!/usr/bin/env python3
# CUI // SP-CTI
"""omx-guard-01/03: a non-Claude harness's tool call -> ICDEV's PreToolUse guard.

The thin Python half of the opencode ``tool.execute.before`` plugin
(``icdev/data/harness_plugins/opencode/icdev-guard.ts``) and the Pi
``tool_call`` extension (``icdev/data/harness_plugins/pi/icdev-guard.ts``,
omx-guard-03). It owns NO check: it
maps the harness's tool call onto Claude Code's ``{tool_name, tool_input}``
shape and asks :func:`tools.airgap.hook_compat.run_pre_tool_check`, which runs
the ONE copy of the checks in ``tools/hooks/shared_checks.py``.

Protocol -- one JSON object on stdin::

    {"tool": "<harness tool name>", "args": {...}}

one JSON verdict on stdout, exit 0::

    {"allowed": bool, "reason": str, "advisory": bool,
     "tool_name": str, "tool_input": {...}}

The mapping is load-bearing, not cosmetic (omx-spike-01 findings): every check
keys on the Claude Code spelling, so ``check_dangerous_rm`` returns None for
opencode's lowercase ``bash`` -- an unmapped call runs every check and blocks
nothing, which is the ``|| true`` failure in another form.

Kill switches are the Claude Code hook's own (``CHECK_KILL_SWITCHES`` and
``ICDEV_PRETOOLUSE_ENFORCE`` in ``.claude/hooks/pre_tool_use.py``), read from
that file rather than copied: ``ICDEV_<CHECK>_GUARD=0`` skips one check, and
``ICDEV_PRETOOLUSE_ENFORCE=0`` turns a refusal into an advisory allow. There is
no ``=monitor`` mode here because ``run_pre_tool_check`` has none; the checks
that ship monitor-only (``network_egress``, ``agent_rules``) already return no
refusal at their defaults.

Fail OPEN, loudly, exactly like ``pre_tool_use.main()``: a bridge that cannot
decide prints an ``allowed`` verdict whose reason says so and logs a warning to
stderr. The plugin does the same for a bridge that cannot be run at all.

Usage::

    echo '{"tool":"bash","args":{"command":"rm -rf /"}}' | \
        python -m tools.hooks.harness_guard --harness opencode
    echo '{"tool":"bash","args":{"command":"rm -rf /"}}' | \
        python -m tools.hooks.harness_guard --harness pi
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Tuple

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The repo root in a checkout; the ``icdev`` package directory in the wheel.
BASE_DIR = repo_root(__file__)

#: harness tool name -> ICDEV (Claude Code) tool name, per harness. Measured in
#: omx-spike-01 (docs/research/omx-spike-01/findings.md). A name not listed
#: passes through unchanged -- every check still sees the payload.
TOOL_NAMES: Dict[str, Dict[str, str]] = {
    "opencode": {
        "bash": "Bash",
        "write": "Write",
        "edit": "Edit",
        "multiedit": "Edit",
        "patch": "Edit",
        "read": "Read",
        "grep": "Grep",
        "glob": "Glob",
        "list": "LS",
        "webfetch": "WebFetch",
    },
    # omx-guard-03. Pi 1.1.0's built-in tools (findings.md, "Pi" table).
    # `powershell` is Pi's Windows shell tool and takes Bash's input shape; it
    # maps to Bash so every shell check sees it, but the rm check parses POSIX
    # syntax only, so a PowerShell-dialect delete is NOT caught (spike gap 7,
    # recorded in docs/features/phase-omx-guard.md).
    "pi": {
        "bash": "Bash",
        "powershell": "Bash",
        "write": "Write",
        "edit": "Edit",
        "read": "Read",
        "grep": "Grep",
        "find": "Glob",
        "ls": "LS",
    },
}

#: harness arg key -> ICDEV arg key (opencode's args are camelCase).
ARG_KEYS: Dict[str, Dict[str, str]] = {
    "opencode": {
        "filePath": "file_path",
        "oldString": "old_string",
        "newString": "new_string",
        "replaceAll": "replace_all",
    },
    "pi": {
        "path": "file_path",
    },
}

HARNESSES = tuple(sorted(TOOL_NAMES))

ENFORCEMENT_ENV = "ICDEV_PRETOOLUSE_ENFORCE"
_OFF_VALUES = ("0", "false", "no", "off")

#: Where the hook that owns the kill-switch table can be found: the source
#: checkout, then the copy the wheel ships for `icdev init`.
_HOOK_CANDIDATES = (
    BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py",
    BASE_DIR / "icdev" / "data" / "claude_bootstrap" / ".claude" / "hooks"
    / "pre_tool_use.py",
    BASE_DIR / "data" / "claude_bootstrap" / ".claude" / "hooks" / "pre_tool_use.py",
)


def to_icdev(harness: str, tool: str, args: Optional[Dict[str, Any]]
             ) -> Tuple[str, Dict[str, Any]]:
    """Map one harness tool call onto ICDEV's ``(tool_name, tool_input)``."""
    names = TOOL_NAMES.get(harness, {})
    keys = ARG_KEYS.get(harness, {})
    name = names.get(str(tool).lower(), str(tool))
    mapped = {keys.get(k, k): v for k, v in (args or {}).items()}
    if harness == "pi" and isinstance(mapped.get("edits"), list):
        mapped.update(_join_pi_edits(mapped["edits"]))
    return name, mapped


def _join_pi_edits(edits: list) -> Dict[str, str]:
    """Pi's ``edits[{oldText,newText}]`` -> one ``old_string``/``new_string``.

    EVERY edit's text is joined, not just the first: a content check
    (``check_direct_sqlite_usage`` reads ``new_string``) must not be stepped
    around by putting the offending text in the second edit.
    """
    parts = [e for e in edits if isinstance(e, dict)]
    return {
        "old_string": "\n".join(str(e.get("oldText") or "") for e in parts),
        "new_string": "\n".join(str(e.get("newText") or "") for e in parts),
    }


def kill_switches(hook_path: Optional[Path] = None) -> Dict[str, str]:
    """The hook's ``CHECK_KILL_SWITCHES`` literal, read by AST -- never copied.

    Parsed, not imported: importing the hook runs its module body on every tool
    call. An unreadable table is ``{}`` (no per-check switch honoured), logged.
    """
    paths = (hook_path,) if hook_path else _HOOK_CANDIDATES
    for path in paths:
        if not path or not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and getattr(node.targets[0], "id", "") == "CHECK_KILL_SWITCHES"):
                    return dict(ast.literal_eval(node.value))
        except (OSError, SyntaxError, ValueError) as exc:
            logger.warning("harness_guard: cannot read kill switches from %s (%s)",
                           path, exc)
    logger.warning("harness_guard: no CHECK_KILL_SWITCHES table found; per-check "
                   "kill switches are not honoured")
    return {}


def disabled_checks(switches: Optional[Dict[str, str]] = None) -> FrozenSet[str]:
    """``check_<name>`` for every check whose kill switch is set off."""
    table = kill_switches() if switches is None else switches
    return frozenset(
        f"check_{name}" for name, env in table.items()
        if os.environ.get(env, "1").strip().lower() in _OFF_VALUES
    )


def enforcement_enabled() -> bool:
    return os.environ.get(ENFORCEMENT_ENV, "1").strip().lower() not in _OFF_VALUES


def decide(harness: str, tool: str, args: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The verdict for one harness tool call. Never raises: fails OPEN, logged."""
    tool_name, tool_input = to_icdev(harness, tool, args)
    base = {"tool_name": tool_name, "tool_input": tool_input, "advisory": False}
    try:
        from tools.airgap.hook_compat import run_pre_tool_check

        verdict = run_pre_tool_check(tool_name, tool_input, skip=disabled_checks())
    except Exception as exc:  # noqa: BLE001 -- fail open, like pre_tool_use.main()
        logger.warning("harness_guard: guard errored (%s: %s); FAILING OPEN",
                       type(exc).__name__, exc)
        return {**base, "allowed": True,
                "reason": f"ICDEV guard bridge error, failed open: "
                          f"{type(exc).__name__}: {exc}"}
    if not verdict.get("allowed") and not enforcement_enabled():
        return {**base, "allowed": True, "advisory": True,
                "reason": f"ADVISORY ({ENFORCEMENT_ENV}=0): {verdict.get('reason', '')}"}
    return {**base, "allowed": bool(verdict.get("allowed")),
            "reason": str(verdict.get("reason", ""))}


# ── installer (``icdev harness install-guard``) ──────────────────────────────

#: Packaged plugin source per harness, relative to the ``icdev/data`` tree:
#: under ``BASE_DIR/icdev`` in a checkout, ``BASE_DIR`` itself in the wheel.
PLUGIN_SOURCES: Dict[str, Path] = {
    "opencode": Path("harness_plugins") / "opencode" / "icdev-guard.ts",
    "pi": Path("harness_plugins") / "pi" / "icdev-guard.ts",
}


def plugin_source(harness: str) -> Path:
    """The packaged plugin file for *harness*, in a checkout or the wheel."""
    rel = PLUGIN_SOURCES[harness]
    for data in (BASE_DIR / "icdev" / "data", BASE_DIR / "data"):
        if (data / rel).is_file():
            return data / rel
    raise FileNotFoundError(f"packaged {harness} guard plugin not found ({rel})")
PLUGIN_FILENAME = "icdev-guard.ts"


def plugin_dir(harness: str, project: Optional[Path] = None,
               global_: bool = False) -> Path:
    """Where the harness loads plugins from (opencode: measured in omx-spike-01).

    opencode -- project: ``<DIR>/.opencode/plugin/``. Global:
    ``$XDG_CONFIG_HOME/opencode/plugin/``, defaulting to ``~/.config`` --
    opencode uses that path on every OS, Windows included.

    pi (Pi 1.1.0 ``docs/configuration.md``) -- project: ``<DIR>/.pi/extensions/``
    (loaded only once the project is trusted, which is why ``pi_cli`` ALSO
    passes the file with an explicit ``-e``). Global: ``$PI_CODING_AGENT_DIR/
    extensions/``, defaulting to ``~/.pi/agent/extensions/``.
    """
    if harness == "pi":
        if global_:
            agent = os.environ.get("PI_CODING_AGENT_DIR") or str(
                Path.home() / ".pi" / "agent")
            return Path(agent) / "extensions"
        return Path(project or Path.cwd()) / ".pi" / "extensions"
    if harness != "opencode":
        raise ValueError(f"no plugin location known for harness {harness!r}")
    if global_:
        config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(config) / "opencode" / "plugin"
    return Path(project or Path.cwd()) / ".opencode" / "plugin"


def guard_module() -> str:
    """The ``-m`` target the plugin spawns: the shim name in a checkout, the
    canonical one in the wheel (where no top-level ``tools`` exists on disk)."""
    if (BASE_DIR / "tools" / "hooks" / "harness_guard.py").is_file():
        return "tools.hooks.harness_guard"
    return "icdev.tools.hooks.harness_guard"


def render_plugin(harness: str, python: Optional[str] = None,
                  root: Optional[Path] = None) -> str:
    """The plugin source with this interpreter, root and module filled in."""
    text = plugin_source(harness).read_text(encoding="utf-8")
    for placeholder, value in (
        ('"__ICDEV_PYTHON__"', python or sys.executable),
        ('"__ICDEV_ROOT__"', str(root or BASE_DIR)),
        ('"tools.hooks.harness_guard"', guard_module()),
    ):
        text = text.replace(placeholder, json.dumps(value))
    return text


def install_guard(harness: str, project: Optional[Path] = None,
                  global_: bool = False) -> Dict[str, Any]:
    """Write the rendered plugin into the harness's plugin dir. Idempotent."""
    target = plugin_dir(harness, project, global_) / PLUGIN_FILENAME
    text = render_plugin(harness)
    unchanged = target.is_file() and target.read_text(encoding="utf-8") == text
    if not unchanged:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    return {"harness": harness, "path": str(target), "changed": not unchanged}


def live_probe(harness: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """An adapter's ``verify_guard()`` body: is the guard wired, MEASURED live.

    Renders the packaged plugin, then runs the exact process it spawns on a
    known-bad call in *harness*'s own spelling and requires a refusal. ``wired``
    is never inferred from a file existing.
    """
    import subprocess

    try:
        render_plugin(harness)
    except Exception as exc:  # noqa: BLE001
        return {"wired": False, "reason": f"guard plugin source unavailable: {exc}"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", guard_module(), "--harness", harness],
            input=json.dumps({"tool": tool, "args": args}), capture_output=True,
            text=True, encoding="utf-8", errors="replace", cwd=str(BASE_DIR),
            timeout=120, shell=False,
        )
        verdict = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"wired": False,
                "reason": f"guard bridge did not answer: {type(exc).__name__}: {exc}"}
    if verdict.get("allowed") is False:
        return {"wired": True,
                "reason": f"bridge refused a known-bad {tool} call: {verdict.get('reason')}"}
    return {"wired": False,
            "reason": f"bridge ALLOWED a known-bad {tool} call: {verdict.get('reason')}"}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.hooks.harness_guard",
        description="Map a harness tool call to ICDEV's guard; print a JSON verdict.",
    )
    parser.add_argument("--harness", required=True, choices=HARNESSES)
    opts = parser.parse_args(argv)

    try:
        request = json.loads(sys.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise ValueError("request is not a JSON object")
    except ValueError as exc:
        logger.warning("harness_guard: unreadable request (%s); FAILING OPEN", exc)
        print(json.dumps({"allowed": True, "advisory": False,
                          "reason": f"ICDEV guard bridge got an unreadable request, "
                                    f"failed open: {exc}"}))
        return 0
    verdict = decide(opts.harness, request.get("tool", ""), request.get("args") or {})
    print(json.dumps(verdict, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
