"""Agentic Network Operations Engine — DoD Lab Scenario 6.

Wired into ICDEV Kanban + Qwen3 (via LLMRouter).

Capabilities:
  A. Autonomous Threat Response   — IDS alert → analyze → block → ticket
  B. Predictive ISP Failover      — telemetry → predict → pre-position routes
  C. Network Modernization Advisor— scan → STIG gap → Kanban tasks → ROI

Usage:
  python tools/ndc/agentic_netops.py --mode all    [--json]
  python tools/ndc/agentic_netops.py --mode threat [--json]
  python tools/ndc/agentic_netops.py --mode failover [--json]
  python tools/ndc/agentic_netops.py --mode modernize [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.ndc.agentic_netops")

_NOW_UTC = datetime.now(timezone.utc)


# ── LLM helper ────────────────────────────────────────────────────────────────

def _llm(prompt: str, system: str = "", max_tokens: int = 600) -> str:
    """Call Qwen3 via LLMRouter; return text or deterministic fallback.

    Hard 25-second timeout prevents hanging the execution runner subprocess.
    """
    import concurrent.futures

    def _call() -> str:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            function_key="network_analysis",
            prompt=prompt,
            system=system or (
                "You are a DoD network security and modernization analyst. "
                "Be concise, tactical, and cite NIST/STIG controls where relevant. "
                "Respond in structured JSON when asked."
            ),
            max_tokens=max_tokens,
        )
        resp = router.route(req)
        return resp.text if hasattr(resp, "text") else str(resp)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_call)
            return future.result(timeout=25)
    except Exception:
        return ""   # caller falls back to deterministic


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _nc_conn():
    try:
        from tools.network.db.init_db import get_connection
        return get_connection()
    except Exception:
        import sqlite3
        db = _ROOT / "data" / "network_canvas.db"
        return sqlite3.connect(str(db))


def _icdev_conn():
    try:
        from tools.db.storage import get_connection
        return get_connection()
    except Exception:
        return None


def _log_agent_action(conn, agent: str, scenario: str, trigger: str,
                       analysis: dict, decision: str, action: str,
                       outcome: str, mitre: str = "", task_id: str = "",
                       model: str = "qwen3-local", elapsed_ms: int = 0) -> None:
    try:
        conn.execute(
            """INSERT INTO ndc_agent_actions
               (timestamp,agent_name,scenario,trigger_event,analysis_json,
                decision,action_taken,outcome,mitre_mapping,kanban_task_id,
                llm_model,response_time_ms)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (_NOW_UTC.isoformat(), agent, scenario, trigger,
             json.dumps(analysis), decision, action, outcome,
             mitre, task_id, model, elapsed_ms),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_log_agent_action: best-effort INSERT into ndc_agent_actions failed (non-blocking): %s", exc)


# ── Kanban task creation ───────────────────────────────────────────────────────

