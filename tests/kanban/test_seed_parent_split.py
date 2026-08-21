# CUI // SP-CTI
"""Shape tests for tools/kanban/seed_parent_split.py (xit-reg-01).

These run against the seed SPECS, never the board: a seeder whose shape is
wrong should be caught before a row exists.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tools.kanban import seed_parent_split as seed
from tools.kanban.gates import is_manual_gate

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _cards() -> dict[str, dict]:
    data = yaml.safe_load((_REPO_ROOT / "args" / "projects.yaml").read_text(encoding="utf-8"))
    return {c["key"]: c for c in data["projects"]}


def test_three_streams_each_behind_their_own_gate():
    specs = seed.all_specs()
    gates = sorted(t["id"] for t in specs if t["id"].endswith("gate-00"))
    assert gates == ["xcore-gate-00", "xft-gate-00", "xit-gate-00"]
    for g in (t for t in specs if t["id"] in gates):
        assert g["status"] == "in_progress"
        assert "RISK:" in g["description"]
        assert is_manual_gate(g["id"], g["title"])


def test_every_work_task_depends_on_its_streams_gate():
    for t in seed.all_specs():
        if t["id"].endswith("gate-00"):
            assert "depends_on_task_id" not in t
            continue
        prefix = t["id"].split("-", 1)[0] + "-"
        assert t.get("depends_on_task_id") == f"{prefix}gate-00", t["id"]


def test_no_work_task_wears_a_gate_shaped_id():
    for t in seed.all_specs():
        if not t["id"].endswith("gate-00"):
            assert not is_manual_gate(t["id"], t["title"]), t["id"]


def test_every_id_is_claimed_by_an_epic_of_its_card():
    cards = _cards()
    for t in seed.all_specs():
        card_key, epic_key, _n = t["id"].split("-", 2)
        card = cards[card_key]
        assert t["id"].startswith(card["task_prefix"]), t["id"]
        epic_keys = {e["key"] for e in card["epics"]}
        assert epic_key in epic_keys, f"{t['id']} has no epic {epic_key!r} on card {card_key}"


def test_ids_are_unique_and_descriptions_stand_alone():
    specs = seed.all_specs()
    ids = [t["id"] for t in specs]
    assert len(ids) == len(set(ids))
    for t in specs:
        assert len(t["description"]) > 200, t["id"]  # rich enough for a cold session
        assert t["title"]


def test_external_streams_are_registered_and_parked(monkeypatch):
    from tools.kanban import repo_registry as rr

    monkeypatch.delenv("ICDEV_KANBAN_REPO_CORE", raising=False)
    monkeypatch.delenv("ICDEV_KANBAN_REPO_FT", raising=False)
    for t in seed.all_specs():
        r = rr.resolve_task_repo(t["id"])
        if t["id"].startswith("xit-"):
            assert r.is_external is False, t["id"]
        else:
            assert r.is_external is True and r.dispatchable is False, t["id"]
