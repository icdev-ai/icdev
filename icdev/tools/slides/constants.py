# CUI // SP-CTI
"""Slide Deck Generator constants.

DeckType, SlideType, Theme, ImageProvider enums and CHECK constraint strings
for the canvas DB schema.
"""
from __future__ import annotations

# ── Feature Flag ──────────────────────────────────────────────────────────────
SLIDES_FEATURE_FLAG = "ICDEV_SLIDES_ENABLED"

# ── Deck Types ────────────────────────────────────────────────────────────────
# The deck types a user may CHOOSE. This list is handed to the index page and the
# new-deck wizard as the deck-type picker, so anything added here becomes a menu
# option. It is not the whole persisted vocabulary — see SYSTEM_DECK_TYPES.
DECK_TYPES: list[str] = [
    "executive_overview",    # ICDEV ecosystem + capabilities overview
    "canvas_deep_dive",      # Single canvas detailed walkthrough
    "govcon_proposal",       # GovCon pitch / capability statement
    "compliance_briefing",   # ATO / FedRAMP / CMMC status
    "weekly_status",         # Genesis auto-generated weekly status
    "custom",                # User-defined free-form deck
    "general_presentation",  # Open-ended topic/occasion deck
    "pitch_deck",            # Audience-aware investment/concept pitch
]

# Deck types the system PERSISTS but never offers as a choice (sdt-vocab-01).
#
# A template-fill deck is produced by uploading a .pptx and filling its shapes;
# there is no wizard path that selects it, so it does not belong in the picker.
# But blueprint.py writes it to slides_decks.deck_type, and CHECK_DECK_TYPE used
# to derive from DECK_TYPES alone — so the route could not persist a deck against
# a correctly-created schema at all. It only appeared to work on databases whose
# slides_decks predated the constraint, which is why a clean checkout surfaced it
# and a long-lived one did not.
#
# Kept separate rather than appended to DECK_TYPES so fixing the constraint does
# not put a non-choice in front of the user. deck_type is the deck's provenance;
# collapsing template_fill into "custom" would erase the only record that a deck
# came from a user-supplied template.
SYSTEM_DECK_TYPES: list[str] = [
    "template_fill",         # Built by filling an uploaded .pptx template
]

# Everything slides_decks.deck_type may legally hold. The CHECK derives from this.
PERSISTED_DECK_TYPES: list[str] = DECK_TYPES + SYSTEM_DECK_TYPES

# ── Tones / Styles ────────────────────────────────────────────────────────────
TONES: list[str] = [
    "professional",  # Polished, corporate, clear
    "fun",           # Light, playful, conversational
    "creative",      # Imaginative, story-driven
    "adventurous",   # Energetic, bold, inspiring action
    "minimal",       # Sparse, refined, essential
    "bold",          # Direct, high-contrast, commanding
]

# ── Citation Styles ───────────────────────────────────────────────────────────
CITATION_STYLES: list[str] = [
    "apa",
    "mla",
    "chicago",
    "inline_links",
]

# ── Output Formats ────────────────────────────────────────────────────────────
OUTPUT_FORMATS: list[str] = [
    "pptx",
    "pdf",
    "html",
]

# ── Tone-driven writing + visual style hints ─────────────────────────────────
TONE_STYLE_HINTS: dict[str, dict[str, str]] = {
    "professional": {
        "writing": "polished, concise, and authoritative; avoid slang and hyperbole",
        "visual": "clean corporate illustration, neutral palette, minimalist isometric diagrams",
    },
    "fun": {
        "writing": "light, playful, conversational; use relatable examples and a warm voice",
        "visual": "friendly flat illustrations, rounded shapes, warm accent colors, occasional whimsy",
    },
    "creative": {
        "writing": "imaginative, story-driven, metaphor-rich; invite curiosity",
        "visual": "vibrant gradients, flowing organic shapes, artistic collage style",
    },
    "adventurous": {
        "writing": "energetic, inspiring action; use vivid verbs and outdoor/exploration metaphors",
        "visual": "dynamic compositions, nature textures, bold horizons, dramatic lighting",
    },
    "minimal": {
        "writing": "ultra-concise, essential, no filler; one idea per bullet",
        "visual": "lots of negative space, thin line art, monochrome with a single accent",
    },
    "bold": {
        "writing": "direct, confident, high-impact; short punchy statements",
        "visual": "high-contrast color blocks, oversized shapes, daring typography-inspired graphics",
    },
}

