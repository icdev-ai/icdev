# CUI // SP-CTI — store publisher governance twin (icx-ent-04)
"""Governance twin over the store publisher (``tools/store_publisher``).

Canvas key ``store_publisher`` (its console is /store-publisher/), observing the feature
``store_publisher.signed_packages``. The evidence and the judgement live in ``tools/store_publisher/service.py``; this
adapter only collects, judges and replays.

A package with no published version (or one whose declared step is not recorded) is an UNVERIFIED check, never a pass.
Findings, each threaded to the ``sp_events`` row they rest on (``store_publisher:<event id>``): ``chain_broken``,
``awaiting_publish``, ``self_published``, ``step_missing``, ``gate_not_passed`` (published with a scanner gate that did
not pass: ICDEV[OS] will block it) and ``redteam_not_passed``. The recorded platform observation (what MCP and the
observatory read) names SYNTHETIC packages only; every other package is only counted. The console's twin route judges
per reader (``judge_for``) and is never recorded.
"""
from __future__ import annotations

from tools.twin_core.governance import GovernanceTwinAdapter, load_policy, now_iso
from tools.twin_core.governance import replay as replay_observation
from tools.twin_core.registry import register_twin


@register_twin
class StorePublisherTwin(GovernanceTwinAdapter):
    canvas_key = "store_publisher"
    display_name = "Store publisher"
    observes = ("store_publisher.signed_packages",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.store_publisher.service import twin_evidence

        return {"canvas": self.canvas_key, "target_id": "store_publisher", "observed_at": now_iso(),
                "policy": load_policy("store_publisher"), "evidence": twin_evidence()}

    def judge_for(self, reader: dict) -> dict:
        """One reader's judgment, never recorded: only the packages that reader may see, the rest counted."""
        from tools.store_publisher.service import twin_evidence
        from tools.twin_core.governance import conclude, digest, unverified_findings

        ev = {"canvas": self.canvas_key, "target_id": "store_publisher", "observed_at": now_iso(),
              "policy": load_policy("store_publisher"), "evidence": twin_evidence(reader=reader)}
        judged = self.judge(ev)
        findings = list(judged["findings"]) + unverified_findings(self.canvas_key, judged["checks"], digest(ev))
        verdict, coverage = conclude(judged["checks"], findings)
        return {"scope": "reader", "observed_at": ev["observed_at"], "verdict": verdict, "coverage_score": coverage,
                "checks": judged["checks"], "findings": findings, "summary": judged["summary"]}

    def judge(self, evidence: dict) -> dict:
        from tools.store_publisher.service import twin_judge

        checks, findings, summary = twin_judge(evidence.get("evidence") or {}, evidence.get("policy") or {},
                                               evidence.get("observed_at") or "")
        unthreaded = [f["rule_id"] for f in findings if not str(f.get("thread_id") or "").startswith("store_publisher:")]
        if unthreaded:
            raise ValueError(f"store publisher findings without a ledger thread: {unthreaded}")
        return {"checks": checks, "findings": findings, "summary": summary}
