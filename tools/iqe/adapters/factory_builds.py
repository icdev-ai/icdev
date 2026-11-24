# CUI // SP-CTI
"""IQE adapter — signed factory builds (icx-ent-03).

Importing this module registers one collection, computed from the fb_events ledger (no table of its own):
  factory_builds.builds — one row per target's newest PUBLISHED signed build that the signed-in person may see (their
                          org, their clearance), its declared step recorded: target, kind, system, digest, version,
                          signing mode, transparency log, published_by. With no signed-in reader it returns nothing:
                          it never falls back to a platform-wide reader.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def builds_adapter(conn: Any = None) -> list[dict]:
    from tools.factory_builds import config, ledger, service

    reader = service.current_reader()
    if reader is None:
        return []           # fail closed: no reader, no rows
    cfg = config.load()
    ch = ledger.chain(ledger.read())
    broken = {p["stream"] for p in ch["_problems"]}
    rows = []
    for t in cfg.get("targets") or []:
        stream = f"target:{t['id']}"
        if not service.visible(t, reader) or stream in broken:
            continue        # a chain that does not verify publishes nothing
        pub = service.replay(ch.get(stream, []))["published"]
        if pub is None or not service.step_ok(pub):
            continue
        rec = pub.get("record") or {}
        rows.append({"target": t["id"], "kind": t["kind"], "system": t["system_id"], "digest": pub["content_digest"],
                     "version": rec.get("version"), "signing": (rec.get("signing") or {}).get("mode"),
                     "transparency_log": (rec.get("transparency_log") or {}).get("mode"),
                     "published_by": pub["actor"]})
    return rows


register_collection("factory_builds.builds", builds_adapter)
