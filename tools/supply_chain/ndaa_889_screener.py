#!/usr/bin/env python3
# CUI // SP-CTI
"""NDAA Section 889 Procurement Compliance Screener.

FY2019 NDAA Section 889 Part A (named entities) and Part B (entity classes
from covered foreign countries per 31 CFR § 201) drive this deterministic
screener. No network, no LLM — pure function library + CLI.

  Part A — Sec 889(a)(1)(B): prohibits USG from procuring/using equipment
  from named entities + their subsidiaries/affiliates, regardless of
  country of origin.

  Part B — Sec 889(a)(1)(A): prohibits USG from contracting with any
  entity that uses covered telecommunications equipment or services as a
  substantial or essential component, or as critical technology, from
  covered foreign countries (CN, RU, KP, IR).

Public API (pure, no DB):
    normalize(s)                       -> normalized string for matching
    is_covered_entity(name)            -> bool  (Part A only)
    match_covered_entity(name)         -> (covered, part, reason)
    screen_item(item_dict)             -> single screening result
    screen_bom(project_id, items)      -> aggregate + per-item result
    generate_attestation(project_id, result) -> signed-text attestation

Tables populated by downstream callers (out of scope here):
    supply_chain_vendors.section_889_status  (CHECK: compliant/under_review/
                                              prohibited/exempt)
    scrm_assessments (consumed by scrm_assessor)

CLI:
    python tools/supply_chain/ndaa_889_screener.py --vendor "Huawei" --json
    python tools/supply_chain/ndaa_889_screener.py --bom-json <file> --json
    python tools/supply_chain/ndaa_889_screener.py --show-covered-entities
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Reference data — FY2019 NDAA Section 889 / 31 CFR § 201
# ---------------------------------------------------------------------------

# Part A — Sec 889(a)(1)(B) — named entities + subsidiaries/affiliates.
# Source: Public Law 115-232 § 889(a)(1)(B); published Federal Register
# list. Subsidiaries/affiliates are folded into "their subsidiaries and
# affiliates" by statute, so any name that contains one of the parent
# strings below is covered.
PART_A_NAMED = (
    "Huawei Technologies",
    "Huawei Device",
    "ZTE Corporation",
    "Hytera Communications",
    "Hangzhou Hikvision Digital Technology",
    "Dahua Technology",
)

# Short-form aliases for Part A parents. Substring match on the
# normalized name (e.g. "huawei" alone is covered because the alias
# "huawei" is a substring of "Huawei Technologies"). These mirror the
# common shortened forms that appear in procurement and vendor master
# systems where the full legal name is not always used.
PART_A_ALIASES = (
    "huawei",
    "zte",
    "hytera",
    "hikvision",
    "dahua",
)

# Part B — Sec 889(a)(1)(A) — covered foreign countries (31 CFR § 201).
PART_B_COVERED_FOREIGN_COUNTRIES: frozenset[str] = frozenset({
    "CN", "CHN",  # People's Republic of China
    "RU", "RUS",  # Russian Federation
    "KP", "PRK",  # Democratic People's Republic of Korea
    "IR", "IRN",  # Islamic Republic of Iran
})

# Part B — entity classes that, when sourced from a covered foreign country,
# trigger restriction. Tokens are normalized (lowercase, no punctuation,
# single-space) substrings searched in item_description.
PART_B_ENTITY_CLASSES: dict[str, tuple[str, ...]] = {
    "video_surveillance": (
        "cctv", "surveillance camera", "nvr", "dvr",
        "ip camera", "video surveillance", "ptz camera",
    ),
    "telecommunications": (
        "5g base station", "5g radio", "core network switch",
        "telecommunications equipment", "router", "cellular base station",
    ),
}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    >>> normalize("Huawei Technologies Co., Ltd.")
    'huawei technologies co ltd'
    """
    if not s:
        return ""
    out = s.lower()
    out = _NON_ALNUM_RE.sub(" ", out)
    out = _WS_RE.sub(" ", out).strip()
    return out


# ---------------------------------------------------------------------------
# Part A matching
# ---------------------------------------------------------------------------

