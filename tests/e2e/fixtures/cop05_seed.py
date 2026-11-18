# CUI // SP-CTI
"""Seed what the FOCUS cost bundle e2e needs (osx-cop-05).

* Two FRESH, card-named test people (synthetic), each with its own dashboard API key so the spec signs each into
  its own browser context: ``cop05i-preparer`` and ``cop05i-publisher``, both role reviewer, which holds the publish
  step. The preparer holds it on purpose: what stops them publishing their own bundle on the page is separation of
  duties (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new
  people.
* The synthetic FOCUS 1.4 export (``tests/fixtures/finops/focus_1_4_sample.csv``: "Example Cloud (synthetic)", one
  billing month, the four declared workloads and two untagged rows), copied into
  ``tests/e2e/fixtures/.cop05_focus/`` (git-ignored), the directory the e2e server reads exports from
  (``ICDEV_FINOPS_FOCUS_DIR``).

Everything goes through the product's own functions, never a hand-written INSERT. The server must be on a throwaway
database (globalSetup asserts it). Prints one JSON line: the run id, each person's id and raw key (a throwaway key
on a throwaway database; the spec only uses it to sign in), the export and the totals it should give.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "tests" / "fixtures" / "finops" / "focus_1_4_sample.csv"
EXPORTS = ROOT / "tests" / "e2e" / "fixtures" / ".cop05_focus"


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="osx-cop-05-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"osx-cop-05 e2e {run}", created_by="osx-cop-05-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def main() -> int:
    from tools.finops import focus_bundle

    run = uuid.uuid4().hex[:8]
    EXPORTS.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLE, EXPORTS / SAMPLE.name)
    cfg = focus_bundle.load_config()
    b = focus_bundle.build(focus_bundle.read_export(EXPORTS / SAMPLE.name, cfg), cfg=cfg)
    out = {
        "run": run,
        "preparer": person("cop05i-preparer", "reviewer", run),
        "publisher": person("cop05i-publisher", "reviewer", run),
        "export": {"dir": str(EXPORTS), "name": SAMPLE.name, "period": focus_bundle.period_of(b),
                   "rows": len(b["rows"]), "digest": b["content_digest"],
                   "workloads": {w["key"]: w for w in b["workloads"]}, "unattributed": b["unattributed"]},
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
