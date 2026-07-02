#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed 9 external GitHub repos as innovation signals for technology scouting.

Follows the same pattern as register_external_patterns.py (Phase 44 — D279).
Source type: external_repo_scouting.

Usage:
    python tools/innovation/seed_external_repos.py --register-all --json
    python tools/innovation/seed_external_repos.py --status --json
    python tools/innovation/seed_external_repos.py --score-all --json
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools.db.storage import get_connection
from tools.compat.db_utils import get_icdev_db_path

DB_PATH = get_icdev_db_path()

# 5-dimension scoring weights (matches innovation_config.yaml)
SCORING_WEIGHTS = {
    "novelty": 0.20,
    "feasibility": 0.25,
    "compliance_alignment": 0.25,
    "user_impact": 0.20,
    "effort": 0.10,
}

EXTERNAL_REPOS = [
    {
        "source": "headroom",
        "title": "Headroom — Reversible Context Compression for AI Agents",
        "description": (
            "Content-aware compression layer (SmartCrusher, CodeCompressor) that reduces "
            "token usage 60-95% for AI agent workflows. Provides MCP server, HTTP proxy, "
            "and library interfaces. Includes CacheAligner for KV cache hits, cross-agent "
            "memory deduplication, and failure mining (headroom learn). "
            "Integration point: tools/llm/router.py compression layer, genesis daemon A2A "
            "message compression. Python library (pip install headroom)."
        ),
        "category": "performance",
        "gotcha_layer": "tool",
        "url": "https://github.com/chopratejas/headroom",
        "scoring_hints": {
            "novelty": 0.90,
            "feasibility": 0.85,
            "compliance_alignment": 0.95,
            "user_impact": 0.95,
            "effort": 0.80,
        },
        "adaptation_target": "tools/llm/router.py — add compression middleware",
        "implementation_status": "pending",
    },
    {
        "source": "last30days-skill",
        "title": "last30days — Parallel Multi-Source Social Trend Synthesis",
        "description": (
            "Agent skill that simultaneously searches Reddit, X, YouTube, TikTok, GitHub, "
            "and Hacker News to synthesize what's trending about a topic in the last 30 days. "
            "Key patterns: intelligent entity disambiguation (handle→subreddit→repo), parallel "
            "multi-source orchestration, cross-source cluster merging with dedup, dual-scoring "
            "(relevance + fun), offline-shareable HTML briefs. 1,012 tests. "
            "Adaptation: implement as a Research engine source adapter at "
            "tools/research/source_scanners/social_trend_scanner.py."
        ),
        "category": "ai_tooling",
        "gotcha_layer": "tool",
        "url": "https://github.com/mvanhorn/last30days-skill",
        "scoring_hints": {
            "novelty": 0.85,
            "feasibility": 0.80,
            "compliance_alignment": 0.90,
            "user_impact": 0.90,
            "effort": 0.75,
        },
        "adaptation_target": "tools/research/source_scanners/ — social_trend_scanner.py",
        "implementation_status": "pending",
    },
    {
        "source": "agent-reach",
        "title": "Agent Reach — Unified Internet Access Layer for AI Agents",
        "description": (
            "Single-entry platform connector framework for AI agents: Twitter, YouTube, Reddit, "
            "GitHub, and more via agent-reach install. Patterns: multi-backend routing (primary + "
            "fallbacks), zero-config where possible (free APIs prioritized), health monitoring "
            "(agent-reach doctor), MCP integration with Exa search, yt-dlp fallback for blocked "
            "YouTube. Addresses fragmented per-engine platform connectors in ICDEV. "
            "Adaptation: consolidate tools/research/source_scanners/* and tools/innovation/web_scanner.py "
            "platform fetches into a shared tools/platform_connectors/ adapter registry."
        ),
        "category": "developer_experience",
        "gotcha_layer": "tool",
        "url": "https://github.com/Panniantong/agent-reach",
        "scoring_hints": {
            "novelty": 0.80,
            "feasibility": 0.85,
            "compliance_alignment": 0.85,
            "user_impact": 0.85,
            "effort": 0.80,
        },
        "adaptation_target": "tools/platform_connectors/ — unified adapter registry",
        "implementation_status": "pending",
    },
    {
        "source": "markitdown",
        "title": "MarkItDown — Multi-Format to Markdown Converter (Microsoft)",
        "description": (
            "Python utility (pip install markitdown) converting PDF, DOCX, PPTX, XLSX, "
            "images (with LLM descriptions), audio transcription, HTML, and YouTube video URLs "
            "to Markdown for LLM ingestion. Modular plugin architecture with optional Azure "
            "Document Intelligence integration for layout-aware PDF extraction. Multiple API "
            "security levels. Directly addresses DIC's limited format support. "
            "Adaptation: tools/document_intelligence/converters/markitdown_adapter.py — "
            "additive wrapper that feeds MarkItDown output into DIC RAG pipeline."
        ),
        "category": "developer_experience",
        "gotcha_layer": "tool",
        "url": "https://github.com/microsoft/markitdown",
        "scoring_hints": {
            "novelty": 0.70,
            "feasibility": 0.95,
            "compliance_alignment": 0.90,
            "user_impact": 0.85,
            "effort": 0.90,
        },
        "adaptation_target": "tools/document_intelligence/converters/markitdown_adapter.py",
        "implementation_status": "pending",
    },
    {
        "source": "iii-hq-iii",
        "title": "iii — Unified Function+Trigger Runtime for Service Composition",
        "description": (
            "Runtime platform that eliminates point-to-point service integrations via "
            "auto-discovery and a unified function/trigger interface. Workers spawn workers. "
            "Built-in distributed tracing across TypeScript, Python, Rust, Go. Zero-integration "
            "architecture. Maps to ICDEV's 15-agent A2A system (ports 8443-8458): the unified "
            "trigger model could replace JSON-RPC 2.0 point-to-point wiring. Observability "
            "consolidation would unify scheduler.ndjson, .logs, audit_trail. "
            "Adaptation: extract architectural patterns for tools/agents/a2a_registry.py; "
            "multi-language support is a significant integration lift."
        ),
        "category": "architecture",
        "gotcha_layer": "tool",
        "url": "https://github.com/iii-hq/iii",
        "scoring_hints": {
            "novelty": 0.85,
            "feasibility": 0.60,
            "compliance_alignment": 0.90,
            "user_impact": 0.85,
            "effort": 0.45,
        },
        "adaptation_target": "tools/agents/a2a_registry.py — unified trigger patterns",
        "implementation_status": "pending",
    },
    {
        "source": "hermes-agent",
        "title": "Hermes Agent — Self-Improving Agent with Persistent Memory",
        "description": (
            "Self-improving AI assistant (NousResearch) with learning loop, persistent memory "
            "via FTS5 session history + LLM summarization, autonomous skill creation, "
            "multi-platform messaging (Telegram, Discord, Slack, CLI), 40+ tool support + MCP, "
            "and Honcho dialectic user modeling. Six terminal backends (local/Docker/SSH/Modal). "
            "Relevant patterns: FTS5 full-text session search for tools/memory/hybrid_search.py, "
            "skill auto-generation for Continuous Harness, multi-platform messaging for "
            "ICDEV notifications beyond current PushNotification MCP."
        ),
        "category": "ai_tooling",
        "gotcha_layer": "tool",
        "url": "https://github.com/nousresearch/hermes-agent",
        "scoring_hints": {
            "novelty": 0.75,
            "feasibility": 0.70,
            "compliance_alignment": 0.80,
            "user_impact": 0.70,
            "effort": 0.65,
        },
        "adaptation_target": "tools/memory/hybrid_search.py — FTS5 patterns; tools/nova/ — skill auto-gen",
        "implementation_status": "pending",
    },
    {
        "source": "tolaria",
        "title": "Tolaria — Offline Git-Backed Markdown Knowledge Vault",
        "description": (
            "Desktop app (Tauri + React) for managing markdown-based knowledge bases. "
            "Files-first with git, plain markdown + YAML frontmatter, AI-agnostic (Claude, "
            "Codex, Gemini), keyboard-centric UI. Types as navigation (not enforcement). "
            "Offline and air-gap-safe by design. ICDEV already has tools/knowledge/ canvas; "
            "Tolaria's git-backed vault pattern and keyboard-shortcut philosophy could improve "
            "knowledge authoring DX and offline export for IL5/IL6 environments. "
            "Adaptation: context/ + hardprompts/ directory structure patterns; air-gap vault export."
        ),
        "category": "developer_experience",
        "gotcha_layer": "context",
        "url": "https://github.com/refactoringhq/tolaria",
        "scoring_hints": {
            "novelty": 0.55,
            "feasibility": 0.75,
            "compliance_alignment": 0.75,
            "user_impact": 0.55,
            "effort": 0.75,
        },
        "adaptation_target": "context/ directory structure — air-gap vault export patterns",
        "implementation_status": "pending",
    },
    {
        "source": "taste-skill",
        "title": "Taste Skill — Parametric Design-Quality Instruction Skills",
        "description": (
            "Framework for reusable agent skills that improve AI-generated UI quality. "
            "Modular skill architecture (npx skills add), tunable parameters "
            "(DESIGN_VARIANCE, MOTION_INTENSITY, VISUAL_DENSITY), image-first workflows, "
            "multi-agent portability via SKILL.md format (works across ChatGPT, Codex, Cursor, Claude). "
            "Anti-generic output research with parametric dials. "
            "Adaptation: hardprompts/ directory — add design-quality instruction templates for "
            "Creative canvas and Slides generator to improve generated UI/presentation code quality."
        ),
        "category": "ai_tooling",
        "gotcha_layer": "hardprompt",
        "url": "https://github.com/leonxlnx/taste-skill",
        "scoring_hints": {
            "novelty": 0.65,
            "feasibility": 0.70,
            "compliance_alignment": 0.75,
            "user_impact": 0.55,
            "effort": 0.80,
        },
        "adaptation_target": "hardprompts/design_quality_instructions.md",
        "implementation_status": "pending",
    },
    {
        "source": "ruview",
        "title": "RuView — WiFi CSI Human Sensing and Edge Inference",
        "description": (
            "ESP32-based passive WiFi Channel State Information sensing for occupancy detection, "
            "vital signs, and movement — no cameras or wearables. Self-supervised learning "
            "(ADR-024), 8KB quantized edge model, Ed25519 witness chain attestation. "
            "105-module inference catalog (health, retail, industrial). "
            "Low immediate ICDEV relevance: hardware-dependent, out-of-scope for current roadmap. "
            "Potential future value if ICDEV integrates IoT monitoring for data-center "
            "occupancy or equipment health. Self-supervised anomaly detection patterns "
            "may be relevant to MONITOR canvas. Defer unless IoT use case confirmed."
        ),
        "category": "infrastructure",
        "gotcha_layer": "arg",
        "url": "https://github.com/ruvnet/RuView",
        "scoring_hints": {
            "novelty": 0.50,
            "feasibility": 0.30,
            "compliance_alignment": 0.60,
            "user_impact": 0.30,
            "effort": 0.25,
        },
        "adaptation_target": "tools/monitor/ — self-supervised anomaly patterns (future)",
        "implementation_status": "pending",
    },
    # --- 2026-06-28 batch: sideshow / httpinspect / loopy / agentcn / trilium ---
    {
        "source": "sideshow",
        "title": "Sideshow — Real-Time Agent Output Rendering Panel",
        "description": (
            "Live browser view for coding agents: renders HTML, Mermaid diagrams, diffs, code, "
            "and images directly from agent stdout in real-time. Threaded user feedback lets "
            "agents iterate without full re-runs. MCP-integrated, built on Solid.js + Hono + "
            "Cloudflare Workers. Key patterns: composable output cards, streaming render pipeline, "
            "in-browser feedback threading. "
            "Adaptation: ACE /coworker canvas needs real-time visual output rendering and a "
            "threaded feedback loop for agent refinement cycles."
        ),
        "category": "ui",
        "gotcha_layer": "tool",
        "url": "https://github.com/modem-dev/sideshow",
        "scoring_hints": {
            "novelty": 0.85,
            "feasibility": 0.75,
            "compliance_alignment": 0.80,
            "user_impact": 0.90,
            "effort": 0.65,
        },
        "adaptation_target": (
            "icdev/tools/ace/ — real-time output rendering + threaded feedback for ACE coworker canvas"
        ),
        "implementation_status": "pending",
    },
    {
        "source": "httpinspect",
        "title": "httpinspect — Zero-Instrumentation eBPF HTTP Monitor",
        "description": (
            "Terminal dashboard for live inter-service HTTP traffic captured at kernel level via "
            "eBPF/TCX — no proxy, no app modifications, no restarts. Requires Linux 6.6+ with BTF "
            "and Clang. Real-time endpoint metrics (request rate, latency, status codes) in a "
            "`top`-like TUI. "
            "Adaptation: ICDEV monitoring canvas zero-instrumentation layer for IL5/IL6 air-gapped "
            "deployments where inserting a proxy is prohibited; informs ZTA network segmentation "
            "canvas traffic analysis without sidecar overhead."
        ),
        "category": "observability",
        "gotcha_layer": "tool",
        "url": "https://github.com/yeet-src/httpinspect",
        "scoring_hints": {
            "novelty": 0.80,
            "feasibility": 0.55,
            "compliance_alignment": 0.70,
            "user_impact": 0.75,
            "effort": 0.50,
        },
        "adaptation_target": (
            "icdev/tools/monitoring/ — zero-instrumentation HTTP capture pattern for air-gapped "
            "IL6/SIPR monitoring strategy"
        ),
        "implementation_status": "pending",
    },
    {
        "source": "loopy",
        "title": "Loopy — Structured Feedback-Driven Workflow Loops for AI Agents",
        "description": (
            "Platform providing named 'loops' — iterative, feedback-driven workflows with "
            "explicit before/after examples and measurable success criteria. Agents run a loop, "
            "receive structured feedback, and refine until criteria are met. Safety-first design: "
            "no auto-scheduling, no production changes without HITL gate. Skill distribution via "
            "npx. Registry-driven loop catalog. Supports Codex, Cursor, Claude Code. "
            "Adaptation: agent_loop.py structured refinement stages with named loop taxonomy; "
            "goals/manifest.md before/after examples; kanban starter card library."
        ),
        "category": "workflow",
        "gotcha_layer": "tool",
        "url": "https://github.com/Forward-Future/loopy",
        "scoring_hints": {
            "novelty": 0.75,
            "feasibility": 0.90,
            "compliance_alignment": 0.90,
            "user_impact": 0.85,
            "effort": 0.80,
        },
        "adaptation_target": (
            "icdev/tools/llm/agent_loop.py — structured loop stages with feedback; "
            "goals/manifest.md — before/after loop examples; kanban starter card library"
        ),
        "implementation_status": "pending",
    },
    {
        "source": "agentcn",
        "title": "agentcn — Zero-Config AI Agent Templates (shadcn model)",
        "description": (
            "Open-source collection of production-ready AI agent templates distributed as CLI "
            "recipes with zero install configuration. Each template ships complete: instructions, "
            "tool definitions, workflow steps. IDE integrations for Cursor and VS Code. Built on "
            "Next.js + TypeScript + shadcn/ui + Radix. 'npx agentcn add <name>' model. "
            "Adaptation: simplify ICDEV /initialize to 3 commands; kanban starter card library "
            "modeled on recipe distribution; Cursor dev-profile bridge improvement."
        ),
        "category": "dx",
        "gotcha_layer": "tool",
        "url": "https://github.com/shadcn-labs/agentcn",
        "scoring_hints": {
            "novelty": 0.70,
            "feasibility": 0.85,
            "compliance_alignment": 0.75,
            "user_impact": 0.80,
            "effort": 0.75,
        },
        "adaptation_target": (
            "icdev/tools/dashboard/ — zero-config UX onboarding; "
            "args/component_registry.yaml — component recipe model; "
            "tools/dev_profile/ — cursor-import-bridge recipe distribution"
        ),
        "implementation_status": "pending",
    },
    {
        "source": "trilium",
        "title": "Trilium — Hierarchical Knowledge Base with Relation Maps & Attribute System",
        "description": (
            "Open-source personal knowledge base (TypeScript/Node.js) scaling to 100k+ notes. "
            "Key patterns: hierarchical cloning (single note placed in multiple tree locations "
            "simultaneously), typed attribute system (labels + typed relation types), bidirectional "
            "relation maps, per-note AES encryption, self-hosted sync server, REST API + JS "
            "scripting automation, Excalidraw canvas sketching, Leaflet geo maps. "
            "Adaptation: Second Brain canvas note-cloning model for multi-context entity placement; "
            "KG entity multi-parent hierarchy; DIC per-document encryption; /components-map "
            "relation map visualization; RAG index sharding strategy for large corpora."
        ),
        "category": "knowledge",
        "gotcha_layer": "tool",
        "url": "https://github.com/TriliumNext/Trilium",
        "scoring_hints": {
            "novelty": 0.80,
            "feasibility": 0.80,
            "compliance_alignment": 0.85,
            "user_impact": 0.90,
            "effort": 0.70,
        },
        "adaptation_target": (
            "icdev/tools/knowledge/ + tools/second_brain/ — hierarchical cloning model, "
            "typed attribute system, bidirectional relation maps; "
            "icdev/tools/dic/ — per-document encryption pattern"
        ),
        "implementation_status": "pending",
    },
]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def register_repo(repo: dict, db_path: Path = DB_PATH) -> dict:
    title = repo["title"]
    description = repo["description"]
    content_hash = _content_hash(title + description)

    try:
        conn = get_connection(db_path=str(db_path))

        existing = conn.execute(
            "SELECT id FROM innovation_signals WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()

        if existing:
            conn.close()
            return {
                "signal_id": dict(existing)["id"],
                "status": "duplicate",
                "is_duplicate": True,
                "title": title,
            }

        signal_id = f"sig-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        hints = repo.get("scoring_hints", {})
        score = sum(hints.get(dim, 0.5) * weight for dim, weight in SCORING_WEIGHTS.items())
        score_meta = {
            "dimensions": hints,
            "weights": SCORING_WEIGHTS,
            "composite": round(score, 4),
            "adaptation_target": repo.get("adaptation_target", ""),
        }

        conn.execute(
            """INSERT INTO innovation_signals
               (id, source, source_type, title, description, url,
                category, innovation_score, score_breakdown, content_hash,
                status, gotcha_layer, implementation_status,
                classification, discovered_at, created_at)
               VALUES (?, ?, 'external_repo_scouting', ?, ?, ?,
                       ?, ?, ?, ?,
                       'triaged', ?, ?,
                       'CUI', ?, ?)""",
            (
                signal_id,
                repo["source"],
                title,
                description,
                repo.get("url", ""),
                repo.get("category", "developer_experience"),
                round(score, 4),
                json.dumps(score_meta),
                content_hash,
                repo.get("gotcha_layer", "tool"),
                repo.get("implementation_status", "pending"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        return {
            "signal_id": signal_id,
            "status": "registered",
            "is_duplicate": False,
            "title": title,
            "innovation_score": round(score, 4),
            "category": repo.get("category"),
            "adaptation_target": repo.get("adaptation_target"),
        }

    except Exception as exc:
        return {"error": str(exc), "title": title}


def register_all(db_path: Path = DB_PATH) -> dict:
    registered = 0
    skipped = 0
    signals = []

    for repo in EXTERNAL_REPOS:
        result = register_repo(repo, db_path)
        signals.append(result)
        if result.get("is_duplicate"):
            skipped += 1
        elif "error" not in result:
            registered += 1

    signals.sort(key=lambda x: x.get("innovation_score", 0), reverse=True)
    return {
        "registered": registered,
        "skipped_duplicates": skipped,
        "total_repos": len(EXTERNAL_REPOS),
        "signals": signals,
    }


def score_repos() -> dict:
    scored = []
    for repo in EXTERNAL_REPOS:
        hints = repo.get("scoring_hints", {})
        score = sum(hints.get(dim, 0.5) * weight for dim, weight in SCORING_WEIGHTS.items())
        scored.append(
            {
                "title": repo["title"],
                "source": repo["source"],
                "score": round(score, 4),
                "breakdown": hints,
                "category": repo.get("category", ""),
                "adaptation_target": repo.get("adaptation_target", ""),
                "implementation_status": repo.get("implementation_status", "pending"),
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"repos": scored, "scoring_weights": SCORING_WEIGHTS}


def get_status(db_path: Path = DB_PATH) -> dict:
    conn = get_connection(db_path=str(db_path))
    rows = conn.execute(
        "SELECT id, title, innovation_score, implementation_status "
        "FROM innovation_signals WHERE source_type='external_repo_scouting' "
        "ORDER BY innovation_score DESC"
    ).fetchall()
    conn.close()
    repos = [dict(r) for r in rows]
    return {
        "total": len(repos),
        "pending": sum(1 for r in repos if r.get("implementation_status") == "pending"),
        "repos": repos,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed external GitHub repos as innovation signals")
    parser.add_argument("--register-all", action="store_true", help="Register all repos")
    parser.add_argument("--status", action="store_true", help="Show seeded repo status")
    parser.add_argument("--score-all", action="store_true", help="Score all repos (dry-run)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.register_all:
        result = register_all()
    elif args.status:
        result = get_status()
    elif args.score_all:
        result = score_repos()
    else:
        result = {"error": "Use --register-all, --status, or --score-all"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "repos" in result and "score" in (result["repos"][0] if result.get("repos") else {}):
            for r in result["repos"]:
                print(f"  [{r.get('implementation_status','?')}] {r['title']} (score={r['score']})")
                print(f"    → {r.get('adaptation_target','')}")
        elif "signals" in result:
            for s in result["signals"]:
                status = s.get("status", "?")
                score = s.get("innovation_score", "")
                print(f"  [{status}] {s['title']} (score={score})")
            print(f"\nRegistered: {result['registered']}, Duplicates: {result['skipped_duplicates']}")
        elif "repos" in result:
            for r in result["repos"]:
                print(f"  {r['title']} ({r.get('implementation_status','?')})")
