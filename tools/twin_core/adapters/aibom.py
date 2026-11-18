# CUI // SP-CTI — AI-BOM export governance twin (osx-cop-02)
"""Governance twin over ICDEV[IT]'s AI-BOM export to ICDEV[OS], canvas key ``aibom``.

It observes ``compliance.aibom_export`` (``tools/compliance/aibom_bundle.py`` holds the evidence and the judgement):
the ``icos-aibom/v1`` bundles (an SPDX 3.0.1 AI document and a CycloneDX 1.7 ML-BOM of ICDEV[IT]'s own AI components),
prepared and published, in the append-only, hash-chained ``aibom_export_events``. An AI-BOM never published is an
unverified check, never a pass, and so is a deployment that declares no AI component at all. Findings, each threaded to
the ledger row it rests on (``aibom:<event id>``):

* ``aibom.stale`` (medium) the newest published AI-BOM is older than the policy allows;
* ``aibom.awaiting_publish`` (low) a prepared AI-BOM has waited too long for a person;
* ``aibom.self_published`` / ``aibom.chain_broken`` (critical).

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
class AIBOMTwin(GovernanceTwinAdapter):
    canvas_key = "aibom"
    display_name = "AI-BOM export"
    # A literal: the conformance audit reads it without importing this module.
    observes = ("compliance.aibom_export",)

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.compliance import aibom_bundle

        return {"canvas": self.canvas_key, "target_id": "compliance.aibom_export", "observed_at": now_iso(),
                "policy": load_policy("aibom"), "aibom": aibom_bundle.twin_evidence()}

    def judge(self, evidence: dict) -> dict:
        from tools.compliance import aibom_bundle

        policy = evidence.get("policy") or {}
        checks, findings, summary = aibom_bundle.twin_judge(evidence.get("aibom") or {}, policy,
                                                            evidence.get("observed_at") or "")
        # Every violation here carries a thread_id: a ledger row, or the observation itself.
        cap = int(policy.get("max_findings") or 50)
        return {"checks": checks, "findings": findings[:cap], "summary": summary}