# ── Slide Types ───────────────────────────────────────────────────────────────
SLIDE_TYPES: list[str] = [
    "title",              # Opening title slide
    "agenda",             # Table of contents / agenda
    "content",            # Standard bullet + graphic slide
    "two_column",         # Split layout: bullets left, graphic right
    "quote",              # Pull quote / highlight slide
    "data",               # Metrics / numbers slide
    "outro",              # Closing / call-to-action slide
    "mermaid_diagram",    # Mermaid syntax diagram (flow/sequence/architecture)
    "three_animation",    # Three.js 3D scene (interactive in web viewer/HTML; placeholder in PPTX)
    "excalidraw_sketch",  # Hand-drawn whiteboard style via rough.js
    "card_grid",          # 3-column card grid (investment overview, capability comparison)
    "table",              # Structured data table (financials, comparisons, ROI breakdowns)
    "svg_art",            # Full-slide vector art rendered as native, editable PPTX shapes
]

# ── Themes ────────────────────────────────────────────────────────────────────
THEMES: list[str] = [
    "midnight_executive",     # NAVY/GOLD — default, matches ICDEV dashboard
    "govcon_proposal",        # NAVY/SILVER — GovCon pitches
    "compliance_briefing",    # NAVY/GREEN — ATO/FedRAMP status decks
    "fun_fiesta",             # CORAL/TEAL/CREAM — playful social decks
    "creative_aurora",        # PURPLE/MAGENTA/MINT — artistic, imaginative
    "adventurous_outdoor",    # FOREST/CLAY/SKY — energetic, nature-inspired
    "minimal_mono",           # WHITE/CHARCOAL — stark, essential
    "bold_neon",              # BLACK/LIME/PINK — high-contrast, commanding
    "investment_deck",        # DEEP NAVY/GOLD+TEAL/PURPLE — pitch/investor aesthetic
]

# ── Image Providers ───────────────────────────────────────────────────────────
IMAGE_PROVIDERS: list[str] = [
    "gpt_image_2",    # OpenAI GPT-Image-2 — best text + professional diagrams
    "imagen_4",       # Google Imagen 4 — enterprise scale, native 16:9, typography
    "ollama_cloud",   # Ollama cloud image gen model (e.g. sdxl / flux)
    "dalle",          # OpenAI DALL-E 3 (legacy)
    "gemini",         # Gemini Imagen 3 (legacy)
    "matplotlib",     # Programmatic fallback — always available
]

# ── Source Types ──────────────────────────────────────────────────────────────
SOURCE_TYPES: list[str] = [
    "icdev_capabilities",   # ICDEV feature catalog (canvases, phases, milestones)
    "canvases",             # Active canvases from feature flags
    "child_apps",           # Showcase / child apps
    "kanban",               # Kanban epics + task burndown
    "genesis",              # Genesis reflex run summaries
    "upload",               # User-uploaded text/PDF/DOCX
]

# ── Deck Status ───────────────────────────────────────────────────────────────
DECK_STATUSES: list[str] = [
    "pending",    # Generation queued
    "running",    # LLM pipeline active (legacy alias)
    "gathering",  # Phase 1: gathering source data
    "planning",   # Phase 2: planning outline
    "generating", # Phase 3: writing slide content
    "graphics",   # Phase 4: generating images
    "building",   # Phase 5: assembling PPTX/exports
    "completed",  # PPTX ready for download — real LLM content end-to-end
    "degraded",   # PPTX ready, but some slides/research fell back (honesty flag)
    "template",   # PPTX ready, but the outline itself is a canned static fallback
    "failed",     # Pipeline error
    "auto",       # Genesis daemon auto-generated
]

# Deck statuses that still yield a downloadable/presentable artifact.
# Degraded/template decks are honestly flagged but remain usable.
DECK_READY_STATUSES: list[str] = ["completed", "degraded", "template", "auto"]

# ── Slide content provenance ─────────────────────────────────────────────────
# Tracks how each slide's content was produced so degraded decks are never
# silently reported as fully generated (wave honesty standard).
PROVENANCE_LLM        = "llm"          # Real LLM-generated content
PROVENANCE_FALLBACK   = "fallback"     # LLM unavailable/failed → canned content
PROVENANCE_STRUCTURAL = "structural"   # Intentionally templated title/outro slide
SLIDE_PROVENANCES: list[str] = [
    PROVENANCE_LLM, PROVENANCE_FALLBACK, PROVENANCE_STRUCTURAL,
]

