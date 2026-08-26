#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Preflight Checks — resource, environment, and session validation.

Runs before each Scout scan to ensure the machine is idle enough
and the environment is ready. All checks return (ok, reason) tuples.

Usage:
    python tools/scout/preflight.py --check-all --json
    python tools/scout/preflight.py --check cpu --json
    python tools/scout/preflight.py --check vram --json
    python tools/scout/preflight.py --check ollama --json
    python tools/scout/preflight.py --check window --json
    python tools/scout/preflight.py --check claude --json
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def check_operating_window(config: Dict) -> Tuple[bool, str]:
    """Is current local hour within the allowed scan window?"""
    sched = config.get("schedule", {})
    start = sched.get("window_start_hour", 8)
    end = sched.get("window_end_hour", 23)
    now_hour = datetime.now().hour
    if start <= now_hour < end:
        return True, f"Within operating window ({start}:00-{end}:00, current: {now_hour}:00)"
    return False, f"Outside operating window ({start}:00-{end}:00, current: {now_hour}:00)"


def check_cpu_usage(max_percent: float = 70.0) -> Tuple[bool, str]:
    """Check if CPU usage is below threshold."""
    try:
        import psutil

        usage = psutil.cpu_percent(interval=2)
        if usage <= max_percent:
            return True, f"CPU usage {usage:.1f}% <= {max_percent}% threshold"
        return False, f"CPU usage {usage:.1f}% > {max_percent}% threshold — deferring"
    except ImportError:
        pass

    # Fallback: wmic on Windows
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "loadpercentage", "/value"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            if "LoadPercentage" in line:
                usage = float(line.split("=")[1].strip())
                if usage <= max_percent:
                    return True, f"CPU usage {usage:.0f}% <= {max_percent}% threshold"
                return False, f"CPU usage {usage:.0f}% > {max_percent}% threshold — deferring"
    except Exception:
        pass

    # Can't determine — allow
    return True, "CPU check unavailable (psutil not installed, wmic failed) — allowing"


