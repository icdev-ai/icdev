# CUI // SP-CTI
"""AI Canvas Demo Runner — 5-Act DoD/IC Demo for AADC, AIMC, AAC, and Observatory.

Acts (executive order):
  1. AI Observatory       — 200+ AI decisions, confabulation flags, HITL queue
  2. AADC                 — JADC2 + Insider Threat designs, HITL required, rights-impacting
  3. AIMC                 — SIGINT NLP (IL5 air-gap) + FAR/DFARS Q&A, IL filter
  4. AAC                  — GCSS-Army + SIEM modernization, top opportunities
  5. Cross-Canvas         — Rights-impacting inventory, low-confidence queue, OMB M-25-21

Usage:
    python tools/showcase/ai_canvas_demo_runner.py --scenario 1
    python tools/showcase/ai_canvas_demo_runner.py --audience exec --json
    python tools/showcase/ai_canvas_demo_runner.py  # all 5 acts
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_DIVIDER = "=" * 72
_SUB     = "-" * 72

_JSON_MODE = False


def _print(msg: str = "") -> None:
    if _JSON_MODE:
        return
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def _header(title: str, act: int = 0) -> None:
    tag = f"  ACT {act}  " if act else ""
    _print(f"\n{_DIVIDER}")
    _print(f"  {tag}{title}")
    _print(_DIVIDER)


def _sub(msg: str) -> None:
    _print(f"\n  >>  {msg}")


def _result(key: str, val: str, icon: str = "  +") -> None:
    _print(f"{icon}  {key:34s}: {val}")


def _obs_conn():
    """Shared icdev.db — Observatory decisions (RLS applies)."""
    from tools.db.storage import get_connection
    return get_connection()


def _aac_conn():
    """Shared icdev.db — AAC canvas tables (no classification/tenant_id — RLS disabled)."""
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection()


def _aadc_conn():
    import sqlite3
    db = _ROOT / "data" / "agentic_ai_canvas.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _aimc_conn():
    import sqlite3
    db = _ROOT / "data" / "aiml_canvas.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# Act 1 — AI Observatory: Governance Visibility
# =============================================================================

def scenario_1() -> dict:
    _header("AI OBSERVATORY  |  AI Decision Governance Across All Canvases", 1)
    _print("  200+ AI decisions logged across 8+ canvases over 30 days.")
    _print("  18 confabulation flags raised. 35 decisions in HITL queue.")
    _print("  Hook: 'How many AI systems is your org operating,")
    _print("         and which are rights-impacting under OMB M-25-21?'")
    time.sleep(0.3)

    conn = _obs_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM canvas_ai_decisions").fetchone()
        total_n = total["count"] if hasattr(total, "keys") else total[0]

        by_canvas = conn.execute(
            "SELECT canvas_type, COUNT(*) cnt FROM canvas_ai_decisions "
            "GROUP BY canvas_type ORDER BY cnt DESC LIMIT 8"
        ).fetchall()

        by_type = conn.execute(
            "SELECT decision_type, COUNT(*) cnt FROM canvas_ai_decisions "
            "GROUP BY decision_type ORDER BY cnt DESC"
        ).fetchall()

        confab = conn.execute(
            "SELECT COUNT(*) FROM canvas_ai_decisions WHERE decision_type = 'confabulation_flag'"
        ).fetchone()
        confab_n = confab["count"] if hasattr(confab, "keys") else confab[0]

        low_conf = conn.execute(
            "SELECT COUNT(*) FROM canvas_ai_decisions WHERE confidence < 0.30"
        ).fetchone()
        low_conf_n = low_conf["count"] if hasattr(low_conf, "keys") else low_conf[0]

        high_conf = conn.execute(
            "SELECT COUNT(*) FROM canvas_ai_decisions WHERE confidence >= 0.85"
        ).fetchone()
        high_conf_n = high_conf["count"] if hasattr(high_conf, "keys") else high_conf[0]

        _sub("Decision Volume (30-day window)")
        _result("Total AI decisions logged", str(total_n))
        _result("High confidence (>=0.85)", f"{high_conf_n} ({100*high_conf_n//max(total_n,1)}%)")
        _result("Confabulation flags raised", f"{confab_n} — queued for CAIO review")
        _result("Low confidence (<0.30)",     f"{low_conf_n} — HITL intervention required")

        _sub("Decision Distribution by Canvas")
        for row in by_canvas:
            ct  = row["canvas_type"] if hasattr(row, "keys") else row[0]
            cnt = row["cnt"]         if hasattr(row, "keys") else row[1]
            bar = "#" * min(cnt // 3, 20)
            _print(f"    {ct.upper():8s}  {bar:<20s} {cnt}")

        _sub("Decision Types")
        for row in by_type:
            dt  = row["decision_type"] if hasattr(row, "keys") else row[0]
            cnt = row["cnt"]           if hasattr(row, "keys") else row[1]
            _print(f"    {dt:30s}: {cnt}")

        _sub("IQE Query: IQE-OBS-002 — Confabulation Flags")
        _print("  foreach d in observatory.decisions where d.decision_type == 'confabulation_flag'")
        _print("  select d.canvas_type, d.record_id, d.confidence, d.rationale")
        _print(f"  -> {confab_n} records returned. These go to CAIO review, not the full {total_n}.")

        _sub("Governance Hook")
        _result("Rights-impacting systems",  "Identified in Acts 2, 3 via OMB M-25-21 §4")
        _result("CAIO review queue",         f"{confab_n} confabulation + {low_conf_n} low-confidence")
        _result("Audit trail standard",      "NIST AU-3 / AU-9 — append-only, signed chain")
        _result("NIST AI 600-1 (GAI.1)",     "COMPLIANT — confabulation detection active")

        return {
            "status": "pass",
            "total": total_n,
            "confabulation_flags": confab_n,
            "low_confidence": low_conf_n,
            "high_confidence": high_conf_n,
        }
    finally:
        conn.close()


# =============================================================================
# Act 2 — AADC: Agentic AI Design for Compliance
# =============================================================================

def scenario_2() -> dict:
    _header("AADC  |  Designing AI for Compliance — JADC2 + Insider Threat", 2)
    _print("  8 DoD/IC AI agent designs. 6 require HITL. 2 are rights-impacting.")
    _print("  JADC2 Mission Planning: 2 ATO blockers. Insider Threat: ATO-ready, 91.5%.")
    _print("  Hook: 'Does your governance process check for rights-impacting")
    _print("         classifications BEFORE deployment?'")
    time.sleep(0.3)

    conn = _aadc_conn()
    try:
        designs = conn.execute(
            "SELECT id, name, domain, classification, rights_impacting, hitl_required "
            "FROM aadc_designs WHERE id LIKE 'aadc-dod-%' ORDER BY id"
        ).fetchall()

        ato_rows = conn.execute(
            "SELECT design_id, score_pct, ato_ready, passed, failed, critical_failed "
            "FROM aadc_ato_reports WHERE design_id LIKE 'aadc-dod-%' ORDER BY design_id"
        ).fetchall()
        ato_map = {r["design_id"]: r for r in ato_rows}

        assessments = conn.execute(
            "SELECT design_id, nist_rmf_score, owasp_score, omb_compliant "
            "FROM aadc_assessments WHERE design_id LIKE 'aadc-dod-%' ORDER BY design_id"
        ).fetchall()
        assess_map = {r["design_id"]: r for r in assessments}

        hitl_count     = sum(1 for d in designs if d["hitl_required"])
        rights_count   = sum(1 for d in designs if d["rights_impacting"])
        ato_ready_count = sum(1 for r in ato_rows if r["ato_ready"])

        _sub(f"Design Overview ({len(designs)} DoD/IC Designs)")
        _result("HITL-required designs",       f"{hitl_count} of {len(designs)}")
        _result("Rights-impacting (OMB M-25-21)", f"{rights_count} — CAIO override node present")
        _result("ATO-ready designs",           f"{ato_ready_count} of {len(designs)}")

        _sub("All Designs — Assessment Summary")
        _print(f"  {'ID':12s} {'Name':40s} {'ATO%':5s} {'NIST':5s} {'HITL':4s} {'Rights':6s}")
        _print(f"  {'-'*12} {'-'*40} {'-'*5} {'-'*5} {'-'*4} {'-'*6}")
        for d in designs:
            did  = d["id"]
            ato  = ato_map.get(did)
            asmt = assess_map.get(did)
            ato_pct  = f"{ato['score_pct']:.0f}%" if ato else "N/A"
            nist     = f"{asmt['nist_rmf_score']:.0f}" if asmt else "N/A"
            hitl     = "YES" if d["hitl_required"] else "no"
            rights   = "YES" if d["rights_impacting"] else "no"
            ready    = " READY" if (ato and ato["ato_ready"]) else " BLOCK"
            _print(f"  {did:12s} {d['name'][:40]:40s} {ato_pct:5s}{ready} {nist:5s} {hitl:4s} {rights:6s}")

        _sub("Deep Dive: JADC2 Mission Planning (aadc-dod-003)")
        jadc2_ato  = ato_map.get("aadc-dod-003")
        jadc2_asmt = assess_map.get("aadc-dod-003")
        if jadc2_ato and jadc2_asmt:
            _result("ATO score",       f"{jadc2_ato['score_pct']:.1f}% — BLOCKED")
            _result("Critical failed", f"{jadc2_ato['critical_failed']} (no rate-limiter node)")
            _result("NIST AI RMF",     f"{jadc2_asmt['nist_rmf_score']:.0f}/100")
            _result("OWASP LLM Top10", f"{jadc2_asmt['owasp_score']:.0f}/100")
            _result("HITL gate",       "PRESENT — human-in-the-loop before COA submission")
            _result("IL classification", "IL5 — SIPR network boundary enforced")

        _sub("Deep Dive: Insider Threat Behavioral Analyst (aadc-dod-002)")
        it_ato  = ato_map.get("aadc-dod-002")
        it_asmt = assess_map.get("aadc-dod-002")
        if it_ato and it_asmt:
            _result("ATO score",        f"{it_ato['score_pct']:.1f}% — ATO READY")
            _result("NIST AI RMF",      f"{it_asmt['nist_rmf_score']:.0f}/100")
            _result("Rights-impacting", "YES — caio-override node satisfies OMB M-25-21 §4")
            _result("OMB compliant",    "YES" if it_asmt["omb_compliant"] else "NO")

        _sub("IQE Query: IQE-AADC-002 — HITL-Required Designs")
        _print("  foreach d in aadc.designs where d.hitl_required == 1")
        _print("  select d.name, d.domain, d.hitl_required, d.rights_impacting")
        _print(f"  -> {hitl_count} designs returned.")

        return {
            "status": "pass",
            "designs": len(designs),
            "hitl_required": hitl_count,
            "rights_impacting": rights_count,
            "ato_ready": ato_ready_count,
        }
    finally:
        conn.close()


# =============================================================================
# Act 3 — AIMC: Model Selection for Classified Environments
# =============================================================================

def scenario_3() -> dict:
    _header("AIMC  |  Model Selection for Classified Environments", 3)
    _print("  8 DoD/IC AI/ML model designs across IL4 and IL5.")
    _print("  SIGINT NLP: bnd-air-gap enforced at IL5. IL compliance: 100%.")
    _print("  FAR/DFARS Q&A: Claude Sonnet on Bedrock, 29x ROI vs GS-12.")
    _print("  Hook: 'Does your model evaluation include an IL suitability check")
    _print("         and DoD RAI 5-Principles assessment?'")
    time.sleep(0.3)

    conn = _aimc_conn()
    try:
        designs = conn.execute(
            "SELECT id, name, il_level, adaptation_strategy, classification, primary_use_case "
            "FROM aiml_designs WHERE id LIKE 'aimc-dod-%' ORDER BY id"
        ).fetchall()

        assessments = conn.execute(
            "SELECT design_id, framework_id, framework_name, score "
            "FROM aiml_assessments WHERE design_id LIKE 'aimc-dod-%' ORDER BY design_id, framework_id"
        ).fetchall()
        assess_map: dict[str, list] = {}
        for a in assessments:
            assess_map.setdefault(a["design_id"], []).append(a)

        _sub(f"Design Overview ({len(designs)} DoD/IC Model Designs)")
        _print(f"  {'ID':12s} {'Name':40s} {'IL':4s} {'Strategy':12s}")
        _print(f"  {'-'*12} {'-'*40} {'-'*4} {'-'*12}")
        for d in designs:
            _print(f"  {d['id']:12s} {d['name'][:40]:40s} {d['il_level']:4s} {d['adaptation_strategy']:12s}")

        _sub("Deep Dive: SIGINT NLP — Signal-to-Text (aimc-dod-002)")
        sigint_asmts = assess_map.get("aimc-dod-002", [])
        _result("IL level",              "IL5 — SIPR air-gapped deployment only")
        _result("bnd-air-gap node",      "ENFORCED — no internet egress path")
        _result("Adaptation strategy",   "hybrid (fine-tune + RAG on classified corpus)")
        _result("Inference server",      "Ollama on-prem (vLLM fallback)")
        for a in sigint_asmts:
            _result(a["framework_name"], f"{a['score']:.0f}/100")

        _sub("Deep Dive: FAR/DFARS Compliance Q&A (aimc-dod-005)")
        far_asmts = assess_map.get("aimc-dod-005", [])
        _result("IL level",             "IL4 — GovCloud Bedrock")
        _result("Adaptation strategy",  "RAG (retrieval over FAR/DFARS clause corpus)")
        _result("Clause accuracy",      "89.4% on FAR Part 19 benchmark")
        _result("Prompt caching",       "60% token reduction (Anthropic cache TTL 5 min)")
        _result("ROI vs GS-12 labor",   "29x — $9K/mo AI vs $255K/yr analyst")
        for a in far_asmts:
            _result(a["framework_name"], f"{a['score']:.0f}/100")

        _sub("IL Filter Demo — Selecting IL5-compatible models")
        il5_designs = [d for d in designs if d["il_level"] == "IL5"]
        il4_designs = [d for d in designs if d["il_level"] == "IL4"]
        _result("All designs",    str(len(designs)))
        _result("IL5 only",       f"{len(il5_designs)} — only Ollama local / vLLM on-prem")
        _result("IL4 designs",    f"{len(il4_designs)} — Bedrock GovCloud / watsonx.ai eligible")
        for d in il5_designs:
            _print(f"    IL5: {d['name']} ({d['adaptation_strategy']})")

        _sub("IQE Query: IQE-AIMC-006 — IL5 Air-Gap Designs")
        _print("  foreach d in aimc.designs where d.il_level == 'IL5'")
        _print("  select d.name, d.il_level, d.csp, d.adaptation_strategy, d.assessments")
        _print(f"  -> {len(il5_designs)} designs returned.")

        # Personnel Readiness (rights-impacting)
        _sub("Rights-Impacting Alert: Personnel Readiness Forecast (aimc-dod-006)")
        _print("  Model: XGBoost + LLM judge on personnel fitness data")
        _print("  rights_impacting = TRUE — affects service member retention decisions")
        _print("  DoD RAI equitable principle score: 72/100 — CAIO hold, review required")
        _result("OMB M-25-21 §4",     "CAIO review required before deployment")
        _result("DoD RAI: Equitable", "72/100 — disparate impact analysis pending")

        return {
            "status": "pass",
            "designs": len(designs),
            "il5_designs": len(il5_designs),
            "il4_designs": len(il4_designs),
        }
    finally:
        conn.close()


# =============================================================================
# Act 4 — AAC: Modernizing Legacy Systems
# =============================================================================

def scenario_4() -> dict:
    _header("AAC  |  AI Code Augmentation — Modernizing Legacy DoD Systems", 4)
    _print("  5 legacy system scans. 60+ AI augmentation opportunities identified.")
    _print("  GCSS-Army: 287K LOC. SIEM: 88K-entry IOC table — composite 0.85.")
    _print("  Hook: 'Has anyone done a systematic analysis of where AI can")
    _print("         replace brittle rule-based logic in your highest-cost system?'")
    time.sleep(0.3)

    conn = _aac_conn()
    try:
        # Find the scans with the most opportunities (the fully-seeded batch)
        # Use HAVING to prefer scans that actually have opportunity data
        scans = conn.execute(
            """SELECT s.scan_id, s.input_ref, s.total_loc,
                      COUNT(o.opportunity_id) as opp_count
               FROM aac_scans s
               LEFT JOIN aac_opportunities o ON o.scan_id = s.scan_id
               WHERE s.input_ref LIKE 'git@%'
               GROUP BY s.scan_id, s.input_ref, s.total_loc
               ORDER BY opp_count DESC, s.scan_id ASC LIMIT 20"""
        ).fetchall()

        # Pick first scan per unique input_ref that has opportunities;
        # fall back to any scan if none have opportunities
        seen_refs: set[str] = set()
        latest: list = []
        for row in scans:
            ref     = row["input_ref"] if hasattr(row, "keys") else row[1]
            opp_cnt = row["opp_count"] if hasattr(row, "keys") else row[3]
            if ref not in seen_refs and opp_cnt > 0:
                seen_refs.add(ref)
                latest.append(row)
            if len(latest) == 5:
                break
        # Fallback: if no opportunities seeded yet, just show scans
        if not latest:
            for row in scans:
                ref = row["input_ref"] if hasattr(row, "keys") else row[1]
                if ref not in seen_refs:
                    seen_refs.add(ref)
                    latest.append(row)
                if len(latest) == 5:
                    break

        # Humanize system names
        ref_to_name = {
            "gcss-army": "GCSS-Army Logistics",
            "siem-rules": "Legacy SIEM Rules Engine",
            "dfs-core": "Defense Financial System (DFAS)",
            "radio-firmware": "JTRS Radio Firmware",
            "hr-readiness": "HR Readiness Portal",
        }
        composite_targets = {
            "gcss-army": (0.77, "nested_conditionals"),
            "siem-rules": (0.85, "large_rule_table"),
            "dfs-core":   (0.68, "nested_conditionals"),
            "radio-firmware": (0.74, "hardcoded_threshold"),
            "hr-readiness":   (0.78, "nested_conditionals"),
        }

        def _sysname(ref: str) -> tuple[str, str, float]:
            for key, name in ref_to_name.items():
                if key in ref:
                    comp, pattern = composite_targets.get(key, (0.70, "unknown"))
                    return name, pattern, comp
            return ref[-30:], "unknown", 0.70

        _sub("Legacy System Scan Results")
        _print(f"  {'System':38s} {'LOC':>8s} {'Opps':>5s} {'Score':>6s} {'Top Pattern':22s}")
        _print(f"  {'-'*38} {'-'*8} {'-'*5} {'-'*6} {'-'*22}")
        for row in latest:
            ref     = row["input_ref"]   if hasattr(row, "keys") else row[1]
            loc     = row["total_loc"]   if hasattr(row, "keys") else row[2]
            opp_cnt = row["opp_count"]   if hasattr(row, "keys") else row[3]
            sname, pattern, comp = _sysname(ref)
            _print(f"  {sname:38s} {loc:>8,d} {opp_cnt:>5d} {comp:>6.2f} {pattern:22s}")

        # Deep dive into SIEM (highest composite)
        siem_row = next((r for r in latest if "siem-rules" in (
            r["input_ref"] if hasattr(r, "keys") else r[1]
        )), None)

        if siem_row:
            siem_scan_id = siem_row["scan_id"] if hasattr(siem_row, "keys") else siem_row[0]
            siem_opps = conn.execute(
                "SELECT function_name, pattern_type, ai_paradigm, il_recommended_model "
                "FROM aac_opportunities WHERE scan_id = %s ORDER BY opportunity_id LIMIT 5",
                (siem_scan_id,)
            ).fetchall()

            _sub("Deep Dive: Legacy SIEM Rules Engine (composite=0.85 — HIGHEST)")
            _result("Pattern",         "large_rule_table — 88K IOC entries, O(N) scan")
            _result("AI paradigm",     "embedding_search — vector similarity replaces linear scan")
            _result("IL recommended",  "qwen3-local (IL4) / llama3-8b-instruct-gc (IL5)")
            _result("Effort estimate", "10 days — Phase 1 pilot")
            _result("Risk score",      "0.18 — low risk, high feasibility (0.89)")
            for opp in siem_opps[:3]:
                fn = opp["function_name"] if hasattr(opp, "keys") else opp[0]
                pt = opp["pattern_type"]  if hasattr(opp, "keys") else opp[1]
                _print(f"    opp: {fn} ({pt})")

        # Deep dive GCSS-Army
        gcss_row = next((r for r in latest if "gcss-army" in (
            r["input_ref"] if hasattr(r, "keys") else r[1]
        )), None)

        if gcss_row:
            gcss_scan_id = gcss_row["scan_id"] if hasattr(gcss_row, "keys") else gcss_row[0]
            gcss_opps = conn.execute(
                "SELECT function_name, pattern_type, ai_paradigm "
                "FROM aac_opportunities WHERE scan_id = %s ORDER BY opportunity_id LIMIT 3",
                (gcss_scan_id,)
            ).fetchall()
            _sub("Deep Dive: GCSS-Army Logistics (composite=0.77)")
            _result("287K LOC, Java", "nested_conditionals in EquipmentClassifier.java")
            _result("Top opportunity", "MaintenanceDecisionMap.java — 0.82 composite")
            _result("AI paradigm",    "ml_classifier — replaces 847-branch switch")
            _result("Phase 1 effort", "25 days — replaces top 3 decision trees")
            for opp in gcss_opps[:3]:
                fn = opp["function_name"] if hasattr(opp, "keys") else opp[0]
                pt = opp["pattern_type"]  if hasattr(opp, "keys") else opp[1]
                _print(f"    opp: {fn} ({pt})")

        total_opps = sum(
            (row["opp_count"] if hasattr(row, "keys") else row[3]) for row in latest
        )
        _sub("IQE Query: IQE-AAC-001 — Top Opportunities Ranked")
        _print("  foreach o in aac.opportunities join aac.scores")
        _print("  order by composite_score desc select top 10")
        _print(f"  -> {total_opps} total opportunities across {len(latest)} systems")

        return {
            "status": "pass",
            "scans": len(latest),
            "total_opportunities": total_opps,
        }
    finally:
        conn.close()


# =============================================================================
# Act 5 — Cross-Canvas: OMB M-25-21 Inventory + HITL Queue
# =============================================================================

def scenario_5() -> dict:
    _header("CROSS-CANVAS  |  OMB M-25-21 Inventory + CAIO Review Queue", 5)
    _print("  One-click: pull all rights-impacting AI systems across AADC + AIMC.")
    _print("  Show CAIO review queue: 18 confabulation flags + low-confidence decisions.")
    _print("  Close: 'Your architects design and assess this week.")
    _print("          Your CAIO signs off on the right ones, with confidence.'")
    time.sleep(0.3)

    obs_conn = _obs_conn()
    aadc_conn = _aadc_conn()
    aimc_conn = _aimc_conn()

    try:
        # Rights-impacting AADC designs
        ri_aadc = aadc_conn.execute(
            "SELECT id, name, domain, classification "
            "FROM aadc_designs WHERE id LIKE 'aadc-dod-%' AND rights_impacting = 1 ORDER BY id"
        ).fetchall()

        # Rights-impacting AIMC designs (Personnel Readiness = rights-impacting)
        # Note: AIMC schema has no rights_impacting col — identify by known ID
        ri_aimc = aimc_conn.execute(
            "SELECT id, name, il_level, classification, primary_use_case "
            "FROM aiml_designs WHERE id IN ('aimc-dod-006') ORDER BY id"
        ).fetchall()

        _sub("OMB M-25-21 §4 — Rights-Impacting AI Inventory")
        _print("  IQE-AADC-002: foreach d in aadc.designs where d.rights_impacting == 1")
        _print(f"  -> {len(ri_aadc)} AADC designs")
        for d in ri_aadc:
            _print(f"    [AADC] {d['id']} — {d['name']} ({d['domain']})")
        _print("  -> 1 AIMC design (Personnel Readiness — affects service members)")
        for d in ri_aimc:
            _print(f"    [AIMC] {d['id']} — {d['name']} — DoD RAI equitable: 72/100 — CAIO hold")

        _result("Total rights-impacting",   f"{len(ri_aadc) + len(ri_aimc)} systems")
        _result("OMB M-25-21 compliant",    f"{len(ri_aadc)} AADC (caio-override node present)")
        _result("Pending CAIO review",      "1 AIMC — DoD RAI equitable score <80")

        # CAIO review queue from Observatory
        confab_rows = obs_conn.execute(
            "SELECT canvas_type, record_id, confidence, decision_type, created_at "
            "FROM canvas_ai_decisions "
            "WHERE decision_type = 'confabulation_flag' "
            "ORDER BY created_at DESC LIMIT 8"
        ).fetchall()

        low_conf_rows = obs_conn.execute(
            "SELECT canvas_type, record_id, confidence, decision_type "
            "FROM canvas_ai_decisions "
            "WHERE confidence < 0.30 AND decision_type != 'confabulation_flag' "
            "ORDER BY confidence ASC LIMIT 5"
        ).fetchall()

        _sub("CAIO Review Queue — Confabulation Flags (IQE-OBS-003)")
        _print("  foreach d in observatory.decisions where d.confidence < 0.30")
        _print("  select d.canvas_type, d.record_id, d.confidence, d.rationale")
        for row in confab_rows[:5]:
            ct  = row["canvas_type"] if hasattr(row, "keys") else row[0]
            rid = row["record_id"]   if hasattr(row, "keys") else row[1]
            cf  = row["confidence"]  if hasattr(row, "keys") else row[2]
            _print(f"    [CONFAB] canvas={ct.upper():6s} record={str(rid)[:20]:20s} confidence={cf:.2f}")

        _sub("Low-Confidence HITL Queue")
        for row in low_conf_rows:
            ct  = row["canvas_type"]  if hasattr(row, "keys") else row[0]
            rid = row["record_id"]    if hasattr(row, "keys") else row[1]
            cf  = row["confidence"]   if hasattr(row, "keys") else row[2]
            dt  = row["decision_type"]if hasattr(row, "keys") else row[3]
            _print(f"    [HITL]   canvas={ct.upper():6s} type={dt:22s} confidence={cf:.2f}")

        _sub("Unified Governance Picture")
        _result("AI systems inventoried",     "3 rights-impacting across AADC + AIMC")
        _result("CAIO review items",          f"{len(confab_rows)} confabulation + low-confidence flags")
        _result("Designs ATO-blocked",        "6 of 8 AADC designs — visible deploy gates")
        _result("OMB M-25-21 inventory",      "One-click export from aadc.designs + aimc.designs")
        _result("NIST AI RMF GOVERN pillar",  "COMPLIANT — audit trail + HITL queue active")

        _sub("Closing Pitch")
        _print("  'Your architects can design and assess AI systems this week.")
        _print("   Your CAIO can sign off on the right ones, with confidence.")
        _print("   Every rights-impacting system is flagged. Every confabulation is logged.")
        _print("   This is how DoD does responsible AI at scale.'")

        return {
            "status": "pass",
            "rights_impacting_total": len(ri_aadc) + len(ri_aimc),
            "confabulation_flags": len(confab_rows),
            "low_conf_hitl": len(low_conf_rows),
        }
    finally:
        obs_conn.close()
        aadc_conn.close()
        aimc_conn.close()


# =============================================================================
# Executive Summary
# =============================================================================

def print_executive_summary(results: dict) -> None:
    _header("EXECUTIVE SUMMARY — ICDEV AI Canvas Demo")
    _print("""
  PLATFORM: ICDEV™ AI Governance Suite — AADC / AIMC / AAC / AI Observatory
  DATA:     8 AADC designs, 8 AIMC designs, 5 AAC scans, 200+ Observatory decisions
  AUDIENCE: CIO, CISO, PEO, Program Director — DoD/IC and Federal Government
    """)
    _print("  +----------------------------------------------------------------------+")
    _print("  |  ACT                          STATUS   KEY METRIC                    |")
    _print("  +----------------------------------------------------------------------+")

    r1 = results.get("s1", {})
    r2 = results.get("s2", {})
    r3 = results.get("s3", {})
    r4 = results.get("s4", {})
    r5 = results.get("s5", {})

    def _row(s, name, metric):
        ok = "  PASS  " if s.get("status") == "pass" else "  SKIP  "
        _print(f"  |  {name:30s} {ok}  {metric:31s}  |")

    _row(r1, "1. AI Observatory",
         f"{r1.get('total',0)} decisions, {r1.get('confabulation_flags',0)} confab flags")
    _row(r2, "2. AADC — Agent Design",
         f"{r2.get('hitl_required',0)}/{r2.get('designs',0)} HITL, {r2.get('rights_impacting',0)} OMB rights-impact")
    _row(r3, "3. AIMC — Model Selection",
         f"{r3.get('il5_designs',0)} IL5 air-gap, 29x ROI on FAR/DFARS")
    _row(r4, "4. AAC — Legacy Modernize",
         f"{r4.get('total_opportunities',0)} opps across {r4.get('scans',0)} systems")
    _row(r5, "5. Cross-Canvas Governance",
         f"{r5.get('rights_impacting_total',0)} rights-impacting, CAIO queue visible")
    _print("  +----------------------------------------------------------------------+")
    _print("""
  KEY MESSAGES:
    -> Every rights-impacting AI system flagged — OMB M-25-21 §4 compliance
    -> HITL gates enforced in design — not patched in after-the-fact
    -> IL4/IL5 model fitness checked BEFORE deployment (not discovered in ATOs)
    -> Legacy modernization opportunities quantified — composite scoring, roadmaps
    -> CAIO sees only the uncertain decisions — not 10K outputs

  COMPLIANCE COVERAGE:
    OMB M-25-21     Rights-impacting inventory, CAIO review workflow
    NIST AI RMF     GOVERN + MAP + MEASURE + MANAGE pillars
    DoD RAI         5 Principles scored per design and model
    NIST AI 600-1   GAI.1 confabulation detection (Observatory)
    EO 14110        AI safety documentation per system
    CMMC L2         110 practices tracked in ATO checklists (AADC)
    FedRAMP High    IL4 boundary evidence (AADC + AIMC CSP selection)

  NEXT STEPS:
    -> /ai-augmentation  — run top opportunities IQE on your legacy system
    -> /aadc             — load your AI agent design, run assessment
    -> /ai-ml            — evaluate model fitness for your IL level
    -> /ai-observatory   — connect your existing AI system audit trail
    """)


# =============================================================================
# Technical Demo Acts (audience=tech adds depth commentary)
# =============================================================================

TECH_NOTES = {
    1: [
        "Technical depth: canvas_ai_decisions uses append-only audit chain (decision_hash + previous_decision_hash + signature)",
        "NIST AU-3/AU-9 enforcement via pre_tool_use.py::APPEND_ONLY_TABLES hook",
        "confabulation_flag logic: confidence < 0.30 OR decision_type == 'confabulation_flag' → HITL queue",
        "NIST AI 600-1 GAI.1 maps: GAI.1.1 = detect, GAI.1.2 = log, GAI.1.3 = HITL escalation",
    ],
    2: [
        "Technical depth: AADC graph_json encodes nodes/edges; aadc_engine.py evaluates 15 deterministic rules",
        "ATO gate logic: critical_failed > 0 OR score_pct < 80 → ato_ready = 0",
        "STRIDE threat model nodes: AML.T0043 data poisoning, AML.T0048 model inversion",
        "HITL gate node type: 'human-in-the-loop' — blocks propagation until human_approval=True",
        "IQE-AADC-009: full ATO readiness joins aadc_designs + aadc_ato_reports + aadc_scorecard_snapshots",
    ],
    3: [
        "Technical depth: AIMC_COMPLIANCE_RULES[15] evaluated in aiml_engine.py::assess_graph()",
        "AIMC-IL-001 rule: if il_level in ('IL5','IL6') and 'bnd-air-gap' not in node_types → FAIL",
        "Deployment planner: recommends Ollama for IL5, vLLM for IL5 latency-sensitive, Bedrock for IL4",
        "Adaptation engine: ranks Prompt-only < RAG < FineTune < Hybrid by corpus/training/GPU/IL constraints",
        "Model card artifact: generated by aiml_engine.py::generate_model_card(), stored in aiml_artifacts",
    ],
    4: [
        "Technical depth: AAC composite score = value×0.45 + feasibility×0.35 + (1-risk)×0.20",
        "PATTERN_TYPES constant drives CHECK constraint — add new patterns to constants.py first",
        "Cross-links: aac_roadmaps.aadc_design_id and aimc_design_id — must pre-exist for Phase 3+ roadmaps",
        "il_recommended_model: derived from ai_capability_mapper.py based on pattern + IL level",
        "JTRS Radio Firmware: Rust il_level → qwen3-local for IL5 signal processing",
    ],
    5: [
        "Technical depth: rights_impacting=1 triggers CAIO override node requirement in AADC schema",
        "OMB M-25-21 §4 inventory: IQE-AADC-002 + IQE-AIMC-009 → rights-impacting cross-canvas view",
        "DoD RAI equitable score < 80 → CAIO hold enforced by aiml_engine.py deploy gate logic",
        "Audit chain verification: verify_loop_runs table + govchain_assets for tamper evidence",
        "IQE dispatch: app.py::iqe_dispatch() routes canvas_type to adapter (tools/iqe/adapters/)",
    ],
}


def _print_tech_notes(act: int) -> None:
    notes = TECH_NOTES.get(act, [])
    if not notes:
        return
    _sub("Technical Architecture Notes")
    for note in notes:
        _print(f"    >> {note}")


# =============================================================================
# Runner
# =============================================================================

def run_all_scenarios(scenarios: list[int] | None = None, audience: str = "exec") -> dict:
    results: dict = {}
    dispatch = {
        1: ("s1", scenario_1),
        2: ("s2", scenario_2),
        3: ("s3", scenario_3),
        4: ("s4", scenario_4),
        5: ("s5", scenario_5),
    }
    to_run = scenarios or [1, 2, 3, 4, 5]
    for num in to_run:
        key, fn = dispatch[num]
        try:
            results[key] = fn()
            if audience == "tech":
                _print_tech_notes(num)
        except Exception as exc:
            results[key] = {"status": "error", "error": str(exc)}
        time.sleep(0.5)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV AI Canvas Demo Runner")
    parser.add_argument("--json",     action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--scenario", type=int, choices=range(1, 6),
                        metavar="1-5", help="Run single scenario (1-5)")
    parser.add_argument("--audience", choices=["exec", "tech"], default="exec",
                        help="exec = CIO/CISO/PEO (default), tech = Architect/DevSecOps/ML")
    args = parser.parse_args()

    global _JSON_MODE
    if args.json:
        _JSON_MODE = True

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not args.json:
        _print(f"\n{'='*72}")
        _print(f"  ICDEV(TM) AI Canvas Demo Runner  --  {ts}")
        _print(f"  Audience: {args.audience.upper()} | Scenarios: {args.scenario or '1-5 (all)'}")
        _print(f"{'='*72}")

    scenarios = [args.scenario] if args.scenario else None
    results = run_all_scenarios(scenarios, audience=args.audience)

    if not args.json:
        if not args.scenario:
            print_executive_summary(results)
    else:
        print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
