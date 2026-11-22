# CUI // SP-CTI
"""Seed what the store-publisher e2e needs (icx-ent-04).

* Three FRESH, card-named test people (synthetic), each with its own dashboard API key: ``ent04-preparer`` (role
  reviewer) and ``ent04-publisher`` (role isso), both of the synthetic packages' organisation (tenant
  ``syn-ent04-org``), cleared CUI; and ``ent04-outsider`` (role isso, cleared CUI) of ANOTHER organisation. Each is
  given read access to the Store Publisher canvas (``canvas_access.grant_access``, as an administrator does). What
  stops the preparer publishing their own package is separation of duties, not a missing role. Emails carry the run id.
* The labelled synthetic inputs of the two synthetic packages (``tools/store_publisher/synthetic.py``): an agent's
  prompt, an MCP server's tool listing and source, each one's SBOM, dependency findings and red-team results.
* A THROWAWAY P-384 publisher key at tests/e2e/fixtures/.ent04_publisher_key.pem (git-ignored), which the e2e dashboard
  signs Agent Cards with through ICDEV_STORE_PUBLISHER_E2E_KEY (honoured only there, and only while the shipped
  configuration names no key). The agent package is prepared on the page by the journey; the MCP SERVER package is
  PREPARED here by ent04-preparer through the product's own ``service.prepare``, with that same key.

Everything goes through the product's own functions. The server must be on a throwaway database (globalSetup asserts
it). Prints one JSON line: the run id, each person's id and raw key (a throwaway key on a throwaway database), and each
prepared package's digest.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ORG, OTHER = "syn-ent04-org", "syn-ent04-elsewhere"
KEY = ROOT / "tests" / "e2e" / "fixtures" / ".ent04_publisher_key.pem"


def person(name: str, role: str, run: str, tenant: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user
    from tools.security.canvas_access import grant_access

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-ent-04-e2e-seed",
                       tenant_id=tenant, clearance_level="CUI")
    key = create_api_key_for_user(user["id"], label=f"icx-ent-04 e2e {run}", created_by="icx-ent-04-e2e-seed")
    grant_access(tenant, "user", user["id"], "store_publisher", "read", granted_by="icx-ent-04-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"], "org": tenant}


def throwaway_signer():
    """A THROWAWAY P-384 key, written where the e2e dashboard reads it (ICDEV_STORE_PUBLISHER_E2E_KEY, git-ignored), so
    the page's Prepare and sign uses the same key the seed's MCP package was signed with. Never a real key."""
    import os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from tools.store_publisher import config, service

    if not KEY.exists():
        KEY.write_bytes(ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(KEY, 0o600)
    os.environ[service.E2E_KEY_ENV] = str(KEY)
    return service.signer_for(config.load())


def main() -> int:
    from tools.store_publisher import service, synthetic

    run = uuid.uuid4().hex[:8]
    preparer = person("ent04-preparer", "reviewer", run, ORG)
    publisher = person("ent04-publisher", "isso", run, ORG)
    outsider = person("ent04-outsider", "isso", run, OTHER)
    synthetic.write_all()
    signer = throwaway_signer()
    who = {"id": preparer["id"], "role": preparer["role"]}
    rdr = {**who, "clearance": "CUI", "org": ORG}
    prepared = {}
    for pid in ("syn-ent04-coverage-mcp",):
        p = service.prepare(pid, who, rdr, version=f"1.0.{int(run[:4], 16)}", signer=signer,
                            note="Synthetic (icx-ent-04 e2e): prepared by the seed with a throwaway key")
        prepared[pid] = {"digest": p["digest"], "event_id": p["event_id"]}
    print(json.dumps({"run": run, "preparer": preparer, "publisher": publisher, "outsider": outsider,
                      "prepared": prepared}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
