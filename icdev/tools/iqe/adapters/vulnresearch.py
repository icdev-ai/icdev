# CUI // SP-CTI
"""IQE adapters — AI-assisted vulnerability discovery (icx-ent-01).

Importing this module registers two collections, computed from the vr_events ledger (no table of their own):
  vulnresearch.findings — one row per CONFIRMED finding in each target's newest run: target, function, weakness,
                          severity, sources, the PoV digest and whether a patch was accepted. Sealed PoV inputs
                          are never a column.
  vulnresearch.targets  — one row per registered target and its state.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def targets_adapter(conn: Any = None) -> list[dict]:  # noqa: ARG001 — computed from the ledger
    from tools.vulnresearch import targets

    return [{"target_id": t["target_id"], "name": t["name"], "status": t["status"],
             "registered_by": t["registered"]["actor"], "registered_at": t["registered"]["created_at"]}
            for t in targets.targets_view()["targets"]]


def findings_adapter(conn: Any = None) -> list[dict]:  # noqa: ARG001 — computed from the ledger
    from tools.vulnresearch import discovery, patches, targets

    rows = []
    for t in targets.targets_view()["targets"]:
        rs = discovery.runs(t["target_id"])
        if not rs:
            continue
        ps = patches.patches_for(t["target_id"])
        for f in rs[0]["run"]["findings"]:
            p = (ps.get(f["finding_id"]) or {}).get("proposed") or {}
            rows.append({"finding_id": f["finding_id"], "target_id": t["target_id"], "target": t["name"],
                         "function": f"{f['module']}:{f['function']}", "file": f["file"], "cwe": f["cwe"],
                         "severity": f.get("severity"), "sources": ",".join(f.get("sources") or []),
                         "pov_digest": f["pov"]["digest"],
                         "patched": (p.get("decided") or {}).get("event") == "patch_accepted"})
    return rows


register_collection("vulnresearch.findings", findings_adapter)
register_collection("vulnresearch.targets", targets_adapter)
