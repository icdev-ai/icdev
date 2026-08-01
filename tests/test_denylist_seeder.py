# CUI // SP-CTI
"""Tests: GovCon deny-list seeding + agency defaults (trust-mask-04)."""

import importlib
from pathlib import Path

import pytest

seeder = importlib.import_module("tools.redaction.denylist_seeder")

_ROOT = Path(__file__).resolve().parent.parent


class TestSeedFromProfile:
    def test_extracts_entity_and_partners(self):
        profile = {
            "entity_name": "Acme Federal Solutions",
            "teaming_partners": ["NovaTech Consulting", "Beacon Systems"],
            "partners": [{"name": "Orion Labs"}, {"org": "Vertex Group"}],
            "key_customers": "Missile Defense Agency",
        }
        seeds = seeder.seed_from_profile(profile)
        orgs = seeds["protected_organizations"]
        assert "Acme Federal Solutions" in orgs
        assert "NovaTech Consulting" in orgs
        assert "Orion Labs" in orgs
        assert "Vertex Group" in orgs
        assert "Missile Defense Agency" in orgs

    def test_dedups_case_insensitive(self):
        profile = {"entity_name": "Acme", "partners": ["acme", "ACME"]}
        assert seeder.seed_from_profile(profile)["protected_organizations"] == ["Acme"]

    def test_empty_profile(self):
        assert seeder.seed_from_profile({})["protected_organizations"] == []

    def test_ignores_empty_and_nonstring(self):
        profile = {"entity_name": "", "partners": [None, 42, {"name": ""}]}
        assert seeder.seed_from_profile(profile)["protected_organizations"] == []


class TestMergeDenylists:
    def test_non_destructive_union(self):
        existing = {"protected_organizations": ["Existing Corp"], "program_names": ["Prog X"]}
        seeds = {"protected_organizations": ["Existing Corp", "New Corp"]}
        merged = seeder.merge_denylists(existing, seeds)
        assert merged["protected_organizations"] == ["Existing Corp", "New Corp"]
        assert merged["program_names"] == ["Prog X"]  # untouched
        # original not mutated
        assert existing["protected_organizations"] == ["Existing Corp"]

    def test_case_insensitive_dedup(self):
        merged = seeder.merge_denylists(
            {"protected_organizations": ["Acme"]},
            {"protected_organizations": ["ACME", "Beta"]},
        )
        assert merged["protected_organizations"] == ["Acme", "Beta"]

    def test_empty_existing(self):
        merged = seeder.merge_denylists({}, {"protected_organizations": ["A"]})
        assert merged["protected_organizations"] == ["A"]


class TestAgencyDefaults:
    def test_agency_surrogates_populated_and_feed_recognizer(self):
        import yaml

        with open(_ROOT / "args" / "redaction_govcon.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        agencies = cfg.get("agency_surrogates") or {}
        assert len(agencies) >= 10  # no longer inert
        assert "Missile Defense Agency" in agencies

        # The recognizer builder turns the map keys into a deny-list recognizer
        # (Presidio-only; returns [] when presidio_analyzer is unavailable).
        try:
            from tools.redaction.govcon_recognizers import build_govcon_recognizers
        except Exception:
            pytest.skip("govcon_recognizers unavailable")
        recognizers = build_govcon_recognizers(cfg)
        if not recognizers:
            pytest.skip("presidio_analyzer not installed — recognizer building unavailable")
        names = [getattr(r, "name", "") for r in recognizers]
        assert any("agency_name_deny_list" in n for n in names)
