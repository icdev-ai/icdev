# CUI // SP-CTI
"""Store publisher CLI (icx-ent-04).

    python -m tools.store_publisher.cli check-config [--json]
    python -m tools.store_publisher.cli packages [--json]                   synthetic packages only (an agent's view)
    python -m tools.store_publisher.cli seed-synthetic [--json]             write the labelled synthetic inputs
    python -m tools.store_publisher.cli export --package <id> --out <file>  a PUBLISHED synthetic package, as ICDEV[OS]
                                                                           imports it

It never prepares or publishes: preparing makes the publisher's key sign and is a signed-in person's act on
/store-publisher/, and publishing is another person's declared step there. ``export`` reads as an agent at the MCP
marking, so it exports only a synthetic package's published version; a customer package is exported from the console
by a person of its org.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Store publisher (icx-ent-04)")
    ap.add_argument("command", choices=("check-config", "packages", "seed-synthetic", "export"))
    ap.add_argument("--package", default="")
    ap.add_argument("--out", default="")
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
        elif a.command == "seed-synthetic":
            from tools.store_publisher import synthetic

            out = synthetic.write_all()
        else:
            out = service.export(a.package, service.mcp_reader("cli"))
            if a.out:
                Path(a.out).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                out = {"written": a.out, "digest": out["publication"]["content_digest"]}
    except (service.Refused, config.ConfigError) as exc:
        print(json.dumps({"error": str(getattr(exc, "message", exc)), "code": getattr(exc, "code", "config")}))
        return 1
    print(json.dumps(out, indent=None if a.json else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
