# CUI // SP-CTI
"""Genesis Reflex: CPMP Monitor — proactive contract health surveillance.

Runs every 3 hours via Genesis daemon. One state pass, then four detection passes:
  0. Overdue Sweep   — compute_overdue_deliverables() → the only thing that ever
                       moves a CDRL to status 'overdue' and fills days_overdue
  1. PMO AI Issues   — auto_detect_issues() → kanban cards for critical/high findings
  2. CPARS Trajectory — predicted score declining toward Marginal → CAT2 alert
  3. Subcontractor Noncompliance — detect_noncompliance() → kanban high-priority
  4. Deliverable Auto-Generation — generate CDRLs due in 14 days

Pass type controlled by trigger_data['pass_type']:
  'full' (default) — all passes
  'deliverables'   — only the overdue sweep + deliverable auto-generation
                     (lightweight, every 3h)
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
        # Cards that were AGGREGATED because one code change closes every
        # affected row (tools/genesis/finding_scope.py). Counted inside
        # cards_created as well — it is one row written either way — and broken
        # out because the number of sessions this saves is the whole point.
        "code_level_cards": 0,
        "cpars_alerts": 0,
        "subcon_alerts": 0,
        "cdrl_generated": 0,
        "deliverables_marked_overdue": 0,
        "errors": [],
    }

    # ── Pass 0: drive the overdue state machine ───────────────────────
    #
    # contract_manager.compute_overdue_deliverables() is what moves a CDRL to
    # status 'overdue' and fills days_overdue. Until now NOTHING called it
    # outside its own argparse block, so on the live board no deliverable had
    # ever reached that state and days_overdue was 0 on every row — including
    # rows 44 days past due.
    #
    # That is not cosmetic, because 'overdue' is the ONLY thing four separate
    # consumers look at, and all four therefore read a permanent zero:
    #
    #   contract_manager.get_contract()      -> overdue_count on the contract page
    #   portfolio_manager                    -> per-contract count AND the
    #                                           portfolio-wide overdue list
    #   cpars_predictor                      -> overdue count feeds the predicted
    #                                           CPARS score, so the predictor is
    #                                           blind to schedule slip
    #   negative_event_tracker               -> gated on days_overdue > 0, so a
    #                                           late CDRL has never once been
    #                                           recorded as a negative event
    #
    # Meanwhile pmo_ai_advisor derives overdue LIVE from due_date, which is why
    # this reflex files a high-severity "N CDRL(s) are past due" card while the
    # contract page next to it says 0 overdue. Same table, two definitions, and
    # only one of them had anything driving it.
    #
    # Runs before the contract loop and unscoped: one query covers deliverables
    # on non-active contracts too (the loop only walks active ones), and one
    # audit row per cycle instead of one per contract. Guarded, because a sweep
    # that raises must not take the surveillance passes below down with it.
    # MERGE NOTE: main fixed this same defect independently (#1618) with a second
    # sweep further down, so merging the two branches textually produced TWO
    # calls — git saw no conflict, and only the count assertions here caught it.
    # Kept as ONE call at this position because the tests pin three things the
    # other placement cannot satisfy: it must run on the 'deliverables' pass as
    # well as 'full', it must precede the issue-detection pass, and it must be
    # unscoped. Both result-key sets are reported so neither side's consumers
    # break — `deliverables_marked_overdue` and main's `overdue_marked` /
    # `overdue_refreshed` describe the same sweep.
    if pass_type in ("full", "deliverables"):
        try:
            from tools.govcon.contract_manager import compute_overdue_deliverables
            swept = compute_overdue_deliverables()
            results["deliverables_marked_overdue"] = swept.get("overdue_count", 0)
            results["overdue_marked"] = swept.get("overdue_count", 0)
            results["overdue_refreshed"] = swept.get("days_refreshed", 0)
        except Exception as e:
            results["errors"].append(f"Overdue sweep: {e}")

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
        # Same contract shape as the success path below — the error return had no
        # 'success' key either, so a reflex that failed to even open a connection
        # was scored identically to one that swept cleanly: both False, both 0.0.
        results["errors"].append(str(e))
        return {
            **results,
            "status": "error",
            "message": str(e),
            "success": False,
            "metric_value": 0,
            "details": results,
            "error": str(e),
        }

    results["contracts_scanned"] = len(active)

    # (main's duplicate Pass-0 sweep was folded into the single call above during
    # the merge — see the MERGE NOTE there. Its portfolio-wide rationale still
    # holds and is why that call passes no contract_id: the loop below visits
    # only status='active' contracts, while portfolio_manager counts overdue
    # CDRLs across ('active', 'option_pending'), and an option-pending
    # contract's deliverables are no less late.)

    # Pass 3 collects across the whole loop and files below it — see the pass
    # header for why scoping cannot happen inside the loop.
    subcon_findings = []
    subcon_population = 0

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
        #
        # COLLECTS here and files after the loop. Scoping a finding — one card
        # for a code defect vs one card per row for a data problem — needs the
        # whole population, and a reflex that writes inside its row loop can
        # never see it. That blindness is the defect this pass shipped: it filed
        # a [SUBCON] ISR/SSR card per contract, seven cards for ONE missing
        # applicability gate, and four sessions fixed the same bug independently
        # (#1628 landed; #1629, #1633, #1635 closed as redundant, two of them
        # having created the same test file path). See tools/genesis/finding_scope.py.
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
                # Counted only once the scan SUCCEEDED. Saturation is "fired on
                # every row it examined", so a contract whose scan raised was
                # never examined and must not dilute the ratio.
                subcon_population += 1
                high_findings = [f for f in findings if f.get("severity") in ("high", "critical")]
                for finding in high_findings:
                    try:
                        subcon_findings.append(
                            _subcon_finding(contract, clabel, cnum, finding)
                        )
                    except Exception as ce:
                        results["errors"].append(f"Subcon finding {cnum}: {ce}")
            except Exception as e:
                results["errors"].append(f"Subcon scan {cnum}: {e}")

        # ── Pass 4: Deliverable 14-Day Auto-Generation ─────────────────
        #
        # The per-contract overdue sweep that used to sit here was REMOVED in the
        # merge with origin/main, which had independently fixed the same defect
        # with the unscoped Pass 0 above. Keeping both was a genuine double
        # sweep: git auto-merged the two branches without a conflict because
        # they touched different regions of this function, so nothing flagged
        # it. Pass 0 strictly dominates — it is called once per cycle instead of
        # once per contract, and it is unscoped, so it also reaches deliverables
        # on option_pending contracts that this loop (status='active' only)
        # never visits, while portfolio_manager counts those as overdue.
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

    # ── Pass 3, filing half ───────────────────────────────────────────
    _file_subcon_cards(subcon_findings, subcon_population, results)

    _write_memory_log(results)
    results["status"] = "ok"
    # GenesisDaemon._run_reflex_impl_inner (tools/genesis/daemon.py) reads
    # success/metric_value/details off this dict. Returning the bare results dict
    # meant `result.get("success", False)` was ALWAYS False — including on a
    # perfectly clean sweep — and metric_value defaulted to 0.0 instead of
    # cards_created. The configured success_metric is `cards_created >= 0`, which
    # 0 satisfies, so this was never a threshold miss: the reflex was scored a
    # failure every single run until the circuit breaker tripped and switched it
    # off, and nothing went red. The sibling reflex in this directory
    # (pmo_option_tracker) has always returned this shape; cpmp_monitor never did.
    #
    # `**results` is spread ALONGSIDE the envelope on purpose, not by accident.
    # Three gated suites — test_genesis_reflex_cpmp_monitor.py, _card_identity.py
    # and _subcon_pass.py — read this return FLAT (`run()["cards_created"]`,
    # `["subcon_alerts"]`). Returning the bare envelope alone breaks 16 of their
    # assertions with KeyError. The daemon only ever reads success/metric_value/
    # details, so carrying both shapes satisfies the daemon without a breaking
    # change to a return value three CI-enforced suites already pin. `details` is
    # the same object, so the two views cannot drift.
    return {**results, "success": True, "metric_value": results["cards_created"], "details": results}


_SUBCON_SOURCE = "cpmp_monitor_subcon"

# Card description budget. Was 500, which is ample for a card about one row and
# too small for an AGGREGATED card, whose whole value is the list of affected
# rows it carries instead of the N-1 cards it replaces. kanban_tasks.description
# is TEXT, so the cap is a readability bound, not a schema one; the evidence
# block reports its own truncation separately (`max_evidence_rows`).
_DESCRIPTION_MAX = 2000


def _subcon_category(finding: Dict):
    """(category, display label, corrective action) for one subcon finding."""
    category = finding.get("category") or "noncompliance"
    label, action = _SUBCON_CATEGORIES.get(
        category,
        (category.replace("_", " ").title(), "Review with the subcontract manager."),
    )
    return category, label, action


def _subcon_finding(contract: Dict, clabel: str, cnum: str, finding: Dict):
    """Wrap one detect_noncompliance() finding for tools/genesis/finding_scope."""
    from tools.genesis.finding_scope import Finding

    cid = contract["id"]
    category, _label, action = _subcon_category(finding)
    # 'category' and 'company_name' are the keys the findings carry;
    # 'issue_type'/'subcontractor_name' never existed.
    company = finding.get("company_name")
    # sub_id is the row identity, so two subs sharing a company_name still get
    # one card each; it falls back to the name, then to empty for the
    # contract-level isr_ssr finding, which has no subcontractor at all.
    instance = finding.get('sub_id') or company or ""

    return Finding(
        # The contract is what the check EXAMINED, so it is the unit saturation
        # is measured over — one contract with four bad subs is still one row
        # looked at, not four.
        subject=str(cid),
        category=category,
        # Unchanged from before this seam existed, so cards already on the board
        # keep colliding with their own id instead of being re-filed.
        dedup_key=f"{cid}:{category}:{instance}",
        # The REMEDY's identity: what a human would be told to do, with nothing
        # about which contract it came from. Two contracts merge only when the
        # instruction is literally the same text — a finding naming its own
        # subcontractor carries that name here and never merges with another's.
        signature=f"{category}|{instance}|{finding.get('description', '')}|{action}",
        evidence=f"{clabel} ({str(cid)[:8]})",
        payload={
            "contract_id": cid,
            "contract_number": cnum,
            "contract_label": clabel,
            "finding": finding,
        },
    )


class _PerRowSpec:
    """A one-finding CardSpec built without importing finding_scope.

    The fallback for a scoping failure, so the pass keeps working even when the
    module it now depends on cannot be imported at all.
    """

    is_aggregated = False

    def __init__(self, finding):
        self.findings = (finding,)
        self.dedup_key = finding.dedup_key


def _file_subcon_cards(findings, population: int, results: Dict) -> None:
    """File pass 3's cards, one per finding OR one per code-level defect.

    Fail-soft in the SAFE direction: if scoping raises, the findings fall back
    to one card per row — the behaviour that predates this seam. Over-reporting
    costs a redundant card; returning early would lose the pass's findings
    entirely and report the silence as a clean sweep, which is the failure mode
    this reflex has already shipped twice.
    """
    if not findings:
        return
    config = None
    try:
        from tools.genesis import finding_scope as fs
        config = fs.load_config()
        specs = fs.group(_SUBCON_SOURCE, findings, population, config)
    except Exception as e:
        results["errors"].append(f"Subcon scoping: {e}")
        specs = [_PerRowSpec(f) for f in findings]

    for spec in specs:
        try:
            if spec.is_aggregated:
                title, description, context = _aggregated_subcon_card(
                    spec, population, config
                )
                label = None  # no contract in the title, so nothing to relabel
            else:
                title, description, context = _per_row_subcon_card(spec.findings[0])
                label = spec.findings[0].payload.get("contract_label")

            wrote = _suggest_kanban_card(
                title=title,
                description=description,
                priority="high",
                context_data=context,
                created_by=_SUBCON_SOURCE,
                dedup_key=spec.dedup_key,
                label=label,
                stats=results,
            )
            if wrote:
                results["subcon_alerts"] += 1
                results["cards_created"] += 1
                if spec.is_aggregated:
                    results["code_level_cards"] += 1
        except Exception as ce:
            results["errors"].append(f"Subcon card {spec.dedup_key}: {ce}")


def _per_row_subcon_card(finding):
    """One contract, one subcontractor gap — the data-level card, unchanged."""
    raw = finding.payload["finding"]
    clabel = finding.payload["contract_label"]
    _category, label, action = _subcon_category(raw)
    company = raw.get("company_name")
    # isr_ssr is contract-level: it has no subcontractor, and printing
    # "Subcontractor: None" reads as missing data.
    subject = f"Subcontractor: {company}\n" if company else ""
    return (
        f"[SUBCON] {clabel}: {label}",
        (
            f"Contract: {clabel}\n"
            f"{subject}"
            f"Issue: {raw.get('description','')}\n"
            f"Severity: {raw.get('severity','').upper()}\n"
            f"Action: {action}"
        ),
        {
            "contract_id": finding.payload["contract_id"],
            "contract_number": finding.payload["contract_number"],
            "finding": raw,
        },
    )


def _aggregated_subcon_card(spec, population: int, config):
    """One card for every affected contract, because one fix closes them all.

    The affected contracts are carried in the DESCRIPTION as evidence and in the
    context data in full — aggregating must not lose what N cards would have
    said, which is exactly how a title-based dedup fails.
    """
    from tools.genesis.finding_scope import evidence_block

    raw = spec.findings[0].payload["finding"]
    _category, label, action = _subcon_category(raw)
    return (
        f"[SUBCON] {label}: identical on all {population} contracts — verify the check",
        (
            f"Issue: {raw.get('description','')}\n"
            f"Severity: {str(raw.get('severity','')).upper()}\n"
            f"Action if genuine: {action}\n"
            f"Scoped code-level because: {spec.reason}\n\n"
            + evidence_block(spec, population, _SUBCON_SOURCE, config)
        ),
        {
            "scope": spec.scope,
            "category": spec.category,
            "reason": spec.reason,
            "population": population,
            "affected": [f.payload for f in spec.findings],
        },
    )


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
                description[:_DESCRIPTION_MAX],
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
                f"{results['cards_created']} cards "
                # The number of sessions the scoping seam saved: an aggregated
                # card is one card standing in for one per affected row.
                f"({results.get('code_level_cards', 0)} aggregated code-level), "
                f"{results.get('cards_relabeled', 0)} relabeled, "
                f"{results['cpars_alerts']} CPARS alerts, "
                f"{results['cdrl_generated']} CDRLs generated, "
                # NOT deliverables_marked_overdue — the merge left both keys set
                # from the same swept['overdue_count'], so printing both read
                # "N newly overdue ... N newly overdue". days_overdue refreshes
                # are the other half of the sweep and the number that shows a
                # lengthening delinquency rather than a new one.
                f"{results['overdue_refreshed']} overdue rows refreshed."
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
