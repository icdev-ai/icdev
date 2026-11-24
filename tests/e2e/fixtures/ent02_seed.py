# CUI // SP-CTI
"""Seed what the evidence-collectors e2e needs (icx-ent-02).

* Three FRESH, card-named test people (synthetic), each with its own dashboard API key: ``ent02-preparer`` (role
  reviewer) and ``ent02-publisher`` (role isso), both of the synthetic system's organisation (tenant ``syn-dib``),
  cleared CUI; and ``ent02-outsider`` (role isso, cleared CUI) of ANOTHER organisation. Each is given read access to
  the Evidence Collectors canvas (``canvas_access.grant_access``, as an administrator does). The preparer holds no
  publish role problem: what stops them publishing their own report is separation of duties. Emails carry the run id.
* One report of the declared synthetic system ``syn-dib-01`` (args/evidence_collectors.yaml), PREPARED by
  ent02-preparer: the REAL collectors read LOCAL STUB sources on loopback (the unit tests' stub, synthetic), dated
  from now, where one device is unencrypted (an objective observed other than satisfied) and the SIEM review search
  is absent (an objective left unverified). The system's configured endpoints are documentation placeholders with no
  credential in any keyring, so collecting from the page is refused as nothing determinable: the journey shows it.

Everything goes through the product's own functions. The server must be on a throwaway database (globalSetup asserts
it). Prints one JSON line: the run id, each person's id and raw key (a throwaway key on a throwaway database), and the
prepared report's digest.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ORG, OTHER = "syn-dib", "syn-elsewhere"


def person(name: str, role: str, run: str, tenant: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user
    from tools.security.canvas_access import grant_access

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-ent-02-e2e-seed",
                       tenant_id=tenant, clearance_level="CUI")
    key = create_api_key_for_user(user["id"], label=f"icx-ent-02 e2e {run}", created_by="icx-ent-02-e2e-seed")
    grant_access(tenant, "user", user["id"], "evidence_collectors", "read", granted_by="icx-ent-02-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"], "org": tenant}


def prepared(preparer: dict) -> dict:
    """Collect from the stub sources with the real collectors, and prepare syn-dib-01's report as the preparer."""
    from tests.test_icx_ent_02_evidence_collectors import Stub, fake_resolve, stub_config
    from tools.evidence_collectors import config, service

    now = datetime.now(timezone.utc).replace(microsecond=0)
    stub = Stub()
    try:
        st = stub.state
        st["aws"] = {k: (v[0], now - timedelta(hours=2)) for k, v in st["aws"].items()}
        for d in st["devices"]:
            d["lastSyncDateTime"] = (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        st["devices"][1]["isEncrypted"] = False
        del st["searches"]["Audit review - daily"]
        scfg = stub_config(stub.base)
        obs = service.collect(config.system(scfg, "syn-dib-01"), scfg, now=now, resolve=fake_resolve)
    finally:
        stub.close()
    who = {"id": preparer["id"], "role": preparer["role"]}
    rdr = {**who, "clearance": "CUI", "org": preparer["org"]}
    return service.prepare("syn-dib-01", who, rdr, observations=obs, now=now,
                           note="Synthetic (icx-ent-02 e2e): collected from local stub sources")


def main() -> int:
    run = uuid.uuid4().hex[:8]
    preparer = person("ent02-preparer", "reviewer", run, ORG)
    publisher = person("ent02-publisher", "isso", run, ORG)
    outsider = person("ent02-outsider", "isso", run, OTHER)
    p = prepared(preparer)
    print(json.dumps({"run": run, "preparer": preparer, "publisher": publisher, "outsider": outsider,
                      "prepared": {"content_digest": p["content_digest"], "event_id": p["event_id"],
                                   "entries": p["entries"], "unverified": p["unverified"]}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
