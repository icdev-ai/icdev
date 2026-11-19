# CUI // SP-CTI — AI-assisted vulnerability discovery governance twin (icx-ent-01)
"""Governance twin over AI-assisted vulnerability discovery (``tools/vulnresearch``).

Canvas key ``vulnresearch`` (its console is /vulnresearch/), observing the feature ``vulnresearch.discovery``. The
evidence and the judgement live in ``tools/vulnresearch/twin.py``; this adapter only collects, judges and replays.

An authorised target with no published findings bundle, and a pipeline with no benchmark measurement, are
UNVERIFIED checks, never passes. Findings, each threaded to the ``vr_events`` row they rest on
(``vulnresearch:<event id>``): ``finding_unpatched`` (a PoV-confirmed finding with no accepted patch),
``authorisation_expired``, ``awaiting_authorisation``, ``patch_awaiting_review``, ``bundle_awaiting_publish``,
``bundle_stale``, ``self_published`` and ``chain_broken`` (critical), ``labels_unreviewed`` and
``isolation_container`` (low: said plainly, never hidden).
"""
from __future__ import annotations

from tools.twin_core.governance import GovernanceTwinAdapter, load_policy, now_iso
from tools.twin_core.governance import replay as replay_observation
from tools.twin_core.registry import register_twin


@register_twin
class VulnResearchTwin(GovernanceTwinAdapter):
    canvas_key = "vulnresearch"
    display_name = "AI-assisted vulnerability discovery"
    observes = ("vulnresearch.discovery",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.vulnresearch.twin import twin_evidence

        ev = {"canvas": self.canvas_key, "target_id": "vulnresearch", "observed_at": now_iso(),
              "policy": load_policy("vulnresearch")}
        ev["discovery"] = twin_evidence()
        return ev

    def judge(self, evidence: dict) -> dict:
        from tools.vulnresearch.twin import twin_judge

        checks, findings, summary = twin_judge(evidence.get("discovery") or {}, evidence.get("policy") or {},
                                               evidence.get("observed_at") or "")
        # Every finding names the vr_events row it rests on (governance.finding refuses one without).
        unthreaded = [f["rule_id"] for f in findings if not str(f.get("thread_id") or "").startswith("vulnresearch:")]
        if unthreaded:
            raise ValueError(f"vulnresearch findings without a ledger thread: {unthreaded}")
        return {"checks": checks, "findings": findings, "summary": summary}
