# CUI // SP-CTI
"""Seed what the vulnerability discovery e2e needs (icx-ent-01).

* Two FRESH, card-named test people (synthetic), each with its own dashboard API key so the spec signs each into
  its own browser context: ``ent01-registrant`` and ``ent01-reviewer``, both role reviewer, which holds every
  step of the three declared templates. The registrant holds them on purpose: what stops them authorising their own
  target, reviewing their own patch or publishing their own bundle is separation of duties
  (``separate_from: builders``), not a missing role. Their emails carry the run id, so every run has new people.
* Nothing else: the journey registers the synthetic benchmark (``context/vulnresearch/benchmark``) on the page.
  The spec gets the patch it proposes from here (a unified diff that adds the bounds check the planted
  ``vb_parser`` lacks), so the run never depends on a model.

Everything goes through the product's own functions, never a hand-written INSERT. The server must be on a throwaway
database (globalSetup asserts it). Prints one JSON line: the run id, each person's id and raw key (a throwaway key
on a throwaway database; the spec only uses it to sign in), the benchmark and the patch.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

BENCH = "context/vulnresearch/benchmark"


def person(name: str, role: str, run: str) -> dict:
    from tools.dashboard.auth import create_api_key_for_user, create_user

    user = create_user(f"{name}+{run}@e2e.icdev.invalid", name, role=role, created_by="icx-ent-01-e2e-seed")
    key = create_api_key_for_user(user["id"], label=f"icx-ent-01 e2e {run}", created_by="icx-ent-01-e2e-seed")
    return {"id": user["id"], "name": name, "role": role, "key": key["raw_key"]}


def parser_patch() -> str:
    """The benchmark's committed reference fix for the planted parser crash (the one a waived run may apply)."""
    return (ROOT / "context" / "vulnresearch" / "benchmark_fixes" / "vb_parser.diff").read_text(encoding="utf-8")


def main() -> int:
    run = uuid.uuid4().hex[:8]
    print(json.dumps({"run": run, "registrant": person("ent01-registrant", "reviewer", run),
                      "reviewer": person("ent01-reviewer", "reviewer", run),
                      "benchmark": {"root": BENCH, "src_root": "src", "test_dir": "tests",
                                    "fuzz": "vb_parser:parse_record, vb_parser_safe:parse_record"},
                      "patch": parser_patch()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
