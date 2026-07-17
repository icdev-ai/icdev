#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC AI Hardware Replacement Recommender.

Given a source device from ni_devices, queries nc_hardware_profiles for
candidate replacements and scores them across hardware parity, feature parity,
cost, and vendor strategy. Optionally retrieves RAG SOPs for migration guidance.

Usage:
    python tools/ndc/replacement_recommender.py --device-id <id> --json
    python tools/ndc/replacement_recommender.py --vendor Cisco --model "ASR 1001-X" --json
    python tools/ndc/replacement_recommender.py --device-id <id> --top-k 5 --rag-sops --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"


def _nc_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _get_device(conn: sqlite3.Connection, device_id: str) -> Optional[sqlite3.Row]:
    row = conn.execute(
        """SELECT id, vendor, model, device_type, firmware_version,
                  replacement_cost, annual_maintenance_cost, eol_date, eos_date,
                  site, rack_location, criticality_score, downstream_count
           FROM ni_devices WHERE id = %s""",
        (device_id,),
    ).fetchone()
    return row


def _get_device_by_model(conn: sqlite3.Connection, vendor: str, model: str) -> Optional[sqlite3.Row]:
    row = conn.execute(
        """SELECT id, vendor, model, device_type, firmware_version,
                  replacement_cost, annual_maintenance_cost, eol_date, eos_date,
                  site, rack_location, criticality_score, downstream_count
           FROM ni_devices WHERE vendor = %s AND model = %s LIMIT 1""",
        (vendor, model),
    ).fetchone()
    return row


def _get_source_profile(conn: sqlite3.Connection, vendor: str, model: str) -> Optional[sqlite3.Row]:
    row = conn.execute(
        """SELECT * FROM nc_hardware_profiles
           WHERE vendor = %s AND model = %s LIMIT 1""",
        (vendor, model),
    ).fetchone()
    return row


