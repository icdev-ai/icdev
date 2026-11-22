# CUI // SP-CTI
"""Store publisher CLI (icx-ent-04).

    python -m tools.store_publisher.cli check-config [--json]
    python -m tools.store_publisher.cli packages [--json]                   synthetic packages only (an agent's view)
    python -m tools.store_publisher.cli seed-synthetic [--json]             write the labelled synthetic inputs

It never prepares, publishes or exports: preparing makes the publisher's key sign and is a signed-in person's act on
/store-publisher/, publishing is another person's declared step there, and the full bundle is taken out only by a
person who packages or publishes (review L4), from the console.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Store publisher (icx-ent-04)")
    ap.add_argument("command", choices=("check-config", "packages", "seed-synthetic"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from tools.store_publisher import config, service

    try:
        if a.command == "check-config":
            cfg = config.load()
            out = {"ok": True, "packages": len(cfg.get("packages") or []), "publisher": cfg["publisher"]["id"],
                   "key_configured": bool((cfg.get("signing") or {}).get("key")), "config_sha256": cfg["_digest"]}
        elif a.command == "packages":
            out = service.packages_view(service.mcp_reader("cli"))
        else:
            from tools.store_publisher import synthetic

            out = synthetic.write_all()
    except (service.Refused, config.ConfigError) as exc:
        print(json.dumps({"error": str(getattr(exc, "message", exc)), "code": getattr(exc, "code", "config")}))
        return 1
    print(json.dumps(out, indent=None if a.json else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
