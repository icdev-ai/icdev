#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.supply_chain.ndaa_889_screener — NDAA Section 889 procurement
compliance screening.

These tests are pure (no DB, no get_connection), matching the
tests/test_dependency_graph.py pattern, so the suite is worktree-safe and
does not depend on the heavy conftest fixture.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.supply_chain import ndaa_889_screener as screener  # noqa: E402
from tools.supply_chain.ndaa_889_screener import (  # noqa: E402
    screen_item,
    screen_bom,
    generate_attestation,
    normalize,
    PART_A_NAMED,
    PART_B_COVERED_FOREIGN_COUNTRIES,
    PART_B_ENTITY_CLASSES,
)


# ---------------------------------------------------------------------------
# is_covered_entity
# ---------------------------------------------------------------------------

class TestIsCoveredEntity:
    """Part A: Named entities prohibited by NDAA Section 889(a)(1)(B)."""

    @pytest.mark.parametrize("name", [
        "Huawei Technologies Co., Ltd.",
        "HUAWEI",
        "huawei device usa",
        "ZTE Corporation",
        "ZTE",
        "Hytera Communications",
        "Hangzhou Hikvision Digital Technology",
        "Dahua Technology",
        "Hikvision",
        "HikVision",
        "dahua",
    ])
    def test_part_a_named_entities_flagged(self, name):
        covered, part, reason = screener.match_covered_entity(name)
        assert covered is True, f"Expected {name!r} to be covered"
        assert part == "A", f"Expected Part A for {name!r}, got {part}"
        assert reason and "Huawei" in reason or "ZTE" in reason or "Hytera" in reason \
            or "Hikvision" in reason or "Dahua" in reason

    @pytest.mark.parametrize("name", [
        "Cisco Systems",
        "Dell Technologies",
        "Apple Inc.",
        "Microsoft Corporation",
        "Juniper Networks",
        "HP Enterprise",
        "IBM",
        "Amazon Web Services",
        "Generic Router Co.",
    ])
    def test_non_covered_names_clear(self, name):
        covered, part, reason = screener.match_covered_entity(name)
        assert covered is False, f"Expected {name!r} to be NOT covered"
        assert part is None
        assert reason is None


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercase(self):
        assert normalize("HUAWEI") == "huawei"

    def test_strips_punctuation(self):
        assert normalize("Huawei Technologies Co., Ltd.") == "huawei technologies co ltd"

    def test_collapses_whitespace(self):
        assert normalize("ZTE   Corporation") == "zte corporation"

    def test_already_clean(self):
        assert normalize("apple") == "apple"


# ---------------------------------------------------------------------------
# screen_item
# ---------------------------------------------------------------------------

class TestScreenItem:
    def test_part_a_prohibits_regardless_of_country(self):
        # Part A is country-agnostic — a Huawei device from USA is still
        # prohibited.
        result = screen_item({
            "vendor_name": "Huawei Technologies",
            "country_of_origin": "US",
            "item_description": "5G base station",
        })
        assert result["status"] == "prohibited"
        assert result["part"] == "A"
        assert "Huawei" in result["matched_entity"]

    def test_part_b_chinese_cctv_is_restricted(self):
        result = screen_item({
            "vendor_name": "Hangzhou Hikvision",
            "country_of_origin": "CN",
            "item_description": "CCTV surveillance camera model X1",
        })
        assert result["status"] == "prohibited"
        assert result["part"] == "A"

    def test_part_b_chinese_router_via_class(self):
        result = screen_item({
            "vendor_name": "Generic Networks",
            "country_of_origin": "CN",
            "item_description": "5G base station radio unit",
        })
        assert result["status"] == "restricted"
        assert result["part"] == "B"
        assert "CN" in (result["matched_entity"] or "")

    def test_allied_cctv_is_compliant(self):
        # CCTV from an allied country is NOT restricted under 889.
        result = screen_item({
            "vendor_name": "Axis Communications",
            "country_of_origin": "SE",
            "item_description": "Network CCTV camera",
        })
        assert result["status"] == "compliant"
        assert result["part"] is None

    def test_chinese_monitor_not_covered_class_is_compliant(self):
        # Monitors are not in the Part B entity class list.
        result = screen_item({
            "vendor_name": "BOE Display",
            "country_of_origin": "CN",
            "item_description": "27-inch LCD monitor",
        })
        assert result["status"] == "compliant"

    def test_manufacturer_field_also_screened(self):
        result = screen_item({
            "vendor_name": "Acme Distributors",
            "manufacturer": "ZTE Corporation",
            "country_of_origin": "US",
            "item_description": "Network switch",
        })
        assert result["status"] == "prohibited"
        assert "ZTE" in result["matched_entity"]

    def test_needs_review_when_ambiguous(self):
        # Country missing and no Part A name match.
        result = screen_item({
            "vendor_name": "Mystery Vendor",
            "item_description": "Telecommunications equipment",
        })
        assert result["status"] == "needs_review"

    def test_result_has_required_keys(self):
        result = screen_item({
            "vendor_name": "Cisco",
            "country_of_origin": "US",
            "item_description": "Router",
        })
        for key in (
            "screening_id", "status", "matched_entity", "part",
            "reason", "recommendation", "screened_at",
        ):
            assert key in result, f"Missing key {key!r}"


# ---------------------------------------------------------------------------
# screen_bom
# ---------------------------------------------------------------------------

