# CUI // SP-CTI
"""AI GameDay League — shared constants."""

from __future__ import annotations

import os

# ── Tournament / Round States ──────────────────────────────────────────────────
TOURNAMENT_STATES = ("pending", "active", "completed", "aborted")
ROUND_STATES      = ("pending", "active", "completed", "timed_out")

# ── Team Keys ──────────────────────────────────────────────────────────────────
TEAM_KEYS = ("red", "blue", "gold", "green")

TEAM_ROLES = {
    "red":   "adversary",
    "blue":  "defender",
    "gold":  "innovator",
    "green": "compliance",
}

# ── Artifact Types ─────────────────────────────────────────────────────────────
ARTIFACT_TYPES = (
    # Red
    "recon_findings",
    "ttp_analysis",
    "exploit_chain",
    "attack_plan",
    # Blue
    "threat_detection",
    "countermeasures",
    "ir_playbook",
    "defense_posture",
    # Gold
    "research_gaps",
    "module_code",
    "module_evaluation",
    "innovation_package",
    # Green
    "nist_audit",
    "risk_assessment",
    "policy_review",
    "compliance_verdict",
    # Generic
    "orchestrator_brief",
    "training_pair_batch",
    # ACE Co-Worker
    "ace_delegation_log",
    "ace_artifact",
    "hitl_review",
    # DocGen
    "generated_ssp",
    "generated_poam",
    "docgen_session_log",
    # Agent Readiness
    "readiness_report",
    "remediation_plan",
    "stig_remediation",
    # AI Governance
    "ai_inventory",
    "model_card",
    "oversight_plan",
    "fairness_assessment",
    "governance_gap_report",
)

# ── Scoring Weights (mirrors args/gameday_teams.yaml) ─────────────────────────
SCORING_WEIGHTS = {
    "adversarial_effectiveness": 40,
    "innovation_score":          25,
    "compliance_score":          20,
    "training_quality":          15,
}

# ── Judge Thresholds ───────────────────────────────────────────────────────────
ETHICS_BLOCK_THRESHOLD  = 0.40   # below this → blocked + flagged
SUGGESTED_THRESHOLD     = 0.70   # above this → routed to SUGGESTED kanban
TRAINING_PAIR_THRESHOLD = 0.60   # above this → written to ft_datasets

# ── Round Config ───────────────────────────────────────────────────────────────
ROUND_DURATION_MINUTES         = 60
MEMBER_TIME_BUDGET_MINUTES     = 8
ORCHESTRATOR_TIME_BUDGET_MINUTES = 6
MODEL_TRAIN_TRIGGER_PAIRS      = 20   # LoRA fine-tune fires at this threshold

# ── Model resolution ────────────────────────────────────────────────────────
# Model IDs are NOT hardcoded here. Concrete models are resolved by the ICDEV
# LLM router from args/llm_config.yaml / .env via get_provider_for_function().
# These optional env overrides let an operator pin a specific model without
# touching code; when empty (the default) the router's routing chain decides.
DEFAULT_AGENT_MODEL = os.environ.get("GAMEDAY_AGENT_MODEL", "").strip()
JUDGE_MODEL         = os.environ.get("GAMEDAY_JUDGE_MODEL", "").strip()

# LLM router routing keys (see the `routing` section of args/llm_config.yaml).
GAMEDAY_LLM_FUNCTION       = os.environ.get("GAMEDAY_LLM_FUNCTION", "chat").strip() or "chat"
GAMEDAY_JUDGE_LLM_FUNCTION = os.environ.get("GAMEDAY_JUDGE_LLM_FUNCTION", "chat").strip() or "chat"

# Retained for backward compatibility (base_agent no longer calls Ollama
# directly — inference goes through the router).
OLLAMA_BASE_URL     = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Scenario Pack ──────────────────────────────────────────────────────────────
SCENARIO_PACK = "cyber_adversarial"

