# CUI // SP-CTI
"""FORGE Academy ontology mappings — mission types, topics, tiers, prerequisites."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mission type → ontology class
# ---------------------------------------------------------------------------
MISSION_TYPE_ONTOLOGY = {
    "coding": "icdev:Lab",
    "guided": "icdev:Lesson",
    "hybrid": "icdev:Assessment",
    "studio": "icdev:Lab",
    "chat": "icdev:Lesson",
    "concept": "icdev:Lesson",
}

# ---------------------------------------------------------------------------
# Topic → domain ontology class
# ---------------------------------------------------------------------------
TOPIC_ONTOLOGY = {
    "llm": "core:Concept",
    "prompting": "core:Concept",
    "rag": "data:DataPipeline",
    "agents": "core:Concept",
    "mcp": "core:Concept",
    "multi_agent": "core:Concept",
    "strands": "core:Concept",
    "langchain": "core:Concept",
    "capstone": "icdev:Assessment",
    "compliance": "compliance:NISTControl",
    "ato": "compliance:NISTControl",
    "governance": "security:Control",
    "govcon": "strategy:AIROIFramework",
    "secops": "security:Control",
    "devops": "infra:ComputeInstance",
    "dataops": "data:DataPipeline",
    "netops": "network:AWSVPC",
    "sre": "infra:ComputeInstance",
    "swe": "core:Design",
    "analyst": "core:Concept",
    "leadership": "strategy:GovernancePosture",
    "studio": "core:Design",
    "forge": "core:Design",
    "ai_foundations": "core:Concept",
    "multimodal": "core:Concept",
}

# ---------------------------------------------------------------------------
# Tier → competency ontology class
# ---------------------------------------------------------------------------
TIER_COMPETENCY = {
    1: "icdev:FundamentalCompetency",
    2: "icdev:IntermediateCompetency",
    3: "icdev:ExpertCompetency",
}

# ---------------------------------------------------------------------------
# Specific mission title overrides (when title contains these keywords)
# ---------------------------------------------------------------------------
TITLE_ONTOLOGY_OVERRIDES = {
    "aws vpc": "network:AWSVPC",
    "vpc": "network:AWSVPC",
    "subnet": "network:Subnet",
    "stig": "compliance:STIGRule",
    "poam": "compliance:NISTControl",
    "war council": "strategy:WarCouncilBrief",
    "roi": "strategy:AIROIFramework",
    "governance": "strategy:GovernancePosture",
    "threat model": "security:Control",
    "boundary": "security:Boundary",
    "data lake": "data:DataLake",
    "relational table": "data:RelationalTable",
    "container": "infra:Container",
    "firewall": "network:Firewall",
    "load balancer": "network:LoadBalancer",
}

# ---------------------------------------------------------------------------
# Prerequisite ontology paths
# Maps mission prereq slug patterns to ontology prerequisite paths.
# ---------------------------------------------------------------------------
PREREQ_ONTOLOGY_PATHS = {
    "m01-llm-fundamentals": [],
    "m02-prompt-engineering": ["core:Concept"],
    "m03-rag-basics": ["core:Concept", "data:DataPipeline"],
    "m04-first-agent": ["data:DataPipeline", "core:Concept"],
    "m05-mcp-protocol": ["core:Concept", "core:Design"],
    "m06-fastmcp": ["core:Concept", "core:Design"],
    "m07-multi-agent": ["core:Concept", "core:Design"],
    "m08-strands-agents": ["core:Concept", "core:Design"],
    "m09-langchain": ["core:Concept", "core:Design"],
    "m10-tier1-capstone": ["core:Concept", "core:Design", "data:DataPipeline"],
    "m-netops-01-topology": ["network:Subnet"],
    "m-netops-02-anomaly": ["network:AWSVPC", "network:Subnet"],
    "m-netops-03-remediation": ["network:AWSVPC", "network:Subnet", "infra:ComputeInstance"],
    "m-netops-04-capstone": ["network:AWSVPC", "infra:ComputeInstance", "security:Boundary"],
    "m-isso-01-stig-triage": ["security:Control", "compliance:STIGRule"],
    "m-isso-02-poam": ["security:Control", "compliance:NISTControl"],
    "m-secops-05-aadc-threat-model": ["security:Control", "core:Design"],
    "m-issm-01-ato-acceleration": ["compliance:NISTControl"],
    "m-issm-02-aadc-ato-design": ["compliance:NISTControl", "core:Design"],
    "m-ciso-01-ai-inventory": ["security:Control"],
    "m-ciso-02-aadc-governance": ["security:Control", "strategy:GovernancePosture"],
    "m-pm-01-govcon-intel": ["strategy:AIROIFramework"],
    "m-leadership-01-ai-roi": ["strategy:AIROIFramework"],
    "m-leadership-02-build-vs-buy": ["strategy:AIROIFramework"],
    "m-leadership-03-ai-risk-comm": ["strategy:GovernancePosture"],
    "m-leadership-04-govern-ai": ["strategy:GovernancePosture"],
    "m-leadership-05-ai-workforce": ["strategy:GovernancePosture"],
    "m-leadership-06-capstone": ["strategy:AIROIFramework", "strategy:GovernancePosture"],
    "m-analyst-01-competitive-intel-agent": ["core:Concept"],
    "m-analyst-02-market-signal-rag": ["core:Concept", "data:DataPipeline"],
    "m-analyst-03-pattern-detection": ["data:DataPipeline"],
    "m-analyst-04-report-generation": ["data:DataPipeline", "core:Concept"],
    "m-analyst-05-capstone": ["data:DataPipeline", "core:Concept"],
    "m-devops-01-pipeline-agent": ["infra:ComputeInstance", "core:Design"],
    "m-dataops-01-advanced-rag": ["data:DataPipeline", "core:Concept"],
    "m-sre-01-slo-agent": ["infra:ComputeInstance", "core:Concept"],
    "m-sre-02-incident-agent": ["infra:ComputeInstance"],
    "m-sre-03-chaos": ["infra:ComputeInstance", "core:Design"],
    "m-swe-01-multi-agent-dag": ["core:Design", "core:Concept"],
    "m-swe-02-mcp-server": ["core:Design", "core:Concept"],
    "m-t3-01-forge-deep-dive": ["core:Design", "core:Concept", "data:DataPipeline"],
    "m-t3-06-child-app-creation": ["core:Design", "icdev:Assessment"],
    "m-t3-07-tier3-capstone": ["core:Design", "icdev:Assessment", "data:DataPipeline"],
    "m-exec-primer-01": [],
    "m-studio-network-canvas": ["network:AWSVPC", "core:Design"],
    "m-chat-agent-interview": ["core:Concept"],
    "m-t1-11-multimodal": ["core:Concept", "data:DataPipeline"],
    "m-swe-aadc-09-ops-config": ["core:Design", "security:Control"],
    "m-swe-sdk-java": ["core:Design"],
    "m-swe-sdk-typescript": ["core:Design"],
    "m-swe-sdk-go": ["core:Design"],
    "m-swe-sdk-dotnet": ["core:Design"],
    "m-dataops-05-fine-tuning": ["data:DataPipeline", "core:Concept"],
    "m-sre-ai-01-llm-observability": ["infra:ComputeInstance", "core:Concept"],
    "m-sre-ai-02-drift-detection": ["infra:ComputeInstance", "data:DataPipeline"],
    "m-sre-ai-03-cost-optimization": ["infra:ComputeInstance", "core:Concept"],
    "m-sre-ai-04-incident-response": ["infra:ComputeInstance", "security:Control"],
    "m-secops-ai-01-prompt-injection": ["security:Control", "core:Design"],
    "m-secops-ai-02-adversarial-robustness": ["security:Control", "core:Design"],
    "m-secops-ai-03-data-poisoning": ["security:Control", "data:DataPipeline"],
}

# ---------------------------------------------------------------------------
# Step type → ontology class
# ---------------------------------------------------------------------------
STEP_TYPE_ONTOLOGY = {
    "coding": "icdev:Lab",
    "configure": "icdev:Lesson",
    "verify": "icdev:Assessment",
    "reflect": "icdev:Assessment",
    "watch": "icdev:Lesson",
    "deploy": "icdev:Lab",
    "design": "icdev:Lab",
}


# ---------------------------------------------------------------------------
# What a passed step is evidence OF  (aca-trn-02)
# ---------------------------------------------------------------------------
# A competency record is worth exactly as much as the thing it was inferred from,
# so the step type decides what may be claimed:
#
#   "domain" — the learner produced something in the mission's subject area, so
#              the mission's topic class is demonstrated.
#   "tier"   — the learner was assessed rather than building, so the mission's
#              depth is demonstrated but not its subject.
#   None     — passive. Watching a walkthrough is being shown a topic, not
#              evidence of it, and a record built from it overstates the learner.
#
# `configure` is deliberately domain work: it is the graded activity on the
# guided (non-technical) role tracks, and excluding it would leave that half of
# the audience with an empty training record no matter how much they finished.
#
# An unrecognised step type maps to None rather than to a default class. A step
# type nobody has classified is not evidence of anything, and inventing a
# competency for it is the failure mode this whole chain exists to prevent.
STEP_TYPE_EVIDENCE: dict[str, str | None] = {
    "coding": "domain",
    "configure": "domain",
    "deploy": "domain",
    "design": "domain",
    "verify": "tier",
    "reflect": "tier",
    "watch": None,
}


def get_mission_ontology_class(mission_type: str) -> str:
    return MISSION_TYPE_ONTOLOGY.get(mission_type, "icdev:Lesson")


def get_topic_ontology_class(topic: str, title: str = "") -> str:
    topic_lc = (topic or "").lower()
    title_lc = (title or "").lower()
    # Check title overrides first
    for keyword, onto in TITLE_ONTOLOGY_OVERRIDES.items():
        if keyword in title_lc:
            return onto
    return TOPIC_ONTOLOGY.get(topic_lc, "core:Concept")


def get_tier_competency(tier: int) -> str:
    return TIER_COMPETENCY.get(tier, "icdev:FundamentalCompetency")


def get_prereq_ontology_paths(slug: str) -> list[str]:
    return PREREQ_ONTOLOGY_PATHS.get(slug, ["core:Concept"])


def get_step_ontology_class(step_type: str) -> str:
    return STEP_TYPE_ONTOLOGY.get(step_type, "icdev:Lesson")


def build_mission_ontology_id(slug: str, mission_type: str, topic: str, title: str = "", tier: int = 1) -> dict:
    """Build a complete ontology descriptor for a mission."""
    return {
        "ontology_id": f"icdev:mission:{slug}",
        "mission_class": get_mission_ontology_class(mission_type),
        "topic_class": get_topic_ontology_class(topic, title),
        "competency_class": get_tier_competency(tier),
        "prereq_ontology_paths": get_prereq_ontology_paths(slug),
    }


def build_mission_competency_classes(topic_class: str, tier: int) -> list[str]:
    """Every competency class a completed mission demonstrates, in order.

    Two claims, not one. The tier class says how deep the work was; the topic
    class says what it was about. Recording only the tier — which is what
    happened before aca-trn-02 — produces a training record that cannot
    distinguish a learner who hardened a boundary from one who wrote a prompt,
    and so cannot answer the only question a training record is ever asked.

    The topic classes are drawn from the same vocabulary as
    PREREQ_ONTOLOGY_PATHS, so a demonstrated competency is directly checkable
    against the prerequisites of the missions the learner has not attempted yet.
    """
    classes = [get_tier_competency(tier)]
    if topic_class and topic_class not in classes:
        classes.append(topic_class)
    return classes


def get_step_competency_class(step_type: str, topic_class: str, tier: int) -> str | None:
    """The competency class a *passed* step demonstrates, or None if it is passive."""
    kind = STEP_TYPE_EVIDENCE.get((step_type or "").lower())
    if kind == "domain":
        return topic_class or "core:Concept"
    if kind == "tier":
        return get_tier_competency(tier)
    return None


def build_step_ontology_id(mission_slug: str, step_num: int, step_type: str,
                            topic_class: str = "", tier: int = 1) -> dict:
    """Build a complete ontology descriptor for a step."""
    return {
        "ontology_id": f"icdev:mission:{mission_slug}:step:{step_num}",
        "step_class": get_step_ontology_class(step_type),
        "competency_class": get_step_competency_class(step_type, topic_class, tier),
    }
