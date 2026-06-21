"""
Curate the ICDEV executive overview deck with the user-approved story arc.

Inserts the 15-slide narrative into deck_id=3 (or creates a new deck),
embeds the 6 dashboard screenshots, then calls the PPTX builder to
regenerate the .pptx file.
"""
from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.slides.db.init_db import get_connection  # noqa: E402
from tools.slides.pptx_builder import build         # noqa: E402

DECK_ID = 4  # Fresh deck, avoid clobbering deck 3 (which has the LLM-generated version)
DECK_TITLE = "ICDEV™: The System That Builds Systems"
THEME = "midnight_executive"
DECK_TYPE = "executive_overview"

# Copy screenshots into the slides output directory so the PPTX builder can find them.
SLIDES_IMG_DIR = ROOT / "tools" / "presentations" / "slides" / "images"
SLIDES_IMG_DIR.mkdir(parents=True, exist_ok=True)
SHOTS_SRC = ROOT / "playwright" / "screenshots"
SHOTS = {
    "home":     "dashboard_home.png",
    "prop":     "dashboard_proposals.png",
    "comp":     "dashboard_compliance.png",
    "cpmp":     "dashboard_cpmp.png",
    "zig":      "dashboard_zig.png",
    "agents":   "dashboard_agents.png",
}
shot_paths: dict[str, str] = {}
for k, name in SHOTS.items():
    src = SHOTS_SRC / name
    if not src.exists():
        print(f"WARNING: {src} not found", file=sys.stderr)
        continue
    dst = SLIDES_IMG_DIR / f"deck_curated_{k}.png"
    shutil.copy2(src, dst)
    shot_paths[k] = str(dst)
    print(f"Copied {name} -> {dst.name}")

