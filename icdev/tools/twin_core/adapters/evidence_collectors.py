# CUI // SP-CTI — evidence collectors governance twin (icx-ent-02)
"""Governance twin over the evidence collectors for customer environments (``tools/evidence_collectors``).

Canvas key ``evidence_collectors`` (its console is /evidence-collectors/), observing the feature
``evidence_collectors.objective_evidence``. The evidence and the judgement live in ``tools/evidence_collectors/
service.py``; this adapter only collects, judges and replays.

A system with no published objective-evidence report is an UNVERIFIED check, never a pass. Findings, each threaded to
the ``ec_events`` row they rest on (``evidence_collectors:<event id>``): ``other_than_satisfied`` (high: an objective
a collector observed other than satisfied), ``stale`` (the newest published report is past the shortest collector
budget: its observations count for nothing), ``unverified_objectives``, ``awaiting_publish``, ``self_published`` and
``chain_broken``. A system above the twin's marking (CUI) is counted, never described.
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
        from tools.evidence_collectors.service import twin_evidence

        return {"canvas": self.canvas_key, "target_id": "evidence_collectors", "observed_at": now_iso(),
                "policy": load_policy("evidence_collectors"), "evidence": twin_evidence()}

    def judge(self, evidence: dict) -> dict:
        from tools.evidence_collectors.service import twin_judge

        checks, findings, summary = twin_judge(evidence.get("evidence") or {}, evidence.get("policy") or {},
                                               evidence.get("observed_at") or "")
        unthreaded = [f["rule_id"] for f in findings
                      if not str(f.get("thread_id") or "").startswith("evidence_collectors:")]
        if unthreaded:
            raise ValueError(f"evidence collector findings without a ledger thread: {unthreaded}")
        return {"checks": checks, "findings": findings, "summary": summary}
