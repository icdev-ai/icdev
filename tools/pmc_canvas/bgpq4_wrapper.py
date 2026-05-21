# CUI // SP-CTI
"""bgpq4 wrapper for AS-SET to prefix-list expansion.

bgpq4 (https://github.com/bgp/bgpq4) is the production-standard tool for
generating prefix-lists and route-policies from IRR (Internet Routing Registry)
AS-SET data.

This wrapper calls bgpq4 via subprocess when available, falling back to the
pure-Python WHOIS implementation in tools/pmc_canvas/rpsl_generator.py when
bgpq4 is not installed.

Configuration (env):
  BGPQ4_PATH      — path to bgpq4 binary (default: 'bgpq4' on $PATH)
  BGPQ4_IRR_HOST  — IRR mirror host (default: whois.radb.net)
  BGPQ4_TIMEOUT   — subprocess timeout seconds (default: 30)
"""
from __future__ import annotations

import json as _json
import os
import shutil
import subprocess
from typing import Optional

BGPQ4_BIN      = os.environ.get("BGPQ4_PATH", "bgpq4")
BGPQ4_IRR_HOST = os.environ.get("BGPQ4_IRR_HOST", "whois.radb.net")
BGPQ4_TIMEOUT  = int(os.environ.get("BGPQ4_TIMEOUT", "30"))


def is_available() -> bool:
    """Return True if bgpq4 binary is accessible on this system."""
    return shutil.which(BGPQ4_BIN) is not None


def expand_as_set(
    as_set: str,
    *,
    ipv6: bool = False,
    format_type: str = "json",
    max_length: Optional[int] = None,
    aggregation: bool = True,
) -> dict:
    """Expand AS-SET to prefix list using bgpq4, or fall back to pure-Python.

    Args:
        as_set:      IRR AS-SET object name (e.g. "AS-EXAMPLE", "AS64512:AS-CUSTOMERS")
        ipv6:        Expand IPv6 prefixes when True; IPv4 otherwise
        format_type: Output format — 'json', 'ios-xr', 'junos', 'eos', 'bird2', 'cisco'
        max_length:  Drop prefixes longer than this length (None = no filter)
        aggregation: Aggregate contiguous prefixes (bgpq4 -A flag)

    Returns dict with 'prefixes' list (json mode) or 'output' string (config mode),
    plus 'count', 'source', 'as_set', 'format', 'ipv6'.
    """
    if not as_set or not as_set.strip():
        return {"error": "as_set required", "prefixes": [], "count": 0}

    if is_available():
        return _bgpq4_expand(as_set, ipv6=ipv6, format_type=format_type,
                             max_length=max_length, aggregation=aggregation)
    return _fallback_expand(as_set, ipv6=ipv6, max_length=max_length)


def _bgpq4_expand(
    as_set: str,
    *,
    ipv6: bool,
    format_type: str,
    max_length: Optional[int],
    aggregation: bool,
) -> dict:
    cmd = [BGPQ4_BIN, "-h", BGPQ4_IRR_HOST]
    if ipv6:
        cmd.append("-6")
    if aggregation:
        cmd.append("-A")
    if max_length is not None:
        cmd.extend(["-l", str(max_length)])

    # format flags
    fmt_flags = {
        "json":   ["-j"],
        "ios-xr": ["-X"],
        "junos":  ["-J"],
        "eos":    ["-e"],
        "bird2":  ["-b"],
        "cisco":  [],
    }
    cmd.extend(fmt_flags.get(format_type, ["-j"]))
    cmd.append(as_set)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=BGPQ4_TIMEOUT
        )
        if proc.returncode != 0:
            return {
                "error": proc.stderr.strip() or "bgpq4 exited non-zero",
                "as_set": as_set, "source": "bgpq4", "prefixes": [], "count": 0,
            }

        output = proc.stdout.strip()
        if format_type == "json":
            try:
                data = _json.loads(output)
                prefixes = data if isinstance(data, list) else data.get("prefixes", [])
                return {
                    "as_set": as_set, "prefixes": prefixes, "count": len(prefixes),
                    "source": "bgpq4", "format": format_type, "ipv6": ipv6,
                }
            except Exception:
                pass  # fall through to raw output

        return {
            "as_set": as_set, "output": output, "count": output.count("\n"),
            "source": "bgpq4", "format": format_type, "ipv6": ipv6,
        }

    except FileNotFoundError:
        return _fallback_expand(as_set, ipv6=ipv6, max_length=max_length)
    except subprocess.TimeoutExpired:
        return {
            "error": f"bgpq4 timed out after {BGPQ4_TIMEOUT}s",
            "as_set": as_set, "source": "bgpq4", "prefixes": [], "count": 0,
        }


def _fallback_expand(as_set: str, *, ipv6: bool, max_length: Optional[int]) -> dict:
    """Pure-Python fallback using rpsl_generator WHOIS expansion."""
    try:
        from tools.pmc_canvas.rpsl_generator import expand_as_set_whois
        prefixes = expand_as_set_whois(as_set, ipv6=ipv6)
        if max_length is not None:
            prefixes = [p for p in prefixes if int(p.split("/")[-1]) <= max_length]
        return {
            "as_set": as_set, "prefixes": prefixes, "count": len(prefixes),
            "source": "rpsl_generator (bgpq4 not installed)",
            "format": "json", "ipv6": ipv6,
        }
    except Exception as exc:
        return {
            "as_set": as_set, "prefixes": [], "count": 0,
            "source": "fallback_error", "error": str(exc),
        }


def check_availability() -> dict:
    """Report bgpq4 availability and fallback path."""
    avail = is_available()
    return {
        "bgpq4_available": avail,
        "bgpq4_path": shutil.which(BGPQ4_BIN) if avail else None,
        "irr_host": BGPQ4_IRR_HOST,
        "fallback": "tools.pmc_canvas.rpsl_generator",
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="bgpq4 wrapper — AS-SET to prefix list")
    parser.add_argument("--as-set", help="AS-SET object name")
    parser.add_argument("--format", default="json",
                        choices=["json", "ios-xr", "junos", "eos", "bird2", "cisco"])
    parser.add_argument("--ipv6", action="store_true")
    parser.add_argument("--max-length", type=int)
    parser.add_argument("--check", action="store_true", help="Check bgpq4 availability")
    parser.add_argument("--json", dest="json_out", action="store_true")
    args = parser.parse_args()

    if args.check:
        result = check_availability()
    elif args.as_set:
        result = expand_as_set(
            args.as_set,
            ipv6=args.ipv6,
            format_type=args.format,
            max_length=args.max_length,
        )
    else:
        parser.print_help()
        raise SystemExit(1)

    print(_json.dumps(result, indent=2))
