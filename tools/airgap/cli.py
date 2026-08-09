#!/usr/bin/env python3

# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Air-Gap CLI — detect, activate, validate, and report air-gap status.

Usage::

    # Detect environment
    python tools/airgap/cli.py --detect

    # Activate air-gap mode (patches LLM routing to local-only)
    python tools/airgap/cli.py --activate

    # Run air-gap health check
    python tools/airgap/cli.py --health

    # Full report (detect + activate + health)
    python tools/airgap/cli.py --full

    # All output as JSON
    python tools/airgap/cli.py --full --json
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.airgap.cli")


def cmd_detect(as_json: bool = False) -> int:
    """Detect air-gap environment."""
    from tools.airgap.detector import detect_environment

    env = detect_environment(use_cache=False)

    if as_json:
        print(json.dumps(env, indent=2))
    else:
        print("\nICDEV™ Air-Gap Environment Detection")
        print("=" * 40)
        print(f"  Air-gapped:        {'YES' if env['airgap'] else 'NO'}")
        local_servers = env.get("local_llm_servers", [])
        if local_servers:
            names = ", ".join(s["name"] for s in local_servers)
            print(f"  Local LLM:         {len(local_servers)} server(s): {names}")
        else:
            print("  Local LLM:         none detected")
        print(f"  Ollama:            {'UP' if env['ollama'] else 'DOWN'}")
        print(f"  Claude Code CLI:   {'found' if env['claude_code_cli'] else 'not found'}")
        print(f"  Claude Code env:   {'active' if env['claude_code_env'] else 'inactive'}")
        print(f"  Anthropic API:     {'reachable' if env['anthropic_api'] else 'unreachable'}")
        print(f"  AWS Bedrock:       {'reachable' if env['bedrock'] else 'unreachable'}")
        print(f"  Config air_gapped: {env['cloud_config_airgap']}")
        print(f"  Env var forced:    {env['env_var_forced']}")

    return 0 if not env["airgap"] else 2


def cmd_activate(as_json: bool = False) -> int:
    """Activate air-gap mode."""
    from tools.airgap.config_patcher import activate_airgap

    result = activate_airgap()

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nAir-gap activation: {result['status']}")
        if result["status"] == "activated":
            print(f"  Local models:   {', '.join(result.get('local_models', []))}")
            print(f"  Routes patched: {result.get('patched_functions_count', 0)}")
            print(f"  Two-tier:       {'enabled' if result.get('two_tier_enabled') else 'disabled'}")
            print(f"  Env shims:      {', '.join(result.get('env_shims', []))}")

    return 0 if result["status"] in ("activated", "already_active") else 1


def cmd_health(as_json: bool = False) -> int:
    """Run air-gap health check."""
    from tools.airgap.health_check import run_health_check

    result = run_health_check()

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\nICDEV™ Air-Gap Health: {result['overall'].upper()}")
        print(f"Timestamp: {result['timestamp']}\n")
        for name, check in result["checks"].items():
            status = check.get("status", "unknown")
            icon = {
                "pass": "+",
                "fail": "X",
                "warn": "!",
                "info": "i",
                "error": "E",
            }.get(status, "?")
            detail = check.get("error", "")
            if not detail and "models" in check:
                detail = f"{check.get('model_count', 0)} models"
            if not detail and "providers" in check:
                detail = ", ".join(check["providers"])
            print(f"  [{icon}] {name:<18} {status:<6} {detail}")

    return 0 if result["overall"] != "unhealthy" else 1


def cmd_full(as_json: bool = False) -> int:
    """Full report: detect + activate + health."""
    from tools.airgap.detector import detect_environment
    from tools.airgap.config_patcher import activate_airgap
    from tools.airgap.health_check import run_health_check

    env = detect_environment(use_cache=False)
    activation = {}
    if env["airgap"]:
        activation = activate_airgap()

    health = run_health_check()

    report = {
        "environment": env,
        "activation": activation,
        "health": health,
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("\n" + "=" * 50)
        print("  ICDEV™ Air-Gap Full Report")
        print("=" * 50)

        print(f"\n  Environment: {'AIR-GAPPED' if env['airgap'] else 'CONNECTED'}")
        local_count = env.get("local_llm_count", 0)
        print(f"  Local LLM servers: {local_count}")
        print(f"  Cloud: {'unreachable' if not env['anthropic_api'] and not env['bedrock'] else 'reachable'}")

        if activation:
            print(f"\n  Activation: {activation.get('status', 'skipped')}")
            if activation.get("local_models"):
                print(f"  Local models: {', '.join(activation['local_models'][:5])}")

        print(f"\n  Health: {health['overall'].upper()}")
        for name, check in health["checks"].items():
            status = check.get("status", "?")
            icon = {"pass": "+", "fail": "X", "warn": "!", "info": "i"}.get(status, "?")
            print(f"    [{icon}] {name}: {status}")

    return 0 if health["overall"] != "unhealthy" else 1


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Air-Gap Compatibility CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tools/airgap/cli.py --detect          # Check if air-gapped
  python tools/airgap/cli.py --activate         # Enable local-only LLM routing
  python tools/airgap/cli.py --health           # Run health checks
  python tools/airgap/cli.py --full --json      # Full report as JSON
        """,
    )
    parser.add_argument("--detect", action="store_true", help="Detect air-gap environment")
    parser.add_argument("--activate", action="store_true", help="Activate air-gap mode")
    parser.add_argument("--health", action="store_true", help="Run health check")
    parser.add_argument("--full", action="store_true", help="Full report")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if not any([args.detect, args.activate, args.health, args.full]):
        parser.print_help()
        return 0

    if args.full:
        return cmd_full(as_json=args.json)
    if args.detect:
        return cmd_detect(as_json=args.json)
    if args.activate:
        return cmd_activate(as_json=args.json)
    if args.health:
        return cmd_health(as_json=args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
