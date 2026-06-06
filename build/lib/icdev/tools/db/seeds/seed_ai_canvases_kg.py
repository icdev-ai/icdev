# CUI // SP-CTI
"""Seed DoD/IC AI Governance Knowledge Graph — 38 nodes + 30+ edges.

Populates: kg_graphs, kg_nodes, kg_edges in shared icdev.db

Node breakdown:
  16 organization nodes: DoD, ODNI, NSA, DISA, DARPA, Army G-6, Navy N2/N6,
                          USAF A2, JAIC/CDAO, CISA, NIST, AWS GovCloud,
                          Azure Gov, IBM GovCloud, OCI GovCloud, FedRAMP PMO
  10 program nodes: JADC2, Maven Smart System, Replicator, GCSS-Army, ADVANA,
                    OPAL, SkyBorg, JWCC, AI Next (DARPA), NSA CCC
  12 standard nodes: NIST AI RMF, NIST AI 600-1, CMMC L2, FedRAMP High,
                     DoD RAI 5 Principles, EO 14110, OMB M-25-21,
                     NIST SP 800-53 Rev 5, IL4, IL5, IL6, MITRE ATLAS v5.4

Run:
    python tools/db/seeds/seed_ai_canvases_kg.py
    python tools/db/seeds/seed_ai_canvases_kg.py --reset
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


GRAPH_ID = "kg-dod-ai-governance-001"
PROJECT_ID = "icdev-dod-ic-demo"

# ── Nodes ─────────────────────────────────────────────────────────────────────
# Format: (id, label, entity_type, properties)

NODES: list[tuple[str, str, str, dict]] = [
    # Organizations (16)
    ("org-dod",         "Department of Defense",                      "organization", {"abbrev": "DoD",     "gov": True}),
    ("org-odni",        "Office of the Director of National Intelligence", "organization", {"abbrev": "ODNI",    "ic": True}),
    ("org-nsa",         "National Security Agency",                   "organization", {"abbrev": "NSA",     "ic": True}),
    ("org-disa",        "Defense Information Systems Agency",         "organization", {"abbrev": "DISA",    "dod": True}),
    ("org-darpa",       "Defense Advanced Research Projects Agency",  "organization", {"abbrev": "DARPA",   "dod": True}),
    ("org-army-g6",     "Army G-6 (CIO/G-6)",                        "organization", {"abbrev": "Army G-6","dod": True}),
    ("org-navy-n2n6",   "Navy N2/N6 (OPNAV)",                        "organization", {"abbrev": "N2/N6",   "dod": True}),
    ("org-usaf-a2",     "Air Force A2 Intelligence",                  "organization", {"abbrev": "USAF A2", "dod": True}),
    ("org-jaic-cdao",   "CDAO (Chief Digital & AI Office)",           "organization", {"abbrev": "CDAO",    "dod": True}),
    ("org-cisa",        "CISA",                                       "organization", {"abbrev": "CISA",    "dhs": True}),
    ("org-nist",        "NIST",                                       "organization", {"abbrev": "NIST",    "doc": True}),
    ("org-aws-gov",     "AWS GovCloud",                               "organization", {"abbrev": "AWS Gov", "csp": True, "fedramp_high": True}),
    ("org-azure-gov",   "Azure Government",                           "organization", {"abbrev": "Azure Gov","csp": True, "fedramp_high": True}),
    ("org-ibm-gov",     "IBM watsonx.ai GovCloud",                   "organization", {"abbrev": "IBM Gov", "csp": True}),
    ("org-oci-gov",     "Oracle GovCloud",                            "organization", {"abbrev": "OCI Gov", "csp": True}),
    ("org-fedramp-pmo", "FedRAMP PMO",                                "organization", {"abbrev": "FedRAMP PMO", "gsa": True}),

    # Programs (10)
    ("prog-jadc2",      "JADC2 — Joint All-Domain C2",                "system",       {"status": "active", "owner": "DoD"}),
    ("prog-maven",      "Maven Smart System",                         "system",       {"status": "active", "owner": "CDAO"}),
    ("prog-replicator", "Replicator Initiative",                      "system",       {"status": "active", "owner": "DEPSECDEF"}),
    ("prog-gcss",       "GCSS-Army",                                  "system",       {"status": "legacy_modernization", "owner": "Army G-6"}),
    ("prog-advana",     "ADVANA (DoD Data Platform)",                 "system",       {"status": "active", "owner": "CDAO"}),
    ("prog-opal",       "OPAL (ODNI AI Platform)",                    "system",       {"status": "active", "owner": "ODNI"}),
    ("prog-skyborg",    "SkyBorg Autonomous Aircraft",                "system",       {"status": "active", "owner": "USAF A2"}),
    ("prog-jwcc",       "JWCC (Joint Warfighting Cloud Capability)",  "system",       {"status": "active", "owner": "DoD"}),
    ("prog-ai-next",    "AI Next (DARPA)",                            "system",       {"status": "active", "owner": "DARPA"}),
    ("prog-nsa-ccc",    "NSA Cybersecurity Collaboration Center",     "system",       {"status": "active", "owner": "NSA"}),

    # Standards (12)
    ("std-nist-ai-rmf",  "NIST AI RMF 1.0",              "standard", {"doc_id": "NIST.AI.100-1", "year": 2023}),
    ("std-nist-ai-600",  "NIST AI 600-1 (GenAI Profile)", "standard", {"doc_id": "NIST.AI.600-1", "year": 2024}),
    ("std-cmmc-l2",      "CMMC Level 2",                  "standard", {"version": "2.0", "practices": 110}),
    ("std-fedramp-high", "FedRAMP High",                  "standard", {"impact_level": "High", "controls": 421}),
    ("std-dod-rai",      "DoD RAI 5 Principles",          "standard", {"doc": "DoD Responsible AI Guidelines", "principles": 5}),
    ("std-eo-14110",     "Executive Order 14110",         "standard", {"signed": "2023-10-30", "subject": "Safe/Secure/Trustworthy AI"}),
    ("std-omb-m2521",    "OMB M-25-21",                   "standard", {"signed": "2025-04-03", "subject": "AI in Federal Government"}),
    ("std-nist-800-53",  "NIST SP 800-53 Rev 5",          "standard", {"families": 20, "controls": 1000}),
    ("std-il4",          "Impact Level 4 (IL4)",          "standard", {"csp_req": "FedRAMP Moderate+"}),
    ("std-il5",          "Impact Level 5 (IL5)",          "standard", {"csp_req": "DoD authorized only", "air_gap_preferred": True}),
    ("std-il6",          "Impact Level 6 (IL6/SECRET)",   "standard", {"csp_req": "SIPR only", "nsa_type1_enc": True}),
    ("std-atlas",        "MITRE ATLAS v5.4",              "standard", {"ttps": 84, "tactics": 14, "domain": "adversarial_ml"}),
]

# ── Edges ─────────────────────────────────────────────────────────────────────
# Format: (src_id, tgt_id, relationship, weight, properties)

EDGES: list[tuple[str, str, str, float, dict]] = [
    # Org → Org / Program governance
    ("prog-maven",      "org-jaic-cdao",  "OPERATES",      1.0, {"role": "program_owner"}),
    ("prog-jadc2",      "org-dod",        "OPERATES",      0.9, {"sponsor": "Joint Staff J6"}),
    ("prog-gcss",       "org-army-g6",    "OPERATES",      1.0, {}),
    ("prog-advana",     "org-jaic-cdao",  "OPERATES",      1.0, {}),
    ("prog-opal",       "org-odni",       "OPERATES",      1.0, {}),
    ("prog-skyborg",    "org-usaf-a2",    "OPERATES",      1.0, {}),
    ("prog-jwcc",       "org-aws-gov",    "DEPLOYED_ON",   0.9, {"contract": "JWCC"}),
    ("prog-jwcc",       "org-azure-gov",  "DEPLOYED_ON",   0.9, {"contract": "JWCC"}),
    ("prog-jwcc",       "org-ibm-gov",    "DEPLOYED_ON",   0.8, {"contract": "JWCC"}),
    ("prog-jwcc",       "org-oci-gov",    "DEPLOYED_ON",   0.8, {"contract": "JWCC"}),
    ("prog-ai-next",    "org-darpa",      "OPERATES",      1.0, {}),
    ("prog-nsa-ccc",    "org-nsa",        "OPERATES",      1.0, {}),
    ("prog-replicator", "org-dod",        "OPERATES",      0.9, {}),

    # CSP authorization under standards
    ("org-aws-gov",     "std-fedramp-high", "SATISFIES",   1.0, {"since": "2022"}),
    ("org-azure-gov",   "std-fedramp-high", "SATISFIES",   1.0, {"since": "2021"}),
    ("org-ibm-gov",     "std-fedramp-high", "SATISFIES",   0.9, {}),
    ("org-fedramp-pmo", "std-fedramp-high", "OPERATES",    1.0, {}),

    # Standard → Standard lineage
    ("std-nist-ai-rmf", "std-eo-14110",   "IMPLEMENTS",   1.0, {"basis": "EO 14110 Sec 4.1 mandates NIST AI RMF"}),
    ("std-nist-ai-600", "std-nist-ai-rmf","IMPLEMENTS",   1.0, {"extends": "GenAI profile extends AI RMF"}),
    ("std-dod-rai",     "std-nist-ai-rmf","REFERENCES",   0.9, {"alignment": "DoD RAI 5 principles aligned with AI RMF Govern"}),
    ("std-omb-m2521",   "std-eo-14110",   "IMPLEMENTS",   1.0, {"basis": "OMB M-25-21 implements EO 14110"}),
    ("std-cmmc-l2",     "std-nist-800-53","SATISFIES",    0.9, {"mapping": "110 practices ↔ NIST 800-53 controls"}),
    ("std-fedramp-high","std-nist-800-53","SATISFIES",    1.0, {}),
    ("std-il4",         "std-fedramp-high","SATISFIES",   0.9, {}),
    ("std-il5",         "std-il4",        "DEPENDS_ON",   0.8, {"note": "IL5 includes all IL4 requirements"}),
    ("std-il6",         "std-il5",        "DEPENDS_ON",   0.8, {"note": "IL6 includes all IL5 requirements"}),

    # AADC design → programs + standards (domain cross-links)
    ("prog-jadc2",      "std-nist-ai-rmf","SATISFIES",    0.8, {"design": "aadc-dod-003 JADC2 Mission Planning"}),
    ("prog-maven",      "std-dod-rai",    "SATISFIES",    0.9, {"design": "Maven Smart System DoD RAI alignment"}),
    ("prog-maven",      "std-nist-ai-rmf","SATISFIES",    0.9, {"design": "Maven AI RMF governance"}),
    ("prog-gcss",       "std-il4",        "SATISFIES",    0.9, {"design": "aadc-dod-001 GCSS-Army AAC modernization"}),
    ("prog-nsa-ccc",    "std-atlas",      "REFERENCES",   1.0, {"note": "NSA CCC references ATLAS TTPs for threat guidance"}),
    ("std-atlas",       "std-nist-ai-rmf","REFERENCES",   0.8, {"note": "ATLAS adversarial TTPs map to AI RMF MEASURE-2.6"}),
    ("std-omb-m2521",   "std-dod-rai",    "REFERENCES",   0.8, {"note": "OMB M-25-21 §4 rights-impacting aligns with DoD RAI Equitable"}),
    ("prog-opal",       "std-il5",        "SATISFIES",    0.9, {"note": "ODNI OPAL operates at IL5"}),
]


# ── Seed Functions ─────────────────────────────────────────────────────────────

def seed_graph(conn) -> None:
    ts = _now()
    conn.execute(
        "INSERT OR IGNORE INTO kg_graphs "
        "(id, project_id, name, description, metadata, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            GRAPH_ID, PROJECT_ID,
            "DoD/IC AI Governance KG",
            "Organizations, programs, and standards governing AI use across DoD and IC.",
            json.dumps({"domain": "dod_ic_ai_governance", "version": "1.0", "nodes": len(NODES), "edges": len(EDGES)}),
            ts, ts,
        ),
    )
    conn.commit()


def seed_nodes(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO kg_nodes "
        "(id, graph_id, label, entity_type, properties, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    ts = _now()
    count = 0
    for nid, label, etype, props in NODES:
        conn.execute(sql, (nid, GRAPH_ID, label, etype, json.dumps(props), ts))
        count += 1
    conn.commit()
    return count


def seed_edges(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO kg_edges "
        "(id, graph_id, source_id, target_id, relationship, weight, properties, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    ts = _now()
    count = 0
    for src, tgt, rel, weight, props in EDGES:
        eid = f"kge-{src[:12]}-{tgt[:12]}"
        conn.execute(sql, (eid, GRAPH_ID, src, tgt, rel, weight, json.dumps(props), ts))
        count += 1
    conn.commit()
    return count


def main(reset: bool = False) -> None:
    conn = get_connection()
    try:
        if reset:
            conn.execute("DELETE FROM kg_edges WHERE graph_id = ?", (GRAPH_ID,))
            conn.execute("DELETE FROM kg_nodes WHERE graph_id = ?", (GRAPH_ID,))
            conn.execute("DELETE FROM kg_graphs WHERE id = ?", (GRAPH_ID,))
            conn.commit()
            print(f"[reset] Cleared KG graph {GRAPH_ID}.")

        seed_graph(conn)
        n_nodes = seed_nodes(conn)
        n_edges = seed_edges(conn)
        print(f"[KG] Seeded: {n_nodes} nodes, {n_edges} edges (graph: {GRAPH_ID})")
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed DoD/IC AI Governance KG")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.as_json:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(reset=args.reset)
        print(json.dumps({"status": "ok", "graph_id": GRAPH_ID,
                          "nodes": len(NODES), "edges": len(EDGES)}))
    else:
        main(reset=args.reset)
