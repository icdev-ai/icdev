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

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _contract_label(contract: Dict) -> str:
    """Human-identifiable label for a contract, for use in card titles.

    ``dict.get(key, default)`` only falls back when the KEY IS ABSENT. Every
    row in cpmp_contracts has a contract_number column, and on real rows it is
    routinely '' or NULL, so ``.get("contract_number", "N/A")`` returned the
    empty string and every card landed titled "[CPMP] : Subcontractor
    Compliance" — unidentifiable, and identical across contracts. Fall back on
    the VALUE, not the key.
    """
    for key in ("contract_number", "title"):
        value = (contract.get(key) or "").strip()
        if value:
            return value
    return f"contract {str(contract.get('id') or '?')[:8]}"


def run(trigger_data=None, context=None):
    """Entry point for Genesis daemon."""
    trigger_data = trigger_data or {}
    pass_type = trigger_data.get("pass_type", "full")

    results = {
        "pass_type": pass_type,
        "contracts_scanned": 0,
        "contracts_unnumbered": 0,
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
        cnum = (contract.get("contract_number") or "").strip()
        ctitle = contract.get("title", "")
        # Card TITLES use the label (never blank); context_data keeps the raw
        # contract_number so downstream consumers still see the true value.
        clabel = _contract_label(contract)

        # An unnumbered contract is COUNTED but NOT skipped. create_contract()
        # defaults contract_number to '' (tools/govcon/contract_manager.py), so on
        # the live board every active contract is unnumbered — skipping them, as
        # main briefly did, drops every finding the reflex exists to surface and
        # reports the silence as "skipped".
        #
        # The real requirement behind that skip was that a card must NAME something
        # a human can act on, and _contract_label() already guarantees that: it
        # falls back number -> title -> "contract <id8>" and is never blank. So the
        # finding reaches the board AND is identifiable, which is what the skip was
        # trying to protect. The counter stays — knowing how much of the portfolio
        # is unnumbered is worth reporting on its own.
        if not cnum:
            results["contracts_unnumbered"] += 1

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
                        wrote = _suggest_kanban_card(
                            title=f"[CPMP] {clabel}: {str(issue.get('type','issue')).replace('_',' ').title()}",
                            description=issue.get("description", "") + "\n\nSuggested: " + issue.get("suggested_action", ""),
                            priority="high" if issue.get("severity") == "critical" else "medium",
                            context_data={"contract_id": cid, "contract_number": cnum, "issue": issue},
                            created_by="cpmp_monitor",
                            dedup_key=f"{cid}:{issue.get('type','issue')}",
                        )
                        results["cards_created"] += 1 if wrote else 0
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
                            wrote = _suggest_kanban_card(
                                title=f"[CPARS RISK] {clabel}: Trajectory toward Marginal Rating",
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
                                dedup_key=f"{cid}:cpars_trajectory",
                            )
                            results["cpars_alerts"] += 1 if wrote else 0
                            results["cards_created"] += 1 if wrote else 0
                            # CAT2 escalation — only alongside a NEW card, or a
                            # standing trajectory re-pages the CAT2 channel every
                            # 3h for as long as the score stays below threshold.
                            #
                            # NOTE: alert_service exports escalate_cat1_FINDING,
                            # which pages on a stig_findings row by id — not this
                            # signature, and not applicable to a CPARS trajectory.
                            # The import below has therefore never resolved. It is
                            # left in place as the declared intent, but the failure
                            # is now REPORTED rather than swallowed by `except:
                            # pass`, which is why nobody noticed the CAT2 channel
                            # was silent. Wiring it needs a real escalation API.
                            if wrote:
                                try:
                                    from tools.notification_service.alert_service import escalate_cat1
                                    escalate_cat1(
                                        finding_title=f"CPARS trajectory alert: {clabel}",
                                        severity="CAT2",
                                        details={"contract_id": cid, "predicted_score": predicted_score},
                                    )
                                except Exception as esc:
                                    results["errors"].append(
                                        f"CPARS CAT2 escalation unavailable for {clabel}: {esc}"
                                    )
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
                        wrote = _suggest_kanban_card(
                            title=f"[SUBCON] {clabel}: {finding.get('issue_type','Noncompliance').replace('_',' ').title()}",
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
                            dedup_key=(
                                f"{cid}:{finding.get('issue_type','noncompliance')}"
                                f":{finding.get('subcontractor_name','')}"
                            ),
                        )
                        results["subcon_alerts"] += 1 if wrote else 0
                        results["cards_created"] += 1 if wrote else 0
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
                            title=f"[CDRL] {clabel}: {generated} deliverable(s) auto-generated",
                            description=(
                                f"Auto-generated {generated} CDRL(s) due within 14 days for {ctitle}.\n"
                                f"Review generated artifacts before submission."
                            ),
                            priority="medium",
                            context_data={"contract_id": cid, "contract_number": cnum, "generated": generated},
                            created_by="cpmp_monitor_cdrl",
                            # An event, not a condition: the batch size is part
                            # of the identity so the next batch gets its own card.
                            dedup_key=f"{cid}:cdrl_generated:{generated}",
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
    dedup_key: str = None,
) -> bool:
    """Create a kanban suggestion card, keyed so one finding is one row.

    Returns True only if a row was actually written, so callers count writes
    rather than attempts — an attempt-counter reports steady card creation
    forever while a working dedup writes nothing, and `_write_memory_log`
    persists that number.

    ``dedup_key`` identifies the FINDING (contract + issue), and the card's id
    is derived from it, so re-detecting the same finding is a primary-key
    collision rather than a new row. The previous scheme — random uuid id,
    dedup by ``title + dispatch_source + status NOT IN (done, dismissed)`` —
    failed in both directions at once:

      * COLLAPSE: titles embedded ``contract_number``, which is '' on real
        rows, so every contract produced the identical title and the dedup
        discarded all but the first. Five active contracts had noncompliant
        subcontractors; the board showed one card.
      * DUPLICATION: promoting a card rewrites ``dispatch_source`` to
        'genesis_scheduler', so the dedup query stopped matching its own card
        and re-created it every 3h cycle while it sat in progress.

    An existing card is left alone in ANY status, including done/dismissed: a
    reflex that resurrects work someone deliberately closed is a nag loop, and
    the underlying condition stays visible on the CPMP dashboard regardless.
    Findings that are events rather than conditions (e.g. CDRL generation)
    encode their magnitude in the key, so a genuinely new occurrence is a
    genuinely new key.
    """
    from tools.db.storage import get_connection
    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: background reflex; kanban_tasks has no classification/tenant_id columns
    try:
        seed = dedup_key or title[:120]
        task_id = "cpmp-" + hashlib.sha256(
            f"{created_by}|{seed}".encode("utf-8")
        ).hexdigest()[:10]

        # Dedup on the card's own id — immune to later rewrites of title,
        # status, or dispatch_source by the kanban pipeline.
        if conn.execute(
            "SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone():
            return False

        now = datetime.now(timezone.utc).isoformat()
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
        return True
    finally:
        conn.close()


def _write_memory_log(results: Dict):
    try:
        # write_to_db, not write_memory — the latter has never existed, so this
        # whole log was an ImportError swallowed by the except below.
        from tools.memory.memory_write import write_to_db
        write_to_db(
            content=(
                f"CPMP monitor [{results['pass_type']}]: "
                f"{results['contracts_scanned']} contracts "
                f"({results['contracts_unnumbered']} skipped, no contract number), "
                f"{results['issues_found']} issues, "
                f"{results['cards_created']} cards, "
                f"{results['cpars_alerts']} CPARS alerts, "
                f"{results['cdrl_generated']} CDRLs generated."
            ),
            entry_type="event",
            source="cpmp_monitor",
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