# ── DB CHECK Constraint strings (derive from Python constants above) ──────────
# PERSISTED_DECK_TYPES, not DECK_TYPES: the constraint has to admit the types the
# system writes as well as the ones a user can pick (sdt-vocab-01).
CHECK_DECK_TYPE     = "deck_type IN (" + ", ".join(f"'{t}'" for t in PERSISTED_DECK_TYPES) + ")"
CHECK_SLIDE_TYPE    = "slide_type IN (" + ", ".join(f"'{t}'" for t in SLIDE_TYPES) + ")"
CHECK_THEME         = "theme IN (" + ", ".join(f"'{t}'" for t in THEMES) + ")"
CHECK_DECK_STATUS   = "status IN (" + ", ".join(f"'{s}'" for s in DECK_STATUSES) + ")"

# ── LLM Routing Function Names ────────────────────────────────────────────────
LLM_FN_OUTLINE     = "slides_outline_planning"
LLM_FN_CONTENT     = "slides_content_generation"
LLM_FN_REVISION    = "slides_content_revision"
LLM_FN_VIZ_PROMPT  = "slides_visual_prompt"
LLM_FN_MERMAID     = "slides_mermaid_generation"
LLM_FN_THREE       = "slides_three_scene_generation"
LLM_FN_EXCALIDRAW  = "slides_excalidraw_generation"
LLM_FN_TABLE       = "slides_table_generation"

# ── Audience Modes ─────────────────────────────────────────────────────────────
AUDIENCE_MODES: list[str] = [
    "investor",       # Market size, ROI, defensibility, traction
    "stakeholder",    # Risk mitigation, compliance, timeline certainty
    "business_owner", # Pain solved, cost savings, ease of adoption
    "customer",       # Relatability, "aha moment", before/after
    "government",     # ATO evidence, CMMC posture, FedRAMP readiness
]

AUDIENCE_MODE_HINTS: dict[str, dict[str, str]] = {
    "investor": {
        "narrative": "Hook (problem pain) → Market size → Solution → Differentiation → Traction → Ask",
        "emphasis": "ROI, market opportunity, defensibility, team momentum",
        "tone_override": "bold",
    },
    "stakeholder": {
        "narrative": "Risk landscape → Compliance posture → Mitigation plan → Timeline → Controls",
        "emphasis": "risk reduction, compliance evidence, accountability, schedule certainty",
        "tone_override": "professional",
    },
    "business_owner": {
        "narrative": "Problem recognized → Cost of status quo → Solution → ROI → Simple next step",
        "emphasis": "cost savings, ease of adoption, concrete outcomes, low switching cost",
        "tone_override": "professional",
    },
    "customer": {
        "narrative": "Relatable pain → Aha moment → How it works → Social proof → Call to action",
        "emphasis": "relatability, simplicity, what's in it for me, testimonials",
        "tone_override": "fun",
    },
    "government": {
        "narrative": "Mission alignment → Compliance posture → Architecture → ATO path → Past performance → Contract vehicle",
        "emphasis": "FedRAMP/CMMC/STIG, IL level, cATO evidence, SBOM, past performance",
        "tone_override": "professional",
    },
}

