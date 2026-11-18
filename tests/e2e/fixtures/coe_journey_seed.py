# CUI // SP-CTI
"""Seed what the CoE journey e2e needs (osx-coe-02: the Engineering CoE block, end to end through the console).

* Four FRESH, card-named test people (synthetic), each with its own dashboard API key so the spec signs each into its
  own browser context: ``coe02j-author`` (proposes the standard and the golden path version, prepares the bundles),
  ``coe02j-steward`` (reviews and adopts the standard, publishes the path version), ``coe02j-requester`` (asks for the
  exemption) and ``coe02j-approver`` (grants it, publishes the bundles). All role reviewer, which holds every step: what
  stops a person deciding their own proposal, request or bundle on the page is separation of duties
  (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new people.
* The standards register's and the golden paths' seeds (``standards.seed``, ``golden_paths.seed``: the labelled
  bootstrap identities, idempotent).

``--lapse <run> --requester <id> --approver <id>``: the mid-journey seed for what a browser cannot do, which is to let
time pass. Once the journey's standard is adopted, the requester asks to waive it for a run-named component and the
approver grants it, through the product's own functions under an INJECTED CLOCK 40 days in the past, for 30 days, so it
has lapsed today. Labelled ``CUI``, the level a console session reads at (as icx-coe-02's seed does).

``--cleanup <run> --by <id>``: the spec's clean-up, by the steward. Both exemptions of the run are revoked (a stop), the
standard is deprecated (a declared step), and any golden path version a failed journey left proposed is refused, so the
specs that run after this one start from the seeds' state.

Everything goes through the product's own functions, never a hand-written INSERT. The server must be on a throwaway
database (globalSetup asserts it). Prints one JSON line.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SCORECARD, RULE = "component-readiness", "iqe-adapter"


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="osx-coe-02-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"osx-coe-02 e2e {run}", created_by="osx-coe-02-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def std_id(run: str) -> str:
    return f"ENG-COE02J-{run.upper()}"


def lapse(run: str, requester: str, approver: str) -> int:
    from tools.idp import exemptions as e

    asked = datetime.now(timezone.utc) - timedelta(days=41)
    granted = asked + timedelta(days=1)
    component = f"coe02j-lapsed-{run}"
    row = e.request_exemption(SCORECARD, RULE, component, scope="standard", scope_ref=std_id(run),
                              reason="Synthetic (osx-coe-02 e2e): granted for 30 days, now lapsed.",
                              requested_by=requester, expires=(asked.date() + timedelta(days=30)).isoformat(),
                              classification="CUI", clock=lambda: asked)
    current = e._latest(e._open_connection(), row["exemption_key"])
    e.decide_exemption(row["exemption_key"], decision="grant", reason="Synthetic (osx-coe-02 e2e): granted then.",
                       read_digest=e.exemption_digest(current), actor={"id": approver, "role": "reviewer"},
                       clock=lambda: granted)
    print(json.dumps({"exemption_key": row["exemption_key"], "component": component,
                      "expires_on": (asked.date() + timedelta(days=30)).isoformat()}))
    return 0


def cleanup(run: str, steward: str) -> int:
    from tools.idp import exemptions as e
    from tools.idp import golden_paths, standards

    who = {"id": steward, "role": "reviewer"}
    out: dict = {"revoked": [], "deprecated": None, "refused": []}
    conn = e._open_connection()
    try:
        for comp in (f"coe02j-{run}", f"coe02j-lapsed-{run}"):
            key = e.exemption_key(SCORECARD, RULE, comp)
            state = e._latest(conn, key)
            if state and state.get("status") in (e.STATUS_APPROVED, e.STATUS_PENDING):
                e.revoke_by_key(key, revoked_by=who, reason="Synthetic (osx-coe-02 e2e): clean-up.")
                out["revoked"].append(key)
    finally:
        conn.close()
    sid = std_id(run)
    try:
        v = standards.standard_view(sid)
        if v.get("in_force"):
            standards.deprecate(sid, v["in_force"], reason="Synthetic (osx-coe-02 e2e): clean-up.",
                                read_digest=v["entry_digest"], actor=who)
            out["deprecated"] = sid
    except standards.StandardRefused:
        pass
    for pid in ("service", "model-service"):
        for ver in golden_paths.path_view(pid)["versions"]:
            if ver["state"] == "proposed" and steward not in ver["builders"]:
                golden_paths.publish(pid, ver["version"], decision="refuse", read_digest=ver["entry_digest"],
                                     reason="Synthetic (osx-coe-02 e2e): left open by a journey.", actor=who)
                out["refused"].append(f"{pid}@{ver['version']}")
    print(json.dumps(out))
    return 0


def main() -> int:
    from tools.idp import golden_paths, standards

    a = sys.argv[1:]
    if len(a) == 6 and a[0] == "--lapse" and a[2] == "--requester" and a[4] == "--approver":
        return lapse(a[1], a[3], a[5])
    if len(a) == 4 and a[0] == "--cleanup" and a[2] == "--by":
        return cleanup(a[1], a[3])
    run = uuid.uuid4().hex[:8]
    out = {
        "run": run,
        "author": person("coe02j-author", "reviewer", run),
        "steward": person("coe02j-steward", "reviewer", run),
        "requester": person("coe02j-requester", "reviewer", run),
        "approver": person("coe02j-approver", "reviewer", run),
        "standards": standards.seed(),
        "golden_paths": golden_paths.seed(),
        "standard": std_id(run),
        "scorecard": SCORECARD,
        "rule": RULE,
        # a component no earlier run used (exemptions are keyed by scorecard, rule and component)
        "component": f"coe02j-{run}",
        # a path version no earlier run used (versions are never reused in the append-only ledger)
        "version": f"1.{int(run[:6], 16)}",
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
