# CUI // SP-CTI — Engineering standards register governance twin (icx-coe-01)
"""Governance twin over the engineering standards register (``tools/idp/standards.py``).

Canvas key ``idp`` (the register's console is /idp/standards), observing the
feature ``coe.standards``. It reads the ledger ``coe_standard_events``, the
declared seeds in ``args/engineering_standards.yaml`` and the IDP scorecards,
and judges what a person should know about the register:

* ``coe.chain.broken``            a row whose hash or entry digest does not
                                  verify, or that is off its standard's chain
                                  (critical; the history cannot be trusted);
* ``coe.in_force.check_unresolved`` a standard in force whose check no longer
                                  resolves (a scorecard rule removed, an
                                  evidence tool deleted): a wish again (high);
* ``coe.in_force.source_drifted``  a repo file a standard in force cites has
                                  changed since it was adopted (medium);
* ``coe.in_force.bootstrap_only``  a standard in force that only the labelled
                                  bootstrap identity recorded: no person has
                                  signed it (low);
* ``coe.draft.waiting``            a draft proposed or reviewed longer ago than
                                  the policy allows (low);
* ``coe.seed.unrecorded``          a declared seed with no ledger row (medium).

Every finding's thread is the ledger row it rests on (``coe_standard:<event
id>``), or the register file (``config:args/engineering_standards.yaml@<sha>``).
Nothing read is never a pass: an unreadable ledger, register or scorecard set is
an unverified check.

icx-coe-02 adds the rest of the CoE's records on the same canvas, observing
``coe.exemptions`` and ``coe.adrs`` (``tools/idp/exemptions.py`` and
``tools/idp/adrs.py`` hold their evidence and judgement):

* ``coe.exemption.expired``        an approved exemption past its expiry: it
                                  fails the scorecard (high), threaded to its row;
* ``coe.exemption.expiring``       one inside the warning window (low);
* ``coe.exemption.migrated``       given an expiry by the migration, not a
                                  person: the owner reviews it (medium);
* ``coe.exemption.no_expiry`` / ``.self_approved`` / ``.unknown_standard`` /
  ``.config_grant`` / ``.chain_broken``;
* ``coe.adr.doc_drift``            the flat ADR log differs from the records;
* ``coe.adr.unexported``           a ledger decision not yet in its record;
* ``coe.adr.record_invalid`` / ``.proposed.waiting`` / ``.chain_broken``.

An ADR record the ledger has never seen is an unverified check, never a pass.
Expiry is judged on the observation's date, so a replay judges the same.

icx-coe-03 observes ``coe.conformance`` too (``tools/idp/conformance.py`` holds
the evidence and judgement): each declared repo's conformance bundles, prepared
and published, in ``coe_conformance_events``. A repo with no published bundle is
an unverified check, never a pass. Findings, each threaded to the ledger row
(``coe_conformance:<event id>``):

* ``coe.conformance.standard_failing`` / ``.expired_exemption`` (high) what the
                                  newest published bundle carries;
* ``coe.conformance.metric_unverified`` (low) a delivery metric with no data;
* ``coe.conformance.stale`` / ``.awaiting_publish`` / ``.self_published`` /
  ``.chain_broken``.

icx-coe-04 observes ``coe.golden_paths`` too (``tools/idp/golden_paths.py`` holds the
evidence and judgement): each golden path's versions in ``coe_golden_path_events``
against the standards in force. A path with no published version, or a version in
use never drift-checked, is an unverified check, never a pass. Findings, each
threaded to the ledger row (``coe_golden_path:<event id>``):

* ``coe.golden_path.behind`` (high) a standard in force that applies is not declared;
* ``coe.golden_path.drift`` (high) / ``.drift_stale`` (medium) / ``.drift_old`` (low);
* ``coe.golden_path.bootstrap_only`` / ``.awaiting_publish`` / ``.declares_retired_standard``
  (low), ``.self_published`` / ``.chain_broken`` (critical).
"""
from __future__ import annotations

from tools.twin_core.governance import (
    GovernanceTwinAdapter,
    age_hours,
    check,
    finding,
    load_policy,
    now_iso,
)
from tools.twin_core.governance import (
    replay as replay_observation,
)
from tools.twin_core.registry import register_twin


