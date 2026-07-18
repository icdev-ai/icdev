# CUI // SP-CTI
"""penta-gd-06 — new TTX scenario regression tests.

Covers the 3 new platform-generation TTX scenarios:
  * document-integrity  — DIC poisoned-source / grounded-citation incident
  * slo-meltdown        — ODC/OHC SLO breach + model-drift triage
  * grounding-red-team  — red team vs citation grounding / provenance gates

Validates that each scenario:
  * is auto-discovered by scenario_loader.list_scenario_slugs()
  * loads without error and satisfies the loader's required-key contract
  * carries rubrics in the ai_scorer format (dimensions as a list of
    {id, weight, prompt}) that are actually scoreable by tools/ttx/ai_scorer
  * can back a TTX session created via session_manager.create_session
    (engine-level; no HTTP, so this does not collide with the blueprint work
    in penta-gd-03/04).
"""

from __future__ import annotations

import pytest

from tools.ttx.ai_scorer import _weighted_total
from tools.ttx.scenario_loader import list_scenario_slugs, load_scenario

# penta-fix-01: `_fallback_score` was removed by penta-gd-04 (commit 23b2d83f5)
# when silent-midpoint scoring was replaced with fail-loud `_unscored`. The
# scoreability assertion below was rewritten against the current contract
# (`_weighted_total`), so this module no longer imports the deleted helper and
# no longer xfails.

NEW_SLUGS = ("document-integrity", "slo-meltdown", "grounding-red-team")


@pytest.fixture
def ttx_env(tmp_path, monkeypatch):
    """Seed a temp SQLite DB with the shared conftest schema (ttx_* tables)."""
    import sqlite3

    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def test_new_slugs_discovered():
    slugs = set(list_scenario_slugs())
    for slug in NEW_SLUGS:
        assert slug in slugs, f"{slug!r} not auto-discovered by scenario_loader"


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_scenario_loads_and_has_required_keys(slug):
    sc = load_scenario(slug)
    assert sc["name"], f"{slug} missing name"
    assert sc["session_mode"] in ("live", "async"), sc["session_mode"]
    assert sc["injects"], f"{slug} has no injects"
    assert sc.get("slug") == slug
    for inj in sc["injects"]:
        assert inj.get("id") and inj.get("title"), f"{slug} inject missing id/title"
        # body file merged inline
        assert inj.get("body_md"), f"{slug}/{inj['id']} body_md not merged"


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_injects_escalate_in_sequence(slug):
    sc = load_scenario(slug)
    seqs = [inj.get("sequence") for inj in sc["injects"]]
    assert all(isinstance(s, int) for s in seqs), f"{slug} injects missing sequence"
    assert seqs == sorted(seqs), f"{slug} injects not ordered by sequence"
    assert len(sc["injects"]) >= 3, f"{slug} should escalate over >=3 injects"


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_rubrics_parse_and_are_scoreable(slug):
    sc = load_scenario(slug)
    for inj in sc["injects"]:
        rub = inj.get("scoring", {}).get("rubric")
        assert isinstance(rub, dict), (
            f"{slug}/{inj['id']} rubric path did not resolve to a dict"
        )
        dims = rub.get("dimensions")
        assert isinstance(dims, list) and dims, (
            f"{slug}/{inj['id']} dimensions must be a non-empty list (ai_scorer format)"
        )
        for dim in dims:
            assert {"id", "weight", "prompt"} <= set(dim), (
                f"{slug}/{inj['id']} dimension missing id/weight/prompt: {dim}"
            )
            assert str(dim["prompt"]).strip(), "empty rubric prompt"
        # weights should form a sane distribution
        total_w = sum(d["weight"] for d in dims)
        assert abs(total_w - 1.0) < 1e-6, f"{slug}/{inj['id']} weights sum={total_w}"
        # ai_scorer must consume it and produce a bounded score. penta-fix-01:
        # asserted against the current scoreability contract (_weighted_total),
        # not the removed silent-midpoint _fallback_score helper.
        wt_full = _weighted_total({d["id"]: 10 for d in dims}, dims)
        assert wt_full == 100, f"{slug}/{inj['id']} all-10 total should be 100, got {wt_full}"
        wt = _weighted_total({d["id"]: 9 for d in dims}, dims)
        assert 0 <= wt <= 100, f"{slug}/{inj['id']} weighted total out of range: {wt}"
        # A missing dimension score must not crash and must stay bounded.
        wt_partial = _weighted_total({dims[0]["id"]: 7}, dims)
        assert 0 <= wt_partial <= 100


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_personas_resolve_inline(slug):
    sc = load_scenario(slug)
    roles = sc.get("roles", [])
    assert roles, f"{slug} has no roles"
    for role in roles:
        assert role.get("id") and role.get("label")
        assert "persona_data" in role, f"{slug}/{role['id']} persona not resolved inline"
        assert role["persona_data"].get("label"), "persona missing label"


@pytest.mark.parametrize("slug", NEW_SLUGS)
def test_session_can_be_created_for_scenario(slug, ttx_env):
    """Engine-level: a TTX session can be created + fetched for each scenario."""
    from tools.ttx.session_manager import create_session, get_session

    sc = load_scenario(slug)
    row = create_session(
        scenario_slug=slug,
        session_mode=sc["session_mode"],
        facilitator_name="penta-gd-06-test",
        duration_minutes=sc.get("duration_minutes", 120),
        max_teams=sc.get("max_teams", 8),
    )
    assert row["scenario_slug"] == slug
    assert row["state"] == "pending"
    fetched = get_session(row["session_id"] if "session_id" in row else row["id"])
    assert fetched is not None
    assert fetched["scenario_slug"] == slug
