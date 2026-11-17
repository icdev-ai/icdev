# CUI // SP-CTI
"""Seed what the engineering standards e2e needs (icx-coe-01).

* Two FRESH, card-named test people (synthetic), each with its own dashboard API key so the
  spec signs each into its own browser context: ``coe01-author`` and ``coe01-steward``, both
  role reviewer, which holds the review, adopt and deprecate steps. The author holds the role on
  purpose: what stops the author signing their own standard on the page is separation of duties
  (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new people and a re-run
  never reuses one.
* The register's declared seeds, recorded by the labelled bootstrap identity
  (``standards.seed``, idempotent), so the page has standards on it.

Everything goes through the product's own functions (``auth.create_user``,
``auth.create_api_key_for_user``, ``standards.seed``), never a hand-written INSERT. The server
must be on a throwaway database (globalSetup asserts it). Prints one JSON line: the run id,
each person's id and raw key (a throwaway key on a throwaway database; the spec only uses it
to sign in), and what was seeded.
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

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role,
                       created_by="icx-coe-01-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"icx-coe-01 e2e {run}",
                                  created_by="icx-coe-01-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def main() -> int:
    from tools.idp import standards

    run = uuid.uuid4().hex[:8]
    out = {
        "run": run,
        "author": person("coe01-author", "reviewer", run),
        "steward": person("coe01-steward", "reviewer", run),
        "seeded": standards.seed(),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
