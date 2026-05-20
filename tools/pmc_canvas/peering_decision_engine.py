# CUI // SP-CTI
"""Peering decision engine — 6-dimension scoring → open/selective/no recommendation."""

from __future__ import annotations

from typing import Any

from tools.pmc_canvas.constants import PEERING_SCORE_WEIGHTS, PEERING_THRESHOLDS


def evaluate_peer(
    peer: dict[str, Any],
    our_asn: int,
    our_ix_ids: list[int] | None = None,
    prefixes: list[dict] | None = None,
) -> dict[str, Any]:
    """Score a peer across 6 dimensions and return a peering recommendation.

    Dimensions:
      1. traffic_ratio — how balanced is the exchange (0.5 = perfect)
      2. prefix_count — within healthy bounds
      3. rpki_validity — percentage of prefixes with valid ROA
      4. irr_registration — percentage of prefixes in IRR
      5. noc_responsiveness — has NOC email / 24x7 contact
      6. ix_presence — shares at least one IX with us

    Returns:
        {
          "peer_asn": int,
          "recommendation": "open" | "selective" | "no",
          "score": float (0.0–1.0),
          "scores": {dimension: float},
          "reasons": [str],
          "blockers": [str],
        }
    """
    weights = PEERING_SCORE_WEIGHTS
    thresholds = PEERING_THRESHOLDS
    reasons: list[str] = []
    blockers: list[str] = []
    scores: dict[str, float] = {}

    # ── 1. Traffic ratio ──────────────────────────────────────────────────────
    ratio = float(peer.get("traffic_ratio", 0.0))
    if ratio <= 0:
        scores["traffic_ratio"] = 0.3
        reasons.append("No traffic ratio data available")
    elif 0.3 <= ratio <= 3.0:
        scores["traffic_ratio"] = 1.0
        reasons.append(f"Balanced traffic ratio ({ratio:.2f}x)")
    elif ratio < 0.1 or ratio > 10.0:
        scores["traffic_ratio"] = 0.1
        reasons.append(f"Highly imbalanced traffic ratio ({ratio:.2f}x)")
    else:
        scores["traffic_ratio"] = 0.6
        reasons.append(f"Moderate traffic ratio ({ratio:.2f}x)")

    # ── 2. Prefix count ───────────────────────────────────────────────────────
    pfx4 = int(peer.get("ipv4_prefix_count", 0))
    pfx6 = int(peer.get("ipv6_prefix_count", 0))
    total_pfx = pfx4 + pfx6
    max_warn_v4 = thresholds.get("max_prefixes_v4_warn", 500_000)
    max_warn_v6 = thresholds.get("max_prefixes_v6_warn", 100_000)

    if total_pfx == 0:
        scores["prefix_count"] = 0.2
        reasons.append("No prefix data available")
    elif pfx4 > max_warn_v4 or pfx6 > max_warn_v6:
        scores["prefix_count"] = 0.4
        reasons.append(f"Large prefix set (v4:{pfx4}, v6:{pfx6}) — review max-prefix limits")
    elif 10 <= pfx4 <= 50_000:
        scores["prefix_count"] = 1.0
        reasons.append(f"Healthy prefix count (v4:{pfx4}, v6:{pfx6})")
    else:
        scores["prefix_count"] = 0.6

    # ── 3. RPKI validity ──────────────────────────────────────────────────────
    if prefixes:
        total_pfx_records = len(prefixes)
        valid_pfx = sum(1 for p in prefixes if p.get("rpki_status") == "valid")
        invalid_pfx = sum(1 for p in prefixes if p.get("rpki_status") == "invalid")
        rpki_pct = valid_pfx / total_pfx_records if total_pfx_records > 0 else 0.0

        if invalid_pfx > 0 and thresholds.get("rpki_invalid_block"):
            blockers.append(f"{invalid_pfx} RPKI-invalid prefix(es) — session should be blocked")
            scores["rpki_validity"] = 0.0
        elif rpki_pct >= 0.9:
            scores["rpki_validity"] = 1.0
            reasons.append(f"Excellent RPKI coverage ({rpki_pct*100:.0f}%)")
        elif rpki_pct >= 0.5:
            scores["rpki_validity"] = 0.6
            reasons.append(f"Partial RPKI coverage ({rpki_pct*100:.0f}%)")
        else:
            scores["rpki_validity"] = 0.2
            reasons.append(f"Poor RPKI ROA coverage ({rpki_pct*100:.0f}%)")
    else:
        scores["rpki_validity"] = 0.3
        reasons.append("No prefix RPKI data — unable to assess ROA coverage")

    # ── 4. IRR registration ───────────────────────────────────────────────────
    irr_as_set = peer.get("irr_as_set", "").strip()
    if prefixes:
        irr_reg = sum(1 for p in prefixes if p.get("irr_registered"))
        irr_pct = irr_reg / len(prefixes) if prefixes else 0.0
        if irr_pct >= 0.9:
            scores["irr_registration"] = 1.0
            reasons.append(f"Good IRR coverage ({irr_pct*100:.0f}% prefixes registered)")
        elif irr_pct >= 0.5:
            scores["irr_registration"] = 0.6
        else:
            scores["irr_registration"] = 0.2
            reasons.append("Poor IRR registration — route filtering unreliable")
    elif irr_as_set:
        scores["irr_registration"] = 0.7
        reasons.append(f"AS-SET registered: {irr_as_set}")
    else:
        scores["irr_registration"] = 0.1
        reasons.append("No IRR AS-SET defined — route filtering not possible")

    # ── 5. NOC responsiveness ─────────────────────────────────────────────────
    noc_email = peer.get("noc_email", "").strip()
    if noc_email and "@" in noc_email:
        scores["noc_responsiveness"] = 1.0
        reasons.append(f"NOC contact on file: {noc_email}")
    else:
        scores["noc_responsiveness"] = 0.0
        reasons.append("No NOC contact email — incident coordination difficult")

    # ── 6. IX presence ────────────────────────────────────────────────────────
    pdb_net_id = peer.get("peeringdb_net_id")
    if pdb_net_id:
        scores["ix_presence"] = 0.8
        reasons.append("Listed on PeeringDB with IX memberships")
    elif our_ix_ids:
        scores["ix_presence"] = 0.4
        reasons.append("Shared IX detected but PeeringDB not synced")
    else:
        scores["ix_presence"] = 0.2
        reasons.append("No shared IX presence confirmed")

    # ── Composite score ───────────────────────────────────────────────────────
    composite = sum(scores.get(dim, 0.0) * w for dim, w in weights.items())

    # Blockers override recommendation regardless of score
    if blockers:
        recommendation = "no"
    elif composite >= thresholds["open_score"]:
        recommendation = "open"
    elif composite >= thresholds["selective_score"]:
        recommendation = "selective"
    else:
        recommendation = "no"

    return {
        "peer_asn": peer.get("asn", 0),
        "org_name": peer.get("org_name", ""),
        "recommendation": recommendation,
        "score": round(composite, 3),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "reasons": reasons,
        "blockers": blockers,
    }


def generate_peering_brief(evaluation: dict[str, Any]) -> str:
    """Generate a plain-English peering recommendation brief."""
    rec = evaluation["recommendation"].upper()
    score = evaluation["score"]
    asn = evaluation.get("peer_asn", "N/A")
    org = evaluation.get("org_name", "")

    lines = [
        f"## Peering Recommendation: {rec} (AS{asn} — {org})",
        f"",
        f"**Composite Score:** {score:.1%}",
        f"",
        "**Key Findings:**",
    ]
    for r in evaluation.get("reasons", []):
        lines.append(f"- {r}")

    if evaluation.get("blockers"):
        lines += ["", "**Blockers (must resolve before peering):**"]
        for b in evaluation["blockers"]:
            lines.append(f"- ⚠ {b}")

    lines += [
        "",
        f"**Recommendation:** {'Establish peering' if rec != 'NO' else 'Do not peer at this time'}.",
    ]
    return "\n".join(lines)