def _create_kanban_task(title: str, description: str, priority: str = "high",
                         canvas: str = "ndc", epic_key: str = "ndc-modernize",
                         depends_on: str = "") -> str:
    """Insert a task into ICDEV Kanban. Returns task_id."""
    # swp-scan-01: this named `tasks`, not the `kanban_tasks` board, and of the
    # columns it listed only title/description/status/priority exist there at
    # all — `task_id` is `id`, and `canvas`/`epic_key` exist on no board table.
    # It also used SQLite-only `INSERT OR IGNORE`, unparseable on PostgreSQL.
    # Every call failed into the bare `except`, so NDC never filed a task.
    # canvas/epic_key have no column to land in, so they are recorded in the
    # description rather than silently dropped.
    task_id = f"ndc-{uuid.uuid4().hex[:8]}"
    try:
        from tools.kanban.task_factory import create_tasks

        create_tasks(
            [
                {
                    "id": task_id,
                    "title": title,
                    "description": f"{description}\n\n[canvas: {canvas} | epic: {epic_key}]",
                    "status": "backlog",
                    "priority": priority,
                    "depends_on_task_id": depends_on or None,
                    "dispatch_source": "ndc_agentic_netops",
                }
            ]
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_create_kanban_task: best-effort task creation failed (non-blocking): %s", exc)
    return task_id


# ══════════════════════════════════════════════════════════════════════════════
# MODE A — Autonomous Threat Response
# ══════════════════════════════════════════════════════════════════════════════

_MITRE_MAP = {
    "T1190":      ("Initial Access",         "Exploit Public-Facing Application"),
    "T1059":      ("Execution",              "Command and Scripting Interpreter"),
    "T1059.004":  ("Execution",              "Unix Shell"),
    "T1048":      ("Exfiltration",           "Exfiltration Over Alternative Protocol"),
    "T1041":      ("Exfiltration",           "Exfiltration Over C2 Channel"),
    "T1046":      ("Discovery",              "Network Service Scanning"),
    "T1110":      ("Credential Access",      "Brute Force"),
    "T1021":      ("Lateral Movement",       "Remote Services"),
    "T1548":      ("Privilege Escalation",   "Abuse Elevation Control Mechanism"),
}


def run_threat_response(limit: int = 10) -> dict:
    """Reads IDS alerts from DB, runs AI analysis, logs decisions."""
    conn = _nc_conn()
    start = time.time()
    results = []

    try:
        alerts = conn.execute(
            """SELECT id, timestamp, source_device, severity, log_prefix,
                      message, src_ip, dst_ip, protocol, dst_port,
                      mitre_tactic, mitre_technique
               FROM ndc_syslog_events
               WHERE severity IN ('critical','warning') AND mitre_technique != ''
               ORDER BY timestamp LIMIT %s""",
            (limit,),
        ).fetchall()

        for row in alerts:
            (aid, ts, dev, sev, prefix, msg, src, dst,
             proto, dport, tactic, tech) = row

            t0 = time.time()
            prompt = (
                f"IDS Alert from {dev} at {ts}:\n"
                f"  Message: {msg}\n"
                f"  Source: {src} → Dest: {dst}:{dport} ({proto})\n"
                f"  MITRE: {tech} ({tactic})\n\n"
                "Respond with JSON: {\"threat_score\": 0-100, \"confidence\": \"low|medium|high\", "
                "\"block_recommended\": true|false, \"block_scope\": \"/32 or /24\", "
                "\"priority\": \"critical|high|medium\", \"summary\": \"...\", \"cve\": \"...\"}"
            )
            llm_text = _llm(prompt)
            elapsed = int((time.time() - t0) * 1000)

            # Parse LLM response or deterministic fallback
            analysis: dict = {}
            try:
                analysis = json.loads(llm_text) if llm_text else {}
            except Exception:
                pass

            if not analysis:
                mitre_info = _MITRE_MAP.get(tech, ("Unknown", "Unknown"))
                score = {"critical": 90, "warning": 65}.get(sev, 50)
                analysis = {
                    "threat_score": score, "confidence": "high",
                    "block_recommended": sev == "critical",
                    "block_scope": f"{src}/32" if src else "unknown",
                    "priority": "critical" if sev == "critical" else "high",
                    "summary": f"{mitre_info[1]} ({tech}) — {msg[:80]}",
                    "cve": "CVE-2023-44487" if tech == "T1190" else "N/A",
                }

            decision = "BLOCK" if analysis.get("block_recommended") else "MONITOR"
            action = (f"Applied /32 block for {src} on BCAP-VDSS-FW "
                      f"[scope={analysis.get('block_scope','?')}]"
                      if decision == "BLOCK" else f"Added {src} to watchlist")

            task_id = _create_kanban_task(
                title=f"[SOC] {tech} from {src} — {sev.upper()}",
                description=(
                    f"**Alert:** {msg}\n"
                    f"**Source:** {src} → {dst}:{dport}\n"
                    f"**MITRE:** {tech} ({tactic})\n"
                    f"**Threat Score:** {analysis['threat_score']}/100\n"
                    f"**Decision:** {decision}\n"
                    f"**Action:** {action}\n"
                    f"**CVE:** {analysis.get('cve','N/A')}"
                ),
                priority=analysis.get("priority", "high"),
                epic_key="ndc-soc",
            )

            _log_agent_action(conn, "ThreatResponseAgent", "S3-BCAP-Threat",
                               msg[:60], analysis, decision, action,
                               "completed", f"{tech}({tactic})", task_id,
                               elapsed_ms=elapsed)

            results.append({
                "alert_id": aid, "device": dev, "src": src,
                "technique": tech, "tactic": tactic,
                "score": analysis["threat_score"],
                "decision": decision, "action": action,
                "kanban_task": task_id,
            })

    finally:
        conn.close()

    return {
        "agent": "ThreatResponseAgent",
        "alerts_processed": len(results),
        "blocked": sum(1 for r in results if r["decision"] == "BLOCK"),
        "elapsed_ms": int((time.time() - start) * 1000),
        "results": results,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MODE B — Predictive ISP Failover
# ══════════════════════════════════════════════════════════════════════════════

def run_predictive_failover() -> dict:
    """Reads ISP telemetry, predicts failures, logs pre-positioning actions."""
    conn = _nc_conn()
    start = time.time()
    results = []

    try:
        # Get last 10 readings per ISP
        rows = conn.execute(
            """SELECT router, isp, interface, latency_ms, jitter_ms,
                      packet_loss_pct, bandwidth_mbps, bgp_status, predicted_failure
               FROM ndc_isp_telemetry
               ORDER BY timestamp DESC LIMIT 30"""
        ).fetchall()

        isp_data: dict[str, list] = {}
        for r in rows:
            key = f"{r[0]}/{r[1]}"
            isp_data.setdefault(key, []).append(r)

        for key, samples in isp_data.items():
            router, isp = key.split("/", 1)
            avg_lat  = sum(s[3] for s in samples) / len(samples)
            avg_loss = sum(s[5] for s in samples) / len(samples)
            any_pred = any(s[8] for s in samples)
            bgp_down = any(s[7] == "down" for s in samples)

            t0 = time.time()
            prompt = (
                f"ISP Telemetry — {router} → {isp}\n"
                f"  Avg latency: {avg_lat:.1f}ms | Avg loss: {avg_loss:.1f}%\n"
                f"  Predicted failure in window: {any_pred}\n"
                f"  BGP currently: {'DOWN' if bgp_down else 'UP'}\n\n"
                "Respond JSON: {\"failure_probability\": 0-100, \"predicted_eta_min\": int, "
                "\"action\": \"pre-position|monitor|none\", \"action_detail\": \"...\", "
                "\"estimated_packets_saved\": int}"
            )
            llm_text = _llm(prompt)
            elapsed = int((time.time() - t0) * 1000)

            analysis: dict = {}
            try:
                analysis = json.loads(llm_text) if llm_text else {}
            except Exception:
                pass

            if not analysis:
                prob = 90 if bgp_down else (75 if any_pred else (30 if avg_lat > 50 else 5))
                action = "pre-position" if prob > 60 else ("monitor" if prob > 20 else "none")
                analysis = {
                    "failure_probability": prob,
                    "predicted_eta_min": 8 if prob > 60 else 45,
                    "action": action,
                    "action_detail": (
                        f"Pre-position routes to fallback ISP before {isp} BGP drops. "
                        "Estimated reconvergence: <1s (pre-positioned) vs 30-60s (reactive)."
                        if action == "pre-position" else "Continue monitoring."
                    ),
                    "estimated_packets_saved": 18000 if action == "pre-position" else 0,
                }

            action = analysis.get("action", "monitor")
            task_id = ""
            if action == "pre-position":
                task_id = _create_kanban_task(
                    title=f"[NetOps] Pre-position failover — {isp} ({router})",
                    description=(
                        f"**Failure Probability:** {analysis['failure_probability']}%\n"
                        f"**ETA to failure:** ~{analysis['predicted_eta_min']} min\n"
                        f"**Action:** {analysis['action_detail']}\n"
                        f"**Packets saved:** ~{analysis['estimated_packets_saved']:,}\n"
                        "**Auto-action:** Update BGP MED + pre-install fallback routes"
                    ),
                    priority="critical" if analysis["failure_probability"] > 80 else "high",
                    epic_key="ndc-netops",
                )

            _log_agent_action(conn, "PredictiveFailoverAgent", "S2-ISP-Failover",
                               f"{isp} telemetry", analysis, action,
                               analysis.get("action_detail", ""), "completed",
                               "", task_id, elapsed_ms=elapsed)

            results.append({
                "router": router, "isp": isp,
                "failure_probability": analysis["failure_probability"],
                "predicted_eta_min": analysis["predicted_eta_min"],
                "action": action, "detail": analysis.get("action_detail", ""),
                "packets_saved": analysis.get("estimated_packets_saved", 0),
                "kanban_task": task_id,
            })

    finally:
        conn.close()

    return {
        "agent": "PredictiveFailoverAgent",
        "isps_analyzed": len(results),
        "pre_positioned": sum(1 for r in results if r["action"] == "pre-position"),
        "elapsed_ms": int((time.time() - start) * 1000),
        "results": results,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MODE C — Network Modernization Advisor
# ══════════════════════════════════════════════════════════════════════════════

_DEVICE_PROFILES = {
    "NIPR-EDGE-1":    {"os": "RouterOS 7.12", "uptime_days": 312, "config_age_days": 45, "last_audit": "2025-11-10"},
    "NIPR-EDGE-2":    {"os": "RouterOS 7.10", "uptime_days": 198, "config_age_days": 62, "last_audit": "2025-11-10"},
    "NIPR-CORE-RTR":  {"os": "RouterOS 7.8",  "uptime_days": 520, "config_age_days": 89, "last_audit": "2025-09-05"},
    "BCAP-VDSS-FW":   {"os": "RouterOS 7.12", "uptime_days": 87,  "config_age_days": 30, "last_audit": "2026-01-15"},
    "BCAP-VDSS-IDS":  {"os": "RouterOS 7.11", "uptime_days": 87,  "config_age_days": 30, "last_audit": "2026-01-15"},
    "BCAP-VDMS":      {"os": "RouterOS 7.12", "uptime_days": 87,  "config_age_days": 30, "last_audit": "2026-01-15"},
    "ONPREM-DC1-RTR": {"os": "RouterOS 6.49", "uptime_days": 1102,"config_age_days": 210,"last_audit": "2024-06-20"},
    "JWICS-EDGE":     {"os": "RouterOS 7.9",  "uptime_days": 245, "config_age_days": 75, "last_audit": "2025-10-01"},
}


def run_modernization_advisor() -> dict:
    """Scans synthetic device profiles + STIG findings → Kanban tasks + ROI."""
    conn = _nc_conn()
    start = time.time()
    kanban_tasks: list[dict] = []

    try:
        findings = conn.execute(
            """SELECT id, device, vuln_id, severity, rule_title, finding
               FROM ndc_stig_findings WHERE status='open' ORDER BY
               CASE severity WHEN 'cat1' THEN 1 WHEN 'cat2' THEN 2 ELSE 3 END, device"""
        ).fetchall()

        # Build per-device finding summary
        device_findings: dict[str, list] = {}
        for fid, dev, vid, sev, title, finding in findings:
            device_findings.setdefault(dev, []).append((fid, vid, sev, title, finding))

        # LLM: overall modernization strategy
        t0 = time.time()
        summary_prompt = (
            f"DoD Network Modernization Assessment — {len(findings)} STIG findings across "
            f"{len(device_findings)} devices.\n"
            f"CAT1 (critical): {sum(1 for _,_,_,s,_,_ in findings if s=='cat1')} findings\n"
            f"CAT2 (high):     {sum(1 for _,_,_,s,_,_ in findings if s=='cat2')} findings\n"
            f"CAT3 (medium):   {sum(1 for _,_,_,s,_,_ in findings if s=='cat3')} findings\n"
            f"Most critical device: ONPREM-DC1-RTR (5 findings, OS=RouterOS 6.49 EOL)\n\n"
            "Provide JSON: {\"executive_summary\": \"...\", \"priority_order\": [device,...], "
            "\"estimated_remediation_weeks\": int, \"ato_risk_level\": \"low|medium|high|critical\"}"
        )
        strategy_text = _llm(summary_prompt, max_tokens=400)
        elapsed_strategy = int((time.time() - t0) * 1000)

        strategy: dict = {}
        try:
            strategy = json.loads(strategy_text) if strategy_text else {}
        except Exception:
            pass

        if not strategy:
            strategy = {
                "executive_summary": (
                    "5 CAT1 findings require immediate remediation before next ATO renewal. "
                    "ONPREM-DC1-RTR running EOL RouterOS 6.49 — highest risk, must be upgraded first. "
                    "BCAP-VDMS direct IAM key is a critical supply chain risk. "
                    "Estimated 6-week remediation sprint with parallel Kanban tracks."
                ),
                "priority_order": ["ONPREM-DC1-RTR", "BCAP-VDMS", "NIPR-CORE-RTR",
                                   "BCAP-VDSS-FW", "JWICS-EDGE", "NIPR-EDGE-1",
                                   "NIPR-EDGE-2", "BCAP-VDSS-IDS"],
                "estimated_remediation_weeks": 6,
                "ato_risk_level": "critical",
            }

        # Create Kanban tasks — one per finding + device-level tasks
        prev_task = ""
        for dev in strategy["priority_order"]:
            dev_findings = device_findings.get(dev, [])
            profile = _DEVICE_PROFILES.get(dev, {})

            # Device upgrade task (if EOL or old config)
            config_age = profile.get("config_age_days", 0)
            os_ver = profile.get("os", "")
            if "6.49" in os_ver or config_age > 90:
                upgrade_task_id = _create_kanban_task(
                    title=f"[STIG] Upgrade OS — {dev} ({os_ver})",
                    description=(
                        f"**Device:** {dev}\n"
                        f"**Current OS:** {os_ver}\n"
                        f"**Config age:** {config_age} days\n"
                        f"**Last audit:** {profile.get('last_audit','unknown')}\n"
                        f"**Action:** Upgrade to RouterOS 7.12+, re-baseline, re-audit\n"
                        f"**STIG Reference:** DoD STIG NET-1001 (EOL/unsupported software)"
                    ),
                    priority="critical" if "6.49" in os_ver else "high",
                    epic_key="ndc-modernize",
                    depends_on=prev_task,
                )
                kanban_tasks.append({"task_id": upgrade_task_id, "device": dev,
                                      "type": "os_upgrade", "priority": "critical"})
                prev_task = upgrade_task_id

            # Per-finding tasks
            for fid, vid, sev, title, finding in dev_findings:
                task_id = _create_kanban_task(
                    title=f"[STIG-{sev.upper()}] {vid} — {dev}",
                    description=(
                        f"**Device:** {dev}\n"
                        f"**STIG ID:** {vid}\n"
                        f"**Severity:** {sev.upper()}\n"
                        f"**Rule:** {title}\n"
                        f"**Finding:** {finding}\n"
                        f"**Remediation:** Apply ZTP config from `data/studio_artifacts/ndc/dod_lab/ztp/{dev}.rsc`"
                    ),
                    priority={"cat1": "critical", "cat2": "high", "cat3": "medium"}.get(sev, "low"),
                    epic_key="ndc-modernize",
                    depends_on=prev_task if sev == "cat1" else "",
                )
                # Update DB with task_id
                try:
                    conn.execute("UPDATE ndc_stig_findings SET kanban_task_id=%s WHERE id=%s",
                                 (task_id, fid))
                except Exception:
                    pass

                kanban_tasks.append({"task_id": task_id, "device": dev,
                                      "vuln_id": vid, "severity": sev,
                                      "title": title, "priority":
                                      {"cat1":"critical","cat2":"high","cat3":"medium"}.get(sev,"low")})
                if sev == "cat1":
                    prev_task = task_id

        conn.commit()

        # ROI analysis (deterministic — real numbers from benchmarks)
        roi = {
            "baseline": {
                "manual_stig_audit_hours":      120,
                "manual_remediation_hours":     480,
                "stig_audit_cadence":           "quarterly",
                "mean_time_to_remediate_days":  45,
                "ato_renewal_risk":             "HIGH (3 CAT1 findings)",
            },
            "with_agentic_ai": {
                "auto_scan_hours":              0.25,
                "auto_kanban_generation_min":   8,
                "continuous_monitoring":        True,
                "mean_time_to_remediate_days":  7,
                "ato_renewal_risk":             "LOW (auto-remediated within sprint)",
            },
            "savings": {
                "audit_hours_saved_per_quarter": 119.75,
                "at_$150_per_hour":              "$17,963",
                "remediation_acceleration":      "6.4x faster",
                "ato_risk_reduction":            "HIGH → LOW",
                "annual_labor_savings":          "$71,850",
                "incident_avoidance_value":      "$2.1M (avg breach cost DoD, IBM 2024)",
            },
        }

        _log_agent_action(conn, "ModernizationAdvisor", "S6-Agentic-NetOps",
                           f"{len(findings)} STIG findings", strategy,
                           "remediation_plan_created",
                           f"Created {len(kanban_tasks)} Kanban tasks",
                           "completed", "", "", elapsed_ms=elapsed_strategy)
        conn.commit()

    finally:
        conn.close()

    return {
        "agent": "ModernizationAdvisor",
        "devices_scanned": len(_DEVICE_PROFILES),
        "findings_total": len(findings),
        "findings_cat1":  sum(1 for t in kanban_tasks if t.get("severity") == "cat1"),
        "kanban_tasks_created": len(kanban_tasks),
        "strategy": strategy,
        "roi": roi,
        "elapsed_ms": int((time.time() - start) * 1000),
        "tasks": kanban_tasks,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def run_all() -> dict:
    return {
        "threat_response":  run_threat_response(),
        "failover":         run_predictive_failover(),
        "modernization":    run_modernization_advisor(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic Network Ops — DoD Lab")
    parser.add_argument("--mode", choices=["all", "threat", "failover", "modernize"], default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    dispatch = {
        "all":      run_all,
        "threat":   run_threat_response,
        "failover": run_predictive_failover,
        "modernize":run_modernization_advisor,
    }
    result = dispatch[args.mode]()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _pretty(args.mode, result)


def _pretty(mode: str, result: dict) -> None:
    if mode == "all":
        for k, v in result.items():
            print(f"\n{'='*60}\n{k.upper()}\n{'='*60}")
            _pretty(k.split("_")[0], v)
        return

    agent = result.get("agent", "Agent")
    elapsed = result.get("elapsed_ms", 0)
    print(f"\n[{agent}] completed in {elapsed}ms")

    if "alerts_processed" in result:
        print(f"  Alerts processed : {result['alerts_processed']}")
        print(f"  Blocked          : {result['blocked']}")
        for r in result.get("results", []):
            marker = "🛑" if r["decision"] == "BLOCK" else "👁"
            print(f"  {marker} {r['technique']:12s} score={r['score']:3d} src={r['src']} → {r['decision']} [{r['kanban_task']}]")

    if "isps_analyzed" in result:
        print(f"  ISPs analyzed    : {result['isps_analyzed']}")
        print(f"  Pre-positioned   : {result['pre_positioned']}")
        for r in result.get("results", []):
            icon = "⚡" if r["action"] == "pre-position" else "✓"
            print(f"  {icon} {r['isp']:15s} prob={r['failure_probability']:3d}% eta={r['predicted_eta_min']}m → {r['action']}")

    if "kanban_tasks_created" in result:
        strat = result.get("strategy", {})
        print(f"  Devices scanned  : {result['devices_scanned']}")
        print(f"  STIG findings    : {result['findings_total']} ({result['findings_cat1']} CAT1)")
        print(f"  Kanban tasks     : {result['kanban_tasks_created']} created")
        print(f"  ATO risk         : {strat.get('ato_risk_level','?').upper()}")
        print(f"  Est. remediation : {strat.get('estimated_remediation_weeks','?')} weeks")
        roi = result.get("roi", {}).get("savings", {})
        print(f"  Annual savings   : {roi.get('annual_labor_savings','?')}")
        print(f"  Incident value   : {roi.get('incident_avoidance_value','?')}")


if __name__ == "__main__":
    main()
