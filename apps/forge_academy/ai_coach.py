# CUI // SP-CTI
"""FORGE Academy AI Coach — FORGE Sensei powered by Ollama via llm/router.py."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_SENSEI_SYSTEM_CODING = """You are FORGE Sensei — the AI coach of FORGE Academy, ICDEV's
gamified AI engineering platform. You guide learners through: LLMs, RAG, agents, MCP,
multi-agent systems, LangChain, Amazon Strands, and ICDEV tooling.

Rules:
- Give ONE focused hint — never the full solution. Make the learner think.
- Use the mission slug and learner code to make your hint specific.
- Keep responses under 120 words.
- Tactical, ops-center tone — mission briefing, not lecture.
- Never reveal test assertions or expected output directly.
- End with a forward-leaning question or directive to maintain momentum.
"""

_SENSEI_SYSTEM_GUIDED = """You are FORGE Sensei — the AI coach of FORGE Academy. You help
non-technical learners (ISSOs, ISSMs, CISOs, PMs) understand AI concepts and compliance
workflows without writing code.

Rules:
- Explain the concept in plain language using a real-world analogy.
- Tie your explanation to the specific mission (STIG triage, ATO, AI governance, etc.).
- Keep responses under 150 words.
- Encouraging, clear, mission-focused tone.
- End with what to try next in the wizard.
"""

_SENSEI_SYSTEM_EXECUTIVE = """You are FORGE Sensei — briefing a senior executive (CIO, CTO, CISO, or SES) through
the FORGE Academy AI Executive Primer or Leadership track. This person makes decisions and controls budgets.
They do not write code. They will brief Congress, boards, and subordinates on AI.

Rules:
- Speak in outcomes, not technology. Use business and policy language exclusively.
- No technical jargon: never say tokens, embeddings, context windows, or inference.
- Tie every concept to a business impact or governance obligation (OMB M-25-21, DoD AI Ethics, ROI).
- Keep responses under 150 words.
- Confident, direct, senior-peer tone — you're briefing a colleague, not lecturing a student.
- End with the one decision or action this executive should take next.
"""

_SENSEI_SYSTEM_ANALYST = """You are FORGE Sensei — guiding an intelligence analyst or data analyst through
FORGE Academy. This person works with structured and unstructured data, produces reports and assessments,
and needs to augment their tradecraft with AI tools — not replace it.

Rules:
- Frame AI capabilities in terms of the intelligence cycle (collect, process, analyze, produce, disseminate).
- Use concrete intelligence tradecraft analogies: OSINT, SIGINT, pattern-of-life, indicators.
- Emphasize grounding and citation — analysts need to defend every claim.
- Keep responses under 140 words.
- Methodical, precise tone — like a senior analyst reviewing a subordinate's work product.
- End with the specific configuration change or reflection step they should complete next.
"""

# Slugs that identify guided (non-technical) missions
_GUIDED_PREFIXES = ("m-isso", "m-issm", "m-ciso", "m-pm", "m-analyst", "m-leader",
                    "m-leadership", "m-exec-primer")

# Deterministic fallback hints keyed by slug fragment
_AADC_PREFIXES = ("m-secops-05", "m-ciso-02", "m-issm-02")
_EXECUTIVE_PREFIXES = ("m-leadership-", "m-exec-primer", "m-leader")
_ANALYST_PREFIXES = ("m-analyst-",)
_ACE_PREFIXES = ("m-ace-", "m-coworker-")
_DOCGEN_PREFIXES = ("m-docgen-",)
_READINESS_PREFIXES = ("m-readiness-", "m-agentready-")
_GOVERNANCE_PREFIXES = ("m-gov-", "m-transparency-", "m-accountability-")

_SENSEI_SYSTEM_AADC = """You are FORGE Sensei — guiding a learner through an AADC (Agentic AI Design Canvas) mission.
The learner is working on a visual agentic system design that must pass compliance checks.

