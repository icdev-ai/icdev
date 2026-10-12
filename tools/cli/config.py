# CUI // SP-CTI
"""`icdev config get <key>` -- read a declared ICDEV policy setting.

Each key is answered by the module that OWNS it, so the value printed is the
value the enforcing code sees (validation and fail-closed defaults included),
never a second parse of the YAML.

    icdev config get anthropic_billing          # -> subscription_only
    icdev config get anthropic_billing --json   # value, source file, warnings
    icdev config list
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional


def _anthropic_billing() -> Dict[str, Any]:
    from tools.llm.anthropic_billing import KEY, load_policy  # noqa: PLC0415

    policy = load_policy()
    return {"key": KEY, "value": policy[KEY], "source": policy["source"],
            "warnings": policy["warnings"]}


#: key -> (resolver, one-line description)
KEYS: Dict[str, tuple] = {
    "anthropic_billing": (
        _anthropic_billing,
        "How Claude may be paid for: subscription_only (default) | allow_metered "
        "(args/guard_anthropic.yaml)",
    ),
}


def get(key: str) -> Dict[str, Any]:
    resolver: Callable[[], Dict[str, Any]] = KEYS[key][0]
    return resolver()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="icdev config")
    subs = parser.add_subparsers(dest="cmd")
    get_p = subs.add_parser("get", help="Print the effective value of a policy key")
    get_p.add_argument("key")
    get_p.add_argument("--json", action="store_true")
    list_p = subs.add_parser("list", help="List the keys `get` understands")
    list_p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "get":
        if args.key not in KEYS:
            print(f"icdev config: unknown key '{args.key}' (known: {', '.join(sorted(KEYS))})",
                  file=sys.stderr)
            return 2
        result = get(args.key)
        print(json.dumps(result, indent=2) if args.json else result["value"])
        return 0
    if args.cmd == "list":
        if args.json:
            print(json.dumps({k: v[1] for k, v in KEYS.items()}, indent=2))
        else:
            for k, v in sorted(KEYS.items()):
                print(f"{k:24} {v[1]}")
        return 0
    parser.print_help()
    return 2