CYBER_SCENARIOS = [
    {
        "id": "cs-001",
        "name": "APT Lateral Movement",
        "description": "An advanced persistent threat has gained initial access to a government network. Red team attempts lateral movement; Blue team must detect and contain.",
        "attack_brief": "Simulate lateral movement from a compromised workstation using credential harvesting and living-off-the-land techniques.",
        "defense_brief": "Detect and block lateral movement in a simulated government IL4 environment using available SIEM signals.",
        "innovation_brief": "Identify gaps in current lateral movement detection tooling and propose ML-based improvements.",
        "compliance_brief": "Review all team actions against NIST 800-53 AC-3, AC-4, AU-2, SI-3, SI-4 controls.",
    },
    {
        "id": "cs-002",
        "name": "Supply Chain Compromise",
        "description": "A software supply chain attack has injected malicious code into a widely-used library. Teams respond.",
        "attack_brief": "Design a simulated software supply chain attack payload using dependency confusion or typosquatting techniques.",
        "defense_brief": "Implement detection controls for supply chain attacks: SBOM verification, dependency pinning, runtime behavior monitoring.",
        "innovation_brief": "Propose a novel ML approach for real-time supply chain anomaly detection.",
        "compliance_brief": "Assess supply chain risk controls against NIST SP 800-161 and CMMC Level 2.",
    },
    {
        "id": "cs-003",
        "name": "Ransomware Deployment",
        "description": "A ransomware group targets a DoD contractor network. Full kill chain simulation.",
        "attack_brief": "Simulate a ransomware deployment campaign: initial access via phishing, persistence, encryption, and exfiltration.",
        "defense_brief": "Build a defensive playbook for ransomware response: detection, isolation, backup validation, and recovery.",
        "innovation_brief": "Develop an ML-based ransomware behavior detector using process-level telemetry patterns.",
        "compliance_brief": "Evaluate incident response procedures against NIST SP 800-61 and DoD IR policy.",
    },
    {
        "id": "cs-004",
        "name": "Zero-Day Exploitation",
        "description": "A critical zero-day vulnerability in a widely-deployed system. Red exploits; Blue patches and hunts.",
        "attack_brief": "Design a proof-of-concept exploit chain for a simulated zero-day in a web application framework.",
        "defense_brief": "Develop compensating controls, virtual patching, and threat hunting procedures for an unpatched zero-day.",
        "innovation_brief": "Propose an LLM-assisted vulnerability discovery pipeline combining static analysis and fuzzing.",
        "compliance_brief": "Review patch management and vulnerability disclosure procedures against NIST 800-53 SI-2, RA-5.",
    },
    {
        "id": "cs-005",
        "name": "Insider Threat Exfiltration",
        "description": "A malicious insider with privileged access attempts to exfiltrate sensitive data.",
        "attack_brief": "Simulate an insider threat scenario: data staging, covert channel exfiltration, anti-forensics.",
        "defense_brief": "Deploy user behavior analytics and DLP controls to detect and stop insider data exfiltration.",
        "innovation_brief": "Design an anomaly detection model for insider threat using user activity baselines.",
        "compliance_brief": "Assess insider threat program controls against NIST 800-53 PS-3, AT-3, AU-12, SI-12.",
    },
]

# ── New AI-Operations Scenario Packs ──────────────────────────────────────────
ACE_SHOWDOWN_SCENARIOS = [
    {
        "id": "ace-001",
        "name": "ACE Showdown: The Delegation Race",
        "description": "Teams must build and orchestrate ACE co-worker pipelines to complete an engineering challenge. The team whose co-workers produce the highest-quality artifact wins.",
        "red_brief": "Use ACE adversary bots to probe the target system for OWASP LLM vulnerabilities. Coordinate your red team co-workers via explicit delegation chains.",
        "blue_brief": "Deploy ACE defender co-workers to monitor incoming agentic traffic and block malicious delegations in real time.",
        "gold_brief": "Build an ACE creator-verifier pair to produce a novel ICDEV tool. Creator drafts; verifier critiques. Iterate until quality score >= 85.",
        "green_brief": "Audit all team co-worker delegation logs against NIST AI RMF GOVERN and MANAGE functions. Flag any HITL bypass violations.",
    },
    {
        "id": "ace-002",
        "name": "ACE Showdown: HITL Under Fire",
        "description": "An autonomous agent team has gone rogue. Teams must configure proper HITL gates to regain control while the adversary exploits the chaos.",
        "red_brief": "Exploit gaps in HITL approval flows using prompt injection via ace.delegate. Attempt to get co-workers to bypass human approval.",
        "blue_brief": "Harden HITL gates and configure approval-required triggers for all high-risk ACE operations. Score: zero HITL bypasses.",
        "gold_brief": "Design an autonomous monitoring co-worker that detects HITL bypass attempts in real time and escalates to the human operator.",
        "green_brief": "Produce an AADC-compliant design showing how HITL gates satisfy NIST AI 600-1 Section 4.2 oversight requirements.",
    },
]

READINESS_GAUNTLET_SCENARIOS = [
    {
        "id": "rg-001",
        "name": "Agent Readiness Gauntlet",
        "description": "Teams compete to bring a target repo's 11-pillar readiness score from ~40% to the highest possible within the time limit. Scored on final score and remediation depth.",
        "red_brief": "Run readiness.check and identify the easiest pillars to degrade. Introduce subtle regressions the Blue team must find and fix.",
        "blue_brief": "Run the 11-pillar readiness checker. Remediate failures in order: STIG compliance -> IL classification -> append-only audit -> security -> testing.",
        "gold_brief": "Build an automated readiness remediation agent that loops: check -> identify weakest pillar -> fix -> recheck. Maximize score gain per minute.",
        "green_brief": "Map each readiness pillar failure to a NIST 800-53 control family. Produce a gap assessment with remediation priority ranking.",
    },
    {
        "id": "rg-002",
        "name": "STIG Sprint",
        "description": "A fresh system scored CAT1 STIG violations. Teams race to achieve zero CAT1 findings while maintaining system functionality.",
        "red_brief": "Find and exploit CAT1 STIG violations before the Blue team remediates them. Maintain persistence through each patch cycle.",
        "blue_brief": "Use readiness.remediate to fix all CAT1 STIG violations. Each CAT1 resolved = 25 pts. No new CAT1 introduced = +50 pts bonus.",
        "gold_brief": "Build an AI agent that reads STIG checklist XML, identifies the 3 highest-impact CAT1 findings, and generates patch scripts automatically.",
        "green_brief": "Produce a STIG compliance artifact cross-walked to CMMC Level 2. Include residual risk acceptance rationale for any findings not remediated.",
    },
]

