# CUI // SP-CTI — evidence collectors governance twin (icx-ent-02)
"""Governance twin over the evidence collectors for customer environments (``tools/evidence_collectors``).

Canvas key ``evidence_collectors`` (its console is /evidence-collectors/), observing the feature
``evidence_collectors.objective_evidence``. The evidence and the judgement live in ``tools/evidence_collectors/
service.py``; this adapter only collects, judges and replays.

A system with no published objective-evidence report is an UNVERIFIED check, never a pass. Findings, each threaded to
the ``ec_events`` row they rest on (``evidence_collectors:<event id>``): ``other_than_satisfied`` (high: an objective
a collector observed other than satisfied), ``stale`` (the newest published report is past the shortest collector
budget: its observations count for nothing), ``unverified_objectives``, ``awaiting_publish``, ``self_published`` and
``chain_broken`` and ``step_missing`` (a decision whose declared step was never recorded: it decides nothing). A
system above the twin's marking (CUI) is counted, never described. The recorded platform observation (what MCP and
the observatory read) names SYNTHETIC systems only; customer systems are totals (``customers.*``, threaded to the
observation itself), with no id, ordinal, event, time or digest, banded, and only from five customer systems up. The console's twin route judges per reader
(``judge_for``: that reader's own systems by name) and is never recorded.
"""
from __future__ import annotations

from tools.twin_core.governance import GovernanceTwinAdapter, load_policy, now_iso
from tools.twin_core.governance import replay as replay_observation
from tools.twin_core.registry import register_twin


@register_twin
class EvidenceCollectorsTwin(GovernanceTwinAdapter):
    canvas_key = "evidence_collectors"
    display_name = "Evidence collectors for customer environments"
    observes = ("evidence_collectors.objective_evidence",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        """The platform observation: synthetic systems by name, customer systems as totals only (twin_evidence)."""
        from tools.evidence_collectors.service import twin_evidence

        at, policy = now_iso(), load_policy("evidence_collectors")
        return {"canvas": self.canvas_key, "target_id": "evidence_collectors", "observed_at": at, "policy": policy,
                "evidence": twin_evidence(observed_at=at,
                                          waiting_days=float((policy or {}).get("waiting_warn_days") or 7))}

    def judge_for(self, reader: dict) -> dict:
        """One reader's judgment, never recorded: only the systems that reader may see, the rest counted."""
        from tools.evidence_collectors.service import twin_evidence

        ev = {"canvas": self.canvas_key, "target_id": "evidence_collectors", "observed_at": now_iso(),
              "policy": load_policy("evidence_collectors"), "evidence": twin_evidence(reader=reader)}
        from tools.twin_core.governance import conclude, digest, unverified_findings

        judged = self.judge(ev)
        findings = list(judged["findings"]) + unverified_findings(self.canvas_key, judged["checks"], digest(ev))
        verdict, coverage = conclude(judged["checks"], findings)
        return {"scope": "reader", "observed_at": ev["observed_at"], "verdict": verdict, "coverage_score": coverage,
                "checks": judged["checks"], "findings": findings, "summary": judged["summary"]}

    def judge(self, evidence: dict) -> dict:
        from tools.evidence_collectors.service import twin_judge
        from tools.twin_core.governance import digest, observation_id

        # The customer totals' thread is this observation itself (as for an unverified check).
        obs_thread = f"twin_obs:{observation_id(self.canvas_key, digest(evidence))}#evidence_collectors.customers"
        checks, findings, summary = twin_judge(evidence.get("evidence") or {}, evidence.get("policy") or {},
                                               evidence.get("observed_at") or "", obs_thread=obs_thread)
        unthreaded = [f["rule_id"] for f in findings
                      if not str(f.get("thread_id") or "").startswith(("evidence_collectors:", obs_thread))]
        if unthreaded:
            raise ValueError(f"evidence collector findings without a ledger thread: {unthreaded}")
        return {"checks": checks, "findings": findings, "summary": summary}