# ── Pitch Template Presets ─────────────────────────────────────────────────────
PITCH_TEMPLATES: dict[str, dict] = {
    "investor_pitch": {
        "label": "Investor Pitch",
        "slides": 10,
        "deck_type": "pitch_deck",
        "arc": [
            "Hook", "The Problem", "Our Solution", "How It Works", "Market Opportunity",
            "Why Now", "Traction & Proof", "Team & Advantage", "The Ask", "Next Steps",
        ],
        "rich_types": {
            "How It Works": "mermaid_diagram",
            "Our Solution": "three_animation",
            "The Problem": "excalidraw_sketch",
        },
    },
    "product_demo": {
        "label": "Product Demo",
        "slides": 8,
        "deck_type": "pitch_deck",
        "arc": [
            "What We Built", "The Problem It Solves", "Live Demo Flow", "Key Features",
            "Architecture", "Results / Metrics", "Getting Started", "Q&A",
        ],
        "rich_types": {
            "Live Demo Flow": "mermaid_diagram",
            "Architecture": "three_animation",
        },
    },
    "govcon_capability": {
        "label": "Government Capability Statement",
        "slides": 12,
        "deck_type": "govcon_proposal",
        "arc": [
            "Mission Alignment", "Company Overview", "Core Competencies", "Past Performance",
            "Technical Approach", "Compliance Posture", "ATO Acceleration", "CMMC Readiness",
            "Key Personnel", "Contract Vehicles", "Differentiators", "Contact & Next Steps",
        ],
        "rich_types": {
            "Technical Approach": "mermaid_diagram",
            "Compliance Posture": "three_animation",
        },
    },
    "board_update": {
        "label": "Board Update",
        "slides": 7,
        "deck_type": "executive_overview",
        "arc": [
            "Executive Summary", "KPIs & Metrics", "Product Milestones", "Pipeline & Revenue",
            "Risks & Mitigations", "Team Updates", "Decisions Needed",
        ],
        "rich_types": {
            "Product Milestones": "mermaid_diagram",
        },
    },
}

# ── PPTX Color Palette (matches generate_exec_deck.py) ────────────────────────
# Midnight Executive theme
PALETTE_MIDNIGHT = {
    "bg":      (0x0A, 0x16, 0x28),   # NAVY
    "accent":  (0xC8, 0xA9, 0x51),   # GOLD
    "text":    (0xFF, 0xFF, 0xFF),   # WHITE
    "subtext": (0xE0, 0xE6, 0xF0),   # LGRAY
    "dark":    (0x1E, 0x3A, 0x5F),   # MGRAY
}
# GovCon Proposal theme
PALETTE_GOVCON = {
    "bg":      (0x0A, 0x16, 0x28),
    "accent":  (0xA8, 0xB2, 0xC1),   # SILVER
    "text":    (0xFF, 0xFF, 0xFF),
    "subtext": (0xD0, 0xD8, 0xE4),
    "dark":    (0x1E, 0x3A, 0x5F),
}
# Compliance Briefing theme
PALETTE_COMPLIANCE = {
    "bg":      (0x0A, 0x16, 0x28),
    "accent":  (0x2E, 0xCC, 0x71),   # GREEN
    "text":    (0xFF, 0xFF, 0xFF),
    "subtext": (0xD0, 0xF0, 0xDC),
    "dark":    (0x1A, 0x5C, 0x36),
}

# Fun / social theme
PALETTE_FUN_FIESTA = {
    "bg":      (0x2A, 0x1A, 0x3E),   # deep plum
    "accent":  (0xFF, 0x7A, 0x59),   # coral
    "text":    (0xFF, 0xFB, 0xF0),   # cream
    "subtext": (0xB8, 0xF2, 0xE6),   # soft teal
    "dark":    (0x1E, 0x14, 0x2E),   # darker plum
}

# Creative / imaginative theme
PALETTE_CREATIVE_AURORA = {
    "bg":      (0x1A, 0x10, 0x3A),   # deep indigo
    "accent":  (0xD9, 0x2B, 0x85),   # magenta
    "text":    (0xF5, 0xFF, 0xFA),   # mint-white
    "subtext": (0xA0, 0xE0, 0xD0),   # seafoam
    "dark":    (0x12, 0x0A, 0x28),   # darker indigo
}

# Adventurous / outdoor theme
PALETTE_ADVENTUROUS_OUTDOOR = {
    "bg":      (0x1A, 0x2F, 0x23),   # deep forest
    "accent":  (0xD9, 0x7D, 0x48),   # clay/orange
    "text":    (0xF4, 0xF9, 0xFF),   # pale sky
    "subtext": (0xA8, 0xD0, 0xE6),   # sky blue
    "dark":    (0x12, 0x22, 0x18),   # darker forest
}

# Minimal / monochrome theme
PALETTE_MINIMAL_MONO = {
    "bg":      (0xFF, 0xFF, 0xFF),   # white
    "accent":  (0x33, 0x33, 0x33),   # charcoal
    "text":    (0x22, 0x22, 0x22),   # near black
    "subtext": (0x66, 0x66, 0x66),   # gray
    "dark":    (0xF2, 0xF2, 0xF2),   # light gray
}