Rules:
- Reference the specific failing check ID and node type the learner is missing.
- Describe WHAT node is missing and WHERE it should connect — never give them the completed design.
- Keep responses under 140 words.
- Ops-center tone: terse, specific, no padding.
- End with the single action they need to take next.
"""

_SENSEI_SYSTEM_ACE = """You are FORGE Sensei — guiding a learner through ACE Co-Worker Engine missions.
ACE is ICDEV's agentic co-worker system: 6 roles (ai_developer, agent_developer, security_analyst, data_engineer, devops_engineer, compliance_officer), ThreadPoolExecutor step loops, and mandatory HITL gates.

Rules:
- Be specific about which ACE role applies to the learner's current step.
- Reference the delegation model: task card -> role assignment -> step loop -> HITL approval -> result.
- Keep responses under 140 words.
- Ops-center tone: terse, specific, action-oriented.
- End with the exact configuration change or delegation pattern the learner should try next.
"""

_SENSEI_SYSTEM_DOCGEN = """You are FORGE Sensei — guiding a learner through DocGen workflow missions.
DocGen is ICDEV's document generation system: session_manager (session lifecycle) + workflow.py (parallel section writers, merge, post-process).

Rules:
- Reference the specific DocGen stage the learner is in: session creation, section dispatch, merge, or post-process.
- Distinguish between session state (pending/active/complete) and workflow state (running/merged/failed).
- Keep responses under 130 words.
- Methodical, precise tone — document quality is the metric.
- End with the specific API call or configuration the learner needs next.
"""

_SENSEI_SYSTEM_READINESS = """You are FORGE Sensei — guiding a learner through Agent Readiness missions.
The 11-pillar readiness checker assesses: code quality, documentation, testing, structure, dependencies, configuration, security (pillars 1-7, ported from kodustech/agent-readiness), plus IL classification, NIST controls, STIG compliance, append-only audit (pillars 8-11, ICDEV extensions).

Rules:
- Identify which pillar is failing and why (percentage, specific criterion ID).
- Give a precise remediation action — file path, what to add, what check it satisfies.
- Keep responses under 130 words.
- Technical, diagnostic tone — like a readiness inspector reviewing a finding.
- End with the exact command or file change that will move the needle.
"""

_SENSEI_SYSTEM_GOVERNANCE = """You are FORGE Sensei — guiding a learner through AI Governance missions (Transparency, Accountability, Governance Intake).
Frameworks covered: OMB M-25-21 (AI inventory, oversight), OMB M-26-04, NIST AI 600-1 (model cards, fairness, confabulation), GAO-21-519SP, CAIO designation requirements.

