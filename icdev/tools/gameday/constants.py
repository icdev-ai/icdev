# CUI // SP-CTI
"""AI GameDay League — shared constants."""

from __future__ import annotations

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

# ── Default Models ─────────────────────────────────────────────────────────────
DEFAULT_AGENT_MODEL = "qwen3.5:9b"
JUDGE_MODEL         = "gemma4:e4b"
OLLAMA_BASE_URL     = "http://localhost:11434"

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
