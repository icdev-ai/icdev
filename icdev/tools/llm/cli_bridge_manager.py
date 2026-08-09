# CUI // SP-CTI
"""CLI Bridge auto-detection and configuration manager.

Detects whether the local ``claude`` CLI (Claude Code) is installed and
configures ``ICDEV_CLI_BRIDGE`` in ``.env`` accordingly.  Designed for
users whose Claude subscription works through the CLI binary rather than
a direct Anthropic API key.

Usage:
    python tools/llm/cli_bridge_manager.py --status
    python tools/llm/cli_bridge_manager.py --auto-config
    python tools/llm/cli_bridge_manager.py --enable
    python tools/llm/cli_bridge_manager.py --disable
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Project root discovery (same heuristic as router.py)
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    p = start.resolve()
    for _ in range(5):
        if (p / "args" / "llm_config.yaml").exists():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return start.resolve().parents[2]


PROJECT_ROOT = _find_repo_root(Path(__file__).resolve().parent.parent.parent)
ENV_FILE = PROJECT_ROOT / ".env"

# ---------------------------------------------------------------------------
# dotenv helpers (lightweight; no python-dotenv dependency required)
# ---------------------------------------------------------------------------


def _read_env() -> dict[str, str]:
    """Read .env as a flat dict (comments ignored, quotes stripped)."""
    result: dict[str, str] = {}
    if not ENV_FILE.exists():
        return result
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        result[key] = value
    return result


def _write_env(key: str, value: str) -> None:
    """Write or update a key in .env, preserving comments and other keys."""
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, _ = stripped.partition("=")
        if k.strip() == key:
            lines[i] = f"{key}={value}"
            found = True
            break

    if not found:
        # Add after the ICDEV Dashboard section or at end
        inserted = False
        for i, line in enumerate(lines):
            if line.strip().startswith("ICDEV_DASHBOARD_API_KEY"):
                lines.insert(i + 1, f"{key}={value}")
                inserted = True
                break
        if not inserted:
            lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")


def _delete_env(key: str) -> None:
    """Remove a key from .env (comment it out rather than delete, for audit)."""
    if not ENV_FILE.exists():
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, _ = stripped.partition("=")
        if k.strip() == key:
            lines[i] = f"# {line}  # disabled by cli_bridge_manager"
            break
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------


def _detect_claude_binary() -> Optional[str]:
    return shutil.which("claude")


def _has_cloud_api_key() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )


def _get_current_env_state() -> Optional[str]:
    env = _read_env()
    val = env.get("ICDEV_CLI_BRIDGE", "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return "enabled"
    if val in ("0", "false", "no", "off"):
        return "disabled"
    return None  # not set


def _should_auto_enable() -> bool:
    binary = _detect_claude_binary()
    if not binary:
        return False
    # Auto-enable when Claude CLI is present but no direct cloud API keys
    # (typical Claude Code-only subscription)
    return not _has_cloud_api_key()

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    binary = _detect_claude_binary()
    env_state = _get_current_env_state()
    has_keys = _has_cloud_api_key()

    print("=== ICDEV CLI Bridge Status ===")
    print(f"  Claude binary on PATH: {'YES (' + str(binary) + ')' if binary else 'NO'}")
    print(f"  Cloud API keys present: {'YES' if has_keys else 'NO'}")
    print(f"  ICDEV_CLI_BRIDGE in .env: {env_state or 'NOT SET'}")
    print(f"  Router _cli_bridge_active: {'YES' if _should_auto_enable() else 'NO'}")
    print()
    if env_state == "enabled":
        if not binary:
            print("WARNING: Bridge is enabled but 'claude' binary is missing.")
            return 1
        print("Bridge is ENABLED and functional.")
    elif env_state == "disabled":
        print("Bridge is explicitly DISABLED.")
    else:
        if binary and not has_keys:
            print("Bridge will AUTO-ENABLE at runtime (binary present, no cloud keys).")
            print("Run --auto-config to persist this to .env.")
        else:
            print("Bridge is INACTIVE.")
    return 0


def cmd_auto_config(args: argparse.Namespace) -> int:
    binary = _detect_claude_binary()
    env_state = _get_current_env_state()
    has_keys = _has_cloud_api_key()

    if not binary:
        print("ERROR: 'claude' binary not found on PATH. Cannot enable bridge.")
        if env_state == "enabled":
            print("Disabling stale ICDEV_CLI_BRIDGE in .env...")
            _delete_env("ICDEV_CLI_BRIDGE")
        return 1

    if env_state == "enabled":
        print("Bridge already enabled in .env — no change needed.")
        return 0

    if has_keys:
        print(
            "Cloud API keys detected (ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY)."
        )
        print(
            "CLI bridge is OPTIONAL — cloud providers will be tried first. "
            "Use --enable to force bridge mode."
        )
        return 0

    # No cloud keys + binary present → enable
    _write_env("ICDEV_CLI_BRIDGE", "true")
    print("AUTO-CONFIGURED: ICDEV_CLI_BRIDGE=true written to .env")
    print("Reason: Claude CLI present, no cloud API keys detected.")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    binary = _detect_claude_binary()
    if not binary:
        print("WARNING: 'claude' not found on PATH — bridge will fail until installed.")
    _write_env("ICDEV_CLI_BRIDGE", "true")
    print("ICDEV_CLI_BRIDGE=true written to .env")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    _delete_env("ICDEV_CLI_BRIDGE")
    print("ICDEV_CLI_BRIDGE disabled in .env")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/llm/cli_bridge_manager.py",
        description="Auto-detect and configure the ICDEV Claude Code CLI bridge.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show current bridge state")
    group.add_argument(
        "--auto-config",
        action="store_true",
        help="Enable bridge if Claude CLI is present and no cloud API keys exist",
    )
    group.add_argument("--enable", action="store_true", help="Force-enable bridge in .env")
    group.add_argument("--disable", action="store_true", help="Force-disable bridge in .env")
    group.add_argument("--json", action="store_true", help="JSON output (use with --status)")
    args = parser.parse_args()

    if args.json and not args.status:
        print("ERROR: --json requires --status")
        return 2

    if args.json:
        import json

        binary = _detect_claude_binary()
        env_state = _get_current_env_state()
        print(
            json.dumps(
                {
                    "claude_binary_found": bool(binary),
                    "claude_binary_path": binary,
                    "cloud_api_keys_present": _has_cloud_api_key(),
                    "env_state": env_state,
                    "auto_enable": _should_auto_enable(),
                },
                indent=2,
            )
        )
        return 0

    if args.status:
        return cmd_status(args)
    if args.auto_config:
        return cmd_auto_config(args)
    if args.enable:
        return cmd_enable(args)
    if args.disable:
        return cmd_disable(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