Rules:
- Reference the specific framework obligation and section number when relevant.
- Use plain English for the concept; cite the framework for the obligation.
- Keep responses under 150 words.
- Policy-advisor tone: clear, authoritative, action-oriented.
- End with the specific artifact or configuration the learner needs to produce next.
"""

_FALLBACK_CODING: dict[str, str] = {
    "llm":        "Think about how tokens work. The model predicts the next token — what context are you giving it?",
    "prompt":     "Chain-of-thought unlocks reasoning. Rewrite your prompt with step-by-step instructions and see what changes.",
    "rag":        "Break it into two steps: retrieve relevant chunks first, then pass them as context to the LLM.",
    "agent":      "An agent needs three things: a model, tools, and a loop. Which one is missing?",
    "mcp":        "MCP is an API contract. Your server exposes tools — what tool does this mission need, and what does it return?",
    "fastmcp":    "FastMCP wraps your Python function as an MCP tool. Check the decorator and the return type.",
    "multi":      "Who's the orchestrator? Agents need a coordinator to avoid stepping on each other.",
    "strands":    "Strands tools are decorated Python functions. Check that your @tool returns a plain dict or string.",
    "langchain":  "LCEL chains are composable pipes: input | prompt | model | parser. Trace the data at each step.",
    "devops":     "The pipeline needs to know the state before it can act. Is your agent reading the right input fields?",
    "secops":     "Security tools emit structured findings. Check the schema — does your output match what the grader expects?",
    "dataops":    "Vector search is nearest-neighbor. Check your embedding function — same model at index and query time?",
    "swe":        "Design before you build. What are the inputs, outputs, and invariants of this function?",
    "netops":     "Network agents work on structured state. Is your topology dict keyed correctly? Trace what shape the next function expects.",
    "sre":        "SRE agents live or die by their SLO math. Check your burn rate calculation — is it using the right window?",
    "capstone":   "Wire the components one at a time. Get each piece to return the right type, then chain them.",
    "t3":         "Read the actual FORGE goal file. The answer to the structure question is always in the source.",
}

_FALLBACK_GUIDED: dict[str, str] = {
    "isso":     "Think of this agent as a smart assistant that reads policy docs for you. In the configure step, tell it which system and which STIG family to focus on.",
    "issm":     "ATO evidence is just documentation with timestamps. The agent collects that automatically — your job is to specify the control families that matter.",
    "ciso":     "An AI governance inventory is like an asset register, but for AI systems. Each entry needs an owner, a purpose, and a risk tier.",
    "pm":       "SAM.gov is a feed of opportunities. The agent filters it by your keywords and NAICS codes — start by defining what a 'good match' looks like for your organization.",
    "analyst":  "Each pipeline stage feeds the next. In the configure step, set your domain and data source first — every downstream stage depends on that decision.",
    "leader":   "Think in outcomes, not features. In the configure step, start with the KPI that matters most to a decision-maker — the agent builds everything around that.",
}

_FALLBACK_EXECUTIVE: dict[str, str] = {
    "roi":        "Start with the cost of the status quo. AI ROI is the gap between what you spend today and what you'd spend with AI — minus the implementation cost. Anchor that number to a real workflow, not a hypothetical.",
    "build":      "Your decision matrix should be dominated by two factors: data sovereignty requirements and your team's ability to maintain what you build. If both are low, buy. If both are high, build.",
    "risk":       "Every AI risk statement needs three components: what could go wrong, what the business impact is, and what you're doing about it. A risk without a mitigation is just a worry — and you can't brief a worry.",
    "govern":     "An AI governance structure without an owner is a decoration. The first question your oversight committee needs to answer: who can shut down an AI system if it goes wrong, and how fast?",
    "workforce":  "The talent question is really a sequencing question: who do you need proficient first to enable everyone else? That person is your AI champion — find them before you buy a single tool.",
    "capstone":   "A transformation roadmap has to be honest about Phase 0. If you skip foundation work and go straight to transformation, you're building on sand. What is the one thing that must be true before anything else can succeed?",
    "primer":     "The most important thing to know about AI: it predicts the most likely next word based on patterns in training data. It doesn't reason, it doesn't know the truth, and it has no memory between sessions unless you give it one.",
    "leadership": "Think in outcomes, not features. In the configure step, start with the decision that matters most — the agent builds everything around that.",
}

_FALLBACK_ANALYST: dict[str, str] = {
    "competitive": "Your intel agent is only as good as its source list. Start with the two highest-signal sources for your domain, get clean output from those, then expand. Breadth without quality produces noise.",
    "market":      "RAG is citation grounding at scale. The key configure decision is chunking strategy — if your chunks are too large, the LLM gets context-stuffed; too small, it loses meaning. Start with paragraph-level chunks.",
    "pattern":     "Anomaly detection has two failure modes: too sensitive (alert fatigue) and too dull (miss the signal). Set your baseline on a period with known-good data, then tune sensitivity to your false-positive tolerance.",
    "report":      "Citation grounding is non-negotiable for intelligence products. Every AI-generated claim must trace to a source document and line number. If your system can't do that, the product isn't defensible.",
    "capstone":    "The handoff between pipeline stages is where intelligence pipelines break. Define the output schema of each stage before you wire them together — the detect stage needs to know exactly what format it receives from collect.",
    "analyst":     "Each pipeline stage feeds the next. In the configure step, set your domain and data source first — every downstream stage depends on that decision.",
}

_FALLBACK_ACE: dict[str, str] = {
    "ace-01":       "Look at the role YAML. Each ACE role has a system_prompt, listen_topics, and steps list. Your co-worker isn't starting because one of those three is missing or malformed.",
    "ace-02":       "HITL gates fire when the step output matches a trigger pattern. Check your approval_required config — it needs both a trigger_field and an approver_role.",
    "ace-03":       "Creator-verifier pairs: the verifier's listen_topics must include the creator's output topic. Check that the topic strings match exactly — no typos.",
    "ace-capstone": "Wire the delegation chain step by step. First get the creator to produce output. Then confirm the verifier receives it. Then add the approval gate. Never wire all three at once.",
}

_FALLBACK_DOCGEN: dict[str, str] = {
    "docgen-01": "Sessions need a doc_type and a source_brief to start. Check your POST body to /api/docgen/sessions — both fields are required before the state transitions to 'active'.",
    "docgen-02": "The workflow dispatches section writers in parallel. If sections are empty, check that your workflow config maps section names to writer functions — the mapping is the bridge.",
    "docgen-03": "DocGen portfolio artifacts need a cert_token field for the certificate to recognize them. Add it to your session metadata when creating the session.",
}

_FALLBACK_READINESS: dict[str, str] = {
    "readiness-01": "Run the check with --json and look at the pillar_scores dict. The lowest percentage pillar is your first target. Most repos fail STIG compliance (pillar 10) first.",
    "readiness-02": "STIG compliance pillar needs STIG marker comments in your code: # STIG V-XXXXX. IL classification needs a CUI header on every sensitive file. Start with those two.",
    "readiness-03": "Wire the checker as a CI gate: run_readiness_check(repo_path) -> check overall_readiness_score -> fail the pipeline if < 0.7. The anomaly detection output tells you which pillar regressed.",
}

_FALLBACK_GOVERNANCE: dict[str, str] = {
    "gov-01":       "An AI inventory entry needs: system name, owner, purpose, risk tier, and data classification. OMB M-25-21 Section 5(a) requires all five. Start with one real system and complete all five fields.",
    "gov-02":       "An oversight plan has three components: who can shut the system down, under what conditions, and in how long. CAIO designation just adds: who signs off on that authority. Wire those four fields first.",
    "gov-03":       "The 6-pillar RICOAS intake reads your system description and scores: requirements clarity, boundary definition, impact classification, control mapping, supply chain risk, and operational readiness. Low scores on pillar 3 (impact) block the rest.",
    "gov-capstone":  "Governance portfolio: inventory -> model card -> oversight plan -> intake score. Each artifact links to the next via the system_id. Get that ID right in Step 1 — it propagates through everything.",
}


def get_hint(
    question: str,
    context: str = "",
    mission_slug: str = "",
    design_id: str = "",
    chain_mode: str = "",
) -> str:
    """Get a FORGE Sensei hint via the LLM router (Ollama preferred for air-gap).

    Args:
        chain_mode: "" for standard routing, "cot" for Chain of Thought,
                    "cod" for Chain of Debate (multiplayer AI-vs-AI).
    """
    is_aadc = any(mission_slug.startswith(p) for p in _AADC_PREFIXES) or design_id
    is_guided = any(mission_slug.startswith(p) for p in _GUIDED_PREFIXES)
    is_executive = any(mission_slug.startswith(p) for p in _EXECUTIVE_PREFIXES)
    is_analyst = any(mission_slug.startswith(p) for p in _ANALYST_PREFIXES)
    is_ace = any(mission_slug.startswith(p) for p in _ACE_PREFIXES)
    is_docgen = any(mission_slug.startswith(p) for p in _DOCGEN_PREFIXES)
    is_readiness = any(mission_slug.startswith(p) for p in _READINESS_PREFIXES)
    is_governance = any(mission_slug.startswith(p) for p in _GOVERNANCE_PREFIXES)

    # Auto-enable CoT for complex concept explanations
    if not chain_mode and _is_complex_concept(question, mission_slug):
        chain_mode = "cot"

    # Build AADC-enriched context: inject live failing checks into the prompt
    aadc_context = ""
    if is_aadc and design_id:
        try:
            from tools.agentic_ai_canvas.db.init_db import init_db as _aadc_init
            from tools.db.storage import get_connection
            import json as _json
            _aadc_init()
            conn = get_connection()
            row = conn.execute(
                "SELECT graph_json, metadata_json FROM aadc_designs WHERE design_id = %s",
                (design_id,),
            ).fetchone()
            if row:
                from tools.agentic_ai_canvas.agentic_engine import assess_design
                result = assess_design(row["graph_json"], _json.loads(row["metadata_json"] or "{}"))
                failing = [c for c in result.get("checks", []) if not c.get("passed")]
                if failing:
                    aadc_context = "FAILING CHECKS: " + ", ".join(
                        f"{c['id']} ({c.get('title','')})" for c in failing[:5]
                    )
        except Exception:
            pass

    if is_aadc:
        system = _SENSEI_SYSTEM_AADC
        prompt = (
            f"Mission: {mission_slug}\n"
            f"{aadc_context}\n"
            f"Context: {context[:400]}\n"
            f"Learner asks: {question}"
        )
    elif is_executive:
        system = _SENSEI_SYSTEM_EXECUTIVE
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"
    elif is_analyst:
        system = _SENSEI_SYSTEM_ANALYST
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"
    elif is_ace:
        system = _SENSEI_SYSTEM_ACE
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"
    elif is_docgen:
        system = _SENSEI_SYSTEM_DOCGEN
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"
    elif is_readiness:
        system = _SENSEI_SYSTEM_READINESS
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"
    elif is_governance:
        system = _SENSEI_SYSTEM_GOVERNANCE
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"
    else:
        system = _SENSEI_SYSTEM_GUIDED if is_guided else _SENSEI_SYSTEM_CODING
        prompt = f"Mission: {mission_slug}\nContext: {context[:500]}\nLearner asks: {question}"

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        provider, model_id, cfg = router.get_provider_for_function("chat")
        if provider is None:
            raise ValueError("no provider available")
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system,
            max_tokens=220,
            temperature=0.5,
            skip_injection_scan=True,
        )

        # Chain of Debate mode: run multiplayer AI-vs-AI and return transcript
        if chain_mode == "cod":
            return _run_debate_mode(router, req, prompt, mission_slug)

        # Chain of Thought mode (including auto-detected complex concepts)
        if chain_mode == "cot":
            req.chain_mode = "cot"

        resp = provider.invoke(req, model_id, cfg)
        txt = (resp.content or "").strip()
        if len(txt) < 10:
            raise ValueError("empty response")
        return txt
    except Exception as exc:
        _log.info("FORGE Sensei LLM unavailable (%s); using fallback hint", exc)
        return _fallback_hint(mission_slug, is_guided, is_aadc, is_executive, is_analyst,
                              is_ace, is_docgen, is_readiness, is_governance)


def _is_complex_concept(question: str, mission_slug: str) -> bool:
    """Heuristic: enable CoT for capstone, multi-agent, AADC, and long questions."""
    complex_slugs = ("capstone", "multi", "aadc", "strands", "mcp", "agentic",
                     "ace", "docgen", "readiness", "governance", "transparency", "accountability")
    if any(fragment in mission_slug for fragment in complex_slugs):
        return True
    if len(question) > 120:
        return True
    complex_keywords = ("why", "how does", "explain", "compare", "evaluate", "architect")
    q_lower = question.lower()
    if any(kw in q_lower for kw in complex_keywords):
        return True
    return False


def _run_debate_mode(router, req, prompt: str, mission_slug: str) -> str:
    """Run Chain of Debate and return a formatted judgment + transcript."""
    try:
        from tools.llm.chain_orchestrator import ChainOrchestrator
        orchestrator = ChainOrchestrator(router=router)
        result = orchestrator.invoke_chain_of_debate("academy_coach", req)

        lines = [
            "**FORGE Sensei — Debate Mode**",
            "",
            f"**Judgment:** {result.content}",
            "",
            "---",
            "**Debate Transcript**",
            "",
        ]
        for rnd in result.rounds:
            step = rnd.get("step", "")
            model = rnd.get("model_id", "")
            if step.startswith("debater_"):
                debater_num = step.replace("debater_", "")
                lines.append(f"> **Debater {debater_num}** ({model})")
            elif step == "judge":
                lines.append(f"> **Judge** ({model})")
            else:
                lines.append(f"> **{step.title()}** ({model})")
            lines.append("")
        lines.append(f"_Confidence: {result.confidence:.2f} | Models: {', '.join(result.models_used)}_")
        return "\n".join(lines)
    except Exception as exc:
        _log.warning("Debate mode failed (%s); falling back to standard hint", exc)
        return _fallback_hint(mission_slug, False, False, False, False, False, False, False, False)


_FALLBACK_AADC: dict[str, str] = {
    "secops-05":  "Open the AADC canvas. Every LLM node needs an input-sanitizer upstream (OWASP LLM01) and an output-validator downstream (LLM02). Drag them in and connect them first — that fixes 2 critical checks immediately.",
    "ciso-02":    "The NIST AI RMF GOVERN checks require an approval-workflow or hitl-gate node AND an audit-logger. Add both, connect them to your agent chain, then re-run the assessment.",
    "issm-02":    "For OWASP LLM06, you need both a pii-detector AND a redaction-engine — both must be present. For LLM08, every autonomous-agent needs a circuit-breaker downstream. Start with those four nodes.",
}


def _fallback_hint(mission_slug: str, is_guided: bool, is_aadc: bool = False,
                   is_executive: bool = False, is_analyst: bool = False,
                   is_ace: bool = False, is_docgen: bool = False,
                   is_readiness: bool = False, is_governance: bool = False) -> str:
    if is_aadc:
        for key, hint in _FALLBACK_AADC.items():
            if key in mission_slug:
                return hint
        return ("Check the assessment panel — the red checks tell you exactly which nodes are missing. "
                "Each check lists the required node type. Drag it onto the canvas and connect it.")
    if is_executive:
        for key, hint in _FALLBACK_EXECUTIVE.items():
            if key in mission_slug:
                return hint
        return ("Think in outcomes, not technology. In the configure step, start with the business decision "
                "that needs to be made — the rest of the framework builds from that anchor.")
    if is_analyst:
        for key, hint in _FALLBACK_ANALYST.items():
            if key in mission_slug:
                return hint
        return ("Every intelligence pipeline has a data quality gate at the front. "
                "If your output is noisy, trace back to the source — the problem is almost always there.")
    if is_ace:
        for key, hint in _FALLBACK_ACE.items():
            if key in mission_slug:
                return hint
        return "Check the co-worker role YAML — role, steps, and listen_topics must all be present before the delegation loop can start."
    if is_docgen:
        for key, hint in _FALLBACK_DOCGEN.items():
            if key in mission_slug:
                return hint
        return "DocGen needs a session before it can run a workflow. Create the session first, get the session_id, then POST to /workflow/run with that ID."
    if is_readiness:
        for key, hint in _FALLBACK_READINESS.items():
            if key in mission_slug:
                return hint
        return "Run the readiness check with --json. The pillar with the lowest percentage is your target. Fix that pillar's top failing criterion first."
    if is_governance:
        for key, hint in _FALLBACK_GOVERNANCE.items():
            if key in mission_slug:
                return hint
        return "Every governance artifact needs a system_id anchor. Create the inventory entry first — every other artifact (model card, oversight plan, intake score) references it."
    table = _FALLBACK_GUIDED if is_guided else _FALLBACK_CODING
    for key, hint in table.items():
        if key in mission_slug:
            return hint
    if is_guided:
        return ("Each wizard step maps directly to an agent configuration parameter. "
                "Re-read the field labels — they describe exactly what the agent needs.")
    return ("Focus on the error message — it's telling you exactly where the break is. "
            "What does the last line say? Start there, not at the top of the stack.")
