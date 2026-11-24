# CUI // SP-CTI — signed factory builds governance twin (icx-ent-03)
"""Governance twin over the signed factory builds (``tools/factory_builds``).

Canvas key ``factory_builds`` (its console is /factory-builds/), observing the feature
``factory_builds.signed_builds``. The evidence and the judgement live in ``tools/factory_builds/service.py``; this
adapter only collects, judges and replays.

A target with no published signed build (or one whose declared step is not recorded) is an UNVERIFIED check, never a
pass. Findings, each threaded to the ``fb_events`` row they rest on (``factory_builds:<event id>``): ``chain_broken``,
``awaiting_publish``, ``self_published``, ``step_missing``, ``promotion_withdrawn`` (a published model demoted in the
fine-tuning registry since) and ``keyless_not_admitted``. The recorded platform observation (what MCP and the
observatory read) names SYNTHETIC targets only; every other target is only counted. The console's twin route judges
per reader (``judge_for``) and is never recorded.
"""
from __future__ import annotations

from tools.twin_core.governance import GovernanceTwinAdapter, load_policy, now_iso
from tools.twin_core.governance import replay as replay_observation
from tools.twin_core.registry import register_twin


@register_twin
class FactoryBuildsTwin(GovernanceTwinAdapter):
    canvas_key = "factory_builds"
    display_name = "Signed factory builds"
    observes = ("factory_builds.signed_builds",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.factory_builds.service import twin_evidence

        return {"canvas": self.canvas_key, "target_id": "factory_builds", "observed_at": now_iso(),
                "policy": load_policy("factory_builds"), "evidence": twin_evidence()}

    def judge_for(self, reader: dict) -> dict:
        """One reader's judgment, never recorded: only the targets that reader may see, the rest counted."""
        from tools.factory_builds.service import twin_evidence
        from tools.twin_core.governance import conclude, digest, unverified_findings

        ev = {"canvas": self.canvas_key, "target_id": "factory_builds", "observed_at": now_iso(),
              "policy": load_policy("factory_builds"), "evidence": twin_evidence(reader=reader)}
        judged = self.judge(ev)
        findings = list(judged["findings"]) + unverified_findings(self.canvas_key, judged["checks"], digest(ev))
        verdict, coverage = conclude(judged["checks"], findings)
        return {"scope": "reader", "observed_at": ev["observed_at"], "verdict": verdict, "coverage_score": coverage,
                "checks": judged["checks"], "findings": findings, "summary": judged["summary"]}

    def judge(self, evidence: dict) -> dict:
        from tools.factory_builds.service import twin_judge

        checks, findings, summary = twin_judge(evidence.get("evidence") or {}, evidence.get("policy") or {},
                                               evidence.get("observed_at") or "")
        unthreaded = [f["rule_id"] for f in findings if not str(f.get("thread_id") or "").startswith("factory_builds:")]
        if unthreaded:
            raise ValueError(f"factory build findings without a ledger thread: {unthreaded}")
        return {"checks": checks, "findings": findings, "summary": summary}
