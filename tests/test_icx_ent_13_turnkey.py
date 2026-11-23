# CUI // SP-CTI
"""icx-ent-13 (osx-ent-13, turnkey packaging): the Academy tracks for the ISSO, ISSM, assessor and agent-store reviewer,
and the retirement of ICDEV's deprecated duplicates.

* each track is a catalogued tier-2 mission for its role, with three lessons, each graded by a valid item bank; the
  lessons date their facts, carry no AWS training or certification content, and never call a system compliant,
  certified or authorised;
* a deprecated duplicate is RETIRED only when its replacement exists and passes: the retired files are gone (and their
  packaged copies), nothing imports them, the replacement's test is CI-listed -- and this file runs the replacement
  itself; every other deprecated generator the card names is KEPT with what still calls it, still marked DEPRECATED
  (deleting a tool with no replacement is the owner's decision, args/retired_tools.yaml).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.constants import ROLES
from apps.forge_academy.content_loader import BUILTIN_MISSIONS, CONTENT_ROOT, discover_steps, load_item_banks
from tests.test_icx_ent_04_store_publisher import NOW, people, signer, sp  # noqa: F401 -- the replacement's fixtures

ROOT = Path(__file__).resolve().parents[1]
TRACKS = {
    "m-isso-07-turnkey-first-sprs": "isso",
    "m-issm-07-turnkey-review": "issm",
    "m-assessor-01-objective-evidence": "assessor",
    "m-store-reviewer-01-certify": "store_reviewer",
}
#: The top-level generators commit 7b8223a4d marked DEPRECATED that the card names.
CARD_NAMES = {"cmmc_assessor", "fedramp_assessor", "ssp_generator", "poam_generator", "stig_checker", "emass_sync",
              "cve_triager", "publish_pipeline", "review_queue", "simulation_engine"}
REGISTER = yaml.safe_load((ROOT / "args" / "retired_tools.yaml").read_text(encoding="utf-8"))


# ── the Academy tracks ───────────────────────────────────────────────────────


def test_each_track_is_a_catalogued_mission_for_its_role():
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    for slug, role in TRACKS.items():
        m = by_slug.get(slug)
        assert m is not None, f"{slug} is not catalogued"
        assert m["tier"] == 2 and m["role_filter"] == role and m["mission_type"] == "guided", slug
        assert role in ROLES and ROLES[role]["type"] == "guided", f"no {role} role to choose"
        assert all(p in by_slug for p in m["prereqs"]), f"{slug}: a prerequisite is not catalogued"


def test_each_track_has_three_lessons_each_graded_by_a_valid_item_bank():
    steps, banks = discover_steps(), load_item_banks()
    for slug in TRACKS:
        got = steps.get(slug) or []
        assert [s["step_num"] for s in got] == [1, 2, 3], slug
        assert all(s["step_type"] == "watch" for s in got), "lessons, not coding labs"
        bank = banks.get(slug) or {}
        assert sorted(bank) == [1, 2, 3], f"{slug}: every lesson is graded"
        for n, items in bank.items():
            assert validate_item_bank(items) == [], (slug, n, validate_item_bank(items))


def _lessons():
    for slug in TRACKS:
        for f in sorted((CONTENT_ROOT / "tier2" / slug / "steps").glob("*.md")):
            yield f, f.read_text(encoding="utf-8")
    for slug in TRACKS:
        yield (CONTENT_ROOT / "item_banks" / f"{slug}.yaml",
               (CONTENT_ROOT / "item_banks" / f"{slug}.yaml").read_text(encoding="utf-8"))


CLAIM = re.compile(r"\b(compliant|certified|authori[sz]ed)\b", re.IGNORECASE)
#: A store reviewer certifies PACKAGES (ICDEV[OS]'s store-review step): that track may say so; no track calls a SYSTEM so.
PACKAGE_CLAIM = re.compile(r"\b(compliant|authori[sz]ed)\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(never|not|no|nothing|nobody|neither|nor)\b|n't\b", re.IGNORECASE)


def test_the_lessons_date_their_facts_name_no_aws_training_and_claim_nothing():
    for f, text in _lessons():
        if f.suffix == ".md":
            assert "as of November 2026" in text, f"{f.name} dates its facts"
        assert not re.search(r"\bAWS\b|Amazon Web Services", text), f"{f.name}: no AWS training or certification content"
        claim = PACKAGE_CLAIM if "store-reviewer" in str(f) else CLAIM
        for sentence in re.split(r"(?<=[.;:?])\s+|\n", text):
            if claim.search(sentence):
                assert NEGATION.search(sentence), f"{f.name}: {sentence.strip()[:160]!r} claims what ICDEV[OS] never says"


# ── the retirement of the deprecated duplicates ──────────────────────────────


def test_every_deprecated_generator_the_card_names_is_accounted_for():
    tools = {t["id"]: t for t in REGISTER["tools"]}
    assert set(tools) == CARD_NAMES and REGISTER["deprecated_in"] == "7b8223a4d"
    for t in tools.values():
        assert t["status"] in ("retired", "kept"), t["id"]
        assert t.get("replacement"), f"{t['id']}: says what replaces it, or that nothing in ICDEV does"
        if t["status"] == "kept":
            assert t.get("why"), f"{t['id']}: a kept duplicate says what still calls it"


def _imports_of(module: str) -> list[str]:
    head, _, leaf = module.rpartition(".")
    pat = re.compile(rf"^\s*(from {re.escape(module)} import|import {re.escape(module)}\b|from {re.escape(head)} import"
                     rf"[^\n]*\b{re.escape(leaf)}\b)", re.MULTILINE)
    hits = []
    for f in ROOT.joinpath("tools").rglob("*.py"):
        if pat.search(f.read_text(encoding="utf-8", errors="replace")):
            hits.append(str(f.relative_to(ROOT)))
    for d in ("tests", "apps"):
        for f in ROOT.joinpath(d).rglob("*.py"):
            if f.name != Path(__file__).name and pat.search(f.read_text(encoding="utf-8", errors="replace")):
                hits.append(str(f.relative_to(ROOT)))
    return hits


def _ci_listed() -> set[str]:
    out = set()
    d = ROOT / "args" / "ci_test_files"
    for f in [d / "core.txt", *sorted((d / "core.d").glob("*.txt"))]:
        out |= {ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    return out


def test_a_retired_duplicate_is_gone_imported_by_nothing_and_its_replacements_test_is_ci_listed():
    listed = _ci_listed()
    retired = [t for t in REGISTER["tools"] if t["status"] == "retired"]
    assert {t["id"] for t in retired} == {"publish_pipeline", "review_queue"}
    for t in retired:
        assert not (ROOT / t["path"]).exists(), f"{t['path']} is retired"
        assert not (ROOT / "icdev" / t["path"]).exists(), "its packaged copy is retired too"
        module = t["path"][:-3].replace("/", ".")
        assert importlib.util.find_spec(module) is None, module
        assert _imports_of(module) == [], f"{module} is still imported"
        test_file, _, test_name = t["test"].partition("::")
        assert test_file in listed, f"{test_file} runs in CI"
        assert re.search(rf"^def {re.escape(test_name)}\(", (ROOT / test_file).read_text(encoding="utf-8"), re.MULTILINE), t["test"]


def test_the_replacement_itself_builds_a_signed_package_that_meets_icos_contract(sp, signer):  # noqa: F811
    """The retired publish pipeline's job, done by its replacement: a signed store package for ICDEV[OS]."""
    from tools.store_publisher import card, config, package

    body = package.build(config.package(sp["cfg"], "syn-ent04-ledger-agent"), sp["cfg"], signer=signer,
                         version="1.0.0", now=NOW)["package"]
    assert package.schema_errors(body) == [] and body["schema"] == "icos-store-package/v1"
    assert card.verify_card(body["agent_card"], {card.kid(signer.spki()): signer.spki()})
    assert package.self_check(body, signer.spki()) == []