# 15-slide curated content (the user-approved story arc)
SLIDES = [
    {
        "position": 1,
        "slide_type": "title",
        "title": "ICDEV™: The System That Builds Systems",
        "bullets": [
            "From RFP to Runtime — Faster, Safer, On-Contract",
            "A Platform Briefing for Program, Capture, and Executive Leaders",
        ],
        "speaker_notes": (
            "Open with the framing: ICDEV is not a single tool. It is a system that builds systems — "
            "software, compliance, and proposals alike. Today I'll show you how a single platform turns "
            "compliance from a tax into a generator, and how three use cases change the math on government "
            "software delivery."
        ),
        "image_path": "",
    },
    {
        "position": 2,
        "slide_type": "content",
        "title": "Agenda: Three Acts",
        "bullets": [
            "1. Why government software delivery is broken — the four forces driving the pain",
            "2. What ICDEV™ does about it — the platform, the agents, the reflexes",
            "3. Three use cases that change the math — Proposals, ATO, Program Management",
        ],
        "speaker_notes": "Three short acts. We'll spend the most time in Act 3, the use cases.",
    },
    {
        "position": 3,
        "slide_type": "content",
        "title": "Government Software Delivery Is Broken",
        "bullets": [
            "2x — Federal IT projects run two times over budget on average",
            "33% — Average schedule slip on large federal IT programs",
            "12–18 months — Typical ATO (Authority To Operate) timeline before a system can go live",
            "70% — Share of large federal IT programs that fail to meet original goals",
        ],
        "speaker_notes": (
            "Source: GAO high-risk list. These numbers are consistent across the last decade. "
            "Even the best federal program offices live with this. The question is not whether to fix it — "
            "it's how."
        ),
    },
    {
        "position": 4,
        "slide_type": "content",
        "title": "Four Forces Driving the Pain",
        "bullets": [
            "Compliance Tax — ATO, FedRAMP, CMMC, STIGs, SBOM live in siloed tools",
            "Capture Tax — 200-page proposals assembled manually under deadline pressure",
            "Program Tax — EVM, CPARS, CDRLs still tracked on spreadsheets",
            "Security Tax — zero-trust and SBOM treated as checkboxes, not engineering",
        ],
        "speaker_notes": (
            "Every one of these forces is real and necessary. The problem is not that the work exists — "
            "the problem is that it lives in 12 different tools, owned by 12 different teams, and never "
            "comes together until the audit. ICDEV's bet is that all four of these are actually the same "
            "work viewed from four angles."
        ),
    },
    {
        "position": 5,
        "slide_type": "content",
        "title": "Enter ICDEV™ — The System That Builds Systems",
        "bullets": [
            "20+ design canvases for AI-assisted design across every engineering domain",
            "15 AI agents on a coordinated A2A (agent-to-agent) protocol",
            "31 always-on reflexes that keep the system healthy while you sleep",
            "From RFP to runtime on one stack — no glue code, no integration tax",
        ],
        "speaker_notes": (
            "Slow down here. ICDEV is not a single product. It is a system of systems. Twenty design canvases "
            "for engineers. Fifteen agents that talk to each other. Thirty-one reflexes running 24/7 in the "
            "background. And all of it on one stack."
        ),
    },
    {
        "position": 6,
        "slide_type": "content",
        "title": "The ICDEV Ecosystem — Six Capability Domains",
        "bullets": [
            "Design Canvases — Network, Security, Infrastructure, Agentic AI, Documents",
            "Compliance & ATO — FedRAMP, CMMC, NIST, cATO, crosswalk engine",
            "GovCon Intelligence — SAM.gov capture, proposals, CPMP post-award",
            "Agentic AI Platform — 15 agents, ANVIL 5-phase build, Genesis reflexes",
            "Security & ZTA — NSA ZIG (7 pillars), zero-trust, OWASP Agentic",
            "AI Governance — OMB M-25-21, NIST AI 600-1, model cards, transparency",
        ],
        "speaker_notes": (
            "Six domains, one platform. Every canvas, every agent, every reflex is wired into the same "
            "data layer. That's the difference between a tool and a system."
        ),
    },
    {
        "position": 7,
        "slide_type": "content",
        "title": "Use Case 1 — Proposals: 14 Days to 5 Days",
        "bullets": [
            "Challenge: A 200-page RFP arrives. You have 14 days. The team spends week 1 reading, week 2 writing, week 3 polishing — and you're already late.",
            "Solution: GovCon canvas scans SAM.gov, mines requirements, scores bid/no-bid, drafts sections, auto-populates compliance matrices.",
            "Outcome: 14 days → 5 days. Bid cycle cut by 60%. Win-rate lift on compliant submissions.",
        ],
        "speaker_notes": (
            "This is the use case capture managers feel first. The GovCon canvas does the reading, the "
            "drafting, and the compliance-mapping in parallel. Your team reviews and decides — not types."
        ),
        "image_path": shot_paths.get("prop", ""),
    },
    {
        "position": 8,
        "slide_type": "content",
        "title": "Use Case 2 — ATO: 18 Months to 60 Days",
        "bullets": [
            "Challenge: A new system needs an ATO. You need SSP, POAM, STIG results, SBOM, FIPS 199 categorization. Each lives in a different tool, owned by a different team.",
            "Solution: Compliance canvas generates SSP from controls catalog, runs NIST 800-53 → FedRAMP/CMMC crosswalk, pulls live STIG/SBOM evidence, populates POAM, ships a complete package. cATO continuous monitoring every 6 hours.",
            "Outcome: 18 months → 60 days for initial ATO. Compliance posture visible in real time.",
        ],
        "speaker_notes": (
            "This is the use case ISSOs and AOs feel first. The compliance canvas turns SSP generation from "
            "a 90-day manual slog into a same-day pull. And once the ATO is granted, cATO monitoring keeps "
            "it that way — every six hours."
        ),
        "image_path": shot_paths.get("comp", ""),
    },
    {
        "position": 9,
        "slide_type": "content",
        "title": "Use Case 3 — Program Management: Spreadsheets to Live Contract",
        "bullets": [
            "Challenge: A $40M contract has 12 CLINs (Contract Line Items), 47 deliverables, and a COR (Contracting Officer's Representative) who wants EVM (Earned Value Management) and CPARS (Contractor Performance Assessment Reporting System) on demand.",
            "Solution: CPMP canvas tracks EVM, predicts CPARS scores, auto-detects negative events, generates CDRLs (Contract Data Requirements List), surfaces contract health in real time.",
            "Outcome: No more end-of-month surprises. CORs get answers in 30 seconds, not 3 days.",
        ],
        "speaker_notes": (
            "This is the use case PMs feel first. The CPMP canvas turns the contract from a static document "
            "into a live dashboard. EVM auto-updates. CPARS auto-predicts. CDRLs auto-generate. CORs get "
            "answers in 30 seconds, not 3 days."
        ),
        "image_path": shot_paths.get("cpmp", ""),
    },
    {
        "position": 10,
        "slide_type": "content",
        "title": "The Live System — At a Glance",
        "bullets": [
            "Home — unified mission view across portfolio, projects, and reflexes",
            "Proposals — live pipeline with bid/no-bid scoring and section drafting",
            "Compliance — real-time posture across FedRAMP, CMMC, NIST, cATO",
            "CPMP — contract health, EVM, CPARS prediction, CDRL auto-generation",
            "Security/ZIG — zero-trust scoring across 7 NSA pillars",
            "Agents — 15-agent mesh with always-on autonomous reflexes",
        ],
        "speaker_notes": (
            "This is the demo slide. Flip to the live dashboard. Every screen behind me is a real product — "
            "not a mock-up. I'll pause here for questions before we go deeper."
        ),
        "image_path": shot_paths.get("home", ""),
    },
    {
        "position": 11,
        "slide_type": "content",
        "title": "What Makes ICDEV Different",
        "bullets": [
            "Compliance-as-generator, not compliance-as-tax",
            "Always-on reflexes (31) — your system fixes itself while you sleep",
            "Air-gap native — works in classified environments, no cloud required",
            "One stack end-to-end — no glue code, no integration tax",
            "Built for federal — FedRAMP, CMMC, NIST, ATO, cATO, NSA ZIG, NDAA 889 baked in",
        ],
        "speaker_notes": (
            "Five things no one else has. Read them slowly. Compliance-as-generator is the most important — "
            "it's why a 18-month ATO can become a 60-day ATO. Air-gap native is the second most important — "
            "it's why ICDEV works in classified environments where the cloud tools can't go."
        ),
    },
    {
        "position": 12,
        "slide_type": "content",
        "title": "The Agentic Engine — 15 Agents, 31 Reflexes",
        "bullets": [
            "Genesis daemon — always-on autonomous operations brain",
            "failure_triage every 30 minutes — catches problems before humans do",
            "awareness cycles every 3 hours — detects drift, gaps, regressions",
            "self-healing with confidence ≥ 0.7 — up to 5 fixes per hour",
            "ANVIL — 5-phase Test-Driven Development (TDD) workflow: Architect → Navigate → Verify → Integrate → Launch",
            "Multi-agent coordination with domain-authority vetoes — no agent oversteps its lane",
        ],
        "speaker_notes": (
            "This is the engine under the hood. The Genesis daemon is what makes ICDEV different from every "
            "other compliance tool. It's not a dashboard you log into. It's a system that watches itself 24/7 "
            "and fixes problems before they become incidents."
        ),
        "image_path": shot_paths.get("agents", ""),
    },
    {
        "position": 13,
        "slide_type": "content",
        "title": "Federal-Ready, Audit-Ready",
        "bullets": [
            "Frameworks: FedRAMP, CMMC, NIST 800-53, FIPS 199/200, NSA ZIG, OMB M-25-21, NIST AI 600-1, GAO-21-519SP, OWASP Agentic",
            "cATO continuous monitoring — every 6 hours, not every 18 months",
            "SBOM (Software Bill of Materials) on every build — every component accounted for",
            "Air-gap deployable — classified, on-prem, disconnected",
            "Audit trail append-only per NIST AU — tamper-proof by design",
        ],
        "speaker_notes": (
            "If you're an ISSO, an AO, or a COR, this is the slide for you. Every framework you need to be "
            "in compliance with, ICDEV is already in compliance with. The audit trail is append-only — "
            "meaning it cannot be modified after the fact. That's the federal standard."
        ),
        "image_path": shot_paths.get("zig", ""),
    },
    {
        "position": 14,
        "slide_type": "content",
        "title": "Three Ways to Engage",
        "bullets": [
            "1. Book a live demo on your data — 30 minutes, your RFP, your ATO",
            "2. Pilot on a single RFP or ATO — 60 days, fixed scope, fixed price",
            "3. Stand up ICDEV in your environment — cloud, on-prem, or air-gap",
        ],
        "speaker_notes": (
            "Pick the one that fits your timeline. A demo is 30 minutes and free. A pilot is 60 days and "
            "fixed scope. A full deployment is whatever your environment needs."
        ),
    },
    {
        "position": 15,
        "slide_type": "outro",
        "title": "ICDEV™ — Let's Build the Future of Federal Software",
        "bullets": [
            "Book a 30-minute demo — hello@icdev.ai — icdev.ai",
            "Federal software delivery doesn't have to be slow, expensive, or risky.",
            "ICDEV is the system that builds the systems.",
        ],
        "speaker_notes": (
            "Close with the mission. Federal software delivery doesn't have to be slow, expensive, or risky. "
            "ICDEV is the system that builds the systems. Book a demo. We'll show you on your data."
        ),
    },
]


