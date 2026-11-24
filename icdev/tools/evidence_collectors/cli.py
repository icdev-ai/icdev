#!/usr/bin/env python3
# CUI // SP-CTI
"""Evidence collectors CLI (icx-ent-02). Reads, prepares drafts and exports; never publishes.

    python -m tools.evidence_collectors.cli systems --json
    python -m tools.evidence_collectors.cli check-config --json
    python -m tools.evidence_collectors.cli collect --system <id> --json           observations only, records nothing
    python -m tools.evidence_collectors.cli prepare --system <id> --agent <name>    a draft, recorded as agent:<name>
    python -m tools.evidence_collectors.cli export --system <id> --out <dir>        the newest PUBLISHED report
    python -m tools.evidence_collectors.cli step-lost --system <id> --event <ece-...> --asked-by "<person>" --reason "<why>"
        the operator's path after a crash: void a PENDING decision whose step was never recorded (service.
        mark_step_lost: only after its grace period, only when the step store is readable and holds no record of it);
        it records the OS user running it beside --agent, and the person who asked

The CLI acts as an agent (``agent:<name>``) at the MCP marking (CUI), in no org: it sees what an agent may, and a
person publishes on /evidence-collectors/. ``export`` writes ``objective-evidence-<system>.json`` exactly as
ICDEV[OS] imports it (``POST /api/v1/controls/evidence``), its publication embedded; nothing unpublished is written.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))


def _os_user() -> str:
    """The OS account running the CLI (from the process's uid, not the environment)."""
    import os

    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, AttributeError):
        import getpass

        return getpass.getuser()


def main(argv: list[str] | None = None) -> int:
    from tools.evidence_collectors import config, service

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("systems", "check-config", "collect", "prepare", "export", "step-lost"))
    ap.add_argument("--system", default="")
    ap.add_argument("--agent", default="cli")
    ap.add_argument("--out", default="")
    ap.add_argument("--event", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--asked-by", default="", help="step-lost: the person who asked for it")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    reader = service.mcp_reader(a.agent)
    try:
        if a.command == "check-config":
            cfg = config.load()
            out = {"ok": True, "sha256": cfg["_sha256"], "systems": [s["id"] for s in cfg.get("systems") or []],
                   "mapping": len(cfg["mapping"])}
        elif a.command == "systems":
            out = service.systems_view(reader)
        elif a.command == "collect":
            cfg = config.load()
            s = config.system(cfg, a.system)
            if s is None or not service.visible(s, reader):
                raise service.Refused(404, "not_found", "no such system")
            out = {"system": a.system, "observations": service.collect(s, cfg)}
        elif a.command == "step-lost":
            out = service.mark_step_lost(a.system, a.event, actor={"id": f"agent:{a.agent}", "role": "service"},
                                         reason=a.reason, asked_by=a.asked_by, os_user=_os_user())
        elif a.command == "prepare":
            out = service.prepare(a.system, {"id": f"agent:{a.agent}", "role": "service"}, reader)
        else:
            b = service.published_bundle(a.system, reader)
            if b is None:
                raise service.Refused(404, "unpublished", "no report of this system has been published")
            if not a.out:
                raise service.Refused(400, "out", "--out <dir> is required")
            path = Path(a.out) / f"objective-evidence-{a.system}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(b, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            out = {"written": str(path), "content_digest": b["content_digest"], "publication": b["publication"]}
    except service.Refused as exc:
        print(json.dumps(exc.to_dict()))
        return 1
    except config.ConfigError as exc:
        print(json.dumps({"error": str(exc), "code": "config", "refused": True}))
        return 1
    print(json.dumps(out, indent=2 if not a.json else None, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