# Bold / neon theme
PALETTE_BOLD_NEON = {
    "bg":      (0x0D, 0x0D, 0x0D),   # black
    "accent":  (0xCC, 0xFF, 0x00),   # electric lime
    "text":    (0xFF, 0xFF, 0xFF),   # white
    "subtext": (0xFF, 0x33, 0x99),   # hot pink
    "dark":    (0x1A, 0x1A, 0x1A),   # dark gray
}

# Investment pitch — dark navy + gold primary + teal/cyan secondary + purple tertiary
# Defense-primes pitch aesthetic with a multi-accent AI tech color system
PALETTE_INVESTMENT_DECK = {
    "bg":      (0x0A, 0x16, 0x28),   # #0A1628 deep navy
    "accent":  (0xD4, 0xA0, 0x17),   # #D4A017 gold (primary)
    "text":    (0xFF, 0xFF, 0xFF),   # white
    "subtext": (0xC8, 0xD2, 0xDC),   # #C8D2DC light blue-gray
    "dark":    (0x0D, 0x1F, 0x3C),   # #0D1F3C card background
    "teal":    (0x00, 0xB4, 0xD8),   # #00B4D8 secondary accent (AI/tech)
    "cyan":    (0x00, 0xD4, 0xFF),   # #00D4FF highlight
    "purple":  (0x7B, 0x2F, 0xBE),   # #7B2FBE AI/ML accent
}

# A LIGHT corporate status-deck theme — white slides, a navy header band, and
# white cards with a coloured top stripe. Modelled on a real status deck.
#
# Every theme above is dark-background; this is the first light one, which is why
# it carries two keys the others do not need:
#   "card"      — card fill (here white, distinct from the white page so a thin
#                 border reads); dark themes let this default to "dark".
#   "band_text" — text colour ON the navy header band. On a light theme the title
#                 must be white, not the blue "accent" (blue-on-navy is unreadable).
#                 Dark themes let this default to "accent".
#   "rotation"  — the signature: card accents cycle blue → purple → green → amber
#                 instead of every card looking the same.
# The builder falls back gracefully, so adding these keys changes nothing for the
# existing dark themes.
PALETTE_CORPORATE_STATUS = {
    "bg":        (0xFF, 0xFF, 0xFF),   # white page
    "accent":    (0x25, 0x63, 0xEB),   # #2563EB primary blue
    "text":      (0x1E, 0x29, 0x3B),   # #1E293B slate — body text
    "subtext":   (0x6B, 0x72, 0x80),   # #6B7280 muted gray
    "dark":      (0x1A, 0x3A, 0x5C),   # #1A3A5C navy — header band
    "card":      (0xFF, 0xFF, 0xFF),   # white cards (border supplies the edge)
    "border":    (0xCB, 0xD5, 0xE1),   # #CBD5E1 hairline card border
    "band_text": (0xFF, 0xFF, 0xFF),   # white title on the navy band
    "teal":      (0x25, 0x63, 0xEB),   # keep the card-grid teal fallback on-brand
    # The accent rotation, in the reference deck's order.
    "rotation":  [(0x25, 0x63, 0xEB),  # blue
                  (0x7C, 0x3A, 0xED),  # purple
                  (0x16, 0xA3, 0x4A),  # green
                  (0xF5, 0x9E, 0x0B)], # amber
}

THEME_PALETTES: dict[str, dict] = {
    "midnight_executive":    PALETTE_MIDNIGHT,
    "govcon_proposal":       PALETTE_GOVCON,
    "compliance_briefing":   PALETTE_COMPLIANCE,
    "fun_fiesta":            PALETTE_FUN_FIESTA,
    "creative_aurora":       PALETTE_CREATIVE_AURORA,
    "adventurous_outdoor":   PALETTE_ADVENTUROUS_OUTDOOR,
    "minimal_mono":          PALETTE_MINIMAL_MONO,
    "bold_neon":             PALETTE_BOLD_NEON,
    "investment_deck":       PALETTE_INVESTMENT_DECK,
    "corporate_status":      PALETTE_CORPORATE_STATUS,
}

# ── Defaults ───────────────────────────────────────────────────────────────────
DEFAULT_THEME        = "midnight_executive"
DEFAULT_DECK_TYPE    = "executive_overview"
DEFAULT_TONE         = "professional"
DEFAULT_CITATION_STYLE = "inline_links"
DEFAULT_OUTPUT_FORMATS = ["pptx"]
DEFAULT_MAX_SLIDES   = 12
MIN_SLIDES           = 5
