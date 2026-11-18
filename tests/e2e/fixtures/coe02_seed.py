# CUI // SP-CTI
"""Seed what the exemptions-and-ADRs e2e needs (icx-coe-02).

* Three FRESH, card-named test people (synthetic), each with its own dashboard API key so the
  spec signs each into its own browser context: ``coe02-requester``, ``coe02-approver`` and
  ``coe02-architect``, all role reviewer, which holds the grant, renew, accept and supersede
  steps. The requester and the architect hold the role on purpose: what stops each deciding their
  own request or ADR on the page is separation of duties (``separate_from: builders``), not a
  missing role. Their emails carry the run id, so every run has new people.
* The standards register's seeds and the ADR records, recorded by their labelled bootstrap
  identities (``standards.seed``, ``adrs.seed``, both idempotent).
* ONE ALREADY-EXPIRED exemption, so the page and the scorecard have a real lapse to show: the
  requester asked for it and the approver granted it through the product's own functions, under
  an INJECTED CLOCK 40 days in the past, for 30 days. Nothing in the product is bypassed: the
  declared step is taken, the digest is bound, the requester is not the approver; only the clock
  the functions are handed is earlier. It is labelled ``CUI``, named explicitly: the level a
  console session writes and reads at (``exemptions.label_for``), as the requester's own request
  on the page would be. Written here with no session it would take the module's default label,
  which the console's row-security predicate does not show a CUI session.

Everything goes through the product's own functions, never a hand-written INSERT. The server must
be on a throwaway database (globalSetup asserts it). Prints one JSON line: the run id, each
person's id and raw key (a throwaway key on a throwaway database; the spec only uses it to sign
in), and the expired exemption.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role,
                       created_by="icx-coe-02-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"icx-coe-02 e2e {run}",
                                  created_by="icx-coe-02-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def expired_exemption(requester: dict, approver: dict, run: str) -> dict:
    """Requested and granted 40 days ago (an injected clock), for 30 days: expired 10 days ago."""
    from tools.idp import exemptions as e

    then = datetime.now(timezone.utc) - timedelta(days=40)
    clock = lambda: then  # noqa: E731 — the clock the functions are handed
    expires = (then.date() + timedelta(days=30)).isoformat()
    row = e.request_exemption("component-readiness", "e2e-spec", f"coe02-lapsed-{run}",
                              reason="Synthetic (icx-coe-02 e2e): granted for 30 days, now lapsed.",
                              requested_by=requester["id"], expires=expires, classification="CUI",
                              clock=clock)
    current = e._latest(e._open_connection(), row["exemption_key"])
    e.decide_exemption(row["exemption_key"], decision="grant",
                       reason="Synthetic (icx-coe-02 e2e): granted in the past.",
                       read_digest=e.exemption_digest(current),
                       actor={"id": approver["id"], "role": approver["role"]}, clock=clock)
    return {"exemption_key": row["exemption_key"], "ref": row["ref"], "expires_on": expires}


def main() -> int:
    from tools.idp import adrs, standards

    run = uuid.uuid4().hex[:8]
    requester = person("coe02-requester", "reviewer", run)
    approver = person("coe02-approver", "reviewer", run)
    out = {
        "run": run,
        "requester": requester,
        "approver": approver,
        "architect": person("coe02-architect", "reviewer", run),
        "standards": standards.seed(),
        "adrs": adrs.seed(),
        "expired": expired_exemption(requester, approver, run),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
