# CUI // SP-CTI
"""Seed what the signed-factory-builds e2e needs (icx-ent-03).

* Three FRESH, card-named test people (synthetic), each with its own dashboard API key: ``ent03-preparer`` (role
  reviewer) and ``ent03-publisher`` (role isso), both of the synthetic targets' organisation (tenant
  ``syn-ent03-org``), cleared CUI; and ``ent03-outsider`` (role isso, cleared CUI) of ANOTHER organisation. Each is
  given read access to the Signed Factory Builds canvas (``canvas_access.grant_access``, as an administrator does).
  What stops the preparer publishing their own build is separation of duties, not a missing role. Emails carry the
  run id.
* The labelled synthetic inputs of the two synthetic targets (``tools/factory_builds/synthetic.py``): an OCI image
  layout, a project, a factory scan, a model's weights files, and the fine-tuning registry rows of a promoted
  synthetic model version (a run-named promoter).
* A THROWAWAY P-256 factory key at tests/e2e/fixtures/.ent03_factory_key.pem (git-ignored), which the e2e dashboard
  signs with through ICDEV_FACTORY_BUILDS_E2E_KEY (honoured only there, and only while the shipped configuration names
  no key). The image target is built on the page by the journey; the MODEL target's build is PREPARED here by
  ent03-preparer through the product's own ``service.prepare``, with that same key.

Everything goes through the product's own functions; the registry rows are synthetic and labelled. The server must
be on a throwaway database (globalSetup asserts it). Prints one JSON line: the run id, each person's id and raw key (a
throwaway key on a throwaway database), and each prepared build's digest.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

ORG, OTHER = "syn-ent03-org", "syn-ent03-elsewhere"


def person(name: str, role: str, run: str, tenant: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user
    from tools.security.canvas_access import grant_access

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-ent-03-e2e-seed",
                       tenant_id=tenant, clearance_level="CUI")
    key = create_api_key_for_user(user["id"], label=f"icx-ent-03 e2e {run}", created_by="icx-ent-03-e2e-seed")
    grant_access(tenant, "user", user["id"], "factory_builds", "read", granted_by="icx-ent-03-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"], "org": tenant}


KEY = ROOT / "tests" / "e2e" / "fixtures" / ".ent03_factory_key.pem"


def throwaway_signer():
    """A THROWAWAY P-256 key, written where the e2e dashboard reads it (ICDEV_FACTORY_BUILDS_E2E_KEY, git-ignored), so
    the page's Build and sign uses the same key the seed's model build was signed with. Never a real key."""
    import os

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from tools.factory_builds import config, service

    if not KEY.exists():
        KEY.write_bytes(ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(KEY, 0o600)
    os.environ[service.E2E_KEY_ENV] = str(KEY)
    s = service.signer_for(config.load())
    return s


def main() -> int:
    from tools.db.storage import get_canvas_connection
    from tools.factory_builds import config, service, synthetic

    run = uuid.uuid4().hex[:8]
    preparer = person("ent03-preparer", "reviewer", run, ORG)
    publisher = person("ent03-publisher", "isso", run, ORG)
    outsider = person("ent03-outsider", "isso", run, OTHER)
    cfg = config.load()
    conn = get_canvas_connection()
    try:
        synthetic.image_inputs(config.root(cfg) / "syn-ent03" / "portal", run)
        # a fresh promoted version per run (the registry may be shared across runs)
        version = int(run[:6], 16) + 100
        m = synthetic.model_inputs(config.root(cfg, "models_root"), config.root(cfg) / "syn-ent03" / "classifier",
                                   version=version)
        synthetic.registry_rows(conn, adapter_path=m["adapter_path"], version=version,
                                promoter=f"syn-ent03-ml-lead-{run}")
    finally:
        conn.close()
    signer = throwaway_signer()
    who = {"id": preparer["id"], "role": preparer["role"]}
    rdr = {**who, "clearance": "CUI", "org": ORG}
    builds = {}
    for tid in ("syn-ent03-classifier",):
        p = service.prepare(tid, who, rdr, version=f"1.0.{int(run[:4], 16)}", signer=signer,
                            note="Synthetic (icx-ent-03 e2e): built by the seed with a throwaway key")
        builds[tid] = {"digest": p["digest"], "event_id": p["event_id"]}
    print(json.dumps({"run": run, "preparer": preparer, "publisher": publisher, "outsider": outsider,
                      "builds": builds}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
