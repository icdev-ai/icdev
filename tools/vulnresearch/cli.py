#!/usr/bin/env python3
# CUI // SP-CTI
"""AI-assisted vulnerability discovery from the command line (icx-ent-01).

The CLI acts as an AGENT (``agent:<name>``): it may register a target, run discovery on an AUTHORISED target,
measure the benchmark, prepare a bundle and export a published one. It can take no person's step: authorising a
target, reviewing a patch and publishing a bundle happen on /vulnresearch/ (declared templates).

Usage:
    python tools/vulnresearch/cli.py status [--target vrt-...] [--json]
    python tools/vulnresearch/cli.py register --name N --root PATH --reason R [--src-root src] [--test-dir tests]
                                                [--fuzz module:function ...]
    python tools/vulnresearch/cli.py run --target vrt-... [--no-model]
    python tools/vulnresearch/cli.py measure --target vrt-... [--no-model]
    python tools/vulnresearch/cli.py prepare --target vrt-...
    python tools/vulnresearch/cli.py export --target vrt-... --published --out DIR   # bundle + publication
    python tools/vulnresearch/cli.py verify DIR [--ledger]

Exit codes: 0 done / verifies, 1 refused / does not verify, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from tools.vulnresearch import benchmark, bundle, discovery, ledger, targets  # noqa: E402


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--as", dest="agent", default="cli", help="the agent name recorded (agent:<name>)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--target")
    r = sub.add_parser("register")
    r.add_argument("--name", required=True)
    r.add_argument("--root", required=True)
    r.add_argument("--reason", required=True)
    r.add_argument("--src-root", default=".")
    r.add_argument("--test-dir", default="")
    r.add_argument("--fuzz", nargs="*", default=[])
    for name in ("run", "measure"):
        x = sub.add_parser(name)
        x.add_argument("--target", required=True)
        x.add_argument("--no-model", action="store_true")
    p = sub.add_parser("prepare")
    p.add_argument("--target", required=True)
    e = sub.add_parser("export")
    e.add_argument("--target", required=True)
    e.add_argument("--published", action="store_true", required=True)
    e.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("dir")
    v.add_argument("--ledger", action="store_true")
    a = ap.parse_args(argv)
    actor = {"id": f"agent:{a.agent}", "role": "service"}
    try:
        if a.cmd == "status":
            out = ({"target": targets.state(a.target), "runs": discovery.runs(a.target)[:1]} if a.target
                   else {"targets": targets.targets_view(), "measurement": benchmark.latest()})
        elif a.cmd == "register":
            out = targets.register(actor, name=a.name, root=a.root, reason=a.reason, src_root=a.src_root,
                                   test_dir=a.test_dir, fuzz_targets=a.fuzz)
        elif a.cmd == "run":
            out = discovery.run(a.target, actor, use_model=not a.no_model)
        elif a.cmd == "measure":
            out = benchmark.measure(a.target, actor, use_model=not a.no_model)
        elif a.cmd == "prepare":
            out = bundle.prepare(a.target, actor)
        elif a.cmd == "export":
            pub = bundle.published(a.target)
            if pub is None:
                print(json.dumps({"status": "refused", "error": f"no findings bundle of {a.target} is published"}))
                return 1
            d = Path(a.out)
            d.mkdir(parents=True, exist_ok=True)
            _write(d / bundle.BUNDLE_FILE, pub["bundle"])
            _write(d / bundle.PUBLICATION_FILE, pub["publication"])
            out = {"status": "written", "dir": str(d), "content_digest": pub["bundle"]["content_digest"],
                   "signed_by": pub["publication"]["signed_by"]}
        else:
            d = Path(a.dir)
            b = json.loads((d / bundle.BUNDLE_FILE).read_text(encoding="utf-8"))
            pf = d / bundle.PUBLICATION_FILE
            pubdoc = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else None
            out = bundle.verify(b, pubdoc, ledger.read() if a.ledger else None)
            print(json.dumps(out, indent=2, default=str))
            return 0 if out["ok"] else 1
    except ledger.LedgerRefused as exc:
        print(json.dumps(exc.to_dict()))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
