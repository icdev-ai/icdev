# CUI // SP-CTI
"""IQE adapter — evidence collectors for customer environments (icx-ent-02).

Importing this module registers one collection, computed from the ec_events ledger (no table of its own):
  evidence_collectors.objectives — one row per objective in each system's newest PUBLISHED objective-evidence report
                                   that the signed-in person may see (their org, their clearance): system,
                                   objective, result, observed_at. With no signed-in reader (outside a dashboard
                                   request) it returns nothing: it never falls back to a platform-wide reader. Observations, credentials and notes are never columns.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def objectives_adapter(conn: Any = None) -> list[dict]:
    from tools.evidence_collectors import config, ledger, service

    reader = service.current_reader()
    if reader is None:
        return []           # fail closed: no reader, no rows
    cfg = config.load()
    ch = ledger.chain(ledger.read())
    broken = {p["stream"] for p in ch["_problems"]}
    rows = []
    for s in cfg.get("systems") or []:
        stream = f"system:{s['id']}"
        if not service.visible(s, reader) or stream in broken:
            continue        # a chain that does not verify publishes nothing
        # A decision whose declared step was never recorded publishes nothing (replay_checked).
        pub = service.replay_checked(ch.get(stream, []))["published"]
        for e in ((pub or {}).get("report") or {}).get("entries") or []:
            rows.append({"system": s["id"], "objective": e["objective"], "result": e["result"],
                         "observed_at": e["observed_at"], "published_by": pub["actor"]})
    return rows


register_collection("evidence_collectors.objectives", objectives_adapter)