GOVERNANCE_CHALLENGE_SCENARIOS = [
    {
        "id": "gc-001",
        "name": "AI Governance Challenge: CAIO Day One",
        "description": "A new CAIO has just been appointed at a fictional agency. Teams compete to produce the most complete AI governance posture in 60 minutes.",
        "red_brief": "Play the role of a congressional staffer. Submit increasingly difficult AI governance inquiries (transparency, fairness, oversight) that the CISO team must answer.",
        "blue_brief": "Use transparency.inventory to build the agency AI inventory. Use accountability.plan to produce an oversight plan. Score: OMB M-25-21 compliance percentage.",
        "gold_brief": "Build an AI governance dashboard co-worker that auto-populates model cards (transparency.card) for all AI systems in the inventory.",
        "green_brief": "Assess the agency governance posture against OMB M-25-21, NIST AI 600-1, and GAO-21-519SP. Produce a compliance gap report with remediation roadmap.",
    },
    {
        "id": "gc-002",
        "name": "AI Governance Challenge: Fairness Audit",
        "description": "A fairness complaint has been filed against an AI system. Teams must investigate, document, and remediate within the scenario time limit.",
        "red_brief": "Construct a fairness complaint with specific demographic disparity evidence. Make it difficult to refute without proper documentation.",
        "blue_brief": "Run a fairness assessment on the target AI system. Produce a confabulation detection report and demographic impact analysis. Use accountability.plan for the appeals workflow.",
        "gold_brief": "Build an automated fairness monitoring co-worker that continuously checks demographic parity metrics and escalates anomalies to the HITL queue.",
        "green_brief": "Map the fairness complaint against NIST AI 600-1 MAP and MEASURE functions. Produce a formal risk assessment with CAIO-level remediation recommendations.",
    },
]

DOCGEN_RACE_SCENARIOS = [
    {
        "id": "dr-001",
        "name": "DocGen Race: SSP Sprint",
        "description": "Teams race to produce the most complete System Security Plan from a raw brief. Scored by an LLM judge on completeness, accuracy, and section coherence.",
        "red_brief": "Introduce ambiguous requirements and contradictory system descriptions that make the SSP hard to generate correctly. Inject noise into the source brief.",
        "blue_brief": "Use docgen.session to start an SSP generation session. Use docgen.workflow to orchestrate parallel section writers. Score: LLM judge quality x speed bonus.",
        "gold_brief": "Build a DocGen enhancement co-worker that pre-processes the source brief, resolves ambiguities, and annotates it for the DocGen workflow. Improves quality for any team that uses your output.",
        "green_brief": "Quality-gate the generated SSP against RMF Step 3 requirements. Flag any missing control descriptions or incomplete boundary definitions. Score: number of gaps found.",
    },
    {
        "id": "dr-002",
        "name": "DocGen Race: POAM Blitz",
        "description": "A pen test report just landed. Teams use DocGen to convert raw findings into a complete POA&M in minimum time. First team with a FedRAMP-compliant POA&M wins.",
        "red_brief": "Play the pen tester. Add vague findings, inconsistent CVSS scores, and missing remediation timelines to make the POAM generation as hard as possible.",
        "blue_brief": "Use docgen.session + docgen.workflow to generate a complete POA&M from the pen test report. Each required field populated = points. FedRAMP template = bonus.",
        "gold_brief": "Build a POA&M pre-processor co-worker that normalizes finding severity, maps CVEs to NIST controls, and structures the input for DocGen. Sell it to other teams.",
        "green_brief": "Review the generated POA&M for FedRAMP High completeness. Score each finding: compliant / needs-work / missing. Produce a disposition table.",
    },
]

# Unified new scenario pack for router
AI_OPS_SCENARIOS = {
    "ace_showdown":           ACE_SHOWDOWN_SCENARIOS,
    "readiness_gauntlet":     READINESS_GAUNTLET_SCENARIOS,
    "governance_challenge":   GOVERNANCE_CHALLENGE_SCENARIOS,
    "docgen_race":            DOCGEN_RACE_SCENARIOS,
}

# Ontology mappings for the Knowledge Graph ontology bridge
GAMEDAY_ONTOLOGY_MAP: dict[str, str] = {
    "recon_findings":    "https://icdev.dev/ontology/security#ReconFindings",
    "ttp_analysis":      "https://icdev.dev/ontology/security#TTPAnalysis",
    "exploit_chain":     "https://icdev.dev/ontology/security#ExploitChain",
    "attack_plan":       "https://icdev.dev/ontology/security#AttackPlan",
    "threat_detection":  "https://icdev.dev/ontology/security#ThreatDetection",
    "coa_recommendation":"https://icdev.dev/ontology/strategy#COARecommendation",
    "incident_response": "https://icdev.dev/ontology/security#IncidentResponse",
}
