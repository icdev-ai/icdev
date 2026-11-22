# CUI // SP-CTI
"""Signed factory builds CLI (icx-ent-03).

    python -m tools.factory_builds.cli check-config [--json]
    python -m tools.factory_builds.cli targets [--json]                  synthetic targets only (an agent's view)
    python -m tools.factory_builds.cli seed-synthetic [--json]           write the labelled synthetic inputs
    python -m tools.factory_builds.cli export --target <id> --out <file> a PUBLISHED synthetic build, as ICDEV[OS] imports it

It never prepares or publishes: preparing makes the factory's key sign and is a signed-in person's act on
/factory-builds/, and publishing is another person's declared step there. ``export`` reads as an agent at the MCP
marking, so it exports only a synthetic target's published build; a customer target's build is exported from the
console by a person of its org.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Signed factory builds (icx-ent-03)")
    ap.add_argument("command", choices=("check-config", "targets", "seed-synthetic", "export"))
    ap.add_argument("--target", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from tools.factory_builds import config, service

    try:
        if a.command == "check-config":
            cfg = config.load()
            out = {"ok": True, "targets": len(cfg.get("targets") or []), "signing": cfg["signing"]["mode"],
                   "key_configured": bool(cfg["signing"].get("key")),
                   "transparency_log": cfg["transparency_log"]["mode"], "config_sha256": cfg["_digest"]}
        elif a.command == "targets":
            out = service.targets_view(service.mcp_reader("cli"))
        elif a.command == "seed-synthetic":
            from tools.factory_builds import synthetic

            out = synthetic.write_all()
        else:
            out = service.export(a.target, service.mcp_reader("cli"))
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