def check_vram_usage(max_gb: float = 6.0) -> Tuple[bool, str]:
    """Check if GPU VRAM usage is below threshold via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            used_mb = float(result.stdout.strip().split("\n")[0])
            used_gb = used_mb / 1024.0
            if used_gb <= max_gb:
                return True, f"VRAM usage {used_gb:.1f}GB <= {max_gb}GB threshold"
            return False, f"VRAM usage {used_gb:.1f}GB > {max_gb}GB threshold — deferring"
    except (FileNotFoundError, Exception):
        pass

    # No GPU or nvidia-smi unavailable — allow
    return True, "VRAM check unavailable (no nvidia-smi) — allowing"


def check_ollama_running(url: str = "http://localhost:11434") -> Tuple[bool, str]:
    """Check if Ollama is running and responsive."""
    try:
        import urllib.request

        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — localhost Ollama
            if resp.status == 200:
                data = json.loads(resp.read())
                model_count = len(data.get("models", []))
                return True, f"Ollama running at {url} ({model_count} models loaded)"
    except Exception as exc:
        return False, f"Ollama not running at {url}: {exc}"

    return False, f"Ollama not responsive at {url}"


def check_claude_code_session(config: Dict) -> Tuple[bool, str]:
    """Check if a Claude Code session is active (user is working)."""
    detection = config.get("preflight", {}).get("claude_code_detection", {})
    process_names = detection.get("process_names", ["claude", "claude-code"])

    # Check for Claude Code processes
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            running = result.stdout.lower()
        else:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            running = result.stdout.lower()

        for name in process_names:
            if name.lower() in running:
                return False, f"Claude Code session detected (process: {name}) — deferring"
    except Exception:
        pass  # Can't check — allow

    return True, "No active Claude Code session detected"


_SHORT_PREMIUM_STRATEGIES = frozenset({
    "short_condor",
    "short_strangle",
    "short_straddle",
    "short_call",
    "short_put",
    "iron_condor",
    "iron_butterfly",
})


def check_liquidity_trap(
    macro_data: Dict,
    strategy: str = "",
    bypass: bool = False,
) -> Tuple[bool, str]:
    """Block short-premium strategies when a macro liquidity trap is active.

    Bypassable per-call via ``bypass=True`` or env
    ``ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE=true``.
    """
    import os

    if bypass or os.environ.get("ICDEV_PREFLIGHT_LIQTRAP_OVERRIDE", "").lower() == "true":
        return True, "Liquidity-trap check bypassed"

    if strategy and strategy not in _SHORT_PREMIUM_STRATEGIES:
        return True, f"Strategy '{strategy}' not subject to liquidity-trap gate"

    # THE LIQUIDITY-TRAP DETECTOR LEFT WITH THE TRADING DOMAIN (xit-rm-02). It lived in
    # tools.trading.ta.macro_liquidity, which is now ICDEV[FT]'s. The gate keeps its
    # fail-open contract -- it always allowed on an unavailable detector -- so the behaviour
    # here is unchanged; what is removed is an import that can no longer succeed.
    return True, "Liquidity-trap check unavailable (detector moved to ICDEV[FT]) — allowing"


def check_all(config: Dict) -> Tuple[bool, str, Dict]:
    """Run all preflight checks. Returns (all_ok, first_failure_reason, details)."""
    pf = config.get("preflight", {})
    results = {}

    # 1. Operating window (hard requirement)
    ok, msg = check_operating_window(config)
    results["window"] = {"ok": ok, "message": msg}
    if not ok:
        return False, msg, results

    # 2. Claude Code session (defer)
    if pf.get("detect_claude_code", True):
        ok, msg = check_claude_code_session(config)
        results["claude_code"] = {"ok": ok, "message": msg}
        if not ok:
            return False, msg, results

    # 3. CPU usage (defer)
    cpu_max = pf.get("cpu_max_percent", 70)
    ok, msg = check_cpu_usage(cpu_max)
    results["cpu"] = {"ok": ok, "message": msg}
    if not ok:
        return False, msg, results

    # 4. VRAM usage (defer)
    vram_max = pf.get("vram_max_gb", 6.0)
    ok, msg = check_vram_usage(vram_max)
    results["vram"] = {"ok": ok, "message": msg}
    if not ok:
        return False, msg, results

    # 5. Ollama (non-blocking — scan proceeds, LLM synthesis skipped)
    if pf.get("check_ollama", True):
        ollama_url = pf.get("ollama_url", "http://localhost:11434")
        ok, msg = check_ollama_running(ollama_url)
        results["ollama"] = {"ok": ok, "message": msg}
        # Don't block on Ollama — just record status

    return True, "All preflight checks passed", results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout preflight checks")
    parser.add_argument("--check-all", action="store_true", help="Run all checks")
    parser.add_argument("--check", choices=["cpu", "vram", "ollama", "window", "claude"], help="Run a specific check")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if check fails")
    args = parser.parse_args()

    # Load config
    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    if args.check_all:
        ok, reason, details = check_all(config)
        result = {"ok": ok, "reason": reason, "checks": details}
    elif args.check == "cpu":
        ok, msg = check_cpu_usage(config.get("preflight", {}).get("cpu_max_percent", 70))
        result = {"ok": ok, "message": msg}
    elif args.check == "vram":
        ok, msg = check_vram_usage(config.get("preflight", {}).get("vram_max_gb", 6.0))
        result = {"ok": ok, "message": msg}
    elif args.check == "ollama":
        url = config.get("preflight", {}).get("ollama_url", "http://localhost:11434")
        ok, msg = check_ollama_running(url)
        result = {"ok": ok, "message": msg}
    elif args.check == "window":
        ok, msg = check_operating_window(config)
        result = {"ok": ok, "message": msg}
    elif args.check == "claude":
        ok, msg = check_claude_code_session(config)
        result = {"ok": ok, "message": msg}
    else:
        parser.error("Specify --check-all or --check <name>")
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")

    if args.gate and not result.get("ok", True):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
