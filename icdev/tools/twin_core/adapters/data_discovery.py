# CUI // SP-CTI — Data discovery export governance twin (osx-cap-02)
"""Governance twin over the data discovery export to ICDEV[OS], canvas key ``data_discovery``.

It observes ``data.discovery_export`` (``tools/data_canvas/discovery_bundle.py`` holds the evidence and the
judgement): each declared source's ``icos-discovery/v1`` bundles, prepared and published, in the append-only,
hash-chained ``data_discovery_export_events``. A source with no published bundle is an unverified check, never a
pass, and so is a deployment that declares no source at all. Findings, each threaded to the ledger row it rests on
(``data_discovery:<event id>``):

* ``data.discovery.quality_failing`` (high) a quality rule the newest published bundle carries as failed;
* ``data.discovery.quality_unrun`` (low) a quality rule that never ran before the bundle was published;
* ``data.discovery.stale`` (medium) / ``.awaiting_publish`` (low) / ``.self_published`` / ``.chain_broken``
  (critical).

An unreadable ledger is an unverified check. The judgement is pure, so a recorded observation replays.
"""
from __future__ import annotations

from tools.twin_core.governance import (
    GovernanceTwinAdapter,
    load_policy,
    now_iso,
)
from tools.twin_core.governance import (
    replay as replay_observation,
)
from tools.twin_core.registry import register_twin


@register_twin
class DataDiscoveryTwin(GovernanceTwinAdapter):
    canvas_key = "data_discovery"
    display_name = "Data discovery export"
    # A literal: the conformance audit reads it without importing this module.
    observes = ("data.discovery_export",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.data_canvas import discovery_bundle

        return {"canvas": self.canvas_key, "target_id": "data.discovery_export", "observed_at": now_iso(),
                "policy": load_policy("data_discovery"), "discovery": discovery_bundle.twin_evidence()}

    def judge(self, evidence: dict) -> dict:
        from tools.data_canvas import discovery_bundle

        policy = evidence.get("policy") or {}
        checks, findings, summary = discovery_bundle.twin_judge(evidence.get("discovery") or {}, policy,
                                                                evidence.get("observed_at") or "")
        # Every violation here carries a thread_id: a ledger row, or the observation itself.
        cap = int(policy.get("max_findings") or 50)
        return {"checks": checks, "findings": findings[:cap], "summary": summary}
