# CUI // SP-CTI
"""Seed what the golden paths e2e needs (icx-coe-04). Everything through the product's own functions.

* Three FRESH, card-named test people (synthetic), each with its own dashboard API key so the spec signs each
  into its own browser context: ``coe04-author`` (proposes a path version), ``coe04-publisher`` (publishes it)
  and ``coe04-steward`` (adopts the new standard and cleans up). All role reviewer, which holds the publish
  step: what stops the author publishing their own version on the page is separation of duties
  (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new people.
* The standards register's seeds and the golden paths' seeds (``standards.seed``, ``golden_paths.seed``: the
  labelled bootstrap identities, idempotent).

``--adopt <run> --author <id> --by <id>``: the mid-journey seed that makes a path BEHIND. A NEW engineering
standard that applies to every service (``ENG-COE04-<RUN>``, checked by a manual evidence step) is proposed by
the author and reviewed and adopted by the steward, through ``tools/idp/standards.py`` (its declared steps).

``--cleanup <run> --by <id>``: the spec's clean-up. The steward deprecates that standard (a declared step), and
refuses any golden path version a failed journey left proposed, so the next run (and the specs after this one)
start from the seeds' state.

The server must be on a throwaway database (globalSetup asserts it). Prints one JSON line.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-coe-04-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"icx-coe-04 e2e {run}", created_by="icx-coe-04-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def std_id(run: str) -> str:
    return f"ENG-COE04-{run.upper()}"


def adopt(run: str, author: str, steward: str) -> int:
    from tools.idp import standards

    sid = std_id(run)
    entry = {"id": sid, "version": "1.0", "owner": "developer",
             "title": f"Every service carries a run book (synthetic, icx-coe-04 e2e {run})",
             "scope": "Every ICDEV[IT] service (synthetic, for the golden paths e2e).",
             "sources": [{"path": "args/golden_paths.yaml"}],
             "check": {"manual_evidence": {"name": "A person reads the service's run book",
                                           "procedure": "Open the service's run book and confirm it names an owner "
                                                        "and the first three things to check (synthetic)."}}}
    standards.propose(entry, {"id": author, "role": "reviewer"}, note="icx-coe-04 e2e (synthetic)")
    d = standards.standard_view(sid)["entry_digest"]
    who = {"id": steward, "role": "reviewer"}
    standards.review(sid, "1.0", decision="endorse", reason="Synthetic (icx-coe-04 e2e).", read_digest=d, actor=who)
    standards.adopt(sid, "1.0", decision="sign", reason="Synthetic (icx-coe-04 e2e).", read_digest=d, actor=who)
    print(json.dumps({"adopted": sid, "version": "1.0"}))
    return 0


def cleanup(run: str, steward: str) -> int:
    from tools.idp import golden_paths, standards

    who = {"id": steward, "role": "reviewer"}
    out = {"deprecated": None, "refused": []}
    sid = std_id(run)
    try:
        v = standards.standard_view(sid)
        if v.get("in_force"):
            standards.deprecate(sid, v["in_force"], reason="Synthetic (icx-coe-04 e2e): clean-up.",
                                read_digest=v["entry_digest"], actor=who)
            out["deprecated"] = sid
    except standards.StandardRefused:
        pass
    for pid in ("service", "model-service"):
        for ver in golden_paths.path_view(pid)["versions"]:
            if ver["state"] == "proposed" and steward not in ver["builders"]:
                golden_paths.publish(pid, ver["version"], decision="refuse", read_digest=ver["entry_digest"],
                                     reason="Synthetic (icx-coe-04 e2e): left open by a journey.", actor=who)
                out["refused"].append(f"{pid}@{ver['version']}")
    print(json.dumps(out))
    return 0


def main() -> int:
    from tools.idp import golden_paths, standards

    a = sys.argv[1:]
    if len(a) == 6 and a[0] == "--adopt" and a[2] == "--author" and a[4] == "--by":
        return adopt(a[1], a[3], a[5])
    if len(a) == 4 and a[0] == "--cleanup" and a[2] == "--by":
        return cleanup(a[1], a[3])
    run = uuid.uuid4().hex[:8]
    out = {
        "run": run,
        "author": person("coe04-author", "reviewer", run),
        "publisher": person("coe04-publisher", "reviewer", run),
        "steward": person("coe04-steward", "reviewer", run),
        "standards": standards.seed(),
        "golden_paths": golden_paths.seed(),
        "standard": std_id(run),
        # A version no earlier run used (versions are never reused in the append-only ledger).
        "version": f"1.{int(run[:6], 16)}",
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
