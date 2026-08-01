# CUI // SP-CTI
"""Genesis Reflex: CPMP Monitor — proactive contract health surveillance.

Runs every 3 hours via Genesis daemon. Three detection passes:
  1. PMO AI Issues   — auto_detect_issues() → kanban cards for critical/high findings
  2. CPARS Trajectory — predicted score declining toward Marginal → CAT2 alert
  3. Subcontractor Noncompliance — detect_noncompliance() → kanban high-priority
  4. Deliverable Auto-Generation — generate CDRLs due in 14 days

Pass type controlled by trigger_data['pass_type']:
  'full' (default) — all four passes
  'deliverables'   — only deliverable auto-generation pass (lightweight, every 3h)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def run(trigger_data=None, context=None):
    """Entry point for Genesis daemon."""
    trigger_data = trigger_data or {}
    pass_type = trigger_data.get("pass_type", "full")

    results = {
        "pass_type": pass_type,
        "contracts_scanned": 0,
        "issues_found": 0,
        "cards_created": 0,
        "cpars_alerts": 0,
        "subcon_alerts": 0,
        "cdrl_generated": 0,
        "errors": [],
    }

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: background reflex, no Flask request/tenant context; cpmp tables use CUI universally
        active = conn.execute(
            "SELECT id, contract_number, title FROM cpmp_contracts WHERE status = 'active'"
        ).fetchall()
        conn.close()
        active = [dict(r) for r in active]
    except Exception as e:
        return {"status": "error", "message": str(e)}

    results["contracts_scanned"] = len(active)

    for contract in active:
        cid = contract["id"]
        cnum = contract.get("contract_number", "N/A")
        ctitle = contract.get("title", "")

        # ── Pass 1: PMO AI Issues ──────────────────────────────────────
        if pass_type in ("full",):
            try:
                from tools.govcon.pmo_ai_advisor import auto_detect_issues
                detection = auto_detect_issues(cid)
                issues = detection.get("issues", [])
                critical = [i for i in issues if i.get("severity") in ("critical", "high")]
                results["issues_found"] += len(issues)
                for issue in critical:
                    try:
                        _suggest_kanban_card(
                            title=f"[CPMP] {cnum}: {str(issue.get('type','issue')).replace('_',' ').title()}",
                            description=issue.get("description", "") + "\n\nSuggested: " + issue.get("suggested_action", ""),
                            priority="high" if issue.get("severity") == "critical" else "medium",
                            context_data={"contract_id": cid, "contract_number": cnum, "issue": issue},
                            created_by="cpmp_monitor",
                        )
                        results["cards_created"] += 1
                    except Exception as ce:
                        results["errors"].append(f"Card creation failed {cnum}: {ce}")
            except Exception as e:
                results["errors"].append(f"PMO issues scan {cnum}: {e}")

        # ── Pass 2: CPARS Trajectory ───────────────────────────────────
        if pass_type in ("full",):
            try:
                from tools.govcon.cpars_predictor import predict_cpars, get_cpars_trend
                prediction = predict_cpars(cid)
                predicted_score = prediction.get("predicted_score", 1.0)
                trend_data = get_cpars_trend(cid)
                trend = trend_data.get("trend", [])

                # Alert if predicted score < 0.65 (Marginal threshold) AND declining
                if predicted_score < 0.65 and len(trend) >= 2:
                    recent = [t.get("predicted_score") for t in trend[-3:] if t.get("predicted_score") is not None]
                    is_declining = len(recent) >= 2 and recent[-1] < recent[0]
                    if is_declining:
                        try:
                            _suggest_kanban_card(
                                title=f"[CPARS RISK] {cnum}: Trajectory toward Marginal Rating",
                                description=(
                                    f"Contract: {ctitle}\n"
                                    f"Predicted CPARS score: {predicted_score:.2f} (Marginal threshold: 0.65)\n"
                                    f"Score trend (last 3 periods): {[round(s, 2) for s in recent]}\n"
                                    f"Predicted rating: {prediction.get('predicted_rating', 'marginal')}\n"
                                    f"Immediate corrective action required to avoid Marginal CPARS rating."
                                ),
                                priority="high",
                                context_data={
                                    "contract_id": cid,
                                    "contract_number": cnum,
                                    "predicted_score": predicted_score,
                                    "trend": recent,
                                },
                                created_by="cpmp_monitor_cpars",
                            )
                            results["cpars_alerts"] += 1
                            results["cards_created"] += 1
                            # CAT2 escalation
                            try:
                                from tools.notification_service.alert_service import escalate_cat1
                                escalate_cat1(
                                    finding_title=f"CPARS trajectory alert: {cnum}",
                                    severity="CAT2",
                                    details={"contract_id": cid, "predicted_score": predicted_score},
                                )
                            except Exception:
                                pass
                        except Exception as ce:
                            results["errors"].append(f"CPARS card {cnum}: {ce}")
            except Exception as e:
                results["errors"].append(f"CPARS trajectory {cnum}: {e}")

        # ── Pass 3: Subcontractor Noncompliance ────────────────────────
        if pass_type in ("full",):
            try:
                from tools.govcon.subcontractor_tracker import detect_noncompliance
                nc = detect_noncompliance(cid)
                findings = nc.get("noncompliance", [])
                high_findings = [f for f in findings if f.get("severity") in ("high", "critical")]
                for finding in high_findings:
                    try:
                        _suggest_kanban_card(
                            title=f"[SUBCON] {cnum}: {finding.get('issue_type','Noncompliance').replace('_',' ').title()}",
                            description=(
                                f"Contract: {ctitle}\n"
                                f"Subcontractor: {finding.get('subcontractor_name','N/A')}\n"
                                f"Issue: {finding.get('description','')}\n"
                                f"Severity: {finding.get('severity','').upper()}\n"
                                f"Action: Initiate flow-down corrective action per FAR 52.219-9."
                            ),
                            priority="high",
                            context_data={"contract_id": cid, "contract_number": cnum, "finding": finding},
                            created_by="cpmp_monitor_subcon",
                        )
                        results["subcon_alerts"] += 1
                        results["cards_created"] += 1
                        try:
                            pass
                        except Exception:
                            pass
                    except Exception as ce:
                        results["errors"].append(f"Subcon card {cnum}: {ce}")
            except Exception as e:
                results["errors"].append(f"Subcon scan {cnum}: {e}")

        # ── Pass 4: Deliverable 14-Day Auto-Generation ─────────────────
        if pass_type in ("full", "deliverables"):
            try:
                from tools.govcon.cdrl_generator import generate_all_due
                gen_result = generate_all_due(cid, days_ahead=14)
                generated = gen_result.get("generated", 0)
                if generated > 0:
                    results["cdrl_generated"] += generated
                    try:
                        _suggest_kanban_card(
                            title=f"[CDRL] {cnum}: {generated} deliverable(s) auto-generated",
                            description=(
                                f"Auto-generated {generated} CDRL(s) due within 14 days for {ctitle}.\n"
                                f"Review generated artifacts before submission."
                            ),
                            priority="medium",
                            context_data={"contract_id": cid, "contract_number": cnum, "generated": generated},
                            created_by="cpmp_monitor_cdrl",
                        )
                    except Exception:
                        pass
            except Exception as e:
                results["errors"].append(f"CDRL gen {cnum}: {e}")

    _write_memory_log(results)
    results["status"] = "ok"
    return results


def _suggest_kanban_card(
    title: str,
    description: str,
    priority: str = "normal",
    context_data: Dict = None,
    created_by: str = "cpmp_monitor",
):
    """Create a kanban suggestion card. Skips duplicates by title + dispatch_source."""
    import uuid as _uuid
    from tools.db.storage import get_connection
    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: background reflex; kanban_tasks has no classification/tenant_id columns
    try:
        # Dedup: skip if same title + same source already open
        existing = conn.execute(
            "SELECT id FROM kanban_tasks WHERE title = %s AND dispatch_source = %s "
            "AND status NOT IN ('done', 'dismissed')",
            (title[:120], created_by),
        ).fetchone()
        if existing:
            return

        now = datetime.now(timezone.utc).isoformat()
        task_id = f"cpmp-{_uuid.uuid4().hex[:10]}"
        conn.execute(
            """INSERT INTO kanban_tasks
               (id, task_type, title, description, status, priority,
                tags, dispatch_source, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'suggested', %s, %s, %s, %s, %s)""",
            (
                task_id,
                "fix",
                title[:120],
                description[:500],
                priority,
                json.dumps(context_data or {}),
                created_by,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _write_memory_log(results: Dict):
    try:
        from tools.memory.memory_write import write_memory
        write_memory(
            content=(
                f"CPMP monitor [{results['pass_type']}]: "
                f"{results['contracts_scanned']} contracts, "
                f"{results['issues_found']} issues, "
                f"{results['cards_created']} cards, "
                f"{results['cpars_alerts']} CPARS alerts, "
                f"{results['cdrl_generated']} CDRLs generated."
            ),
            memory_type="event",
        )
    except Exception:
        pass


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    result = run()
    print(json.dumps(result, indent=2, default=str))