def _get_candidates(conn: sqlite3.Connection, device_type: str, exclude_vendor_model: tuple[str, str]) -> List[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM nc_hardware_profiles
           WHERE device_type = %s
             AND NOT (vendor = %s AND model = %s)
             AND (eol_date IS NULL OR eol_date > date('now', '+2 years'))
           ORDER BY throughput_gbps DESC""",
        (device_type, exclude_vendor_model[0], exclude_vendor_model[1]),
    ).fetchall()


def _safe_float(val) -> float:
    try:
        return float(val or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _score_hardware_parity(src: sqlite3.Row, cand: sqlite3.Row) -> float:
    """Score 0–1 based on hardware spec parity."""
    scores = []

    # Throughput
    src_t = _safe_float(src["throughput_gbps"])
    cand_t = _safe_float(cand["throughput_gbps"])
    if src_t > 0:
        scores.append(min(cand_t / src_t, 2.0) / 2.0)

    # PPS
    src_p = _safe_float(src["pps_mpps"])
    cand_p = _safe_float(cand["pps_mpps"])
    if src_p > 0:
        scores.append(min(cand_p / src_p, 2.0) / 2.0)

    # Rack units (lower is better — efficiency)
    src_ru = _safe_int(src["rack_units"])
    cand_ru = _safe_int(cand["rack_units"])
    if src_ru > 0:
        scores.append(min(src_ru / max(cand_ru, 1), 1.0))

    # Power typical
    src_pw = _safe_float(src["power_typical_w"])
    cand_pw = _safe_float(cand["power_typical_w"])
    if src_pw > 0:
        scores.append(min(src_pw / max(cand_pw, 1), 1.0))

    return round(sum(scores) / len(scores), 4) if scores else 0.5


def _score_feature_parity(src: sqlite3.Row, cand: sqlite3.Row) -> float:
    """Score 0–1 based on feature spec parity."""
    features = [
        ("routing_table_size", True),
        ("arp_table_size", True),
        ("mac_table_size", True),
        ("nat_sessions", True),
        ("vpn_tunnels", True),
        ("vlan_count", True),
    ]
    scores = []
    for col, higher_is_better in features:
        src_v = _safe_int(src[col])
        cand_v = _safe_int(cand[col])
        if src_v > 0:
            ratio = cand_v / src_v if higher_is_better else src_v / max(cand_v, 1)
            scores.append(min(ratio, 2.0) / 2.0)
    return round(sum(scores) / len(scores), 4) if scores else 0.5


def _score_cost(src: sqlite3.Row, cand: sqlite3.Row) -> float:
    """Score 0–1 where lower replacement cost = higher score (bounded)."""
    src_cost = _safe_float(src["replacement_cost"])
    cand_cost = _safe_float(cand["replacement_cost"])
    if src_cost <= 0:
        return 0.5
    if cand_cost <= 0:
        return 0.5
    ratio = src_cost / cand_cost
    # If candidate costs less, ratio > 1 → good, but cap at 1.0
    return round(min(ratio, 1.0), 4)


def _score_vendor_strategy(src: sqlite3.Row, cand: sqlite3.Row, prefer_same_vendor: bool = True) -> float:
    """Score vendor transition: same vendor = easier migration."""
    src_v = (src["vendor"] or "").lower()
    cand_v = (cand["vendor"] or "").lower()
    if src_v == cand_v:
        return 1.0 if prefer_same_vendor else 0.6
    # Multi-vendor transitions are harder but may be strategic
    return 0.6 if prefer_same_vendor else 0.9


def _days_until_eol(eol_date: str | None) -> int:
    if not eol_date:
        return 9999
    from datetime import datetime, timezone
    try:
        d = datetime.strptime(eol_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(0, (d - datetime.now(timezone.utc)).days)
    except Exception:
        return 9999


def _score_eol_freshness(cand: sqlite3.Row) -> float:
    days = _days_until_eol(cand["eol_date"])
    if days >= 730:
        return 1.0
    if days >= 365:
        return 0.8
    if days >= 180:
        return 0.5
    return 0.2


def _rag_sops_for_migration(src_vendor: str, src_model: str, cand_vendor: str, cand_model: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Retrieve relevant SOP chunks from RAG for migration guidance."""
    try:
        from tools.llm import get_embedding_provider
        from tools.rag.vector_store_factory import VectorStoreFactory

        provider = get_embedding_provider()
        store = VectorStoreFactory.create()

        query = f"{src_vendor} {src_model} to {cand_vendor} {cand_model} migration runbook best practices"
        emb = provider.embed(query)
        results = store.search(emb, top_k=top_k, filters={"source_type": "ndc_sops"})
        return [
            {
                "chunk_id": r.chunk_id,
                "content": r.content[:500],
                "score": round(r.score, 4),
            }
            for r in results
        ]
    except Exception:
        return []


def recommend(
    device_id: Optional[str] = None,
    vendor: Optional[str] = None,
    model: Optional[str] = None,
    top_k: int = 5,
    prefer_same_vendor: bool = True,
    include_rag_sops: bool = False,
) -> Dict[str, Any]:
    """Generate replacement recommendations for a source device."""
    conn = _nc_conn()
    try:
        if device_id:
            src_device = _get_device(conn, device_id)
        elif vendor and model:
            src_device = _get_device_by_model(conn, vendor, model)
        else:
            return {"error": "Provide --device-id or both --vendor and --model"}

        if not src_device:
            return {"error": "Source device not found"}

        src_profile = _get_source_profile(conn, src_device["vendor"], src_device["model"])
        if not src_profile:
            return {"error": f"Hardware profile not found for {src_device['vendor']} {src_device['model']}"}

        candidates = _get_candidates(
            conn, src_device["device_type"],
            (src_device["vendor"], src_device["model"]),
        )

        scored: List[Dict[str, Any]] = []
        for cand in candidates:
            hw = _score_hardware_parity(src_profile, cand)
            feat = _score_feature_parity(src_profile, cand)
            cost = _score_cost(src_profile, cand)
            vendor_score = _score_vendor_strategy(src_profile, cand, prefer_same_vendor)
            eol_fresh = _score_eol_freshness(cand)

            # Composite: weighted average
            composite = round(
                hw * 0.25 +
                feat * 0.25 +
                cost * 0.20 +
                vendor_score * 0.15 +
                eol_fresh * 0.15,
                4,
            )

            scored.append({
                "rank": 0,
                "vendor": cand["vendor"],
                "model": cand["model"],
                "device_type": cand["device_type"],
                "form_factor": cand["form_factor"],
                "rack_units": cand["rack_units"],
                "throughput_gbps": cand["throughput_gbps"],
                "pps_mpps": cand["pps_mpps"],
                "routing_table_size": cand["routing_table_size"],
                "mac_table_size": cand["mac_table_size"],
                "vpn_tunnels": cand["vpn_tunnels"],
                "vlan_count": cand["vlan_count"],
                "replacement_cost": cand["replacement_cost"],
                "eol_date": cand["eol_date"],
                "scores": {
                    "hardware_parity": hw,
                    "feature_parity": feat,
                    "cost": cost,
                    "vendor_strategy": vendor_score,
                    "eol_freshness": eol_fresh,
                },
                "composite_score": composite,
            })

        scored.sort(key=lambda x: x["composite_score"], reverse=True)
        for i, s in enumerate(scored[:top_k], 1):
            s["rank"] = i

        top_recs = scored[:top_k]

        # RAG enrichment
        rag_sops: List[Dict[str, Any]] = []
        if include_rag_sops and top_recs:
            rag_sops = _rag_sops_for_migration(
                src_device["vendor"], src_device["model"],
                top_recs[0]["vendor"], top_recs[0]["model"],
            )

        return {
            "classification": "CUI // SP-CTI",
            "source_device": {
                "id": src_device["id"],
                "label": src_device["label"] if "label" in src_device.keys() else "",
                "vendor": src_device["vendor"],
                "model": src_device["model"],
                "device_type": src_device["device_type"],
                "site": src_device["site"],
                "rack_location": src_device["rack_location"],
                "eol_date": src_device["eol_date"],
                "eos_date": src_device["eos_date"],
                "replacement_cost": src_device["replacement_cost"],
                "criticality_score": src_device["criticality_score"],
                "downstream_count": src_device["downstream_count"],
            },
            "recommendations": top_recs,
            "rag_sops": rag_sops,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Replacement Recommender")
    parser.add_argument("--device-id", type=str, help="Source device ID")
    parser.add_argument("--vendor", type=str, help="Source device vendor")
    parser.add_argument("--model", type=str, help="Source device model")
    parser.add_argument("--top-k", type=int, default=5, help="Number of recommendations")
    parser.add_argument("--prefer-same-vendor", action="store_true", default=True, help="Favor same-vendor replacements")
    parser.add_argument("--prefer-multi-vendor", action="store_true", help="Favor multi-vendor replacements")
    parser.add_argument("--rag-sops", action="store_true", help="Retrieve RAG SOPs for top recommendation")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = recommend(
        device_id=args.device_id,
        vendor=args.vendor,
        model=args.model,
        top_k=args.top_k,
        prefer_same_vendor=not args.prefer_multi_vendor,
        include_rag_sops=args.rag_sops,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"Error: {result['error']}")
            sys.exit(1)
        src = result["source_device"]
        print(f"Source: {src['vendor']} {src['model']} ({src['device_type']}) at {src['site']}")
        print(f"Recommendations (top {args.top_k}):")
        for rec in result["recommendations"]:
            print(
                f"  #{rec['rank']} {rec['vendor']} {rec['model']} "
                f"score={rec['composite_score']} cost=${rec['replacement_cost']:,.0f} "
                f"hw={rec['scores']['hardware_parity']} feat={rec['scores']['feature_parity']}"
            )
        if result.get("rag_sops"):
            print(f"RAG SOPs: {len(result['rag_sops'])} chunks retrieved")


if __name__ == "__main__":
    main()