def _part_a_match(normalized_name: str) -> Optional[str]:
    """Return the matched parent entity string, or None.

    Two-stage match:
      1. Substring match on any full parent (e.g. "huawei technologies
         co ltd" contains "huawei technologies") — returns the parent.
      2. Substring match on any short-form alias (e.g. "huawei" alone,
         or "hikvision" alone) — returns the parent (not the alias) so
         the reason text is human-readable.
    """
    for parent in PART_A_NAMED:
        if parent.lower() in normalized_name:
            return parent
    for alias in PART_A_ALIASES:
        if alias in normalized_name:
            # Find the canonical parent for the matched alias.
            for parent in PART_A_NAMED:
                if alias in parent.lower():
                    return parent
    return None


# ---------------------------------------------------------------------------
# Part B matching
# ---------------------------------------------------------------------------

def _part_b_match(country_code: str, normalized_desc: str) -> Optional[tuple[str, str]]:
    """Return (entity_class, country_code) on match, else None.

    A Part B hit requires BOTH the country be in the covered list AND the
    description contain at least one token from a Part B entity class.
    """
    country_upper = (country_code or "").upper()
    if country_upper not in PART_B_COVERED_FOREIGN_COUNTRIES:
        return None
    for entity_class, tokens in PART_B_ENTITY_CLASSES.items():
        for token in tokens:
            if token in normalized_desc:
                return entity_class, country_upper
    return None


# ---------------------------------------------------------------------------
# Public API — covered entity lookup
# ---------------------------------------------------------------------------

def is_covered_entity(vendor_name: str) -> bool:
    """Part A test only. Use match_covered_entity for Part A + B detail."""
    _, covered, _ = match_covered_entity(vendor_name)
    return covered


def match_covered_entity(vendor_name: str) -> tuple[bool, Optional[str], Optional[str]]:
    """Match a vendor name against NDAA Section 889 covered entity lists.

    Returns:
        (covered: bool, part: 'A' | 'B' | None, reason: str | None)

    Part A takes precedence over Part B — if a name is on the Part A list,
    we report that even when country / description would also trigger B.
    """
    norm = normalize(vendor_name)
    if not norm:
        return False, None, None

    a = _part_a_match(norm)
    if a:
        return True, "A", f"Vendor name contains Part A named entity: {a}"

    return False, None, None


# ---------------------------------------------------------------------------
# Public API — single item screening
# ---------------------------------------------------------------------------