def test_the_retired_tools_mcp_entry_points_still_refuse():
    from tools.mcp import marketplace_server

    for h in (marketplace_server.handle_publish_asset, marketplace_server.handle_review_asset,
              marketplace_server.handle_list_pending):
        out = h({"asset_path": "x", "asset_type": "skill", "tenant_id": "t", "review_id": "r", "reviewer_id": "a",
                 "decision": "approved", "rationale": "r"})
        assert out["code"] == "superseded" and out["use"] == "tools/store_publisher"


@pytest.mark.parametrize("tool", sorted(CARD_NAMES - {"publish_pipeline", "review_queue"}))
def test_a_kept_duplicate_is_still_there_and_still_marked_deprecated(tool):
    t = next(x for x in REGISTER["tools"] if x["id"] == tool)
    assert t["status"] == "kept"
    head = (ROOT / t["path"]).read_text(encoding="utf-8").splitlines()[:5]
    assert any("DEPRECATED" in ln for ln in head), f"{t['path']} keeps its marker until it is retired"


def test_the_openclaw_bridge_no_longer_looks_for_the_retired_review_queue():
    """Its health check said whether the review queue was importable; it is deleted, so it says so without looking."""
    import inspect

    from tools.marketplace import openclaw_bridge

    assert openclaw_bridge._HAS_REVIEW is False
    assert "review_queue\")" not in inspect.getsource(openclaw_bridge), "no find_spec for a retired module"
    assert openclaw_bridge.health_check()["checks"]["review_queue_available"] is False
