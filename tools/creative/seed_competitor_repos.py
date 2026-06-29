#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed external repo pain points as creative signals for gap scoring.

Maps 9 scouted GitHub repos to ICDEV feature gaps and inserts them as
creative_pain_points (status='new') for gap_scorer.py to score.

Usage:
    python tools/creative/seed_competitor_repos.py --seed-all --json
    python tools/creative/seed_competitor_repos.py --status --json
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

# Pain points derived from the 9 scouted repos
# Each maps a real ICDEV user/developer pain to a repo that demonstrates a solution
PAIN_POINTS = [
    {
        "title": "Agent workflows exhaust token budgets on long multi-step tasks",
        "description": (
            "Long ICDEV workflows (20+ steps, ANVIL 5-phase builds, genesis daemon A2A messages) "
            "regularly exceed context windows or incur high token costs. No compression layer "
            "exists between agent steps or in the LLM router. Headroom demonstrates reversible "
            "content-aware compression (60-95% reduction) with KV-cache alignment and cross-agent "
            "memory dedup via MCP server interface."
        ),
        "category": "performance",
        "frequency": 85,
        "severity": "critical",
        "keywords": ["token", "context window", "compression", "agent", "workflow", "cost"],
        "competitor_ids": [],
        "signal_source": "headroom-github",
        "source_repo": "https://github.com/chopratejas/headroom",
    },
    {
        "title": "Innovation and Research engines miss social-proof velocity signals",
        "description": (
            "ICDEV's Innovation Engine (web_scanner.py) and Research Engine lack real-time "
            "social-proof data: Reddit discussions, Hacker News threads, YouTube community "
            "commentary, and GitHub trending. The creative_signals social source adapters "
            "(Reddit, GitHub Issues) are stubbed. last30days demonstrates parallel multi-source "
            "orchestration with entity disambiguation, cross-source cluster merging, and "
            "dual-scoring (relevance + community engagement)."
        ),
        "category": "reporting",
        "frequency": 70,
        "severity": "high",
        "keywords": ["social", "reddit", "hacker news", "trend", "velocity", "research", "signals"],
        "competitor_ids": [],
        "signal_source": "last30days-github",
        "source_repo": "https://github.com/mvanhorn/last30days-skill",
    },
    {
        "title": "Each engine maintains its own platform connector causing duplication and fragility",
        "description": (
            "tools/research/source_scanners/, tools/innovation/web_scanner.py, and "
            "tools/creative/source_scanner.py each implement bespoke platform connectors "
            "(YouTube transcript fetching, Reddit scraping, GitHub search). When a platform "
            "changes its API, three separate places break. Agent Reach demonstrates a unified "
            "multi-backend routing layer (primary + fallbacks, health monitoring, zero-config "
            "where free APIs exist) that consolidates this fragmentation."
        ),
        "category": "integration",
        "frequency": 65,
        "severity": "high",
        "keywords": ["connector", "integration", "platform", "adapter", "consolidation", "DRY"],
        "competitor_ids": [],
        "signal_source": "agent-reach-github",
        "source_repo": "https://github.com/Panniantong/agent-reach",
    },
    {
        "title": "DIC document ingestion limited to PDF and text; DOCX, PPTX, XLSX not supported",
        "description": (
            "The Document Intelligence Canvas (DIC) ingestion pipeline handles PDFs and plain "
            "text but users regularly have DOCX contracts, PPTX briefings, XLSX data sheets, "
            "and audio recordings that cannot be ingested without manual conversion. MarkItDown "
            "(Microsoft) provides a modular Python library (pip install markitdown) that converts "
            "all these formats to Markdown ready for RAG ingestion, with optional Azure Document "
            "Intelligence for layout-aware PDF extraction."
        ),
        "category": "integration",
        "frequency": 60,
        "severity": "high",
        "keywords": ["document", "format", "DOCX", "PPTX", "XLSX", "ingestion", "DIC", "markdown"],
        "competitor_ids": [],
        "signal_source": "markitdown-github",
        "source_repo": "https://github.com/microsoft/markitdown",
    },
    {
        "title": "Creative canvas and Slides generator produce generic, low-quality UI code",
        "description": (
            "ICDEV's Creative canvas spec_generator.py and Slides generator produce functional "
            "but visually generic output. There are no design-quality constraints, typography "
            "guidelines, or layout dials applied during generation. Taste Skill demonstrates "
            "parametric design-quality instruction skills (DESIGN_VARIANCE, MOTION_INTENSITY, "
            "VISUAL_DENSITY) in a portable SKILL.md format that works across AI assistants "
            "and produces significantly higher-quality output."
        ),
        "category": "ux",
        "frequency": 45,
        "severity": "medium",
        "keywords": ["UI", "design", "quality", "slides", "creative", "typography", "layout"],
        "competitor_ids": [],
        "signal_source": "taste-skill-github",
        "source_repo": "https://github.com/leonxlnx/taste-skill",
    },
    {
        "title": "Multi-agent A2A wiring is point-to-point with no unified observability",
        "description": (
            "ICDEV's 15 agents (ports 8443-8458) communicate via point-to-point JSON-RPC 2.0 "
            "over mutual TLS. Adding a new agent requires registering its port, updating A2A "
            "routing tables, and wiring telemetry separately. iii demonstrates a unified "
            "function+trigger runtime where workers auto-discover each other and tracing is "
            "built-in across TypeScript, Python, Rust, and Go without point-to-point wiring."
        ),
        "category": "integration",
        "frequency": 40,
        "severity": "medium",
        "keywords": ["agent", "A2A", "observability", "tracing", "runtime", "discovery"],
        "competitor_ids": [],
        "signal_source": "iii-github",
        "source_repo": "https://github.com/iii-hq/iii",
    },
    {
        "title": "ICDEV memory search lacks full-text session history retrieval",
        "description": (
            "tools/memory/hybrid_search.py provides vector + keyword hybrid search but does not "
            "support FTS5 full-text search over session conversation history. Long sessions "
            "lose context about earlier decisions. Hermes Agent demonstrates FTS5 session "
            "history with LLM-driven summarization as a persistent memory backend, enabling "
            "agents to retrieve relevant prior session context efficiently."
        ),
        "category": "performance",
        "frequency": 35,
        "severity": "medium",
        "keywords": ["memory", "search", "FTS5", "session", "history", "context", "retrieval"],
        "competitor_ids": [],
        "signal_source": "hermes-agent-github",
        "source_repo": "https://github.com/nousresearch/hermes-agent",
    },
    {
        "title": "Knowledge authoring in ICDEV has no offline-export or git-native vault path",
        "description": (
            "tools/knowledge/ canvas stores entries in PostgreSQL. There is no path to export "
            "the knowledge base to a git-native vault for air-gap environments (IL5/IL6) or "
            "offline knowledge review. Tolaria demonstrates a files-first + git pattern with "
            "plain markdown + YAML frontmatter that could serve as an export/import bridge "
            "between ICDEV's DB-backed knowledge canvas and air-gapped installations."
        ),
        "category": "integration",
        "frequency": 30,
        "severity": "low",
        "keywords": ["knowledge", "offline", "air-gap", "git", "vault", "markdown", "export"],
        "competitor_ids": [],
        "signal_source": "tolaria-github",
        "source_repo": "https://github.com/refactoringhq/tolaria",
    },
    {
        "title": "ICDEV MONITOR canvas lacks self-supervised anomaly detection for sensor data",
        "description": (
            "The MONITOR canvas detects anomalies in log data but has no capability for "
            "edge/IoT sensor data streams. RuView demonstrates self-supervised learning "
            "(8KB quantized model on ESP32) for passive WiFi CSI sensing. While hardware-dependent "
            "and out-of-scope for current roadmap, the self-supervised anomaly detection "
            "training approach (no labeled data required) could inform MONITOR canvas "
            "improvements for unlabeled log streams."
        ),
        "category": "performance",
        "frequency": 15,
        "severity": "low",
        "keywords": ["monitor", "anomaly", "sensor", "IoT", "edge", "self-supervised"],
        "competitor_ids": [],
        "signal_source": "ruview-github",
        "source_repo": "https://github.com/ruvnet/RuView",
    },
    # --- 2026-06-28 batch: sideshow / httpinspect / loopy / agentcn / trilium ---
    {
        "title": "ACE coworker output is text-only with no real-time visual rendering of agent artifacts",
        "description": (
            "The ACE /coworker canvas displays agent output as plain text or markdown. "
            "Agents regularly produce HTML prototypes, Mermaid diagrams, code diffs, and "
            "image artifacts that require a separate rendering step outside ICDEV. Sideshow "
            "demonstrates live composable output cards (HTML, Mermaid, diff, image, code) "
            "rendered in a browser panel with threaded user feedback that drives the next "
            "agent iteration — eliminating the external rendering step entirely."
        ),
        "category": "ux",
        "frequency": 75,
        "severity": "high",
        "keywords": ["ACE", "coworker", "rendering", "HTML", "Mermaid", "diagram", "output", "real-time"],
        "competitor_ids": [],
        "signal_source": "sideshow-github",
        "source_repo": "https://github.com/modem-dev/sideshow",
    },
    {
        "title": "ICDEV has no zero-instrumentation inter-service HTTP visibility for air-gapped deployments",
        "description": (
            "ICDEV's monitoring canvas relies on application-level logging and sidecar proxies "
            "to observe inter-service traffic. In IL5/IL6 air-gapped environments, inserting a "
            "proxy or modifying application code is often prohibited. httpinspect demonstrates "
            "kernel-level eBPF/TCX capture that shows all HTTP traffic between services without "
            "any application changes, proxy insertion, or network tap — directly applicable to "
            "ZTA canvas traffic analysis and monitoring canvas observability in restricted networks."
        ),
        "category": "automation",
        "frequency": 60,
        "severity": "medium",
        "keywords": ["monitoring", "HTTP", "eBPF", "observability", "air-gap", "IL5", "IL6", "ZTA", "zero-instrumentation"],
        "competitor_ids": [],
        "signal_source": "httpinspect-github",
        "source_repo": "https://github.com/yeet-src/httpinspect",
    },
    {
        "title": "ICDEV agent loops lack named loop stages with explicit feedback cycles and success criteria",
        "description": (
            "tools/llm/agent_loop.py runs agent steps but has no concept of named loop types, "
            "structured before/after state capture, or explicit per-loop success criteria. Users "
            "must manually re-run agents after providing feedback with no built-in refinement "
            "stage tracking. Loopy demonstrates named loops with explicit 8-step guidance, "
            "safety gates (HITL, no prod auto-changes), before/after examples, and a distributable "
            "loop catalog that maps directly to ICDEV's goal workflow pattern."
        ),
        "category": "automation",
        "frequency": 80,
        "severity": "high",
        "keywords": ["agent loop", "feedback", "refinement", "workflow", "HITL", "success criteria", "named loop"],
        "competitor_ids": [],
        "signal_source": "loopy-github",
        "source_repo": "https://github.com/Forward-Future/loopy",
    },
    {
        "title": "ICDEV onboarding requires 5-10 manual setup steps with no zero-config path for new users",
        "description": (
            "New ICDEV users must run /initialize, configure .env, set up MCP servers, apply a "
            "profile, and seed a kanban board before they can use any canvas. agentcn demonstrates "
            "a zero-config 'npx agentcn add <template>' model where a complete agent setup "
            "(instructions + tools + workflow) is installed in one command with no manual "
            "configuration. Adapting this recipe distribution model would reduce ICDEV onboarding "
            "from 5-10 steps to 3 commands and surface the kanban starter card library."
        ),
        "category": "onboarding",
        "frequency": 85,
        "severity": "high",
        "keywords": ["onboarding", "zero-config", "setup", "initialize", "UX", "DX", "recipe", "template"],
        "competitor_ids": [],
        "signal_source": "agentcn-github",
        "source_repo": "https://github.com/shadcn-labs/agentcn",
    },
    {
        "title": "Second Brain and Knowledge Graph lack hierarchical entity cloning and per-document encryption",
        "description": (
            "ICDEV's Second Brain canvas and knowledge graph store entities in a flat/tagged "
            "structure. There is no concept of placing a single knowledge entity in multiple "
            "hierarchical contexts simultaneously (cloning) without duplication. Additionally, "
            "DIC documents are encrypted at the tenant/DB level but not per-document, making "
            "selective sharing of individual documents with different clearance levels impossible. "
            "Trilium demonstrates per-note AES encryption and hierarchical cloning at 100k+ note "
            "scale — directly applicable to ICDEV's multi-clearance Gov/DoD knowledge base needs."
        ),
        "category": "security",
        "frequency": 75,
        "severity": "high",
        "keywords": ["knowledge", "hierarchy", "cloning", "encryption", "per-document", "Second Brain", "KG", "DIC", "clearance"],
        "competitor_ids": [],
        "signal_source": "trilium-github",
        "source_repo": "https://github.com/TriliumNext/Trilium",
    },
]


