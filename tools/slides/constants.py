# CUI // SP-CTI
"""Slide Deck Generator constants.

DeckType, SlideType, Theme, ImageProvider enums and CHECK constraint strings
for the canvas DB schema.
"""
from __future__ import annotations

# ── Feature Flag ──────────────────────────────────────────────────────────────
SLIDES_FEATURE_FLAG = "ICDEV_SLIDES_ENABLED"

# ── Deck Types ────────────────────────────────────────────────────────────────
DECK_TYPES: list[str] = [
    "executive_overview",    # ICDEV ecosystem + capabilities overview
    "canvas_deep_dive",      # Single canvas detailed walkthrough
    "govcon_proposal",       # GovCon pitch / capability statement
    "compliance_briefing",   # ATO / FedRAMP / CMMC status
    "weekly_status",         # Genesis auto-generated weekly status
    "custom",                # User-defined free-form deck
]

# ── Slide Types ───────────────────────────────────────────────────────────────
SLIDE_TYPES: list[str] = [
    "title",        # Opening title slide
    "agenda",       # Table of contents / agenda
    "content",      # Standard bullet + graphic slide
    "two_column",   # Split layout: bullets left, graphic right
    "quote",        # Pull quote / highlight slide
    "data",         # Metrics / numbers slide
    "outro",        # Closing / call-to-action slide
]

# ── Themes ────────────────────────────────────────────────────────────────────
THEMES: list[str] = [
    "midnight_executive",   # NAVY/GOLD — default, matches ICDEV dashboard
    "govcon_proposal",      # NAVY/SILVER — GovCon pitches
    "compliance_briefing",  # NAVY/GREEN — ATO/FedRAMP status decks
]

# ── Image Providers ───────────────────────────────────────────────────────────
IMAGE_PROVIDERS: list[str] = [
    "ollama_cloud",   # Ollama cloud image gen model (e.g. sdxl)
    "dalle",          # OpenAI DALL-E 3
    "gemini",         # Gemini Imagen 3
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
    "running",    # LLM pipeline active
    "completed",  # PPTX ready for download
    "failed",     # Pipeline error
    "auto",       # Genesis daemon auto-generated
]

# ── DB CHECK Constraint strings (derive from Python constants above) ──────────
CHECK_DECK_TYPE     = "deck_type IN (" + ", ".join(f"'{t}'" for t in DECK_TYPES) + ")"
CHECK_SLIDE_TYPE    = "slide_type IN (" + ", ".join(f"'{t}'" for t in SLIDE_TYPES) + ")"
CHECK_THEME         = "theme IN (" + ", ".join(f"'{t}'" for t in THEMES) + ")"
CHECK_DECK_STATUS   = "status IN (" + ", ".join(f"'{s}'" for s in DECK_STATUSES) + ")"

# ── LLM Routing Function Names ────────────────────────────────────────────────
LLM_FN_OUTLINE    = "slides_outline_planning"
LLM_FN_CONTENT    = "slides_content_generation"
LLM_FN_REVISION   = "slides_content_revision"
LLM_FN_VIZ_PROMPT = "slides_visual_prompt"

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

THEME_PALETTES: dict[str, dict] = {
    "midnight_executive": PALETTE_MIDNIGHT,
    "govcon_proposal":    PALETTE_GOVCON,
    "compliance_briefing": PALETTE_COMPLIANCE,
}

# ── Defaults ───────────────────────────────────────────────────────────────────
DEFAULT_THEME       = "midnight_executive"
DEFAULT_DECK_TYPE   = "executive_overview"
DEFAULT_MAX_SLIDES  = 12
MIN_SLIDES          = 5
