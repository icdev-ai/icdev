# CUI // SP-CTI
"""Genesis Reflex: CPMP Monitor — proactive contract health surveillance.

Runs every 3 hours via Genesis daemon. One state refresh, then four passes:
  0. Overdue Sweep   — compute_overdue_deliverables() → maintain status/days_overdue
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


def _placeholder_titles() -> frozenset:
    """Title values that name no particular contract.

    Sourced from contract_manager so the two cannot drift: if the default that
    create_contract() stamps is ever changed, this follows it. Imported lazily
    with a literal fallback because this reflex is loaded by the Genesis daemon
    and must not fail to import when a govcon dependency is unavailable.
    """
    default = "Untitled Contract"
    try:
        from tools.govcon.contract_manager import DEFAULT_CONTRACT_TITLE
        default = DEFAULT_CONTRACT_TITLE
    except Exception:
        pass
    return frozenset({default.casefold()})


# Display label and corrective action per detect_noncompliance() category.
# The reflex read a nonexistent 'issue_type' key, so every card it could have
# filed would have been titled "Noncompliance" and carried the flow-down remedy
# regardless of category — a CMMC gap arriving with a FAR 52.219-9 flow-down
# action. Keyed on 'category', which is what the finding actually carries.
_SUBCON_CATEGORIES = {
    "flowdown": (
        "Flow-Down",
        "Initiate flow-down corrective action per FAR 52.219-9.",
    ),
    "cybersecurity": (
        "Cybersecurity",
        "Issue cure notice; verify NIST SP 800-171 implementation per DFARS 252.204-7012.",
    ),
    "cmmc": (
        "CMMC",
        "Establish the subcontractor's CMMC level per DFARS 252.204-7021.",
    ),
    "isr_ssr": (
        "ISR/SSR",
        "File the outstanding ISR/SSR in eSRS per FAR 52.219-9(d).",
    ),
}


def _contract_label(contract: Dict) -> str:
    """Human-identifiable label for a contract, for use in card titles.

    ``dict.get(key, default)`` only falls back when the KEY IS ABSENT. Every
    row in cpmp_contracts has a contract_number column, and on real rows it is
    routinely '' or NULL, so ``.get("contract_number", "N/A")`` returned the
    empty string and every card landed titled "[CPMP] : Subcontractor
    Compliance" — unidentifiable, and identical across contracts. Fall back on
    the VALUE, not the key.

    A present value is not automatically an identifying one. create_contract()
    stamps 'Untitled Contract' on any contract created without a title, so
    falling back to `title` reintroduced the very collapse the paragraph above
    describes, one door over: on 2026-08-12 four DIFFERENT active contracts
    (df32ba49, 6d67ff20, 719d5e59, 8143e17a) each held a board card titled
    exactly "[CPMP] Untitled Contract: Overdue Deliverables", two of them
    dispatched to sessions at the same time. The card ids differ, so nothing
    was dropped — but a title that cannot distinguish which of four contracts
    it means is not actionable, which is the whole point of the label.

    So a placeholder is treated as ABSENT and the chain falls through to the
    id, which is the only field guaranteed to identify exactly one contract.
    """
    placeholders = _placeholder_titles()
    for key in ("contract_number", "title"):
        value = (contract.get(key) or "").strip()
        if value and value.casefold() not in placeholders:
            return value
    return f"contract {str(contract.get('id') or '?')[:8]}"


def _superseded_titles(title: str, label: str) -> frozenset:
    """The same card title as written by a SUPERSEDED labelling rule.

    Fixing `_contract_label` only ever helped cards that did not exist yet.
    `_suggest_kanban_card` dedups on the card id, and a card id is derived from
    the contract id and issue type — never from the label — so once a card is on
    the board the reflex sees a primary-key collision and returns without
    looking at the title. Both label fixes therefore landed and left the already
    filed cards permanently unidentifiable: on 2026-08-13 the live board still
    carried two cards titled "[CPMP] : Overdue Deliverables" from before the
    first fix and five titled "[CPMP] Untitled Contract: ..." from before the
    second, and the four sessions dispatched onto the latter could not tell
    which of four contracts they had been given.

    This returns the titles the CURRENT title would have had under each label
    that has since been ruled unidentifiable — '' (the .get() default bug) and
    every `_placeholder_titles()` entry. Matching is exact and the set is
    finite, so a title edited by a human or rewritten by the kanban pipeline is
    never in it and is never touched. That makes the repair a one-way ratchet:
    unidentifiable -> identifiable, and nothing else.

    Empty if `title` was not built from `label`, so a caller that formats its
    title differently silently opts out rather than matching by accident.

    Values are CASEFOLDED for comparison: `_placeholder_titles()` is casefolded
    at the source (it feeds a casefolded membership test in `_contract_label`),
    while the board stores whatever casing create_contract() stamped — so an
    exact-case set would never match the 'Untitled Contract' rows it exists for.
    """
    marker = f" {label}: "
    idx = title.find(marker) if label else -1
    if idx == -1:
        return frozenset()
    tag = title[:idx]  # "[CPMP]", "[SUBCON]", "[CDRL]" — kept, only the label varies
    suffix = title[idx + len(marker):]
    superseded = {""} | set(_placeholder_titles())
    return frozenset(
        f"{tag} {old}: {suffix}".casefold() for old in superseded
    ) - {title.casefold()}


def run(trigger_data=None, context=None):
    """Entry point for Genesis daemon."""
    trigger_data = trigger_data or {}
    pass_type = trigger_data.get("pass_type", "full")

    results = {
        "pass_type": pass_type,
        "contracts_scanned": 0,
        "contracts_unnumbered": 0,
        "overdue_marked": 0,
        "overdue_refreshed": 0,
        "issues_found": 0,
        "cards_created": 0,
        # Stale titles repaired in place — NOT new cards, so kept out of
        # cards_created, which must stay a count of rows actually written.
        "cards_relabeled": 0,
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

    # ── Pass 0: refresh the overdue state the other passes are read from ──
    #
    # compute_overdue_deliverables() is the only writer of
    # cpmp_deliverables.status='overdue' and days_overdue, and until this call
    # it had no caller but its own CLI flag. Contract health, the CPARS
    # schedule dimension, the portfolio rollup and negative_event_tracker all
    # READ those two fields, so on 2026-08-13 every one of them reported 0
    # overdue and green health on a board carrying 26 CDRLs 44 days past due —
    # while this reflex filed high-priority cards saying "5 CDRL(s) are past
    # due" off pmo_ai_advisor's separate date-based count. Refreshing the state
    # BEFORE the passes that consume it is what makes the two agree.
    #
    # Swept portfolio-wide rather than per-contract: the loop below visits only
    # status='active' contracts, but portfolio_manager counts overdue CDRLs
    # across ('active', 'option_pending'), and an option-pending contract's
    # deliverables are no less late.
    try:
        from tools.govcon.contract_manager import compute_overdue_deliverables
        swept = compute_overdue_deliverables()
        results["overdue_marked"] = swept.get("overdue_count", 0)
        results["overdue_refreshed"] = swept.get("days_refreshed", 0)
    except Exception as e:
        results["errors"].append(f"Overdue sweep: {e}")

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
                            label=clabel,
                            stats=results,
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
                                label=clabel,
                                stats=results,
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
                # detect_noncompliance() returns its list under 'findings'. This
                # read 'noncompliance' — a key that function has never returned —
                # so the list was empty on every cycle for every contract, and the
                # pass was inert from its first commit. No exception, no error
                # entry, subcon_alerts steady at 0 and status 'ok'. Pass 1 counts
                # noncompliant subs with its own SQL, so the board still showed
                # "N subcontractor(s) have incomplete flow-down or cybersecurity
                # gaps" while the card naming WHICH sub and WHICH gap was the one
                # thing missing.
                findings = nc.get("findings", [])
                high_findings = [f for f in findings if f.get("severity") in ("high", "critical")]
                for finding in high_findings:
                    try:
                        # 'category' and 'company_name' are the keys the findings
                        # carry; 'issue_type'/'subcontractor_name' never existed.
                        category = finding.get("category") or "noncompliance"
                        company = finding.get("company_name")
                        label, action = _SUBCON_CATEGORIES.get(
                            category,
                            (category.replace("_", " ").title(), "Review with the subcontract manager."),
                        )
                        # isr_ssr is contract-level: it has no subcontractor, and
                        # printing "Subcontractor: None" reads as missing data.
                        subject = f"Subcontractor: {company}\n" if company else ""
                        wrote = _suggest_kanban_card(
                            title=f"[SUBCON] {clabel}: {label}",
                            description=(
                                f"Contract: {clabel}\n"
                                f"{subject}"
                                f"Issue: {finding.get('description','')}\n"
                                f"Severity: {finding.get('severity','').upper()}\n"
                                f"Action: {action}"
                            ),
                            priority="high",
                            context_data={"contract_id": cid, "contract_number": cnum, "finding": finding},
                            created_by="cpmp_monitor_subcon",
                            # sub_id is the row identity, so two subs sharing a
                            # company_name still get one card each; it falls back
                            # to the name, then to the category alone for the
                            # contract-level isr_ssr finding.
                            dedup_key=(
                                f"{cid}:{category}"
                                f":{finding.get('sub_id') or company or ''}"
                            ),
                            label=clabel,
                            stats=results,
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
                            label=clabel,
                            stats=results,
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
    label: str = None,
    stats: Dict = None,
) -> bool:
    """Create a kanban suggestion card, keyed so one finding is one row.

    Returns True only if a row was INSERTED, so callers count writes rather
    than attempts — an attempt-counter reports steady card creation forever
    while a working dedup writes nothing, and `_write_memory_log` persists that
    number. Repairing a stale title (below) is not a creation and returns
    False; it is counted separately under ``stats['cards_relabeled']``.

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

    The one exception is the card's TITLE. Because the id is derived from the
    contract and issue and never from the label, a card that predates a label
    fix keeps its unidentifiable title forever — the collision check returns
    before the title is ever compared. When ``label`` is supplied and the stored
    title is EXACTLY one this finding would have had under a superseded
    labelling rule (see `_superseded_titles`), it is rewritten in place. The
    match is against a finite set of known-bad titles, so a human edit or a
    pipeline rewrite never qualifies, and the ratchet only ever runs
    unidentifiable -> identifiable. Nothing else about the row is touched: not
    status, not priority, not description — repairing a name must not reopen,
    re-prioritise, or otherwise resurrect work.
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
        existing = conn.execute(
            "SELECT title FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if existing:
            stored = (dict(existing).get("title") or "")
            if stored.casefold() in _superseded_titles(title[:120], label):
                conn.execute(
                    "UPDATE kanban_tasks SET title = %s WHERE id = %s",
                    (title[:120], task_id),
                )
                conn.commit()
                if stats is not None:
                    stats["cards_relabeled"] = stats.get("cards_relabeled", 0) + 1
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
                f"({results['contracts_unnumbered']} unnumbered), "
                f"{results['overdue_marked']} newly overdue, "
                f"{results['issues_found']} issues, "
                f"{results['cards_created']} cards, "
                f"{results.get('cards_relabeled', 0)} relabeled, "
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
