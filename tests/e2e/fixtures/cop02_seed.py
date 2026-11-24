# CUI // SP-CTI
"""Seed what the AI-BOM export e2e needs (osx-cop-02).

Two FRESH, card-named test people (synthetic): ``cop02i-prep`` and ``cop02i-pub``, both role reviewer, which holds the
publish step, each with its own dashboard API key so the spec signs each into its own browser context. The preparer
holds the role on purpose: what stops them publishing their own AI-BOM is separation of duties
(``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new people.

The AI components are the SYNTHETIC fixtures under tests/e2e/fixtures/aibom/, which the server reads through
``ICDEV_AIBOM_SOURCES_E2E=tests/e2e/fixtures/aibom_sources.yaml``. The AI-BOM is prepared and published by the spec
through the page, never here. The server must be on a throwaway database (globalSetup asserts it). Prints one JSON
line: the run id and each person's id and raw key (a throwaway key on a throwaway database).
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

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="osx-cop-02-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"osx-cop-02 e2e {run}", created_by="osx-cop-02-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def main() -> int:
    run = uuid.uuid4().hex[:8]
    print(json.dumps({"run": run, "preparer": person("cop02i-prep", "reviewer", run),
                      "publisher": person("cop02i-pub", "reviewer", run)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
