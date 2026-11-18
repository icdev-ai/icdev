# CUI // SP-CTI
"""Seed what the CoP learning paths e2e needs (icx-cop-01).

* Three FRESH, card-named test people (synthetic), each with its own dashboard API key so the spec signs each into
  its own browser context: ``cop01a-learner`` (developer: takes missions), ``cop01a-preparer`` and
  ``cop01a-publisher`` (both reviewer, which holds the publish step). The preparer holds it on purpose: what stops
  them publishing the batch they prepared is separation of duties (``separate_from: builders``), not a missing role.
  Their emails carry the run id, so every run has new people.
* The answers the learner gives, read from the declared config (``args/academy_cop_tracks.yaml``) the way a learner
  who studied the readings would answer: right for every mission of the cloud path and for one engineering mission,
  and one deliberately wrong answer, so the journey sees a failed attempt recorded and counting for nothing.

Everything goes through the product's own functions, never a hand-written INSERT. The server must be on a throwaway
database (globalSetup asserts it). Prints one JSON line: the run id, each person's id and raw key (a throwaway key on
a throwaway database; the spec only uses it to sign in) and the answers.
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

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-cop-01-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"icx-cop-01 e2e {run}", created_by="icx-cop-01-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def answers() -> dict:
    from tools.idp import cop_tracks

    cfg = cop_tracks.load_config()
    out = {}
    for t in cfg["tracks"]:
        for m in t["missions"]:
            out[m["id"]] = {q["id"]: q["answer"] for q in m["questions"]}
    return {
        "cloud_path": "cop-cloud-foundations",
        "cloud": [m["id"] for t in cfg["tracks"] if t["id"] == "cop-cloud-foundations" for m in t["missions"]],
        "engineering_path": "cop-engineering-foundations",
        "engineering": "eng-red-first",
        "wrong": {"mission": "eng-red-first", "question": "q1",
                  "option": (out["eng-red-first"]["q1"] + 1) % 3},
        "key": {k: v for k, v in out.items() if k == "eng-red-first" or k.startswith("cloud-")},
        "path_prefix": cfg["xapi"]["path_prefix"],
    }


def main() -> int:
    run = uuid.uuid4().hex[:8]
    out = {
        "run": run,
        "learner": person("cop01a-learner", "developer", run),
        "preparer": person("cop01a-preparer", "reviewer", run),
        "publisher": person("cop01a-publisher", "reviewer", run),
        "answers": answers(),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