def main() -> None:
    conn = get_connection()
    cur = conn.cursor()

    # Create the deck record (use RETURNING so we get the new id even with RealDictCursor)
    cur.execute(
        "INSERT INTO slides_decks (title, deck_type, theme, status, source_types, slide_count) "
        "VALUES (%s, %s, %s, 'pending', %s, 0) RETURNING deck_id",
        (DECK_TITLE, DECK_TYPE, THEME, json.dumps(["icdev_capabilities", "canvases", "kanban", "curated"])),
    )
    row = cur.fetchone()
    # RealDictCursor returns a dict; raw cursor returns a tuple
    deck_id = row["deck_id"] if isinstance(row, dict) else row[0]
    print(f"Created deck_id={deck_id}")

    # Insert slides
    for s in SLIDES:
        cur.execute(
            "INSERT INTO slides_slides (deck_id, position, slide_type, title, bullets, speaker_notes, image_path) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                deck_id,
                s["position"],
                s["slide_type"],
                s["title"],
                json.dumps(s["bullets"]),
                s.get("speaker_notes", ""),
                s.get("image_path", ""),
            ),
        )
    print(f"Inserted {len(SLIDES)} slides")
    conn.commit()

    # Build slide dicts the PPTX builder expects
    slide_dicts: list[dict] = []
    for s in SLIDES:
        slide_dicts.append({
            "title": s["title"],
            "bullets": s["bullets"],
            "speaker_notes": s.get("speaker_notes", ""),
            "slide_type": s["slide_type"],
            "image_path": s.get("image_path", ""),
        })

    # Build the PPTX
    pptx_path = build(slide_dicts, theme=THEME, title=DECK_TITLE)
    print(f"Built PPTX: {pptx_path}")

    # Update the deck record
    cur.execute(
        "UPDATE slides_decks SET status='completed', pptx_path=?, slide_count=?, completed_at=CURRENT_TIMESTAMP WHERE deck_id=?",
        (pptx_path, len(slide_dicts), deck_id),
    )
    conn.commit()
    print(f"Finalized deck_id={deck_id} status=completed slide_count={len(slide_dicts)}")
    print(f"PPTX_PATH={pptx_path}")


if __name__ == "__main__":
    main()