class TestScreenBom:
    PROJECT = "proj-bom-889"

    def _sample_bom(self):
        return [
            {"vendor_name": "Cisco Systems", "country_of_origin": "US",
             "item_description": "Catalyst 9300 switch"},
            {"vendor_name": "Huawei Technologies", "country_of_origin": "CN",
             "item_description": "5G base station"},
            {"vendor_name": "BOE Display", "country_of_origin": "CN",
             "item_description": "LCD monitor 27in"},
        ]

    def test_blocks_tier1_on_prohibited(self):
        result = screen_bom(self.PROJECT, self._sample_bom())
        assert result["tier1_blocked"] is True
        assert result["prohibited_count"] == 1
        assert result["compliant_count"] == 2
        assert result["total_items"] == 3

    def test_clean_bom_not_blocked(self):
        bom = [
            {"vendor_name": "Cisco", "country_of_origin": "US",
             "item_description": "Switch"},
            {"vendor_name": "Dell", "country_of_origin": "US",
             "item_description": "Server"},
        ]
        result = screen_bom(self.PROJECT, bom)
        assert result["tier1_blocked"] is False
        assert result["prohibited_count"] == 0
        assert result["compliant_count"] == 2

    def test_attestation_hash_stable(self):
        bom = self._sample_bom()
        r1 = screen_bom(self.PROJECT, bom)
        r2 = screen_bom(self.PROJECT, bom)
        assert r1["attestation_hash"] == r2["attestation_hash"]

    def test_attestation_hash_changes_with_input(self):
        r1 = screen_bom(self.PROJECT, self._sample_bom())
        r2 = screen_bom(self.PROJECT, self._sample_bom()[:2])
        assert r1["attestation_hash"] != r2["attestation_hash"]

    def test_includes_project_id_and_timestamp(self):
        result = screen_bom(self.PROJECT, self._sample_bom())
        assert result["project_id"] == self.PROJECT
        assert "screened_at" in result
        # ISO 8601-ish
        assert re.match(r"^\d{4}-\d{2}-\d{2}T", result["screened_at"])

    def test_includes_per_item_results(self):
        result = screen_bom(self.PROJECT, self._sample_bom())
        assert "items" in result
        assert len(result["items"]) == 3
        for item in result["items"]:
            assert "status" in item


# ---------------------------------------------------------------------------
# generate_attestation
# ---------------------------------------------------------------------------

class TestGenerateAttestation:
    PROJECT = "proj-attest-889"

    def test_contains_project_id(self):
        bom = [{"vendor_name": "Cisco", "country_of_origin": "US",
                "item_description": "Switch"}]
        result = screen_bom(self.PROJECT, bom)
        text = generate_attestation(self.PROJECT, result)
        assert self.PROJECT in text

    def test_contains_sha256(self):
        bom = [{"vendor_name": "Cisco", "country_of_origin": "US",
                "item_description": "Switch"}]
        result = screen_bom(self.PROJECT, bom)
        # The module stamps the real screened_at on the result AFTER
        # hashing, so strip the volatile field to recompute the digest.
        result_for_hash = {**result, "screened_at": None}
        # Strip volatile per-item fields (screening_id, screened_at).
        canonical_items = [
            {k: v for k, v in r.items() if k not in ("screening_id", "screened_at")}
            for r in result_for_hash["items"]
        ]
        canonical = {
            "items": canonical_items,
            "counts": {
                "total": result["total_items"],
                "prohibited": result["prohibited_count"],
                "restricted": result["restricted_count"],
                "needs_review": result["needs_review_count"],
                "compliant": result["compliant_count"],
            },
            "tier1_blocked": result["tier1_blocked"],
        }
        expected = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        text = generate_attestation(self.PROJECT, result)
        assert expected in text

    def test_contains_classification_banner(self):
        bom = [{"vendor_name": "Cisco", "country_of_origin": "US",
                "item_description": "Switch"}]
        result = screen_bom(self.PROJECT, bom)
        text = generate_attestation(self.PROJECT, result)
        assert "CUI" in text

    def test_contains_signer_string(self):
        bom = [{"vendor_name": "Cisco", "country_of_origin": "US",
                "item_description": "Switch"}]
        result = screen_bom(self.PROJECT, bom)
        text = generate_attestation(self.PROJECT, result)
        assert "icdev-supply-chain-agent" in text

    def test_marks_blocked_status(self):
        bom = [{"vendor_name": "Huawei", "country_of_origin": "CN",
                "item_description": "5G base station"}]
        result = screen_bom(self.PROJECT, bom)
        text = generate_attestation(self.PROJECT, result)
        assert "BLOCKED" in text or "blocked" in text.lower()


# ---------------------------------------------------------------------------
# Reference data sanity
# ---------------------------------------------------------------------------

class TestReferenceData:
    def test_part_a_includes_all_5_parents(self):
        text = " ".join(PART_A_NAMED).lower()
        for entity in ("huawei", "zte", "hytera", "hikvision", "dahua"):
            assert entity in text, f"Missing Part A parent: {entity}"

    def test_part_b_covers_889_covered_foreign_countries(self):
        # 889 Part B specifically names CN, RU, KP, IR as covered foreign
        # countries (31 CFR § 201).
        for code in ("CN", "RU", "KP", "IR"):
            assert code in PART_B_COVERED_FOREIGN_COUNTRIES, code

    def test_part_b_entity_classes_present(self):
        assert "video_surveillance" in PART_B_ENTITY_CLASSES
        assert "telecommunications" in PART_B_ENTITY_CLASSES