@register_twin
class EngineeringStandardsTwin(GovernanceTwinAdapter):
    canvas_key = "idp"
    display_name = "Engineering standards register"
    observes = ("coe.standards", "coe.exemptions", "coe.adrs", "coe.conformance", "coe.golden_paths")

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        ev = self._collect_standards()
        from tools.idp import adrs, conformance, exemptions, golden_paths

        # icx-coe-02: the exemptions and the ADRs, read the same way (never raising).
        ev["exemptions"] = exemptions.twin_evidence()
        ev["adrs"] = adrs.twin_evidence()
        # icx-coe-03: the conformance bundles per repo.
        ev["conformance"] = conformance.twin_evidence()
        # icx-coe-04: the golden paths against the standards in force.
        ev["golden_paths"] = golden_paths.twin_evidence()
        return ev

    def _collect_standards(self) -> dict:
        from tools.idp import standards

        ev: dict = {"canvas": self.canvas_key, "target_id": "coe.standards",
                    "observed_at": now_iso(), "policy": load_policy("engineering_standards"),
                    "register": None, "ledger": None, "errors": {}}
        try:
            reg = standards.load_register()
            ev["register"] = {"path": standards.REGISTER_RELPATH, "sha256": reg["_sha256"],
                              "seeds": sorted(standards.normalise(e)["id"]
                                              for e in reg["standards"])}
        except Exception as exc:  # noqa: BLE001 — unreadable is unverified
            ev["errors"]["register"] = str(exc)
        try:
            rows = standards.read_events()
            ch = standards.chain(rows)
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["ledger"] = str(exc)
            return ev
        try:
            cards = standards._scorecards(standards.BASE_DIR)
        except Exception as exc:  # noqa: BLE001
            ev["errors"]["scorecards"] = str(exc)
            cards = None
        items = []
        for sid, ordered in sorted(ch.items()):
            if sid == "_problems":
                continue
            st = standards.replay(ordered)
            force = st["versions"].get(st["in_force"]) if st["in_force"] else None
            latest = st["latest"] or {}
            item = {"id": sid, "state": latest.get("state"), "version": latest.get("version"),
                    "last_event": st["history"][-1]["event_id"] if st["history"] else None,
                    "last_at": st["history"][-1]["created_at"] if st["history"] else None,
                    "in_force": None}
            if force is not None:
                now_sources = {s.get("path"): s.get("sha256")
                               for s in standards.pin_sources(force["entry"]) if s["kind"] == "path"}
                item["in_force"] = {
                    "version": force["version"], "event": force.get("adopted_event"),
                    "by": force.get("adopted_by"), "kind": force.get("adopted_kind"),
                    "check": (standards.resolve_check(force["entry"], scorecards=cards)
                              if cards is not None else None),
                    "sources_then": {s.get("path"): s.get("sha256")
                                     for s in force.get("adopted_sources") or []
                                     if s.get("kind") == "path"},
                    "sources_now": now_sources,
                }
            items.append(item)
        ev["ledger"] = {"rows": len(rows), "problems": ch["_problems"], "standards": items}
        return ev

    def judge(self, evidence: dict) -> dict:
        errors = evidence.get("errors") or {}
        policy = evidence.get("policy") or {}
        reg, ledger = evidence.get("register"), evidence.get("ledger")
        checks = [
            check("coe.register", "Declared seeds (args/engineering_standards.yaml)",
                  reg is not None, errors.get("register") or "register not read"),
            check("coe.ledger", "Lifecycle ledger (coe_standard_events)", ledger is not None,
                  errors.get("ledger") or "ledger not read"),
            check("coe.scorecards", "IDP scorecards (args/scorecards/)",
                  ledger is not None and "scorecards" not in errors,
                  errors.get("scorecards") or "not read: the ledger was not read"),
        ]
        findings = []
        cap = int(policy.get("max_findings") or 50)
        for p in (ledger or {}).get("problems") or []:
            findings.append(finding(
                "coe.chain.broken", "critical", f"{p['standard_id']}: {p['problem']}",
                thread_id=f"coe_standard:{p['event_id']}",
                recommendation="The history of this standard cannot be trusted; find who wrote "
                               "the row (the ledger is append-only) and treat it as an incident."))
        recorded = set()
        waiting_days = float(policy.get("draft_waiting_warn_days") or 14)
        for s in (ledger or {}).get("standards") or []:
            recorded.add(s["id"])
            force = s.get("in_force")
            if force:
                chk = force.get("check")
                if chk is not None and not chk.get("resolved"):
                    findings.append(finding(
                        "coe.in_force.check_unresolved", "high",
                        f"{s['id']} {force['version']} is in force but its check no longer resolves",
                        thread_id=f"coe_standard:{force['event']}", detail=chk.get("why", ""),
                        recommendation="Propose a new version with a check that exists, or "
                                       "deprecate it: a standard without a check is a wish."))
                drifted = sorted(p for p, then in (force.get("sources_then") or {}).items()
                                 if then and (force.get("sources_now") or {}).get(p) != then)
                if drifted:
                    findings.append(finding(
                        "coe.in_force.source_drifted", "medium",
                        f"{s['id']} {force['version']}: {len(drifted)} source(s) changed since adoption",
                        thread_id=f"coe_standard:{force['event']}", detail=", ".join(drifted),
                        recommendation="A steward reads the change and keeps the standard, or "
                                       "proposes a new version that says what changed."))
                if force.get("kind") == "bootstrap":
                    findings.append(finding(
                        "coe.in_force.bootstrap_only", "low",
                        f"{s['id']} {force['version']} is in force on a bootstrap seed: "
                        "no person has signed it",
                        thread_id=f"coe_standard:{force['event']}",
                        recommendation="The owner proposes it as a new version and a steward "
                                       "signs it, or leaves it recorded as seeded."))
            if s.get("state") in ("proposed", "reviewed"):
                age = age_hours(s.get("last_at"), evidence.get("observed_at"))
                if age is not None and age > waiting_days * 24:
                    findings.append(finding(
                        "coe.draft.waiting", "low",
                        f"{s['id']} {s['version']} has been {s['state']} for {age / 24:.0f} days",
                        thread_id=f"coe_standard:{s['last_event']}",
                        recommendation="A steward reviews, returns, signs or refuses it."))
        if reg is not None and ledger is not None:
            for sid in reg["seeds"]:
                if sid not in recorded:
                    findings.append(finding(
                        "coe.seed.unrecorded", "medium",
                        f"{sid} is declared in the register but not in the ledger",
                        thread_id=f"config:{reg['path']}@{reg['sha256'][:12]}",
                        recommendation="Run python tools/idp/standards.py --seed."))
        # icx-coe-02: the exemptions and the ADRs (absent from an observation recorded before).
        extra_summary: dict = {}
        if "exemptions" in evidence:
            from tools.idp import exemptions

            c, f, s = exemptions.twin_judge(evidence["exemptions"] or {}, policy,
                                            evidence.get("observed_at") or "")
            checks += c
            findings += f
            extra_summary.update(s)
        if "adrs" in evidence:
            from tools.idp import adrs

            c, f, s = adrs.twin_judge(evidence["adrs"] or {}, policy,
                                      evidence.get("observed_at") or "")
            checks += c
            findings += f
            extra_summary.update(s)
        if "conformance" in evidence:
            from tools.idp import conformance

            c, f, s = conformance.twin_judge(evidence["conformance"] or {},
                                             policy.get("conformance") or {},
                                             evidence.get("observed_at") or "")
            checks += c
            findings += f
            extra_summary.update(s)
        if "golden_paths" in evidence:
            from tools.idp import golden_paths

            c, f, s = golden_paths.twin_judge(evidence["golden_paths"] or {},
                                              policy.get("golden_paths") or {},
                                              evidence.get("observed_at") or "")
            checks += c
            findings += f
            extra_summary.update(s)
        if len(findings) > cap:
            findings = findings[:cap]
        items = (ledger or {}).get("standards") or []
        summary = {
            "standards": len(items),
            "in_force": sum(1 for s in items if s.get("in_force")),
            "signed_by_a_person": sum(1 for s in items
                                      if (s.get("in_force") or {}).get("kind") == "person"),
            "drafts": sum(1 for s in items if s.get("state") in ("proposed", "reviewed")),
            "ledger_rows": (ledger or {}).get("rows"),
            "chain_intact": ledger is not None and not ledger.get("problems"),
            **extra_summary,
        }
        return {"checks": checks, "findings": findings, "summary": summary}