def _keyword_fingerprint(keywords: list) -> str:
    normalized = sorted(k.lower().strip() for k in keywords)
    return hashlib.sha256("|".join(normalized).encode()).hexdigest()[:16]


def _content_hash(title: str, description: str) -> str:
    return hashlib.sha256((title + description).encode()).hexdigest()[:16]


def seed_pain_point(pp: dict, db_path: Path = DB_PATH) -> dict:
    title = pp["title"]
    description = pp["description"]
    kfp = _keyword_fingerprint(pp.get("keywords", []))

    try:
        conn = get_connection(db_path=str(db_path))

        existing = conn.execute(
            "SELECT id FROM creative_pain_points WHERE keyword_fingerprint = ?",
            (kfp,),
        ).fetchone()

        if existing:
            conn.close()
            return {
                "pain_point_id": dict(existing)["id"],
                "status": "duplicate",
                "is_duplicate": True,
                "title": title,
            }

        pp_id = f"pp-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """INSERT INTO creative_pain_points
               (id, title, description, category, frequency, signal_ids,
                competitor_ids, keyword_fingerprint, keywords, severity,
                status, first_seen, last_seen, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, 'CUI')""",
            (
                pp_id,
                title,
                description,
                pp.get("category", "integration"),
                pp.get("frequency", 30),
                json.dumps([pp.get("signal_source", "")]),
                json.dumps(pp.get("competitor_ids", [])),
                kfp,
                json.dumps(pp.get("keywords", [])),
                pp.get("severity", "medium"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        return {
            "pain_point_id": pp_id,
            "status": "seeded",
            "is_duplicate": False,
            "title": title,
            "category": pp.get("category"),
            "frequency": pp.get("frequency"),
            "severity": pp.get("severity"),
            "source_repo": pp.get("source_repo"),
        }

    except Exception as exc:
        return {"error": str(exc), "title": title}


def seed_all(db_path: Path = DB_PATH) -> dict:
    seeded = 0
    skipped = 0
    results = []

    for pp in PAIN_POINTS:
        result = seed_pain_point(pp, db_path)
        results.append(result)
        if result.get("is_duplicate"):
            skipped += 1
        elif "error" not in result:
            seeded += 1

    return {
        "seeded": seeded,
        "skipped_duplicates": skipped,
        "total": len(PAIN_POINTS),
        "pain_points": results,
    }


def get_status(db_path: Path = DB_PATH) -> dict:
    conn = get_connection(db_path=str(db_path))
    rows = conn.execute(
        "SELECT id, title, category, frequency, severity, status, composite_score "
        "FROM creative_pain_points "
        "WHERE signal_ids LIKE '%github%' "
        "ORDER BY COALESCE(composite_score, 0) DESC"
    ).fetchall()
    conn.close()
    pps = [dict(r) for r in rows]
    return {"total": len(pps), "pain_points": pps}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed external repo pain points for creative engine")
    parser.add_argument("--seed-all", action="store_true", help="Seed all pain points")
    parser.add_argument("--status", action="store_true", help="Show seeded pain points")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.seed_all:
        result = seed_all()
    elif args.status:
        result = get_status()
    else:
        result = {"error": "Use --seed-all or --status"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "pain_points" in result:
            for p in result["pain_points"]:
                st = p.get("status", p.get("severity", "?"))
                score = p.get("composite_score", p.get("frequency", ""))
                print(f"  [{st}] {p['title']} ({score})")
        if "seeded" in result:
            print(f"\nSeeded: {result['seeded']}, Duplicates: {result['skipped_duplicates']}")
