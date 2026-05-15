from __future__ import annotations
# CUI // SP-CTI
"""FORGE Academy content loader — parse bundled mission content + seed catalog."""

import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

CONTENT_ROOT = Path(__file__).parent / "content"


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (metadata dict, remaining markdown)."""
    if not raw.startswith("---\n"):
        return {}, raw
    parts = raw.split("---\n", 2)
    if len(parts) < 3:
        return {}, raw
    fm_text = parts[1].strip()
    body = parts[2]
    meta: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, body

# ---------------------------------------------------------------------------
# Seed the mission catalog (called by migrate() once on first run)
# ---------------------------------------------------------------------------

BUILTIN_MISSIONS = [
    # ── TIER 1: Foundations ─────────────────────────────────────────────────
    {
        "slug": "m01-llm-fundamentals",
        "title": "LLM Fundamentals",
        "tagline": "Understand the engine behind every AI system you'll ever build.",
        "tier": 1, "topic": "llm", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 200, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 25,
        "prereqs": [],
        "source_credit": "goagiq/GenAI-LLM-101 (adapted)",
    },
    {
        "slug": "m02-prompt-engineering",
        "title": "Prompt Engineering",
        "tagline": "The difference between a chatbot and a weapon is the prompt.",
        "tier": 1, "topic": "prompting", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 250, "order_idx": 2,
        "difficulty": "beginner", "estimated_minutes": 30,
        "prereqs": ["m01-llm-fundamentals"],
    },
    {
        "slug": "m03-rag-basics",
        "title": "RAG Basics",
        "tagline": "Make any LLM smarter with your own data. No fine-tuning required.",
        "tier": 1, "topic": "rag", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 300, "order_idx": 3,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m02-prompt-engineering"],
    },
    {
        "slug": "m04-first-agent",
        "title": "Your First Agent",
        "tagline": "An LLM + tools + a loop. That's all an agent is. Let's build one.",
        "tier": 1, "topic": "agents", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 4,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m03-rag-basics"],
        "source_credit": "goagiq/agent_swarm (adapted)",
    },
    {
        "slug": "m05-mcp-protocol",
        "title": "MCP Protocol",
        "tagline": "The universal plugin system for AI. Every serious agent speaks MCP.",
        "tier": 1, "topic": "mcp", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 5,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m04-first-agent"],
        "source_credit": "goagiq/mcp-fastapi-server (adapted)",
    },
    {
        "slug": "m06-fastmcp",
        "title": "FastMCP",
        "tagline": "Build your first MCP server in Python. It runs in 15 minutes.",
        "tier": 1, "topic": "mcp", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 6,
        "difficulty": "intermediate", "estimated_minutes": 30,
        "prereqs": ["m05-mcp-protocol"],
        "source_credit": "goagiq/Ollama-MCP (adapted)",
    },
    {
        "slug": "m07-multi-agent",
        "title": "Multi-Agent Coordination",
        "tagline": "One agent is powerful. A coordinated swarm is unstoppable.",
        "tier": 1, "topic": "multi_agent", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 7,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m05-mcp-protocol"],
        "source_credit": "goagiq/sentiment-swarm-agents (adapted)",
    },
    {
        "slug": "m08-strands-agents",
        "title": "Amazon Strands Agents",
        "tagline": "AWS-native agent SDK. Tool decorators + orchestration in pure Python.",
        "tier": 1, "topic": "strands", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 8,
        "difficulty": "advanced", "estimated_minutes": 40,
        "prereqs": ["m07-multi-agent"],
    },
    {
        "slug": "m09-langchain",
        "title": "LangChain Essentials",
        "tagline": "LCEL chains, memory, agents. The framework that started the movement.",
        "tier": 1, "topic": "langchain", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 9,
        "difficulty": "advanced", "estimated_minutes": 40,
        "prereqs": ["m07-multi-agent"],
    },
    {
        "slug": "m10-tier1-capstone",
        "title": "Tier 1 Capstone",
        "tagline": "Wire RAG + Agent + MCP into one system. This is what real AI engineering looks like.",
        "tier": 1, "topic": "capstone", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 600, "order_idx": 10,
        "difficulty": "advanced", "estimated_minutes": 60,
        "prereqs": ["m08-strands-agents", "m09-langchain"],
    },
    # ── TIER 2: Non-technical (ISSO) ─────────────────────────────────────────
    {
        "slug": "m-isso-01-stig-triage",
        "title": "STIG Triage Agent",
        "tagline": "Configure an AI that reads 600-page STIGs so you don't have to.",
        "tier": 2, "topic": "compliance", "role_filter": "isso",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-isso-02-poam",
        "title": "POA&M Intelligence",
        "tagline": "Auto-populate POA&M from scan results. Minutes, not days.",
        "tier": 2, "topic": "compliance", "role_filter": "isso",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 2,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m-isso-01-stig-triage"],
    },
    # ── TIER 2: ISSM ─────────────────────────────────────────────────────────
    {
        "slug": "m-issm-01-ato-acceleration",
        "title": "ATO Acceleration",
        "tagline": "ATO in weeks, not years. Configure the evidence collection pipeline.",
        "tier": 2, "topic": "ato", "role_filter": "issm",
        "mission_type": "guided",
        "xp_reward": 350, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 25,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: CISO ─────────────────────────────────────────────────────────
    {
        "slug": "m-ciso-01-ai-inventory",
        "title": "AI Governance Inventory",
        "tagline": "Know every AI system in your portfolio. OMB M-25-21 compliant.",
        "tier": 2, "topic": "governance", "role_filter": "ciso",
        "mission_type": "guided",
        "xp_reward": 350, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 25,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: PM ────────────────────────────────────────────────────────────
    {
        "slug": "m-pm-01-govcon-intel",
        "title": "GovCon Opportunity Intel",
        "tagline": "SAM.gov scanner + AI matching. Find your next contract before the competition.",
        "tier": 2, "topic": "govcon", "role_filter": "pm",
        "mission_type": "hybrid",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 25,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: SecOps (AADC) ───────────────────────────────────────────────
    {
        "slug": "m-secops-05-aadc-threat-model",
        "title": "AADC Threat Modeling",
        "tagline": "Map every MITRE ATLAS technique to your agentic pipeline. Fix before an adversary finds it.",
        "tier": 2, "topic": "secops", "role_filter": "secops_eng,isso",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 5,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: CISO (AADC) ─────────────────────────────────────────────────
    {
        "slug": "m-ciso-02-aadc-governance",
        "title": "AI Governance Architecture",
        "tagline": "Design the governance layer that keeps your AI systems accountable. NIST AI RMF compliant.",
        "tier": 2, "topic": "governance", "role_filter": "ciso",
        "mission_type": "guided",
        "xp_reward": 400, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 30,
        "prereqs": ["m-ciso-01-ai-inventory"],
    },
    # ── TIER 2: ISSM (AADC) ──────────────────────────────────────────────────
    {
        "slug": "m-issm-02-aadc-ato-design",
        "title": "Compliant AI System Design",
        "tagline": "Design an agentic system that passes OWASP LLM Top 10 — before the STIG auditor arrives.",
        "tier": 2, "topic": "ato", "role_filter": "issm",
        "mission_type": "guided",
        "xp_reward": 400, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 30,
        "prereqs": ["m-issm-01-ato-acceleration"],
    },
    # ── TIER 2: DevOps ───────────────────────────────────────────────────────
    {
        "slug": "m-devops-01-pipeline-agent",
        "title": "Pipeline Agent",
        "tagline": "Your CI/CD pipeline just got an AI co-pilot. Build it in one session.",
        "tier": 2, "topic": "devops", "role_filter": "devops",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 3 ────────────────────────────────────────────────────────────────
    {
        "slug": "m-t3-01-forge-deep-dive",
        "title": "FORGE Framework Deep Dive",
        "tagline": "Read a real goal file. Trace a live tool call. Understand the machine.",
        "tier": 3, "topic": "forge", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 1,
        "difficulty": "advanced", "estimated_minutes": 50,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── STUDIO + CHAT LABS ────────────────────────────────────────────────────
    {
        "slug": "m-studio-network-canvas",
        "title": "Network Design Canvas Lab",
        "tagline": "Design a production network topology using ICDEV Studio. AI auto-validates.",
        "tier": 2, "topic": "studio", "role_filter": "devops,swe_arch",
        "mission_type": "studio",
        "xp_reward": 400, "order_idx": 20,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-chat-agent-interview",
        "title": "Agent Design Interview",
        "tagline": "Have a conversation with FORGE Sensei to design your first agent. No code.",
        "tier": 1, "topic": "agents", "role_filter": "isso,issm,ciso,pm,analyst,leadership",
        "mission_type": "chat",
        "xp_reward": 250, "order_idx": 4,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m02-prompt-engineering"],
    },
    # ── TIER 2: Analyst ──────────────────────────────────────────────────────
    {
        "slug": "m-analyst-01-data-intel",
        "title": "Data Intelligence Setup",
        "tagline": "Configure an AI agent to ingest and organize data from your analysis domain.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-analyst-02-pattern-detection",
        "title": "Pattern Detection Agent",
        "tagline": "Surface anomalies, trends, and clusters automatically — no SQL required.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 2,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m-analyst-01-data-intel"],
    },
    {
        "slug": "m-analyst-03-report-gen",
        "title": "Automated Report Generator",
        "tagline": "From raw findings to polished report in seconds. Configure once, run forever.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 3,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-analyst-02-pattern-detection"],
    },
    {
        "slug": "m-analyst-04-predictive",
        "title": "Predictive Intelligence",
        "tagline": "Configure trend forecasting and early-warning indicators for your domain.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 325, "order_idx": 4,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-analyst-03-report-gen"],
    },
    {
        "slug": "m-analyst-05-capstone",
        "title": "Analyst Capstone",
        "tagline": "Deploy a full intelligence cycle: collect → detect → report → predict.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 500, "order_idx": 5,
        "difficulty": "advanced", "estimated_minutes": 35,
        "prereqs": ["m-analyst-04-predictive"],
    },
    # ── TIER 2: Leadership (V1 — 6-mission track) ───────────────────────────
    {
        "slug": "m-leadership-01-ai-roi",
        "title": "AI ROI Framework",
        "tagline": "Quantify the business case. Build a cost/benefit model that survives a budget meeting.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 11,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-leadership-02-build-vs-buy",
        "title": "Build vs. Buy vs. Partner",
        "tagline": "Vendor evaluation rubric. Total cost of ownership. The decision that defines your AI stack.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 12,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m-leadership-01-ai-roi"],
    },
    {
        "slug": "m-leadership-03-ai-risk-comm",
        "title": "AI Risk Communication",
        "tagline": "Translate technical risk into board language. Be ready for the Congress question.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 13,
        "difficulty": "intermediate", "estimated_minutes": 20,
        "prereqs": ["m-leadership-02-build-vs-buy"],
    },
    {
        "slug": "m-leadership-04-govern-ai",
        "title": "Governing AI — Policy and Structure",
        "tagline": "CAIO designation. AI use case inventory. OMB M-25-21 compliance. Build the governance layer.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 325, "order_idx": 14,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-leadership-03-ai-risk-comm"],
    },
    {
        "slug": "m-leadership-05-ai-workforce",
        "title": "AI Workforce Strategy",
        "tagline": "Upskill vs. hire vs. attrition. Build the team that can execute your AI roadmap.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 325, "order_idx": 15,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-leadership-04-govern-ai"],
    },
    {
        "slug": "m-leadership-06-capstone",
        "title": "Leadership Capstone — AI Transformation Roadmap",
        "tagline": "Build a complete AI transformation roadmap. Output: board-ready briefing.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 600, "order_idx": 16,
        "difficulty": "advanced", "estimated_minutes": 35,
        "prereqs": ["m-leadership-05-ai-workforce"],
    },
    # ── TIER 2: Analyst (V1 — plan slugs) ───────────────────────────────────
    {
        "slug": "m-analyst-01-competitive-intel-agent",
        "title": "Competitive Intelligence Agent",
        "tagline": "Configure an AI that monitors SAM.gov, news feeds, and procurement data for you.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 11,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-analyst-02-market-signal-rag",
        "title": "Market Signal RAG",
        "tagline": "Connect an LLM to your document stores. Query PDFs, spreadsheets, and databases in plain English.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 12,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m-analyst-01-competitive-intel-agent"],
    },
    {
        "slug": "m-analyst-03-pattern-detection",
        "title": "Pattern Detection",
        "tagline": "Surface anomalies, trends, and leading indicators automatically — no SQL required.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 13,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-analyst-02-market-signal-rag"],
    },
    {
        "slug": "m-analyst-04-report-generation",
        "title": "AI-Assisted Report Generation",
        "tagline": "From raw findings to polished report in seconds — with citation grounding so you can defend every claim.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 325, "order_idx": 14,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-analyst-03-pattern-detection"],
    },
    {
        "slug": "m-analyst-05-capstone",
        "title": "Analyst Capstone — Full Intelligence Cycle",
        "tagline": "Collect → detect → report → predict. Wire all four stages into one end-to-end pipeline.",
        "tier": 2, "topic": "analyst", "role_filter": "analyst",
        "mission_type": "guided",
        "xp_reward": 600, "order_idx": 15,
        "difficulty": "advanced", "estimated_minutes": 35,
        "prereqs": ["m-analyst-04-report-generation"],
    },
    # ── TIER 2: Executive AI Primer (zero prerequisites) ────────────────────
    {
        "slug": "m-exec-primer-01",
        "title": "AI Executive Primer",
        "tagline": "30 minutes. No code. Walk away knowing what AI can do, what it can't, and what you own.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership,analyst,isso,issm,ciso,pm",
        "mission_type": "guided",
        "xp_reward": 200, "order_idx": 0,
        "difficulty": "beginner", "estimated_minutes": 30,
        "prereqs": [],
    },
    # ── TIER 2: Leadership (legacy — kept for backward compat) ──────────────
    {
        "slug": "m-leader-01-ai-maturity",
        "title": "AI Maturity Assessment",
        "tagline": "Know where your organization stands on the AI adoption curve before you invest.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-leader-02-roi",
        "title": "AI ROI Framework",
        "tagline": "Quantify the business case. Configure cost/benefit analysis for AI investments.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 2,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m-leader-01-ai-maturity"],
    },
    {
        "slug": "m-leader-03-exec-dash",
        "title": "Executive Intelligence Dashboard",
        "tagline": "AI-powered KPI monitoring with natural language Q&A. Ask your data anything.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 350, "order_idx": 3,
        "difficulty": "intermediate", "estimated_minutes": 25,
        "prereqs": ["m-leader-02-roi"],
    },
    {
        "slug": "m-leader-04-capstone",
        "title": "Leadership Capstone",
        "tagline": "Deploy an AI governance posture dashboard across your entire organization.",
        "tier": 2, "topic": "leadership", "role_filter": "leadership",
        "mission_type": "guided",
        "xp_reward": 500, "order_idx": 4,
        "difficulty": "advanced", "estimated_minutes": 35,
        "prereqs": ["m-leader-03-exec-dash"],
    },
    # ── TIER 2: NetOps ───────────────────────────────────────────────────────
    {
        "slug": "m-netops-01-topology",
        "title": "Network Topology Agent",
        "tagline": "Build an agent that parses, maps, and analyzes your network configuration.",
        "tier": 2, "topic": "netops", "role_filter": "netops",
        "mission_type": "coding",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-netops-02-anomaly",
        "title": "Anomaly Detection Agent",
        "tagline": "Detect traffic anomalies before they become incidents. ML-powered, zero dashboards.",
        "tier": 2, "topic": "netops", "role_filter": "netops",
        "mission_type": "coding",
        "xp_reward": 325, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-netops-01-topology"],
    },
    {
        "slug": "m-netops-03-remediation",
        "title": "Auto-Remediation Agent",
        "tagline": "Detect → diagnose → fix. Close the loop from alert to resolution automatically.",
        "tier": 2, "topic": "netops", "role_filter": "netops",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 3,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m-netops-02-anomaly"],
    },
    {
        "slug": "m-netops-04-capstone",
        "title": "NetOps Capstone",
        "tagline": "Deploy a full network monitoring and response pipeline. Topology → detect → remediate.",
        "tier": 2, "topic": "netops", "role_filter": "netops",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 4,
        "difficulty": "advanced", "estimated_minutes": 50,
        "prereqs": ["m-netops-03-remediation"],
    },
    # ── TIER 2: SRE ──────────────────────────────────────────────────────────
    {
        "slug": "m-sre-01-slo-agent",
        "title": "SLO Monitoring Agent",
        "tagline": "Build an agent that tracks SLOs, burns rates, and pages on-call before users notice.",
        "tier": 2, "topic": "sre", "role_filter": "sre",
        "mission_type": "coding",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-sre-02-incident-agent",
        "title": "Incident Response Agent",
        "tagline": "AI-powered runbook execution. Alert fires → agent diagnoses → runbook runs.",
        "tier": 2, "topic": "sre", "role_filter": "sre",
        "mission_type": "coding",
        "xp_reward": 325, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-sre-01-slo-agent"],
    },
    {
        "slug": "m-sre-03-chaos",
        "title": "Chaos Engineering Agent",
        "tagline": "Break things on purpose. Build an agent that runs controlled failure injection.",
        "tier": 2, "topic": "sre", "role_filter": "sre",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 3,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m-sre-02-incident-agent"],
    },
    {
        "slug": "m-sre-04-capstone",
        "title": "SRE Capstone",
        "tagline": "Full reliability loop: SLO monitoring → incident response → chaos validation.",
        "tier": 2, "topic": "sre", "role_filter": "sre",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 4,
        "difficulty": "advanced", "estimated_minutes": 50,
        "prereqs": ["m-sre-03-chaos"],
    },
    # ── TIER 2: Multi-Language SDK (V2 — modernization bridge) ──────────────
    {
        "slug": "m-swe-sdk-java",
        "title": "Claude API from Spring Boot",
        "tagline": "Add AI to your Java app without rewriting it. Streaming, tool use, and structured output from Spring Boot.",
        "tier": 2, "topic": "swe", "role_filter": "swe,swe_arch",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 21,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-swe-sdk-typescript",
        "title": "Claude SDK in Next.js",
        "tagline": "Streaming chat UI + server-side tool calling in TypeScript. Ship a production AI feature in one session.",
        "tier": 2, "topic": "swe", "role_filter": "swe,swe_arch",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 22,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-swe-sdk-go",
        "title": "Claude API from Go",
        "tagline": "Structured output, retries, and concurrent tool calls from a Go service. Zero Python.",
        "tier": 2, "topic": "swe", "role_filter": "swe,swe_arch",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 23,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-swe-sdk-dotnet",
        "title": "Claude API from C# / .NET",
        "tagline": "Dependency injection pattern, streaming, and tool use with the Anthropic .NET SDK.",
        "tier": 2, "topic": "swe", "role_filter": "swe,swe_arch",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 24,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: AADC Ops Config Generator (Track B) ─────────────────────────
    {
        "slug": "m-swe-aadc-09-ops-config",
        "title": "AADC Ops Config Generator",
        "tagline": "Turn your AADC canvas into a runtime ops plan. Generate tool configs and Kanban wiring tasks in one click.",
        "tier": 2, "topic": "swe", "role_filter": "swe,swe_arch,devops",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 20,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: DataOps — Fine-Tuning Hands-On ──────────────────────────────
    {
        "slug": "m-dataops-05-fine-tuning",
        "title": "Fine-Tuning a Model End-to-End",
        "tagline": "Generate training pairs, run a fine-tuning job, evaluate quality, and promote to production. Full AIMC pipeline.",
        "tier": 2, "topic": "dataops", "role_filter": "swe,swe_arch,mleng",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 25,
        "difficulty": "advanced", "estimated_minutes": 50,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: SRE-AI — Production AI Ops (V3a) ────────────────────────────
    {
        "slug": "m-sre-ai-01-llm-observability",
        "title": "LLM Observability",
        "tagline": "Instrument a production AI system: token tracking, latency percentiles, error dashboards. Real tools, real data.",
        "tier": 2, "topic": "sre", "role_filter": "sre,devops",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 26,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-sre-ai-02-drift-detection",
        "title": "AI Model Drift Detection",
        "tagline": "Configure drift thresholds, interpret quality/latency/token drift events, and trigger retraining from a severity alert.",
        "tier": 2, "topic": "sre", "role_filter": "sre,devops",
        "mission_type": "coding",
        "xp_reward": 425, "order_idx": 27,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-sre-ai-01-llm-observability"],
    },
    {
        "slug": "m-sre-ai-03-cost-optimization",
        "title": "AI Cost Optimization",
        "tagline": "Token budgeting, prompt compression, cost-aware model routing, caching strategies. Stop burning money on inference.",
        "tier": 2, "topic": "sre", "role_filter": "sre,devops,swe_arch",
        "mission_type": "coding",
        "xp_reward": 425, "order_idx": 28,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-sre-ai-01-llm-observability"],
    },
    {
        "slug": "m-sre-ai-04-incident-response",
        "title": "AI Incident Response",
        "tagline": "AI-specific runbooks: hallucination triage, model rollback, auto-resolution. When your AI breaks, you know what to do.",
        "tier": 2, "topic": "sre", "role_filter": "sre,devops",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 29,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m-sre-ai-02-drift-detection", "m-sre-ai-03-cost-optimization"],
    },
    # ── TIER 2: SecOps-AI — AI Security (V3b) ───────────────────────────────
    {
        "slug": "m-secops-ai-01-prompt-injection",
        "title": "Prompt Injection Defense",
        "tagline": "Build a prompt injection detector. Test against OWASP LLM Top 10 attack patterns. Wire into AADC guardrail nodes.",
        "tier": 2, "topic": "secops", "role_filter": "secops_eng,isso,swe",
        "mission_type": "coding",
        "xp_reward": 425, "order_idx": 30,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-secops-ai-02-adversarial-robustness",
        "title": "Adversarial Robustness",
        "tagline": "Red-team your own agent. Run a full OWASP LLM Top 10 audit against your system. Fix what you find.",
        "tier": 2, "topic": "secops", "role_filter": "secops_eng,isso",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 31,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m-secops-ai-01-prompt-injection"],
    },
    {
        "slug": "m-secops-ai-03-data-poisoning",
        "title": "RAG Corpus Integrity",
        "tagline": "Detect data poisoning and quality drift in your RAG corpus. Validate integrity before every deployment.",
        "tier": 2, "topic": "secops", "role_filter": "secops_eng,isso,swe",
        "mission_type": "coding",
        "xp_reward": 425, "order_idx": 32,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-secops-ai-01-prompt-injection"],
    },
    # ── TIER 1: Multimodal AI (V3d — M11) ───────────────────────────────────
    {
        "slug": "m-t1-11-multimodal",
        "title": "Multimodal AI",
        "tagline": "Vision models, document understanding, and image-in-prompt. Build a document classifier and wire it into a RAG pipeline.",
        "tier": 1, "topic": "ai_foundations", "role_filter": "all",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 11,
        "difficulty": "beginner", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
]

# ---------------------------------------------------------------------------
# Step definitions for Phase 1 missions (guided — configure + reflect)
# Each entry: content_path relative to CONTENT_ROOT
# ---------------------------------------------------------------------------

def _guided_steps(slug: str, steps_cfg: list) -> list:
    """Build step defs for a guided mission. steps_cfg: list of (title, step_type, file, fields)."""
    out = []
    for i, (title, step_type, filename, fields) in enumerate(steps_cfg, start=1):
        schema = {"fields": fields} if fields else {}
        out.append({
            "step_num": i,
            "title": title,
            "step_type": step_type,
            "content_path": f"tier2/{slug}/{filename}",
            "config_schema": schema,
            "xp_partial": 75 if step_type == "reflect" else 50,
            "skill_tag": slug.split("-")[1] if "-" in slug else slug,
            "estimated_seconds": 420 if step_type == "reflect" else 300,
        })
    return out


BUILTIN_STEPS: dict[str, list] = {
    # ── Leadership V1 ────────────────────────────────────────────────────────
    "m-leadership-01-ai-roi": _guided_steps("m-leadership-01-ai-roi", [
        ("AI ROI — Concepts", "watch", "step-1.md", []),
        ("Build Your ROI Model", "configure", "step-2.md", [
            {"id": "initiative", "label": "Which AI initiative are you evaluating?", "type": "text"},
            {"id": "current_cost", "label": "Estimated current annual cost of this workflow ($)", "type": "text"},
            {"id": "ai_cost", "label": "Estimated AI-assisted cost ($)", "type": "text"},
            {"id": "impl_cost", "label": "Estimated implementation cost ($)", "type": "text"},
            {"id": "payback_months", "label": "Estimated payback period (months)", "type": "text"},
        ]),
        ("Your ROI Playbook", "reflect", "step-3.md", [
            {"id": "first_investment", "label": "What AI investment would you fund first and why?", "type": "textarea"},
            {"id": "kpi_90d", "label": "What KPI proves success in 90 days?", "type": "text"},
        ]),
    ]),
    "m-leadership-02-build-vs-buy": _guided_steps("m-leadership-02-build-vs-buy", [
        ("Build vs. Buy vs. Partner — Framework", "watch", "step-1.md", []),
        ("Score Your Decision", "configure", "step-2.md", [
            {"id": "initiative", "label": "AI initiative being evaluated", "type": "text"},
            {"id": "data_sovereignty", "label": "Data sovereignty requirement (1-5)", "type": "text"},
            {"id": "customization", "label": "Customization needed (1-5)", "type": "text"},
            {"id": "time_to_value", "label": "Time-to-value priority (1-5)", "type": "text"},
            {"id": "recommendation", "label": "Your recommendation: Build / Buy / Partner", "type": "select",
             "options": ["Build", "Buy", "Partner", "Hybrid"]},
        ]),
        ("Your Decision Memo", "reflect", "step-3.md", [
            {"id": "decision", "label": "Your recommendation and top 3 reasons", "type": "textarea"},
            {"id": "risks", "label": "Biggest risk of this choice", "type": "textarea"},
        ]),
    ]),
    "m-leadership-03-ai-risk-comm": _guided_steps("m-leadership-03-ai-risk-comm", [
        ("AI Risk — The 5 Categories", "watch", "step-1.md", []),
        ("Build Your Risk Framework", "configure", "step-2.md", [
            {"id": "system_name", "label": "AI system name or description", "type": "text"},
            {"id": "top_risk", "label": "Highest-priority risk for this system", "type": "select",
             "options": ["Hallucination / Wrong Output", "Data Leakage", "Model Bias", "Adversarial Attack", "Workforce Impact"]},
            {"id": "mitigation", "label": "Mitigation posture (1-2 sentences)", "type": "textarea"},
            {"id": "residual", "label": "Residual risk after mitigation", "type": "textarea"},
        ]),
        ("Your Incident Communications Plan", "reflect", "step-3.md", [
            {"id": "day1_message", "label": "What would you say to stakeholders in the first 24 hours if your AI system produced a wrong output?", "type": "textarea"},
            {"id": "escalation", "label": "Who do you call first? (role, not name)", "type": "text"},
        ]),
    ]),
    "m-leadership-04-govern-ai": _guided_steps("m-leadership-04-govern-ai", [
        ("AI Governance — Policy and Structure", "watch", "step-1.md", []),
        ("Design Your Governance Structure", "configure", "step-2.md", [
            {"id": "registry_owner", "label": "Who owns the AI Use Case Registry in your org?", "type": "text"},
            {"id": "oversight_body", "label": "What existing body could serve as your AI Oversight Committee?", "type": "text"},
            {"id": "caio_candidate", "label": "Who is your CAIO candidate (role title)?", "type": "text"},
            {"id": "policy_gap", "label": "What is the biggest AI policy gap in your org today?", "type": "textarea"},
        ]),
        ("Your First AI Policy Decision", "reflect", "step-3.md", [
            {"id": "first_policy", "label": "What is the first AI policy you would write? Describe it in 2-3 sentences.", "type": "textarea"},
            {"id": "timeline", "label": "How long would it take to get it approved and published?", "type": "text"},
        ]),
    ]),
    "m-leadership-05-ai-workforce": _guided_steps("m-leadership-05-ai-workforce", [
        ("AI Workforce Strategy", "watch", "step-1.md", []),
        ("Map Your Workforce AI Readiness", "configure", "step-2.md", [
            {"id": "team_size", "label": "How many people are in your team/organization?", "type": "text"},
            {"id": "ai_native_roles", "label": "Which roles need to be AI-native (not just AI-aware)?", "type": "textarea"},
            {"id": "upskill_vs_hire", "label": "Upskill vs. Hire strategy for AI roles", "type": "select",
             "options": ["Mostly upskill existing", "Balanced", "Mostly hire new", "Outsource/partner"]},
            {"id": "timeline_months", "label": "Target timeline to reach 50% L2+ competency (months)", "type": "text"},
        ]),
        ("Your 90-Day Workforce Plan", "reflect", "step-3.md", [
            {"id": "top_3_people", "label": "Who are the 3-5 people you would invest in first for AI upskilling?", "type": "textarea"},
            {"id": "training_path", "label": "What training path would you put them on?", "type": "textarea"},
        ]),
    ]),
    "m-leadership-06-capstone": _guided_steps("m-leadership-06-capstone", [
        ("AI Transformation Roadmap — Overview", "watch", "step-1.md", []),
        ("Draft Your Roadmap", "configure", "step-2.md", [
            {"id": "q1_foundation", "label": "Q1 — Foundation: What infrastructure/skills work must happen first?", "type": "textarea"},
            {"id": "q2_quick_wins", "label": "Q2 — Quick Wins: Name 1-2 AI initiatives you'd pilot in Q2.", "type": "textarea"},
            {"id": "q3_augmentation", "label": "Q3 — Augmentation: What human-in-the-loop AI systems would you deploy?", "type": "textarea"},
            {"id": "q4_transformation", "label": "Q4 — Transformation: What is your most ambitious AI initiative?", "type": "textarea"},
        ]),
        ("Your Board Briefing Summary", "reflect", "step-3.md", [
            {"id": "board_bullets", "label": "Summarize your AI transformation roadmap in 5 bullet points for a board briefing.", "type": "textarea"},
            {"id": "success_metric", "label": "What single metric would prove your AI transformation is on track after 12 months?", "type": "text"},
        ]),
    ]),
    # ── Analyst V1 ───────────────────────────────────────────────────────────
    "m-analyst-01-competitive-intel-agent": _guided_steps("m-analyst-01-competitive-intel-agent", [
        ("Competitive Intelligence Agents — Overview", "watch", "step-1.md", []),
        ("Configure Your Intel Agent", "configure", "step-2.md", [
            {"id": "target_domain", "label": "Target domain or organization to monitor", "type": "text"},
            {"id": "data_sources", "label": "Data sources to monitor (select all that apply)", "type": "textarea"},
            {"id": "key_indicators", "label": "Top 3 indicators to track", "type": "textarea"},
            {"id": "alert_threshold", "label": "What event triggers an immediate alert?", "type": "textarea"},
        ]),
        ("Your Intelligence Product", "reflect", "step-3.md", [
            {"id": "intel_product", "label": "What intelligence product would this agent produce, and who is the consumer?", "type": "textarea"},
            {"id": "cadence", "label": "How often would the agent deliver its product (hourly / daily / weekly)?", "type": "text"},
        ]),
    ]),
    "m-analyst-02-market-signal-rag": _guided_steps("m-analyst-02-market-signal-rag", [
        ("RAG for Intelligence Analysts", "watch", "step-1.md", []),
        ("Configure Your RAG Pipeline", "configure", "step-2.md", [
            {"id": "document_types", "label": "Document types in your corpus (PDFs, spreadsheets, databases, etc.)", "type": "textarea"},
            {"id": "query_types", "label": "Top 3 question types you want to ask your data", "type": "textarea"},
            {"id": "grounding_req", "label": "Citation requirement — must every answer cite a source?", "type": "select",
             "options": ["Yes — every claim must cite source", "Preferred but not required", "Synthesis without citation is acceptable"]},
        ]),
        ("RAG Quality Check", "reflect", "step-3.md", [
            {"id": "corpus", "label": "What is the most important document corpus in your domain?", "type": "textarea"},
            {"id": "top_questions", "label": "What are the top 3 questions you wish you could instantly ask it?", "type": "textarea"},
        ]),
    ]),
    "m-analyst-03-pattern-detection": _guided_steps("m-analyst-03-pattern-detection", [
        ("Anomaly and Trend Detection", "watch", "step-1.md", []),
        ("Configure Your Detector", "configure", "step-2.md", [
            {"id": "metric", "label": "Key metric to monitor", "type": "text"},
            {"id": "baseline_period", "label": "Baseline period (e.g., last 30 days, last quarter)", "type": "text"},
            {"id": "sensitivity", "label": "Alert sensitivity", "type": "select",
             "options": ["High (more alerts, some false positives)", "Medium (balanced)", "Low (only major anomalies)"]},
            {"id": "false_positive_tolerance", "label": "How many false positives per week is acceptable?", "type": "text"},
        ]),
        ("Your Pattern Hypothesis", "reflect", "step-3.md", [
            {"id": "pattern", "label": "Describe one pattern in your domain that AI could detect earlier than current methods.", "type": "textarea"},
            {"id": "impact", "label": "What decision would earlier detection enable?", "type": "textarea"},
        ]),
    ]),
    "m-analyst-04-report-generation": _guided_steps("m-analyst-04-report-generation", [
        ("AI-Assisted Report Generation", "watch", "step-1.md", []),
        ("Configure Your Report Generator", "configure", "step-2.md", [
            {"id": "report_type", "label": "Report type", "type": "select",
             "options": ["SITREP", "Intelligence Assessment", "Trend Report", "Executive Summary", "Other"]},
            {"id": "data_inputs", "label": "Primary data inputs for this report", "type": "textarea"},
            {"id": "citation_req", "label": "Citation requirement", "type": "select",
             "options": ["Every claim must be traceable", "Major claims only", "Summary/synthesis acceptable"]},
            {"id": "review_workflow", "label": "Who approves before distribution (role)?", "type": "text"},
        ]),
        ("Your Intelligence Product Analysis", "reflect", "step-3.md", [
            {"id": "frequent_report", "label": "Describe the report you produce most frequently.", "type": "textarea"},
            {"id": "time_savings", "label": "How much time does it take today? What would 80% AI automation mean for your capacity?", "type": "textarea"},
        ]),
    ]),
    "m-analyst-05-capstone": _guided_steps("m-analyst-05-capstone", [
        ("Full Intelligence Cycle Overview", "watch", "step-1.md", []),
        ("Wire Your End-to-End Pipeline", "configure", "step-2.md", [
            {"id": "collect_source", "label": "Collect stage: What does your intel agent ingest?", "type": "textarea"},
            {"id": "detect_metric", "label": "Detect stage: What patterns/anomalies does your detector watch for?", "type": "textarea"},
            {"id": "report_template", "label": "Report stage: What format does the output take?", "type": "text"},
            {"id": "predict_horizon", "label": "Predict stage: What time horizon does your forecaster cover?", "type": "text"},
        ]),
        ("Your Capstone Intelligence Brief", "reflect", "step-3.md", [
            {"id": "intel_brief", "label": "Describe a complete intelligence product from your domain using all 4 pipeline stages. Who uses the output and what decisions does it drive?", "type": "textarea"},
        ]),
    ]),
    # ── SRE-AI — Production AI Ops ──────────────────────────────────────────
    "m-sre-ai-01-llm-observability": _guided_steps("m-sre-ai-01-llm-observability", [
        ("LLM Observability — What to Measure", "watch", "step-1.md", []),
        ("Instrument Your Agent with token_tracker.py", "configure", "step-2.md", [
            {"id": "agent_id", "label": "Agent or project ID to track", "type": "text"},
            {"id": "monthly_budget_usd", "label": "Monthly token budget (USD)", "type": "text"},
            {"id": "warning_threshold", "label": "Warning threshold (e.g. 0.8 = 80% of budget)", "type": "text"},
            {"id": "latency_p99_target", "label": "P99 latency target (ms)", "type": "text"},
            {"id": "error_rate_threshold", "label": "Alert if error rate exceeds (e.g. 0.05 = 5%)", "type": "text"},
        ]),
        ("Observability Runbook", "reflect", "step-3.md", [
            {"id": "on_budget_exceeded", "label": "What happens when check_budget() returns 'block'? Describe your circuit-breaker plan.", "type": "textarea"},
            {"id": "latency_alert_action", "label": "When P99 latency spikes 3x above baseline, what is your first diagnostic step?", "type": "textarea"},
        ]),
    ]),
    "m-sre-ai-02-drift-detection": _guided_steps("m-sre-ai-02-drift-detection", [
        ("AI Drift — 4 Types You Must Monitor", "watch", "step-1.md", []),
        ("Configure Drift Thresholds with model_monitor.py", "configure", "step-2.md", [
            {"id": "model_id", "label": "Model ID to monitor (e.g. qwen3-local)", "type": "text"},
            {"id": "quality_warning_pct", "label": "Quality degradation warning threshold (% drop from baseline)", "type": "text"},
            {"id": "latency_warning_pct", "label": "Latency increase warning threshold (%)", "type": "text"},
            {"id": "token_inflation_pct", "label": "Token inflation warning threshold (%)", "type": "text"},
            {"id": "retrain_trigger", "label": "At what severity level do you auto-trigger retraining?", "type": "select",
             "options": ["critical only", "warning or critical", "info or above (aggressive)"]},
        ]),
        ("Drift Response Protocol", "reflect", "step-3.md", [
            {"id": "critical_response", "label": "When detect_drift() returns severity='critical', what is your 3-step response?", "type": "textarea"},
            {"id": "baseline_update", "label": "When do you reset_baseline()? What conditions must be true before resetting?", "type": "textarea"},
        ]),
    ]),
    "m-sre-ai-03-cost-optimization": _guided_steps("m-sre-ai-03-cost-optimization", [
        ("AI Cost Optimization — 5 Levers", "watch", "step-1.md", []),
        ("Configure Cost Controls", "configure", "step-2.md", [
            {"id": "primary_model", "label": "Primary (expensive) model", "type": "text"},
            {"id": "edge_model", "label": "Edge/cheap model for routing (e.g. qwen3-local)", "type": "text"},
            {"id": "routing_strategy", "label": "Cost-aware routing strategy", "type": "select",
             "options": ["Complexity-based (route simple queries to edge)", "Budget-based (fall back when budget low)", "Latency-based (use edge for <200ms SLA)", "Always edge with cloud fallback"]},
            {"id": "cache_ttl_seconds", "label": "Prompt cache TTL (seconds, 0 = disabled)", "type": "text"},
            {"id": "compression_enabled", "label": "Enable prompt compression?", "type": "select",
             "options": ["Yes — strip whitespace and redundant context", "Yes — aggressive (may reduce quality)", "No — preserve full context"]},
        ]),
        ("Cost Optimization Findings", "reflect", "step-3.md", [
            {"id": "biggest_waste", "label": "After running get_cost_dashboard(), what is the highest-cost function in your system?", "type": "textarea"},
            {"id": "optimization_plan", "label": "Which two optimizations from recommend_optimizations() would you implement first and why?", "type": "textarea"},
        ]),
    ]),
    "m-sre-ai-04-incident-response": _guided_steps("m-sre-ai-04-incident-response", [
        ("AI Incident Types and Triage", "watch", "step-1.md", []),
        ("Build Your AI Runbook with auto_resolver.py", "configure", "step-2.md", [
            {"id": "hallucination_threshold", "label": "At what quality score (0.0-1.0) do you classify a response as a hallucination risk?", "type": "text"},
            {"id": "confidence_threshold", "label": "auto_resolver confidence threshold for auto-resolution (default 0.7)", "type": "text"},
            {"id": "escalation_threshold", "label": "Escalation threshold (below this confidence, page human)", "type": "text"},
            {"id": "rollback_trigger", "label": "What drift event triggers an automatic model rollback?", "type": "select",
             "options": ["Critical quality_degradation only", "Any critical severity drift event", "Critical OR warning quality_degradation", "Manual only — never auto-rollback"]},
        ]),
        ("Incident Retrospective", "reflect", "step-3.md", [
            {"id": "runbook_gap", "label": "Name one AI incident type not covered by auto_resolver.py today. How would you handle it?", "type": "textarea"},
            {"id": "postmortem_format", "label": "Describe your AI incident postmortem format. What 5 fields must every AI incident postmortem include?", "type": "textarea"},
        ]),
    ]),
    # ── SecOps-AI — AI Security ──────────────────────────────────────────────
    "m-secops-ai-01-prompt-injection": _guided_steps("m-secops-ai-01-prompt-injection", [
        ("Prompt Injection — The OWASP LLM01 Threat", "watch", "step-1.md", []),
        ("Build a Prompt Injection Detector", "configure", "step-2.md", [
            {"id": "attack_patterns", "label": "Which attack patterns will your detector screen for? (select all that apply)", "type": "textarea"},
            {"id": "detection_mode", "label": "Detection mode", "type": "select",
             "options": ["Block on match (highest safety)", "Sanitize and allow (strip suspicious content)", "Log and allow (monitoring only)", "Reject with explanation"]},
            {"id": "aadc_node", "label": "Which AADC guardrail node type will this detector wire into?", "type": "select",
             "options": ["guardrail (input filter)", "circuit-breaker (emergency stop)", "audit-logger (log-only)", "trace-collector (forensics)"]},
            {"id": "false_positive_strategy", "label": "How will you handle false positives that block legitimate requests?", "type": "textarea"},
        ]),
        ("Red Team Your Detector", "reflect", "step-3.md", [
            {"id": "bypass_attempt", "label": "Describe one prompt injection technique your detector would NOT catch today. How would you fix it?", "type": "textarea"},
            {"id": "defense_depth", "label": "What defense-in-depth layers exist AFTER your prompt injection detector (assume it's bypassed)?", "type": "textarea"},
        ]),
    ]),
    "m-secops-ai-02-adversarial-robustness": _guided_steps("m-secops-ai-02-adversarial-robustness", [
        ("OWASP LLM Top 10 — Red Team Framework", "watch", "step-1.md", []),
        ("Audit Your Agent Against OWASP LLM Top 10", "configure", "step-2.md", [
            {"id": "agent_description", "label": "Describe the agent you are red-teaming (purpose + tools)", "type": "textarea"},
            {"id": "llm01_finding", "label": "LLM01 — Prompt Injection: what is this agent's highest-risk input vector?", "type": "textarea"},
            {"id": "llm03_finding", "label": "LLM03 — Training Data Poisoning: is your model's training data validated? Y/N + details", "type": "text"},
            {"id": "llm06_finding", "label": "LLM06 — Sensitive Information Disclosure: what PII or CUI could the model leak?", "type": "textarea"},
            {"id": "llm09_finding", "label": "LLM09 — Overreliance: where does your system present AI output as ground truth?", "type": "textarea"},
        ]),
        ("Remediation Plan", "reflect", "step-3.md", [
            {"id": "top_vulnerability", "label": "What is the highest-severity vulnerability you found? Describe the exploit scenario.", "type": "textarea"},
            {"id": "fix_priority", "label": "List your top 3 remediations in priority order with estimated effort.", "type": "textarea"},
        ]),
    ]),
    "m-secops-ai-03-data-poisoning": _guided_steps("m-secops-ai-03-data-poisoning", [
        ("RAG Corpus Integrity — Threat Model", "watch", "step-1.md", []),
        ("Configure Corpus Validation with quality_feedback_loop.py", "configure", "step-2.md", [
            {"id": "corpus_id", "label": "RAG corpus or collection ID to validate", "type": "text"},
            {"id": "quality_threshold", "label": "Minimum document quality score to accept (0.0-1.0)", "type": "text"},
            {"id": "poisoning_indicators", "label": "What poisoning indicators will you scan for?", "type": "select",
             "options": ["Sudden topic drift (new documents don't match corpus theme)", "Quality score drop across recent additions", "Source domain anomaly (unexpected origins)", "All of the above"]},
            {"id": "validation_cadence", "label": "How often will you run the feedback cycle?", "type": "select",
             "options": ["Before every RAG deployment", "Daily (overnight batch)", "Weekly", "On-demand only"]},
        ]),
        ("Corpus Integrity Retrospective", "reflect", "step-3.md", [
            {"id": "poison_scenario", "label": "Describe a realistic data poisoning scenario for your specific RAG corpus. How would an attacker inject content?", "type": "textarea"},
            {"id": "detection_gap", "label": "What poisoning technique would quality_feedback_loop.py NOT catch? How would you close that gap?", "type": "textarea"},
        ]),
    ]),
    # ── AADC Ops Config Generator mission ────────────────────────────────────
    "m-swe-aadc-09-ops-config": _guided_steps("m-swe-aadc-09-ops-config", [
        ("Design → Runtime: The Ops Config Generator", "watch", "step-1.md", []),
        ("Generate a Config for Your Design", "configure", "step-2.md", [
            {"id": "design_id", "label": "AADC design ID to generate config for", "type": "text"},
            {"id": "drift_warning_pct", "label": "Drift warning threshold (% deviation from baseline)", "type": "text"},
            {"id": "token_budget_usd", "label": "Monthly token budget (USD)", "type": "text"},
            {"id": "guardrail_mode", "label": "Guardrail mode", "type": "select",
             "options": ["block (stop on match)", "sanitize (strip and allow)", "log_only (monitoring)"]},
            {"id": "create_kanban_tasks", "label": "Create Kanban wiring tasks?", "type": "select",
             "options": ["Yes — create one task per tool", "No — config file only"]},
        ]),
        ("Ops Config Review", "reflect", "step-3.md", [
            {"id": "missing_nodes", "label": "What ops/safety nodes are NOT in your design that should be? Add them to the canvas and regenerate.", "type": "textarea"},
            {"id": "first_task", "label": "Which Kanban task will you wire up first and why?", "type": "textarea"},
            {"id": "custom_mapping", "label": "Would you add any custom node → tool mappings to args/aadc_node_tool_map.yaml? If so, describe them.", "type": "textarea"},
        ]),
    ]),
    # ── Multi-Language SDK missions ──────────────────────────────────────────
    "m-swe-sdk-java": _guided_steps("m-swe-sdk-java", [
        ("Spring Boot + Claude API — Architecture", "watch", "step-1.md", []),
        ("Add Claude to Your Spring Boot Service", "configure", "step-2.md", [
            {"id": "endpoint_purpose", "label": "What endpoint will call the LLM? (e.g., /api/summarize)", "type": "text"},
            {"id": "input_type", "label": "Input type", "type": "select",
             "options": ["User text (free-form)", "Structured JSON", "Document/file content", "Database record"]},
            {"id": "streaming", "label": "Streaming response required?", "type": "select",
             "options": ["Yes — stream tokens to client", "No — single JSON response"]},
            {"id": "tool_use", "label": "Does the LLM need to call tools (functions)?", "type": "select",
             "options": ["Yes", "No"]},
        ]),
        ("Production Considerations", "reflect", "step-3.md", [
            {"id": "error_handling", "label": "How will you handle LLM API failures without breaking your Spring Boot service?", "type": "textarea"},
            {"id": "testing_approach", "label": "How will you test the AI feature — unit test, integration test, or contract test?", "type": "textarea"},
        ]),
    ]),
    "m-swe-sdk-typescript": _guided_steps("m-swe-sdk-typescript", [
        ("Next.js + Claude SDK — Streaming Architecture", "watch", "step-1.md", []),
        ("Build the AI Feature", "configure", "step-2.md", [
            {"id": "ui_pattern", "label": "UI interaction pattern", "type": "select",
             "options": ["Chat interface (multi-turn)", "Single-shot form + result", "Inline completion (copilot-style)", "Background job + result page"]},
            {"id": "tool_calling", "label": "Server-side tool calling needed?", "type": "select",
             "options": ["Yes — LLM should call my API routes", "No — generation only"]},
            {"id": "streaming_ux", "label": "How will you handle streaming in the UI?", "type": "select",
             "options": ["ReadableStream + TextDecoder", "Server-Sent Events", "WebSocket", "Poll for completion"]},
        ]),
        ("TypeScript AI Feature Review", "reflect", "step-3.md", [
            {"id": "state_management", "label": "How will you manage conversation state across server and client in Next.js?", "type": "textarea"},
            {"id": "edge_runtime", "label": "Will you run this on the Edge Runtime or Node.js runtime? Explain your choice.", "type": "textarea"},
        ]),
    ]),
    "m-swe-sdk-go": _guided_steps("m-swe-sdk-go", [
        ("Go + Claude API — Structured Output & Concurrency", "watch", "step-1.md", []),
        ("Design Your Go Integration", "configure", "step-2.md", [
            {"id": "output_type", "label": "Response format your Go service needs", "type": "select",
             "options": ["Structured JSON (schema-validated)", "Plain text / Markdown", "Streaming text", "Tool call results"]},
            {"id": "retry_strategy", "label": "Retry strategy for API failures", "type": "select",
             "options": ["Exponential backoff (recommended)", "Fixed interval", "No retry — fail fast", "Circuit breaker"]},
            {"id": "concurrency", "label": "Will you fan out concurrent LLM calls?", "type": "select",
             "options": ["Yes — goroutine pool", "Yes — errgroup", "No — sequential only"]},
        ]),
        ("Go Service Production Notes", "reflect", "step-3.md", [
            {"id": "context_cancellation", "label": "How will you propagate context cancellation from the HTTP handler to the Claude API call?", "type": "textarea"},
            {"id": "schema_validation", "label": "Describe how you would validate the structured JSON output against your Go struct schema.", "type": "textarea"},
        ]),
    ]),
    "m-swe-sdk-dotnet": _guided_steps("m-swe-sdk-dotnet", [
        (".NET / C# + Anthropic SDK — DI Pattern", "watch", "step-1.md", []),
        ("Configure Your .NET Integration", "configure", "step-2.md", [
            {"id": "di_lifetime", "label": "DI service lifetime for the Claude client", "type": "select",
             "options": ["Singleton (recommended for HttpClient)", "Scoped", "Transient"]},
            {"id": "async_pattern", "label": "Async pattern", "type": "select",
             "options": ["async/await throughout", "IAsyncEnumerable for streaming", "Task.WhenAll for parallel calls"]},
            {"id": "config_source", "label": "Where will the API key live in .NET config?", "type": "select",
             "options": ["appsettings.json + IOptions<T>", "Environment variable only", "Azure Key Vault / AWS Secrets Manager", "User Secrets (local dev)"]},
        ]),
        (".NET Integration Review", "reflect", "step-3.md", [
            {"id": "testing", "label": "How will you mock the Claude client in unit tests? Describe the interface design.", "type": "textarea"},
            {"id": "polly", "label": "Would you use Polly for retry/circuit-breaker on the Claude API calls? Explain your policy design.", "type": "textarea"},
        ]),
    ]),
    # ── DataOps fine-tuning ──────────────────────────────────────────────────
    "m-dataops-05-fine-tuning": _guided_steps("m-dataops-05-fine-tuning", [
        ("Fine-Tuning Pipeline Overview", "watch", "step-1.md", []),
        ("Design Your Training Dataset", "configure", "step-2.md", [
            {"id": "base_model", "label": "Base model to fine-tune", "type": "select",
             "options": ["claude-haiku-4-5", "claude-sonnet-4-6", "Local model via Ollama", "Open-source base (Llama, Mistral, etc.)"]},
            {"id": "task_type", "label": "What task are you fine-tuning for?", "type": "text"},
            {"id": "pair_count", "label": "Approximate number of training pairs you can generate", "type": "text"},
            {"id": "quality_signal", "label": "How will you generate ground-truth labels for your pairs?", "type": "textarea"},
        ]),
        ("Evaluate and Promote", "configure", "step-3.md", [
            {"id": "eval_metric", "label": "Primary evaluation metric", "type": "select",
             "options": ["ROUGE (summarization)", "Accuracy (classification)", "Win rate vs. base model (LLM judge)", "Human preference rating", "Domain-specific task score"]},
            {"id": "promotion_gate", "label": "Minimum eval score to promote model to production", "type": "text"},
            {"id": "rollback_trigger", "label": "What degradation triggers an automatic rollback?", "type": "textarea"},
        ]),
        ("Fine-Tuning Retrospective", "reflect", "step-4.md", [
            {"id": "dataset_quality", "label": "What was the hardest part of getting high-quality training pairs?", "type": "textarea"},
            {"id": "production_ops", "label": "What monitoring would you put on the fine-tuned model in production to detect drift?", "type": "textarea"},
        ]),
    ]),
    # ── Tier 1 M11: Multimodal AI ────────────────────────────────────────────
    "m-t1-11-multimodal": [
        {
            "step_num": 1,
            "title": "Multimodal AI — Vision, Documents, Images",
            "step_type": "watch",
            "content_path": "tier1/m11-multimodal/step-1.md",
            "config_schema": {},
            "xp_partial": 50,
            "skill_tag": "multimodal",
            "estimated_seconds": 300,
        },
        {
            "step_num": 2,
            "title": "Image-in-Prompt: Build a Document Classifier",
            "step_type": "coding",
            "content_path": "tier1/m11-multimodal/step-2.md",
            "config_schema": {
                "fields": [
                    {"id": "document_type", "label": "What type of documents will your classifier handle?", "type": "select",
                     "options": ["Government forms / PDFs", "Technical diagrams", "Scanned reports", "Mixed document types"]},
                    {"id": "output_categories", "label": "List the classification categories (comma-separated)", "type": "text"},
                    {"id": "confidence_threshold", "label": "Minimum confidence to accept a classification (0.0-1.0)", "type": "text"},
                ]
            },
            "xp_partial": 100,
            "skill_tag": "multimodal",
            "estimated_seconds": 600,
        },
        {
            "step_num": 3,
            "title": "Wire the Classifier into a RAG Pipeline",
            "step_type": "reflect",
            "content_path": "tier1/m11-multimodal/step-3.md",
            "config_schema": {
                "fields": [
                    {"id": "pre_filter_strategy", "label": "How will you use the classifier output to filter documents before RAG retrieval?", "type": "textarea"},
                    {"id": "fallback_handling", "label": "What happens when confidence is below threshold? Describe your fallback.", "type": "textarea"},
                    {"id": "latency_tradeoff", "label": "What is the latency cost of adding a vision step? How do you mitigate it?", "type": "textarea"},
                ]
            },
            "xp_partial": 75,
            "skill_tag": "multimodal",
            "estimated_seconds": 420,
        },
    ],
    # ── Executive AI Primer ──────────────────────────────────────────────────
    "m-exec-primer-01": _guided_steps("m-exec-primer-01", [
        ("What Is an LLM?", "watch", "step-1.md", []),
        ("AI in Your Organization — Opportunities", "watch", "step-2.md", []),
        ("AI Risks — The 5 You Own", "configure", "step-3.md", [
            {"id": "top_risk", "label": "Which of the 5 risks is most relevant to your organization right now?", "type": "select",
             "options": ["Hallucination / Wrong Output", "Data Leakage", "Model Bias", "Adversarial Manipulation", "Workforce Displacement"]},
            {"id": "mitigation_plan", "label": "What is your current mitigation for that risk?", "type": "textarea"},
        ]),
        ("Your AI Briefing Card", "reflect", "step-4.md", [
            {"id": "what_ai_can_do", "label": "What AI can do for my organization", "type": "textarea"},
            {"id": "risks_i_own", "label": "Risks I own as a leader", "type": "textarea"},
            {"id": "first_investment", "label": "What I'd invest in first", "type": "text"},
            {"id": "team_needs", "label": "What I need from my team", "type": "textarea"},
            {"id": "leadership_brief", "label": "What I'd brief to senior leadership in 2 sentences", "type": "textarea"},
        ]),
    ]),
}


def seed_mission_catalog() -> None:
    """Upsert all builtin missions and seed their steps on first creation."""
    try:
        from .db import upsert_mission
        from tools.db.storage import get_connection
        conn = get_connection()
        for m in BUILTIN_MISSIONS:
            mission_id = upsert_mission(m)
            # Seed steps only if none exist for this mission
            existing = conn.execute(
                "SELECT COUNT(*) FROM fa_mission_steps WHERE mission_id=?", (mission_id,)
            ).fetchone()[0]
            if existing == 0 and m["slug"] in BUILTIN_STEPS:
                _seed_steps(conn, mission_id, m["slug"])
        conn.commit()
        _log.info("FORGE Academy: seeded/updated %d missions", len(BUILTIN_MISSIONS))
    except Exception as e:
        _log.warning("Mission catalog seed failed: %s", e)


def _seed_steps(conn, mission_id: int, mission_slug: str) -> None:
    """Insert step records for a mission from BUILTIN_STEPS."""
    steps = BUILTIN_STEPS.get(mission_slug, [])
    for step in steps:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO fa_mission_steps
                   (mission_id, step_num, title, step_type, content_path,
                    config_schema_json, xp_partial, skill_tag, hint_allowed, estimated_seconds)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    mission_id,
                    step["step_num"],
                    step["title"],
                    step.get("step_type", "configure"),
                    step.get("content_path", ""),
                    json.dumps(step.get("config_schema", {})),
                    step.get("xp_partial", 50),
                    step.get("skill_tag", ""),
                    1,
                    step.get("estimated_seconds", 300),
                ),
            )
        except Exception as exc:
            _log.debug("Step seed %s step %s: %s", mission_slug, step.get("step_num"), exc)


# ---------------------------------------------------------------------------
# Load step content from files
# ---------------------------------------------------------------------------

def _md_to_html(text: str) -> str:
    """Convert markdown text to safe HTML."""
    try:
        import markdown as md_lib
        return md_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    except Exception:
        import html
        return f"<pre>{html.escape(text)}</pre>"


def load_step_content(content_path: str) -> dict:
    """Load and render markdown content for a step from the content directory.

    Returns dict with keys: html (str), frontmatter (dict).
    """
    if not content_path:
        return {"html": "", "frontmatter": {}}
    full = CONTENT_ROOT / content_path
    try:
        raw = full.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(raw)
        return {"html": _md_to_html(body), "frontmatter": fm}
    except FileNotFoundError:
        return {"html": f"<p><em>Content file not found: <code>{content_path}</code></em></p>", "frontmatter": {}}
    except Exception as e:
        _log.warning("load_step_content %s: %s", content_path, e)
        return {"html": "", "frontmatter": {}}


def load_starter_code(path: str) -> str:
    if not path:
        return ""
    full = CONTENT_ROOT / path
    try:
        return full.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "# Write your code here\n"


def load_test_code(path: str) -> str:
    if not path:
        return ""
    full = CONTENT_ROOT / path
    try:
        return full.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def get_mission_with_steps(slug: str) -> dict | None:
    """Return mission dict with steps and content loaded."""
    from .db import get_mission, get_mission_steps
    from .ontology import build_mission_ontology_id
    mission = get_mission(slug)
    if not mission:
        return None
    # Attach ontology metadata
    mission_onto = build_mission_ontology_id(
        slug=mission["slug"],
        mission_type=mission.get("mission_type", "coding"),
        topic=mission.get("topic", ""),
        title=mission.get("title", ""),
        tier=mission.get("tier", 1),
    )
    mission["ontology"] = mission_onto
    steps = get_mission_steps(mission["id"])
    for step in steps:
        content = load_step_content(step.get("content_path", ""))
        step["content_md"] = content["html"]
        step["content_frontmatter"] = content["frontmatter"]
        step["ontology_id"] = content["frontmatter"].get("ontology_id", "")
        step["step_class"] = content["frontmatter"].get("step_class", "")
        step["starter_code"] = load_starter_code(step.get("starter_code_path", ""))
        step["test_code"] = load_test_code(step.get("test_code_path", ""))
        try:
            parsed_cfg = json.loads(step.get("config_schema_json") or "{}")
        except Exception:
            parsed_cfg = {}
        step["config_schema"] = parsed_cfg
        step["config_schema_json"] = parsed_cfg  # expose as dict for tojson serialization
    mission["steps"] = steps
    return mission
