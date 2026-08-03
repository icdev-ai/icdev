from __future__ import annotations
# CUI // SP-CTI
"""FORGE Academy content loader — parse bundled mission content + seed catalog."""

import json
import logging
import re
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
    # NOTE (penta-fix-02): the "m-analyst-05-capstone" entry that used to sit
    # here was a duplicate slug — the complete, fully step-wired capstone (prereq
    # m-analyst-04-report-generation, with a step definition in the registry)
    # lives in the competitive-intel analyst track below. The ON CONFLICT(slug)
    # upsert silently collapsed the two, so this stale copy is removed.
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
    # ── TIER 2: NetOps — PNA Predictors ──────────────────────────────────────
    {
        "slug": "m-netops-pna-01",
        "title": "Predictive Network Analytics",
        "tagline": "6 ML predictors for proactive network management. BGP, capacity, compliance drift, supply chain.",
        "tier": 2, "topic": "network", "role_filter": "netops,devops,sre",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 5,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: SRE — Observability & XAI ────────────────────────────────────
    {
        "slug": "m-sre-xai-01",
        "title": "Observability & Explainable AI",
        "tagline": "OTel traces + AgentSHAP + PROV-AGENT. Know exactly why your agent did what it did.",
        "tier": 2, "topic": "observability", "role_filter": "sre,devops,secops_eng",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 5,
        "difficulty": "advanced", "estimated_minutes": 45,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── TIER 2: ACE Co-Worker Track ──────────────────────────────────────────
    {
        "slug": "m-ace-01-roles-delegation",
        "title": "ACE Co-Worker Roles & Delegation",
        "tagline": "Stop building solo agents. Build a team that delegates, verifies, and learns.",
        "tier": 2, "topic": "ace", "role_filter": "ai_developer,agent_developer,swe_arch",
        "mission_type": "hybrid",
        "xp_reward": 350, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-ace-02-creator-verifier",
        "title": "ACE Creator-Verifier Pattern",
        "tagline": "Two co-workers checking each other is worth ten solo runs.",
        "tier": 2, "topic": "ace", "role_filter": "ai_developer,agent_developer,swe_arch",
        "mission_type": "hybrid",
        "xp_reward": 400, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-ace-01-roles-delegation"],
    },
    {
        "slug": "m-ace-03-multi-role-pipeline",
        "title": "ACE Multi-Role Pipeline",
        "tagline": "Chain six specialists into one autonomous team. This is agentic engineering.",
        "tier": 2, "topic": "ace", "role_filter": "ai_developer,agent_developer,swe_arch",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 3,
        "difficulty": "advanced", "estimated_minutes": 50,
        "prereqs": ["m-ace-02-creator-verifier"],
    },
    {
        "slug": "m-ace-capstone",
        "title": "ACE Capstone: Full Co-Worker Pipeline",
        "tagline": "Analyze a real repo with a 3-role ACE pipeline. Delegate everything.",
        "tier": 2, "topic": "ace", "role_filter": "ai_developer,agent_developer,swe_arch",
        "mission_type": "hybrid",
        "xp_reward": 600, "order_idx": 4,
        "difficulty": "advanced", "estimated_minutes": 60,
        "prereqs": ["m-ace-03-multi-role-pipeline"],
    },
    # ── TIER 2: DocGen Track ──────────────────────────────────────────────────
    {
        "slug": "m-docgen-01-session-lifecycle",
        "title": "DocGen Session Lifecycle",
        "tagline": "Turn a raw brief into a structured SSP in minutes, not days.",
        "tier": 2, "topic": "docgen", "role_filter": "isso,issm,swe_arch,dataops",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 25,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-docgen-02-portfolio-artifact",
        "title": "DocGen Portfolio Artifacts",
        "tagline": "Every document you generate becomes certification evidence. Build your portfolio.",
        "tier": 2, "topic": "docgen", "role_filter": "isso,issm,swe_arch,dataops",
        "mission_type": "guided",
        "xp_reward": 350, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 30,
        "prereqs": ["m-docgen-01-session-lifecycle"],
    },
    # ── TIER 2: Agent Readiness Track ────────────────────────────────────────
    {
        "slug": "m-readiness-01-eleven-pillars",
        "title": "The 11-Pillar Readiness Framework",
        "tagline": "Know exactly how agent-ready your codebase is. Score it.",
        "tier": 2, "topic": "readiness", "role_filter": "secops_eng,isso,swe_arch,devops",
        "mission_type": "coding",
        "xp_reward": 350, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-readiness-02-remediation",
        "title": "Readiness Remediation",
        "tagline": "STIG markers, CUI headers, CI gates. Move your score from 40% to 90%.",
        "tier": 2, "topic": "readiness", "role_filter": "secops_eng,isso,swe_arch,devops",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 40,
        "prereqs": ["m-readiness-01-eleven-pillars"],
    },
    {
        "slug": "m-readiness-03-continuous",
        "title": "Continuous Readiness Monitoring",
        "tagline": "Wire the checker as a CI gate. Regressions get caught before they ship.",
        "tier": 2, "topic": "readiness", "role_filter": "secops_eng,isso,swe_arch,devops",
        "mission_type": "guided",
        "xp_reward": 300, "order_idx": 3,
        "difficulty": "beginner", "estimated_minutes": 20,
        "prereqs": ["m-readiness-02-remediation"],
    },
    # ── TIER 2: AI Governance Track ──────────────────────────────────────────
    {
        "slug": "m-gov-01-transparency",
        "title": "AI Transparency: OMB M-25-21",
        "tagline": "Know every AI system in your portfolio. OMB says you have to. Here's how.",
        "tier": 2, "topic": "governance", "role_filter": "ciso,issm,leadership,pm",
        "mission_type": "guided",
        "xp_reward": 350, "order_idx": 1,
        "difficulty": "beginner", "estimated_minutes": 30,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-gov-02-accountability",
        "title": "AI Accountability: CAIO & Oversight",
        "tagline": "Who can shut it down? Under what conditions? In how long? Answer those first.",
        "tier": 2, "topic": "governance", "role_filter": "ciso,issm,leadership,pm",
        "mission_type": "guided",
        "xp_reward": 400, "order_idx": 2,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m-gov-01-transparency"],
    },
    {
        "slug": "m-gov-03-intake",
        "title": "AI Governance Intake: 6 Pillars",
        "tagline": "Score your deployment on 6 pillars + the 7th governance dimension.",
        "tier": 2, "topic": "governance", "role_filter": "ciso,issm,leadership,pm",
        "mission_type": "guided",
        "xp_reward": 350, "order_idx": 3,
        "difficulty": "intermediate", "estimated_minutes": 30,
        "prereqs": ["m-gov-02-accountability"],
    },
    {
        "slug": "m-gov-capstone",
        "title": "AI Governance Capstone: Full Portfolio",
        "tagline": "5 artifacts. One system. Inventory, model card, oversight plan, ethics, intake.",
        "tier": 2, "topic": "governance", "role_filter": "ciso,issm,leadership,pm",
        "mission_type": "guided",
        "xp_reward": 700, "order_idx": 4,
        "difficulty": "advanced", "estimated_minutes": 60,
        "prereqs": ["m-gov-03-intake"],
    },
    # ── penta-aca-04 missions — ICDEV platform AI subsystems (batch 1) ────────
    # NOTE: penta-aca-05 (batch 2) appends AFTER this block. Keep new missions
    # here so both batches stay conflict-free.
    {
        "slug": "m-cortex-01-unified-ai-layer",
        "title": "ICDEV Cortex — The Unified AI Layer",
        "tagline": "One governed facade over RAG, KG, documents, and keyword search. Stop wiring five backends by hand.",
        "tier": 2, "topic": "cortex", "role_filter": "swe,swe_arch,ai_developer",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m03-rag-basics"],
    },
    {
        "slug": "m-dic-01-grounded-citations",
        "title": "Document Intelligence — Grounded Citations",
        "tagline": "Ingest, cite, and gate. Every claim carries a [source:] or it does not ship.",
        "tier": 2, "topic": "dic", "role_filter": "swe,swe_arch,analyst,isso",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m03-rag-basics"],
    },
    {
        "slug": "m-graphrag-01-kg-traversal",
        "title": "GraphRAG & the Knowledge Graph",
        "tagline": "Vectors find similar chunks. The graph finds connected facts. GraphRAG uses both.",
        "tier": 2, "topic": "graphrag", "role_filter": "swe,swe_arch,ai_developer,analyst",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 1,
        "difficulty": "advanced", "estimated_minutes": 40,
        "prereqs": ["m03-rag-basics"],
    },
    {
        "slug": "m-iqe-01-collections-adapters",
        "title": "IQE — In-App Query Engine",
        "tagline": "Ask any canvas a question in plain language. Collections + adapters make it possible.",
        "tier": 2, "topic": "iqe", "role_filter": "swe,swe_arch,analyst",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-kanban-01-governed-pipeline",
        "title": "The Governed Delivery Pipeline",
        "tagline": "Task to merge, with gates that hold. Understand the lifecycle before you ship into it.",
        "tier": 2, "topic": "kanban", "role_filter": "swe,swe_arch,devops",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    # ── penta-aca-05 missions — ICDEV platform AI subsystems (batch 2) ─────────
    # Foundry/ACF, Strategos, ZIG zero trust, TRUST grounding, design-canvas trio.
    # Appended AFTER the batch-1 block so both batches stay conflict-free.
    {
        "slug": "m-foundry-01-capability-pipeline",
        "title": "Autonomous Capability Foundry — 0 to 1",
        "tagline": "Invent a net-new canvas with no human in the loop. The novelty gate and CoD decide what lives.",
        "tier": 2, "topic": "foundry", "role_filter": "swe,swe_arch,ai_developer",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 1,
        "difficulty": "advanced", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-strategos-01-signal-wargaming",
        "title": "Strategos — Signals to Wargamed Decision",
        "tagline": "Score raw OSINT, keep the top signals, then wargame the call. DIB intelligence, end to end.",
        "tier": 2, "topic": "strategos", "role_filter": "analyst,swe,swe_arch",
        "mission_type": "coding",
        "xp_reward": 500, "order_idx": 1,
        "difficulty": "advanced", "estimated_minutes": 40,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-zig-01-zero-trust-maturity",
        "title": "NSA ZIG — Scoring Zero Trust Maturity",
        "tagline": "7 pillars, 42 capabilities, one posture score. Then find the pillar to invest in next.",
        "tier": 2, "topic": "zig", "role_filter": "isso,issm,secops_eng,swe_arch",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m10-tier1-capstone"],
    },
    {
        "slug": "m-trust-01-citation-grounding",
        "title": "TRUST — Grounding, Provenance, Fail-Closed Egress",
        "tagline": "Measure how well a claim is grounded, decide include/flag/abstain, stamp provenance, block un-redacted egress.",
        "tier": 2, "topic": "trust", "role_filter": "swe,swe_arch,analyst,isso",
        "mission_type": "coding",
        "xp_reward": 450, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 35,
        "prereqs": ["m03-rag-basics"],
    },
    {
        "slug": "m-canvas-trio-01-design-canvases",
        "title": "The Design Canvas Trio — DDC, ODC, NDC",
        "tagline": "Route a design need to the right canvas — or return None. Registry-driven, no forced fits.",
        "tier": 2, "topic": "canvas", "role_filter": "swe,swe_arch,devops,pm",
        "mission_type": "coding",
        "xp_reward": 400, "order_idx": 1,
        "difficulty": "intermediate", "estimated_minutes": 30,
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
    # ── penta-aca-04 missions ─────────────────────────────────────────────────
    # Single-step coding missions. Step files live under
    # content/tier2/<slug>/steps/step1_{starter,test}.py + step1_<name>.md.
    # penta-aca-05 (batch 2) appends its BUILTIN_STEPS entries AFTER this block.
    "m-cortex-01-unified-ai-layer": [
        {
            "step_num": 1,
            "title": "Route a request through the Cortex facade",
            "step_type": "coding",
            "content_path": "tier2/m-cortex-01-unified-ai-layer/steps/step1_cortex.md",
            "starter_code_path": "tier2/m-cortex-01-unified-ai-layer/steps/step1_starter.py",
            "test_code_path": "tier2/m-cortex-01-unified-ai-layer/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "cortex", "estimated_seconds": 600,
        },
    ],
    "m-dic-01-grounded-citations": [
        {
            "step_num": 1,
            "title": "Parse and validate [source:] citations",
            "step_type": "coding",
            "content_path": "tier2/m-dic-01-grounded-citations/steps/step1_dic.md",
            "starter_code_path": "tier2/m-dic-01-grounded-citations/steps/step1_starter.py",
            "test_code_path": "tier2/m-dic-01-grounded-citations/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "dic", "estimated_seconds": 600,
        },
    ],
    "m-graphrag-01-kg-traversal": [
        {
            "step_num": 1,
            "title": "Traverse kg_edges for GraphRAG retrieval",
            "step_type": "coding",
            "content_path": "tier2/m-graphrag-01-kg-traversal/steps/step1_graphrag.md",
            "starter_code_path": "tier2/m-graphrag-01-kg-traversal/steps/step1_starter.py",
            "test_code_path": "tier2/m-graphrag-01-kg-traversal/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "graphrag", "estimated_seconds": 600,
        },
    ],
    "m-iqe-01-collections-adapters": [
        {
            "step_num": 1,
            "title": "Register a collection and dispatch a query",
            "step_type": "coding",
            "content_path": "tier2/m-iqe-01-collections-adapters/steps/step1_iqe.md",
            "starter_code_path": "tier2/m-iqe-01-collections-adapters/steps/step1_starter.py",
            "test_code_path": "tier2/m-iqe-01-collections-adapters/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "iqe", "estimated_seconds": 600,
        },
    ],
    "m-kanban-01-governed-pipeline": [
        {
            "step_num": 1,
            "title": "Enforce the task lifecycle and its gates",
            "step_type": "coding",
            "content_path": "tier2/m-kanban-01-governed-pipeline/steps/step1_kanban.md",
            "starter_code_path": "tier2/m-kanban-01-governed-pipeline/steps/step1_starter.py",
            "test_code_path": "tier2/m-kanban-01-governed-pipeline/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "kanban", "estimated_seconds": 600,
        },
    ],
    # ── penta-aca-05 missions (batch 2) ───────────────────────────────────────
    # Single-step coding missions. Step files live under
    # content/tier2/<slug>/steps/step1_{starter,test}.py + step1_<name>.md.
    "m-foundry-01-capability-pipeline": [
        {
            "step_num": 1,
            "title": "Novelty gate, CoD go/no-go, and task-graph seed",
            "step_type": "coding",
            "content_path": "tier2/m-foundry-01-capability-pipeline/steps/step1_foundry.md",
            "starter_code_path": "tier2/m-foundry-01-capability-pipeline/steps/step1_starter.py",
            "test_code_path": "tier2/m-foundry-01-capability-pipeline/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "foundry", "estimated_seconds": 600,
        },
    ],
    "m-strategos-01-signal-wargaming": [
        {
            "step_num": 1,
            "title": "Score signals, prioritize, and wargame",
            "step_type": "coding",
            "content_path": "tier2/m-strategos-01-signal-wargaming/steps/step1_strategos.md",
            "starter_code_path": "tier2/m-strategos-01-signal-wargaming/steps/step1_starter.py",
            "test_code_path": "tier2/m-strategos-01-signal-wargaming/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "strategos", "estimated_seconds": 600,
        },
    ],
    "m-zig-01-zero-trust-maturity": [
        {
            "step_num": 1,
            "title": "Score ZIG pillars and roll up maturity",
            "step_type": "coding",
            "content_path": "tier2/m-zig-01-zero-trust-maturity/steps/step1_zig.md",
            "starter_code_path": "tier2/m-zig-01-zero-trust-maturity/steps/step1_starter.py",
            "test_code_path": "tier2/m-zig-01-zero-trust-maturity/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "zig", "estimated_seconds": 600,
        },
    ],
    "m-trust-01-citation-grounding": [
        {
            "step_num": 1,
            "title": "Attribution, confidence, provenance, fail-closed egress",
            "step_type": "coding",
            "content_path": "tier2/m-trust-01-citation-grounding/steps/step1_trust.md",
            "starter_code_path": "tier2/m-trust-01-citation-grounding/steps/step1_starter.py",
            "test_code_path": "tier2/m-trust-01-citation-grounding/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "trust", "estimated_seconds": 600,
        },
    ],
    "m-canvas-trio-01-design-canvases": [
        {
            "step_num": 1,
            "title": "Route a design need to DDC / ODC / NDC",
            "step_type": "coding",
            "content_path": "tier2/m-canvas-trio-01-design-canvases/steps/step1_canvas.md",
            "starter_code_path": "tier2/m-canvas-trio-01-design-canvases/steps/step1_starter.py",
            "test_code_path": "tier2/m-canvas-trio-01-design-canvases/steps/step1_test.py",
            "config_schema": {},
            "xp_partial": 150, "skill_tag": "canvas", "estimated_seconds": 600,
        },
    ],
}


def seed_mission_catalog() -> None:
    """Upsert all builtin missions and seed their steps on first creation."""
    # Fail loud on duplicate slugs: the ON CONFLICT(slug) upsert below would
    # otherwise SILENTLY collapse two distinct mission definitions sharing a slug
    # (the m-analyst-05-capstone bug fixed in penta-fix-02), quietly dropping one
    # mission from the catalog. A duplicate is a content-authoring error.
    _slugs = [m["slug"] for m in BUILTIN_MISSIONS]
    _dupes = sorted({s for s in _slugs if _slugs.count(s) > 1})
    if _dupes:
        raise ValueError(
            f"Duplicate mission slug(s) in BUILTIN_MISSIONS: {_dupes} — "
            "each slug must be unique (ON CONFLICT(slug) upsert would collapse them)."
        )
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        # Fast path: skip if catalog is already fully seeded
        # Hand-written catalog plus any mission whose content is on disk but
        # which no catalog entry covers (fga-wire-07).
        discovered = discover_steps()
        catalog = all_missions(discovered)

        # Same collapse risk as the BUILTIN_MISSIONS check above, now that the
        # catalog has a second source. discover_missions() excludes catalogued
        # slugs, so this should be unreachable — assert it rather than trust it,
        # because the failure mode is a mission silently vanishing.
        _all = [m["slug"] for m in catalog]
        _all_dupes = sorted({s for s in _all if _all.count(s) > 1})
        if _all_dupes:
            raise ValueError(
                f"Duplicate mission slug(s) across the combined catalog: {_all_dupes} — "
                "a discovered mission collided with a hand-written one."
            )

        # Attach newly-discovered code assets to steps that are ALREADY seeded.
        # This has to run BEFORE the fast-path return below, because that return
        # fires on exactly the databases this pass exists to repair. aca-hon-05
        # first placed the reconcile inside _seed_steps, which the fast path skips
        # entirely: on the live database (124 missions >= catalog size) it never
        # executed, and Tier 1 stayed ungradeable after a restart. Cheap and
        # idempotent — it only writes to rows whose asset paths are still empty.
        reconcile_all_step_assets(conn, discovered)
        # aca-hon-02: refresh the catalogue's user-visible fields on an already-seeded
        # database. The ON CONFLICT upsert below ALREADY sets title/tagline correctly
        # — it just sits after the fast-path return, so on any seeded database it
        # never ran and 34 derived missions kept the mechanical titles they were first
        # written with ('Chromadb Rag', 'Ciso Capstone'). This is the same
        # fix-is-inert-on-existing-data trap as the asset reconcile and the
        # mission_type reconcile; making the catalogue self-correcting removes the
        # whole class rather than adding a third bespoke pass.
        retire_superseded_missions(conn, discovered)
        # aca-hon-04: correct stored mission_type against the steps that actually
        # exist. Must run AFTER the asset reconcile, because that pass promotes steps
        # to 'coding', and BEFORE the fast-path return below for the same reason it
        # applies to assets — the rows needing correction are already seeded.
        reconcile_mission_types(conn)

        # NOTE: no fast-path return here any more. It used to skip the whole seed once
        # the mission count matched, which meant the ON CONFLICT upsert below — the
        # thing that keeps title/tagline/xp_reward in step with the catalogue — never
        # ran on a seeded database. Three separate defects traced back to it
        # (unreachable asset reconcile, stale mission_type, stale derived titles).
        # The upsert is one executemany over ~124 rows on start-up; correctness is
        # worth more than skipping it.

        # Batch upsert all missions in one executemany + single commit (avoids N individual commits)
        rows = [
            (
                m["slug"], m["title"], m.get("tagline", ""),
                m.get("tier", 1), m.get("topic", ""), m.get("role_filter", "all"),
                m.get("mission_type", "coding"), m.get("xp_reward", 200),
                json.dumps(m.get("prereqs", [])), m.get("order_idx", 0),
                m.get("difficulty", "intermediate"), m.get("estimated_minutes", 30),
                m.get("source_credit", ""),
                # aca-trn-03: a declared objective wins; otherwise read it out of the
                # mission's own first step. NULL, never "", so "no objective authored"
                # is one state in the database rather than two.
                (m.get("learning_objective")
                 or objective_for_mission(discovered.get(m["slug"]))) or None,
            )
            for m in catalog
        ]
        conn.executemany(
            """INSERT INTO fa_missions
               (slug,title,tagline,tier,topic,role_filter,mission_type,xp_reward,
                prereq_slugs_json,order_idx,difficulty,estimated_minutes,source_credit,
                learning_objective)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(slug) DO UPDATE SET
                 title=excluded.title, tagline=excluded.tagline,
                 xp_reward=excluded.xp_reward, order_idx=excluded.order_idx,
                 learning_objective=COALESCE(excluded.learning_objective,
                                             fa_missions.learning_objective)""",
            rows,
        )
        conn.commit()

        # Fetch all IDs in one query instead of N per-mission SELECTs
        slugs = [m["slug"] for m in catalog]
        slug_to_id = {}
        for row in conn.execute(
            "SELECT id, slug FROM fa_missions WHERE slug IN ({})".format(
                ",".join(["%s"] * len(slugs))
            ),
            slugs,
        ).fetchall():
            slug_to_id[row["slug"]] = row["id"]

        # Seed steps for missions that have none. Source of truth is
        # BUILTIN_STEPS where an entry exists (curated titles, xp weights,
        # starter/test code paths), otherwise the authored markdown discovered
        # on disk. Before fga-wire-01 only the dict was consulted, so 53 of 89
        # missions rendered "Content is being authored" — 43 of them with their
        # content already committed.
        seeded_from_disk = 0
        for m in catalog:
            mission_id = slug_to_id.get(m["slug"])
            if not mission_id:
                continue
            existing = conn.execute(
                "SELECT COUNT(*) FROM fa_mission_steps WHERE mission_id=%s", (mission_id,)
            ).fetchone()[0]
            if existing:
                continue
            steps = steps_for(m["slug"], discovered)
            if not steps:
                continue
            _seed_steps(conn, mission_id, m["slug"], steps=steps)
            if m["slug"] not in BUILTIN_STEPS:
                seeded_from_disk += 1
        if seeded_from_disk:
            _log.info(
                "FORGE Academy: seeded %d mission(s) from discovered content", seeded_from_disk
            )
        conn.commit()

        # aca-trn-01: item banks last, because they resolve (mission_slug, step_num)
        # to a step id and therefore need the step rows above to exist. Idempotent
        # and re-run every start-up, so editing the authored YAML corrects the
        # database — the same self-correcting property the catalogue reconciles
        # above were added for (aca-hon-02/04/05), applied to assessment content.
        try:
            seed_item_banks(conn)
        except Exception:
            # A bad bank costs its steps their assessment, not the whole catalogue.
            _log.warning("item bank seed failed", exc_info=True)
        _log.info("FORGE Academy: seeded/updated %d missions (%d derived from content)",
                  len(catalog), len(catalog) - len(BUILTIN_MISSIONS))
    except Exception as e:
        _log.warning("Mission catalog seed failed: %s", e)


# ---------------------------------------------------------------------------
# Filesystem discovery of authored step content (fga-wire-01)
# ---------------------------------------------------------------------------
# Steps used to be seeded ONLY from the hand-maintained BUILTIN_STEPS dict, so a
# mission absent from that dict got zero step rows permanently and the UI showed
# "Content is being authored" even with its markdown sitting on disk. 53 of 89
# missions had no steps; 43 of those had authored content. Adding 43 more dict
# entries would have cleared the symptom and left the mechanism that produced it,
# so discovery replaces the dict as the source of truth for *which* steps exist.
#
# Discovery keys on the frontmatter, not the path. Content uses three different
# layouts (tier1/<slug>/steps/stepN_x.md, tier2/<family>/<slug>/steps/stepN_x.md,
# tier2/<slug>/step-N.md) but every one of the 212 files carries
# `ontology_id: icdev:mission:<slug>:step:<n>` and an H1 title, which makes the
# mission and step number unambiguous without guessing at directory conventions.

#: icdev:mission:<mission-slug>:step:<step-num>
_ONTOLOGY_STEP_RE = re.compile(
    r"icdev:mission:(?P<slug>[A-Za-z0-9._-]+):step:(?P<num>\d+)"
)

#: frontmatter step_class -> the fa_mission_steps.step_type vocabulary.
_STEP_CLASS_TO_TYPE = {
    "lesson": "watch",
    "assessment": "reflect",
    "reflect": "reflect",
    "configure": "configure",
    "lab": "coding",
    "coding": "coding",
    "verify": "configure",
    "design": "reflect",
}

_DEFAULT_STEP_TYPE = "watch"
_DEFAULT_XP = 50
_DEFAULT_SECONDS = 300


def _step_type_from_class(step_class: str) -> str:
    """Map `step_class: icdev:Lesson` to a step_type the UI understands."""
    tail = str(step_class or "").split(":")[-1].strip().lower()
    return _STEP_CLASS_TO_TYPE.get(tail, _DEFAULT_STEP_TYPE)


# Classification banners appear as the first markdown heading in some content files.
# They are markings, not titles (aca-hon-02): m11-multimodal's card tagline was the
# literal string "CUI // SP-CTI" because _title_from_body returned the first heading
# it found, that heading became the step title, and the step title became the
# mission tagline.
_CLASSIFICATION_TOKENS = ("CUI", "SP-CTI", "SECRET", "NOFORN", "FOUO", "TOP SECRET")


def _is_classification_heading(text: str) -> bool:
    """A heading that is only a classification marking, not a title."""
    stripped = (text or "").strip().strip("[]").strip()
    if not stripped:
        return False
    upper = stripped.upper()
    if not any(tok in upper for tok in _CLASSIFICATION_TOKENS):
        return False
    # A real title may mention CUI ("STIG markers, CUI headers, CI gates"), so only
    # reject headings that are essentially nothing but markings and separators.
    residue = upper
    for tok in _CLASSIFICATION_TOKENS:
        residue = residue.replace(tok, "")
    residue = residue.replace("TEMPLATE:", "")
    return not any(ch.isalnum() for ch in residue)


def _title_from_body(body: str, fallback: str) -> str:
    """First markdown H1 that is not a classification banner, else the fallback."""
    for line in (body or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            heading = line[2:].strip()
            if _is_classification_heading(heading):
                continue  # a marking, not a title — keep looking
            return heading[:200]
    return fallback


# Step types that make a mission "hands-on". aca-hon-04: mission_type was taken from
# the FIRST step alone (and simply declared for hand-written entries), so 34 missions
# advertised 'coding' with no coding step at all — every Tier-1 mission among them.
def _title_head(title: str | None) -> str:
    """The subject part of a title, before any subtitle separator.

    "Multimodal AI - Vision, Documents, Images" -> "multimodal ai". Used to spot a
    derived mission that covers the same subject as a catalogued one even though the
    full strings differ (aca-hon-03).
    """
    text = (title or "").strip()
    for sep in ("—", "–", " - ", ":", "|"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return " ".join(text.lower().split())


# ---------------------------------------------------------------------------
# Learning objectives (aca-trn-03)
# ---------------------------------------------------------------------------
# A mission card advertises XP, estimated minutes and difficulty — three costs —
# and never states what the learner will be able to DO afterwards. For training
# used in a compliance context the objective is the auditable unit: "this learner
# was trained on X" needs an X, and a tagline ("The difference between a chatbot
# and a weapon is the prompt.") is marketing copy, not one.
#
# This is an EXTRACTION pass, not an authoring one. Nothing here writes an
# objective an author did not: where the content states one it is surfaced, and
# where it does not the column stays NULL and both surfaces omit the line. A
# plausible-looking objective synthesised from a tagline would put un-authored
# text on the surface that most needs to be true — the aca-hon-* failure mode
# (mechanical slug titles, step-type badges that did not match the steps) applied
# to an auditable field. An absent objective is a visible content gap; an invented
# one is an invisible false record.
#
# Two sources, in priority order:
#   1. ``learning_objective:`` in the step's frontmatter — the explicit channel,
#      for an author stating it outright. Nothing carries it yet; it exists so
#      authoring one does not require touching this module.
#   2. The lead paragraph of an objective-bearing section in the mission's FIRST
#      step. No file uses a literal "## Learning Objective" heading today, but 29
#      missions open with "## What You'll Build" and 8 with "## Mission Brief",
#      and those paragraphs are already written as "you're building X that does Y".

#: Section headings whose lead paragraph states what the learner will produce.
#: Ordered: an explicit objective heading beats a build/brief section describing
#: the same thing more loosely.
_OBJECTIVE_HEADINGS = (
    "learning objective", "learning objectives",
    "objective", "objectives",
    "mission brief", "your mission",
    "what you'll learn", "what you will learn",
    "what you'll build", "what you will build",
    "what you'll do", "what you will do",
    # The most common objective-bearing heading in this catalogue by a wide margin
    # (48 first steps). Imperative rather than declarative — "Build a X that does
    # Y" instead of "you will be able to build a X" — but it is the author stating
    # the outcome, which is what an extraction pass is here to surface. Ranked last
    # so a mission carrying both a brief and a task list surfaces the brief.
    "your task", "your tasks",
)

#: Below this a match is a sentence fragment, not a statement of capability.
#: Better absent — and visibly so — than truncated into something unauditable.
_OBJECTIVE_MIN_CHARS = 40
#: A card line, not an essay. Longer text is cut back to a sentence boundary.
_OBJECTIVE_MAX_CHARS = 320

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS_RE = re.compile(r"(\*\*|__|`|~~)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _normalise_heading(text: str) -> str:
    """`## What You'll Build` body -> `what you'll build`, typographic quotes folded."""
    cleaned = (text or "").replace("’", "'").replace("‘", "'")
    cleaned = cleaned.strip().strip("#").strip().rstrip(":").strip()
    return " ".join(cleaned.lower().split())


def _flatten_markdown(text: str) -> str:
    """Markdown prose -> one clean line: links to their label, emphasis dropped."""
    flat = _MD_LINK_RE.sub(r"\1", text or "")
    flat = _MD_MARKS_RE.sub("", flat)
    # Single '*' / '_' only between word characters is emphasis; leave snake_case
    # identifiers alone, because they are usually the thing being taught.
    flat = re.sub(r"(?<!\w)[*_](?=\w)|(?<=\w)[*_](?!\w)", "", flat)
    return " ".join(flat.split()).strip()


def _trim_to_sentence(text: str) -> str:
    """Cut over-long prose back to a sentence boundary, else an ellipsis."""
    if len(text) <= _OBJECTIVE_MAX_CHARS:
        return text
    window = text[:_OBJECTIVE_MAX_CHARS]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut >= _OBJECTIVE_MIN_CHARS:
        return window[: cut + 1].strip()
    cut = window.rfind(" ")
    return (window[:cut] if cut > 0 else window).strip() + "…"


def _lead_paragraph(lines: list[str], start: int) -> str:
    """First prose paragraph after ``start``, skipping non-prose blocks.

    Stops at the next heading so a section with no prose of its own (straight into
    a code block or a list) yields nothing rather than borrowing the next
    section's text.
    """
    para: list[str] = []
    in_fence = False
    for line in lines[start + 1:]:
        stripped = line.strip()
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            if para:
                break
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            break
        if not stripped:
            if para:
                break
            continue
        # Lists, tables, quotes and images are structure, not a prose statement.
        if stripped[0] in "-*+>|!" or re.match(r"^\d+[.)]\s", stripped):
            if para:
                break
            continue
        para.append(stripped)
    return _flatten_markdown(" ".join(para))


def extract_learning_objective(raw: str) -> str:
    """The learning objective stated by one step's markdown, or "" if none is.

    ``raw`` is the whole file including frontmatter. Returns "" rather than a
    guess: the caller stores NULL and the UI omits the line.
    """
    fm, body = _parse_frontmatter(raw or "")
    declared = _flatten_markdown(str(fm.get("learning_objective") or ""))
    if declared:
        return _trim_to_sentence(declared)

    lines = (body or "").splitlines()
    headings: dict[str, int] = {}
    in_fence = False
    for idx, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip().startswith("#"):
            continue
        name = _normalise_heading(line)
        # First occurrence wins; a heading repeated later is a different section.
        headings.setdefault(name, idx)

    for wanted in _OBJECTIVE_HEADINGS:
        idx = headings.get(wanted)
        if idx is None:
            continue
        para = _lead_paragraph(lines, idx)
        # A trailing colon means the paragraph introduces a list or code block.
        # The sentence still reads as an objective once the colon is dropped.
        para = para.rstrip(":").strip()
        if len(para) < _OBJECTIVE_MIN_CHARS:
            continue
        # A paragraph carrying a question is the exercise being posed, not the
        # capability being claimed — "identify: what listen_topics does it
        # subscribe to?" is what the learner does DURING the mission. Surfacing
        # it as the objective would put a quiz item on the field an audit reads,
        # so the mission falls through to NULL and states none. This bites the
        # "your task" sections hardest, which is where it is needed.
        if "?" in para:
            continue
        return _trim_to_sentence(para)
    return ""


def objective_for_mission(steps: list | None) -> str:
    """A mission's objective: the one its first step states, else "".

    Later steps state per-step tasks, not the mission's outcome, so only step 1 is
    consulted. Steps are expected in ``step_num`` order (``discover_steps`` sorts).
    """
    for step in steps or []:
        objective = (step.get("learning_objective") or "").strip()
        if objective:
            return objective
        break  # only the first step speaks for the mission
    return ""


def mission_type_from_steps(steps: list | None) -> str:
    """Derive a mission's advertised type from its actual step composition.

    Any coding step makes the mission 'coding' — that is the thing a learner is
    promised and the thing that can be graded. Otherwise the most common step type
    wins, so a mission is described by what it mostly is.
    """
    types = [
        (s.get("step_type") or "").strip().lower()
        for s in (steps or [])
        if (s.get("step_type") or "").strip()
    ]
    if not types:
        return "watch"
    if "coding" in types:
        return "coding"
    counts: dict[str, int] = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    # Ties resolve by first appearance, which keeps the result stable.
    return max(types, key=lambda t: (counts[t], -types.index(t)))


def retire_superseded_missions(conn, discovered: dict | None = None) -> int:
    """Deactivate derived missions discovery no longer produces. Returns count.

    aca-hon-03: m11-multimodal was derived before the duplicate-subject check existed
    and duplicates the catalogued m-t1-11-multimodal. Excluding it from derivation
    stops it being RE-created but does nothing about the row already in the database,
    which stayed active and kept rendering as a second Tier-1 card for one subject.

    Scope is deliberately narrow: only rows whose source_credit marks them derived,
    and only when discovery no longer yields that slug. A hand-written catalogue entry
    is never retired automatically — that is a content decision (see migration 314 for
    m-leader-02-roi). Sets is_active=0 rather than deleting, so any learner progress
    and the audit trail survive.
    """
    if discovered is None:
        discovered = discover_steps()
    try:
        still_derived = {m["slug"] for m in discover_missions(discovered)}
        rows = conn.execute(
            "SELECT id, slug FROM fa_missions "
            "WHERE is_active=1 AND source_credit LIKE %s", ("%derived%",),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — start-up path; must not break boot
        _log.warning("retire_superseded_missions could not read the catalogue: %s", exc)
        return 0

    retired = 0
    for row in rows:
        mid = row["id"] if hasattr(row, "keys") else row[0]
        slug = row["slug"] if hasattr(row, "keys") else row[1]
        if slug in still_derived:
            continue
        try:
            conn.execute("UPDATE fa_missions SET is_active=0 WHERE id=%s", (mid,))
            retired += 1
            _log.info(
                "FORGE Academy: retired derived mission %s — discovery no longer "
                "produces it (superseded or duplicate)", slug,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("could not retire %s: %s", slug, exc)
    if retired:
        try:
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            _log.warning("retire_superseded_missions commit failed: %s", exc)
    return retired


def reconcile_mission_types(conn) -> int:
    """Correct stored fa_missions.mission_type against actual steps. Returns changes.

    Discovery alone cannot fix this: the offending rows are already seeded, and
    _seed_steps uses INSERT OR IGNORE. That is the same trap aca-hon-05 fell into
    twice — a fix on the insert path is inert against an existing database — so this
    is a reconcile pass, and it commits.
    """
    try:
        rows = conn.execute(
            "SELECT m.id, m.slug, m.mission_type FROM fa_missions m WHERE m.is_active=1"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — runs at start-up; must not break boot
        _log.warning("mission_type reconcile could not read missions: %s", exc)
        return 0

    changed = 0
    for row in rows:
        mid = row["id"] if hasattr(row, "keys") else row[0]
        slug = row["slug"] if hasattr(row, "keys") else row[1]
        stored = (row["mission_type"] if hasattr(row, "keys") else row[2]) or ""
        try:
            steps = [
                {"step_type": (r[0] if not hasattr(r, "keys") else r["step_type"])}
                for r in conn.execute(
                    "SELECT step_type FROM fa_mission_steps WHERE mission_id=%s", (mid,)
                ).fetchall()
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning("mission_type reconcile: steps unavailable for %s: %s", slug, exc)
            continue
        if not steps:
            continue  # a Coming Soon mission keeps its declared type
        derived = mission_type_from_steps(steps)
        if derived != stored:
            conn.execute(
                "UPDATE fa_missions SET mission_type=%s WHERE id=%s", (derived, mid)
            )
            changed += 1
            _log.info(
                "FORGE Academy: mission_type %s: %r -> %r (from %d steps)",
                slug, stored, derived, len(steps),
            )
    if changed:
        try:
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            _log.warning("mission_type reconcile commit failed: %s", exc)
    return changed


# Step types where a hint is meaningful. aca-hyg-04: hint_allowed was 1 on all 212
# steps, including watch steps (there is nothing to hint about reading a page) and
# reflect steps (where a hint IS the answer to the multiple-choice question). The
# column was written and then ignored by both the runner and the hint route.
_HINTABLE_STEP_TYPES = frozenset({"coding", "design", "configure", "deploy", "verify"})


def hint_allowed_for(step_type: str | None) -> bool:
    """Whether asking the coach for a hint makes sense for this step type."""
    return (step_type or "").strip().lower() in _HINTABLE_STEP_TYPES


def _code_assets_for(md_path: Path, step_num: int) -> tuple[str, str]:
    """Find the authored ``stepN_starter.py`` / ``stepN_test.py`` beside a step.

    aca-hon-05: discovery globbed ``*.md`` only, so the Python assets authored next
    to the prose were never attached — 124 files across 60 mission directories,
    including all ten Tier-1 missions. Every m01 step was step_type='watch' with
    empty asset paths while step1_starter.py and step1_test.py sat unused in the
    same folder. That is why the onboarding path advertised CODING and had nothing
    to grade.

    Returns ``(starter_rel, test_rel)``, each '' when absent. Paths are relative to
    CONTENT_ROOT, matching every other path in a step record.
    """
    out = []
    for suffix in ("starter", "test"):
        candidate = md_path.parent / f"step{step_num}_{suffix}.py"
        out.append(
            candidate.relative_to(CONTENT_ROOT).as_posix()
            if candidate.is_file() else ""
        )
    return out[0], out[1]


def discover_steps() -> dict:
    """Scan CONTENT_ROOT and return ``{mission_slug: [step-record, ...]}``.

    Records match the BUILTIN_STEPS shape so both sources feed one writer. Files
    whose frontmatter carries no parsable ontology_id are skipped rather than
    guessed at — a step attached to the wrong mission is worse than one that is
    visibly absent.

    Authored ``stepN_starter.py`` / ``stepN_test.py`` siblings are attached where
    they exist (see ``_code_assets_for``). A sibling TEST promotes the step to
    'coding', because the test is what makes it gradeable; a starter on its own is
    attached for the editor but does not change the declared step type. Promoting
    without a test would manufacture a 'coding' step that aca-int-02 can never
    credit, which is worse than leaving it as the lesson its frontmatter declares.
    """
    found: dict = {}
    if not CONTENT_ROOT.is_dir():
        return found

    for path in sorted(CONTENT_ROOT.rglob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = _parse_frontmatter(raw)
        match = _ONTOLOGY_STEP_RE.search(str(fm.get("ontology_id") or ""))
        if not match:
            continue
        rel = path.relative_to(CONTENT_ROOT).as_posix()
        step_num = int(match.group("num"))
        starter_rel, test_rel = _code_assets_for(path, step_num)
        step_type = _step_type_from_class(fm.get("step_class"))
        if test_rel:
            step_type = "coding"
        found.setdefault(match.group("slug"), []).append({
            "step_num": step_num,
            "title": _title_from_body(body, path.stem.replace("_", " ").title()),
            "step_type": step_type,
            "content_path": rel,
            "starter_code_path": starter_rel,
            "test_code_path": test_rel,
            "config_schema": {},
            "xp_partial": _DEFAULT_XP,
            "skill_tag": str(fm.get("skill_tag") or ""),
            "estimated_seconds": _DEFAULT_SECONDS,
            # aca-trn-03: carried on every step, read from step 1 only
            # (objective_for_mission). "" where the content states none.
            "learning_objective": extract_learning_objective(raw),
        })

    for slug, steps in found.items():
        steps.sort(key=lambda s: s["step_num"])
        seen: set = set()
        deduped = []
        for st in steps:
            if st["step_num"] in seen:
                _log.warning(
                    "FORGE Academy: duplicate step %d for mission %s (%s) — keeping the first",
                    st["step_num"], slug, st["content_path"],
                )
                continue
            seen.add(st["step_num"])
            deduped.append(st)
        found[slug] = deduped
    return found


# ---------------------------------------------------------------------------
# Mission discovery — authored content with no catalog entry (fga-wire-07)
# ---------------------------------------------------------------------------
# Step discovery above fixes missions the catalog KNOWS about. A second,
# previously unrecorded gap sits one level up: 37 mission directories carry
# authored steps but appear in no catalog at all, so no amount of step discovery
# reaches them. They are whole track continuations — tier3/m-t3-02..07, m-pm-02..06,
# m-issm-03..06 — where the content was written and the catalog stopped short.
#
# Mission metadata (tagline, xp, difficulty) is NOT on disk; only the step files
# are. Rather than invent a curated-looking catalog entry, these are derived
# deterministically from the slug and path and marked in source_credit so a
# reviewer can tell a derived mission from a hand-curated one at a glance.

_MISSION_SLUG_RE = re.compile(r"^m-(?P<family>[a-z0-9]+)-(?:[a-z0-9]+-)?(?P<num>\d+)-")

#: Slug family -> the role_filter the browse UI filters on.
_FAMILY_ROLE = {
    "ciso": "ciso", "issm": "issm", "isso": "isso", "pm": "pm",
    "secops": "secops", "swe": "swe", "devops": "devops",
    "dataops": "dataops", "sre": "sre", "netops": "netops",
}

_DERIVED_CREDIT = "derived from authored content (fga-wire-07)"


def _tier_from_path(content_path: str) -> int:
    head = (content_path or "").split("/", 1)[0]
    if head.startswith("tier") and head[4:].isdigit():
        return int(head[4:])
    return 2


def _humanise_slug(slug: str) -> str:
    """`m-t3-02-write-your-first-tool` -> `Write Your First Tool`."""
    parts = [p for p in slug.split("-") if not p.isdigit()]
    if parts and parts[0] == "m":
        parts = parts[1:]
    if parts and (parts[0] in _FAMILY_ROLE or parts[0].startswith("t")):
        parts = parts[1:] or parts
    return " ".join(p.capitalize() for p in parts) or slug


def discover_missions(discovered: dict | None = None) -> list:
    """Catalog entries for authored missions absent from BUILTIN_MISSIONS.

    Excludes a slug whose family+number already has a catalogued mission: that
    is superseded or renamed content, and adding it would put two missions at the
    same position in a track. Those are reported for a human instead.
    """
    if discovered is None:
        discovered = discover_steps()
    catalogued = {m["slug"] for m in BUILTIN_MISSIONS}

    def _track_key(slug: str):
        m = _MISSION_SLUG_RE.match(slug)
        return (m.group("family"), m.group("num")) if m else None

    taken = {k for k in (_track_key(s) for s in catalogued) if k}
    # aca-hon-03: the track-slot check keys on family+number, so m11-multimodal and
    # the catalogued m-t1-11-multimodal look unrelated and BOTH ended up in the
    # catalogue as adjacent Tier-1 cards about the same subject. Compare the derived
    # human title against catalogued titles as well.
    # Compare the title HEAD — the part before a subtitle separator. m11-multimodal
    # derives "Multimodal AI - Vision, Documents, Images" against the catalogued
    # "Multimodal AI": the same subject, so an exact match would miss it.
    catalogued_titles = {_title_head(m.get("title")) for m in BUILTIN_MISSIONS}
    catalogued_titles.discard("")

    out: list = []
    for slug in sorted(set(discovered) - catalogued):
        steps = discovered.get(slug) or []
        if not steps:
            continue
        key = _track_key(slug)
        if key and key in taken:
            _log.warning(
                "FORGE Academy: %s has authored content but its track position is "
                "already held by a catalogued mission — not auto-catalogued; "
                "resolve by hand (superseded or renamed?)", slug,
            )
            continue
        first = steps[0]
        match = _MISSION_SLUG_RE.match(slug)
        family = match.group("family") if match else ""

        # aca-hon-02: title and tagline were INVERTED. title was
        # _humanise_slug(slug) — a title-cased slug fragment like 'Ciso Capstone' or
        # 'Chromadb Rag' — while the authored human title sat in the tagline. 35 of
        # 124 missions showed the mechanical string as their card title.
        human_title = (first.get("title") or "").strip()
        title = human_title or _humanise_slug(slug)
        # The tagline was only ever a copy of the step title; leaving it identical
        # would just print the title twice on the card.
        tagline = "" if title == human_title else human_title

        if _title_head(title) in catalogued_titles:
            _log.warning(
                "FORGE Academy: %s derives the title %r, which a catalogued mission "
                "already uses — not auto-catalogued to avoid two cards for one "
                "subject; resolve by hand (aca-hon-03)", slug, title,
            )
            continue

        out.append({
            "slug": slug,
            "title": title,
            "tagline": tagline,
            "tier": _tier_from_path(first.get("content_path", "")),
            "topic": family or "general",
            "role_filter": _FAMILY_ROLE.get(family, "all"),
            # aca-hon-04: was first.get("step_type"), so a watch intro hid a coding
            # exercise behind a 'watch' label (and vice versa).
            "mission_type": mission_type_from_steps(steps),
            "xp_reward": 100,
            "order_idx": int(match.group("num")) if match else 99,
            "difficulty": "intermediate",
            "estimated_minutes": max(5, (len(steps) * _DEFAULT_SECONDS) // 60),
            "prereqs": [],
            "source_credit": _DERIVED_CREDIT,
            "learning_objective": objective_for_mission(steps),
        })
    return out


def all_missions(discovered: dict | None = None) -> list:
    """BUILTIN_MISSIONS plus any mission discovered from authored content."""
    return list(BUILTIN_MISSIONS) + discover_missions(discovered)


def steps_for(mission_slug: str, discovered: dict | None = None) -> list:
    """Steps for one mission: the hand-authored dict wins, else discovery.

    BUILTIN_STEPS keeps priority because its entries carry curated titles,
    xp weights and starter/test code paths that the markdown does not express.
    """
    if mission_slug in BUILTIN_STEPS:
        return BUILTIN_STEPS[mission_slug]
    if discovered is None:
        discovered = discover_steps()
    return discovered.get(mission_slug, [])


def _seed_steps(conn, mission_id: int, mission_slug: str, steps: list | None = None) -> None:
    """Insert step records for a mission.

    ``steps`` defaults to the BUILTIN_STEPS entry so existing callers are
    unchanged; the seeder passes discovered steps for missions the dict does
    not cover.
    """
    if steps is None:
        steps = BUILTIN_STEPS.get(mission_slug, [])
    for step in steps:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO fa_mission_steps
                   (mission_id, step_num, title, step_type, content_path,
                    starter_code_path, test_code_path,
                    config_schema_json, xp_partial, skill_tag, hint_allowed, estimated_seconds)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    mission_id,
                    step["step_num"],
                    step["title"],
                    step.get("step_type", "configure"),
                    step.get("content_path", ""),
                    step.get("starter_code_path", ""),
                    step.get("test_code_path", ""),
                    json.dumps(step.get("config_schema", {})),
                    step.get("xp_partial", 50),
                    step.get("skill_tag", ""),
                    # aca-hyg-04: was a hardcoded 1 for every step, so watch and
                    # reflect steps advertised a hint that makes no sense there.
                    1 if hint_allowed_for(step.get("step_type", "configure")) else 0,
                    step.get("estimated_seconds", 300),
                ),
            )
        except Exception as exc:
            _log.debug("Step seed %s step %s: %s", mission_slug, step.get("step_num"), exc)

    _reconcile_step_assets(conn, mission_id, mission_slug, steps)


def reconcile_all_step_assets(conn, discovered: dict | None = None) -> int:
    """Attach discovered code assets across the whole catalog. Returns rows touched.

    Runs independently of the per-mission insert path so it reaches databases that
    are already fully seeded — see the call site in ``seed_mission_catalog``, which
    must invoke this before its already-seeded fast-path return.

    Never raises: this executes during dashboard start-up, so a cold or partially
    migrated database must not break boot.
    """
    if discovered is None:
        discovered = discover_steps()
    touched = 0
    for slug, steps in (discovered or {}).items():
        try:
            row = conn.execute(
                "SELECT id FROM fa_missions WHERE slug=%s", (slug,)
            ).fetchone()
        except Exception as exc:
            _log.warning("FORGE Academy: asset reconcile could not read missions: %s", exc)
            return touched
        if not row:
            continue  # authored content with no catalog entry — nothing to attach to
        mission_id = row["id"] if hasattr(row, "keys") else row[0]
        try:
            _reconcile_step_assets(conn, mission_id, slug, steps)
            touched += 1
        except Exception as exc:
            _log.warning("FORGE Academy: asset reconcile failed for %s: %s", slug, exc)

    # Commit once for the whole pass. _reconcile_step_assets issues UPDATEs and does
    # NOT commit: when it was reached only from _seed_steps a later commit in the
    # seeding flow happened to cover it, but this pass runs before the seeder's
    # already-seeded fast-path return, so nothing else ever committed and every
    # UPDATE was discarded. Verified against the live database — the log said
    # "attached code assets to m01-llm-fundamentals step 1" while the row never
    # changed.
    #
    # This is invisible to a test that writes and reads back on ONE in-memory
    # connection, because uncommitted writes are visible inside their own
    # transaction. tests/test_aca_reconcile_commit.py uses a file-backed database
    # and a SECOND connection so a missing commit fails.
    if touched:
        try:
            conn.commit()
        except Exception as exc:
            _log.warning("FORGE Academy: asset reconcile commit failed: %s", exc)
    return touched


def _reconcile_step_assets(conn, mission_id: int, mission_slug: str, steps: list) -> None:
    """Attach newly-discovered code assets to steps that were already seeded.

    ``_seed_steps`` uses INSERT OR IGNORE, so a step row written before
    ``discover_steps`` learned about ``stepN_starter.py``/``stepN_test.py``
    (aca-hon-05) keeps its original values forever. Every one of the 212 steps in
    production was seeded that way — all with step_type='watch' and empty asset
    paths — so without this pass the discovery fix would be inert against any
    existing database and Tier 1 would stay ungradeable.

    Deliberately conservative, because seeding runs on every dashboard start:

      * only fills an asset path that is currently empty — never overwrites a
        path already recorded (BUILTIN_STEPS entries stay authoritative);
      * only promotes step_type to 'coding', and only when a test is attached, so
        it can never demote an authored type or create an ungradeable coding step
        (which aca-int-02 would refuse to credit anyway);
      * idempotent — a second run matches nothing and writes nothing.
    """
    for step in steps:
        test_path = step.get("test_code_path") or ""
        starter_path = step.get("starter_code_path") or ""
        if not (test_path or starter_path):
            continue
        try:
            row = conn.execute(
                "SELECT id, step_type, starter_code_path, test_code_path "
                "FROM fa_mission_steps WHERE mission_id=%s AND step_num=%s",
                (mission_id, step["step_num"]),
            ).fetchone()
            if not row:
                continue
            stored = dict(row) if hasattr(row, "keys") else {
                "id": row[0], "step_type": row[1],
                "starter_code_path": row[2], "test_code_path": row[3],
            }
            updates: dict = {}
            if test_path and not (stored.get("test_code_path") or ""):
                updates["test_code_path"] = test_path
            if starter_path and not (stored.get("starter_code_path") or ""):
                updates["starter_code_path"] = starter_path
            # Promote only once a test is actually present on the row.
            will_have_test = updates.get("test_code_path") or stored.get("test_code_path")
            if will_have_test and stored.get("step_type") != "coding":
                updates["step_type"] = "coding"
            if not updates:
                continue
            assignments = ", ".join(f"{col}=?" for col in updates)
            conn.execute(
                f"UPDATE fa_mission_steps SET {assignments} WHERE id=%s",  # noqa: S608
                (*updates.values(), stored["id"]),
            )
            _log.info(
                "FORGE Academy: attached code assets to %s step %s (%s)",
                mission_slug, step["step_num"], ", ".join(sorted(updates)),
            )
        except Exception as exc:
            _log.warning(
                "Step asset reconcile %s step %s: %s",
                mission_slug, step.get("step_num"), exc,
            )


# ---------------------------------------------------------------------------
# Load step content from files
# ---------------------------------------------------------------------------

# Allowlist for rendered-markdown HTML. Covers exactly what the markdown
# extensions below can emit (headings, lists, tables, fenced code, links,
# emphasis) — nothing that can execute script. Anything outside this set is
# escaped (rendered inert), so untrusted LLM coach output cannot inject markup.
_MD_ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "pre", "code", "blockquote", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "strong", "em", "b", "i", "u", "del", "ins", "sub", "sup", "kbd", "abbr",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "col", "colgroup",
})
_MD_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "span": ["class"],
    "th": ["align"],
    "td": ["align"],
    "abbr": ["title"],
}
_MD_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _sanitize_html(rendered: str, raw_fallback: str = "") -> str:
    """Allowlist-sanitize already-rendered HTML.

    Uses ``bleach`` when available (``strip=False`` so disallowed tags such as
    ``<script>`` are escaped to inert text rather than dropped). When bleach is
    unavailable (air-gap minimal install) we fail SAFE: escape the raw source so
    no markup survives at all.
    """
    try:
        import bleach
    except Exception:
        import html
        return f"<pre>{html.escape(raw_fallback or rendered)}</pre>"
    return bleach.clean(
        rendered,
        tags=set(_MD_ALLOWED_TAGS),
        attributes=_MD_ALLOWED_ATTRS,
        protocols=_MD_ALLOWED_PROTOCOLS,
        strip=False,
    )


def _md_to_html(text: str) -> str:
    """Convert markdown text to sanitized HTML.

    The output is allowlist-sanitized (see ``_sanitize_html``) so this is safe
    for UNTRUSTED input — notably LLM coach hints surfaced at
    ``/api/academy/coach/hint`` — not only first-party lesson content.
    """
    text = text or ""
    try:
        import markdown as md_lib
        rendered = md_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    except Exception:
        import html
        return f"<pre>{html.escape(text)}</pre>"
    return _sanitize_html(rendered, raw_fallback=text)


# ---------------------------------------------------------------------------
# Item banks (aca-trn-01)
# ---------------------------------------------------------------------------
# Banks are authored in YAML next to the lesson they assess, under
# content/item_banks/<mission-slug>.yaml, and seeded into fa_assessment_items.
#
# Authored as content rather than as rows in a migration for the same reason the
# steps themselves are: a migration is applied once, so correcting a badly-worded
# question would need a second migration, and the bank would drift out of step with
# the lesson it belongs to. Seeding is idempotent and re-runs on every start-up, so
# editing the YAML corrects the database.

ITEM_BANK_ROOT = CONTENT_ROOT / "item_banks"


def load_item_banks() -> dict:
    """Parse every authored item bank. Returns ``{mission_slug: {step_num: [item]}}``.

    Malformed banks are skipped with a warning rather than raising: a bad bank must
    cost its own step its assessment, not take start-up down with it. The step then
    classifies as `acknowledged`, which grades honestly (``assessed: False``) instead
    of pretending to have assessed anything.
    """
    if not ITEM_BANK_ROOT.is_dir():
        return {}
    try:
        import yaml
    except ImportError:
        _log.warning("PyYAML unavailable — item banks not seeded")
        return {}

    banks: dict[str, dict[int, list]] = {}
    for path in sorted(ITEM_BANK_ROOT.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            _log.warning("item bank %s is not parseable", path.name, exc_info=True)
            continue
        slug = str(doc.get("mission") or path.stem).strip()
        if not slug:
            continue
        for entry in doc.get("steps") or []:
            try:
                step_num = int(entry.get("step"))
            except (TypeError, ValueError):
                _log.warning("item bank %s has a step with no usable number", path.name)
                continue
            items = []
            for raw in entry.get("items") or []:
                options = [str(o) for o in (raw.get("options") or [])]
                items.append({
                    "item_key": str(raw.get("key") or "").strip(),
                    "prompt": str(raw.get("prompt") or "").strip(),
                    "options": options,
                    "correct_index": raw.get("correct"),
                    "explanation": str(raw.get("explanation") or "").strip(),
                    "difficulty": str(raw.get("difficulty") or "core").strip(),
                })
            if items:
                banks.setdefault(slug, {})[step_num] = items
    return banks


def seed_item_banks(conn) -> int:
    """Upsert authored item banks onto their steps. Returns the item count written.

    Runs on every start-up, after the steps exist, and is idempotent: an item is
    keyed on ``(step_id, item_key)`` so re-seeding corrects a reworded prompt in
    place rather than duplicating it. Learner attempts reference ``item_key``, so a
    corrected item stays the same item in the ledger.

    A bank that fails ``validate_item_bank`` is REFUSED, not partially written — a
    half-seeded bank would serve a learner a question with no correct answer in it.
    """
    banks = load_item_banks()
    if not banks:
        return 0
    from .assessment import validate_item_bank

    written = 0
    for slug, by_step in banks.items():
        row = conn.execute(
            "SELECT id FROM fa_missions WHERE slug=%s", (slug,)
        ).fetchone()
        if not row:
            _log.warning("item bank references unknown mission %r", slug)
            continue
        mission_id = row["id"] if hasattr(row, "keys") else row[0]

        for step_num, items in by_step.items():
            problems = validate_item_bank(items)
            if problems:
                # Refused at seed time rather than discovered by a learner
                # mid-assessment.
                _log.warning("item bank %s step %s refused: %s",
                             slug, step_num, "; ".join(problems))
                continue
            step_row = conn.execute(
                "SELECT id FROM fa_mission_steps WHERE mission_id=%s AND step_num=%s",
                (mission_id, step_num),
            ).fetchone()
            if not step_row:
                _log.warning("item bank %s references missing step %s", slug, step_num)
                continue
            step_id = step_row["id"] if hasattr(step_row, "keys") else step_row[0]

            for item in items:
                existing = conn.execute(
                    "SELECT id FROM fa_assessment_items WHERE step_id=%s AND item_key=%s",
                    (step_id, item["item_key"]),
                ).fetchone()
                payload = (
                    item["prompt"], json.dumps(item["options"]),
                    int(item["correct_index"]), item["explanation"],
                    item["difficulty"],
                )
                if existing:
                    eid = existing["id"] if hasattr(existing, "keys") else existing[0]
                    conn.execute(
                        "UPDATE fa_assessment_items SET prompt=%s, options_json=%s, "
                        "correct_index=%s, explanation=%s, difficulty=%s, is_active=1 "
                        "WHERE id=%s",
                        (*payload, eid),
                    )
                else:
                    conn.execute(
                        "INSERT INTO fa_assessment_items "
                        "(step_id, item_key, prompt, options_json, correct_index, "
                        " explanation, difficulty) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (step_id, item["item_key"], *payload),
                    )
                written += 1
    if written:
        conn.commit()
        _log.info("FORGE Academy: seeded %d assessment item(s)", written)
    return written


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