def screen_item(item: dict) -> dict:
    """Screen one procurement line item.

    item keys (all optional except vendor_name):
        vendor_name      str  — primary name to check
        manufacturer     str  — secondary name to check (folded into name)
        country_of_origin str — ISO-2/3 code (US, CN, USA, CHN, …)
        item_description str  — free text, scanned for Part B entity class

    Returns a screening result dict with:
        screening_id, status, matched_entity, part, reason,
        recommendation, screened_at
    """
    vendor = item.get("vendor_name", "")
    manufacturer = item.get("manufacturer", "")
    country = (item.get("country_of_origin") or "").upper()
    description = item.get("item_description", "")

    combined_name = f"{vendor} {manufacturer}".strip()
    norm_name = normalize(combined_name)
    norm_desc = normalize(description)

    matched_entity: Optional[str] = None
    part: Optional[str] = None
    status: str
    reason: str
    recommendation: str

    # Part A first — country-agnostic prohibition.
    a_match = _part_a_match(norm_name)
    if a_match:
        status = "prohibited"
        part = "A"
        matched_entity = a_match
        reason = (
            f"Vendor/manufacturer '{combined_name}' matches NDAA Section 889 "
            f"Part A named entity: {a_match}. Per Sec 889(a)(1)(B), USG "
            f"procurement of any equipment, system, or service from this "
            f"entity — or its subsidiaries and affiliates — is prohibited."
        )
        recommendation = (
            "Remove this item from the BOM. Identify a compliant alternate. "
            "Document the substitution in the procurement audit trail."
        )
    else:
        # Part B — covered country + covered entity class.
        b_match = _part_b_match(country, norm_desc)
        if b_match:
            entity_class, cc = b_match
            status = "restricted"
            part = "B"
            matched_entity = f"{cc}:{entity_class}"
            reason = (
                f"Item sourced from covered foreign country {cc} and "
                f"matches Part B entity class '{entity_class}'. Per Sec "
                f"889(a)(1)(A), USG cannot contract with any entity that "
                f"uses such equipment as a substantial or essential "
                f"component, or as critical technology."
            )
            recommendation = (
                "Source an alternate from a non-covered foreign country, or "
                "obtain a written attestation that this item is not used as "
                "a substantial/essential component of the deliverable."
            )
        elif not country and norm_desc:
            # Description has content but country is missing. If the
            # description matches a Part B entity class token, we cannot
            # clear without country — flag for review.
            class_match = None
            for entity_class, tokens in PART_B_ENTITY_CLASSES.items():
                if any(tok in norm_desc for tok in tokens):
                    class_match = entity_class
                    break
            if class_match:
                status = "needs_review"
                reason = (
                    f"Item description matches Part B entity class "
                    f"'{class_match}', but country_of_origin is missing. "
                    f"Per Sec 889(a)(1)(A), origin must be confirmed before "
                    f"Tier 1 submission."
                )
                recommendation = (
                    "Request country_of_origin from the vendor, then "
                    "re-screen."
                )
            else:
                status = "compliant"
                reason = (
                    f"Vendor '{combined_name}' is not on the Part A named "
                    f"entity list, and no Part B entity class match was "
                    f"found in the item description."
                )
                recommendation = "No action required."
        elif not country and not norm_desc:
            # Insufficient information to clear.
            status = "needs_review"
            reason = (
                "Missing country_of_origin and item_description; cannot "
                "complete Part B screening. Collect full provenance before "
                "Tier 1 submission."
            )
            recommendation = (
                "Request country_of_origin and a specific item_description "
                "from the vendor, then re-screen."
            )
        else:
            status = "compliant"
            reason = (
                f"Vendor '{combined_name}' is not on the Part A named "
                f"entity list, and the country ({country or 'unspecified'}) "
                f"is not in the Part B covered foreign country set, or the "
                f"item description does not match a covered entity class."
            )
            recommendation = "No action required."

    return {
        "screening_id": str(uuid.uuid4()),
        "vendor_name": combined_name,
        "country_of_origin": country or None,
        "item_description": description,
        "status": status,
        "matched_entity": matched_entity,
        "part": part,
        "reason": reason,
        "recommendation": recommendation,
        "screened_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public API — full BOM screening
# ---------------------------------------------------------------------------

def screen_bom(project_id: str, items: list[dict]) -> dict:
    """Screen a full Bill of Materials.

    Returns:
        {
            project_id, screened_at, total_items,
            prohibited_count, restricted_count,
            needs_review_count, compliant_count,
            tier1_blocked: bool,
            items: [screen_item results],
            attestation_hash: sha256 of canonical JSON
        }
    """
    if not project_id:
        raise ValueError("project_id is required")
    if not isinstance(items, list):
        raise TypeError("items must be a list of dicts")

    per_item = [screen_item(it) for it in items]
    prohibited = sum(1 for r in per_item if r["status"] == "prohibited")
    restricted = sum(1 for r in per_item if r["status"] == "restricted")
    needs_review = sum(1 for r in per_item if r["status"] == "needs_review")
    compliant = sum(1 for r in per_item if r["status"] == "compliant")
    total = len(per_item)

    # Tier 1 is blocked if any item is prohibited OR if any item is flagged
    # needs_review (insufficient info to clear).
    tier1_blocked = (prohibited + needs_review) > 0

    # Build the canonical result body (items + counts), hash for
    # attestation. Volatile per-item fields (screening_id, screened_at)
    # are stripped from the hashed body so the digest is stable for
    # identical inputs. The attestation block is the carrier of those
    # volatile fields.
    body = {
        "project_id": project_id,
        "screened_at": None,  # filled after hash
        "total_items": total,
        "prohibited_count": prohibited,
        "restricted_count": restricted,
        "needs_review_count": needs_review,
        "compliant_count": compliant,
        "tier1_blocked": tier1_blocked,
        "items": per_item,
    }
    # First pass: hash with screened_at=None so it's deterministic.
    attestation_hash = _hash_canonical(_canonical_body(body))

    # Finalize: stamp the real timestamp on the returned object.
    screened_at = datetime.now(timezone.utc).isoformat()
    body["screened_at"] = screened_at

    return {
        **body,
        "attestation_hash": attestation_hash,
    }


# ---------------------------------------------------------------------------
# Public API — attestation
# ---------------------------------------------------------------------------

_SIGNER = "icdev-supply-chain-agent"
_CLASSIFICATION = "CUI // SP-CTI"


def _canonical_body(result: dict) -> dict:
    """Build the canonical body for hashing.

    Strips volatile per-item fields (screening_id, screened_at) so the
    digest is stable for identical inputs. Used by both screen_bom and
    generate_attestation so the attestation hash matches the result's
    attestation_hash field.
    """
    canonical_items = [
        {k: v for k, v in r.items() if k not in ("screening_id", "screened_at")}
        for r in result.get("items", [])
    ]
    return {
        "items": canonical_items,
        "counts": {
            "total": result.get("total_items", 0),
            "prohibited": result.get("prohibited_count", 0),
            "restricted": result.get("restricted_count", 0),
            "needs_review": result.get("needs_review_count", 0),
            "compliant": result.get("compliant_count", 0),
        },
        "tier1_blocked": result.get("tier1_blocked", False),
    }


def _hash_canonical(canonical: dict) -> str:
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def generate_attestation(project_id: str, result: dict) -> str:
    """Return a human-readable attestation block.

    Includes project_id, ISO timestamp, SHA-256 of the canonical result
    body, signer, classification banner, and the Tier 1 block status.
    Suitable for embedding in a procurement PRD or audit_trail entry.
    """
    digest = _hash_canonical(_canonical_body(result))
    blocked = result.get("tier1_blocked", False)
    block_label = "BLOCKED — Tier 1 submission is not authorized" if blocked else "CLEARED — Tier 1 submission is authorized"

    return (
        f"{_CLASSIFICATION}\n"
        f"NDAA Section 889 Compliance Attestation\n"
        f"Project: {project_id}\n"
        f"Screened at: {result.get('screened_at', '')}\n"
        f"Signer: {_SIGNER}\n"
        f"Total items: {result.get('total_items', 0)}\n"
        f"  Prohibited (Part A): {result.get('prohibited_count', 0)}\n"
        f"  Restricted (Part B): {result.get('restricted_count', 0)}\n"
        f"  Needs review:         {result.get('needs_review_count', 0)}\n"
        f"  Compliant:            {result.get('compliant_count', 0)}\n"
        f"Tier 1 status: {block_label}\n"
        f"Attestation hash (SHA-256 of canonical result body): {digest}\n"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_show_covered_entities() -> int:
    payload = {
        "source": "FY2019 NDAA Section 889 / 31 CFR § 201",
        "part_a_named": list(PART_A_NAMED),
        "part_b_covered_foreign_countries": sorted(PART_B_COVERED_FOREIGN_COUNTRIES),
        "part_b_entity_classes": {
            k: list(v) for k, v in PART_B_ENTITY_CLASSES.items()
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


def _cli_vendor(name: str) -> int:
    covered, part, reason = match_covered_entity(name)
    print(json.dumps({
        "vendor": name,
        "covered": covered,
        "part": part,
        "reason": reason,
    }, indent=2))
    return 0


def _cli_bom(path: str) -> int:
    with open(path, "r", encoding="utf-8") as fh:
        bom = json.load(fh)
    if not isinstance(bom, list):
        print("Error: BOM JSON must be a list of items", file=sys.stderr)
        return 2
    project_id = "cli-bom"
    result = screen_bom(project_id, bom)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NDAA Section 889 procurement compliance screener"
    )
    parser.add_argument("--vendor", help="Single vendor name to check")
    parser.add_argument("--bom-json", help="Path to BOM JSON (list of items)")
    parser.add_argument("--show-covered-entities", action="store_true",
                        help="Print the canonical covered-entity reference list")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON (default for --vendor and --bom-json)")
    args = parser.parse_args()

    try:
        if args.show_covered_entities:
            return _cli_show_covered_entities()
        if args.vendor:
            return _cli_vendor(args.vendor)
        if args.bom_json:
            return _cli_bom(args.bom_json)
        parser.print_help()
        return 0
    except (FileNotFoundError, ValueError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
