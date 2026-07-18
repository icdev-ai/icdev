# CUI // SP-CTI
"""ICDEV Cortex canvas constants — modes and domain lenses.

The /cortex dashboard canvas is the Snowflake-Intelligence-style entry point
over the unified Cortex facade (tools/cortex/api.py). These constants describe
the surface the canvas presents: the eight facade *modes* and the *domain
lenses* that map onto ``CortexContext.domain`` for retrieval/governance scoping.

Kept declarative on purpose — the blueprint and template read these so the two
never drift, and tests can assert the canvas advertises exactly the facades the
package exports.

There are eight facade modes (complete / reason / classify / extract / search /
ask / govern / agent); each maps onto one public facade in tools/cortex/api.py.
"""
from __future__ import annotations

# ── Cortex facade modes ────────────────────────────────────────────────────────
# Each mode corresponds to one public facade in tools/cortex. ``facade`` is the
# callable name; ``ready`` marks whether the facade exists on this branch (the
# canvas is a skeleton — chat/search stubs degrade gracefully until wired).
CORTEX_MODES = [
    {"key": "complete", "label": "Complete", "icon": "✍️", "facade": "complete",
     "desc": "Free-form completion routed through the governed LLM chain."},
    {"key": "reason", "label": "Reason", "icon": "🧠", "facade": "reason",
     "desc": "Multi-step reasoning (CoT / debate / council) over the governed chain."},
    {"key": "classify", "label": "Classify", "icon": "🏷️", "facade": "classify",
     "desc": "Assign text to exactly one of a caller-supplied label set."},
    {"key": "extract", "label": "Extract", "icon": "🧲", "facade": "extract",
     "desc": "Pull structured fields from unstructured text."},
    {"key": "search", "label": "Search", "icon": "🔍", "facade": "search",
     "desc": "Unified retrieval across RAG, KG, DIC, and keyword KB backends."},
    {"key": "ask", "label": "Ask (Analyst)", "icon": "💬", "facade": "ask",
     "desc": "Cortex Analyst — grounded, cited answers over your data."},
    {"key": "govern", "label": "Govern", "icon": "🛡️", "facade": "govern",
     "desc": "Run the TRUST governance pipeline over any text + sources."},
    {"key": "agent", "label": "Agent", "icon": "🤖", "facade": "agent",
     "desc": "Launch a single agent loop or an ACE co-worker team toward a goal."},
]

# Modes whose facades are present on this branch. The skeleton wires chat + IQE;
# the remaining facades are advertised but degrade to a stub response.
CORTEX_MODE_KEYS = [m["key"] for m in CORTEX_MODES]
DEFAULT_MODE = "ask"

# ── Domain lenses ──────────────────────────────────────────────────────────────
# A lens scopes retrieval + governance to a slice of the platform. The ``domain``
# value threads into CortexContext.domain; ``general`` is the unscoped default.
CORTEX_DOMAIN_LENSES = [
    {"key": "general", "label": "General", "icon": "🌐",
     "desc": "No domain scoping — search everything the caller may read."},
    {"key": "proposal", "label": "Proposal / Capture", "icon": "📝",
     "desc": "Win themes, past performance, RFI/RFP evidence."},
    {"key": "compliance", "label": "Compliance", "icon": "📋",
     "desc": "NIST 800-53, FedRAMP, CMMC, STIG controls and crosswalks."},
    {"key": "network", "label": "Network / Infra", "icon": "🛰️",
     "desc": "Topology, hardware profiles, migration analysis."},
    {"key": "security", "label": "Security", "icon": "🔐",
     "desc": "Threat intel, vulnerabilities, ZTA posture."},
    {"key": "document", "label": "Documents", "icon": "📄",
     "desc": "Ingested corpora — DIC collections, SOPs, runbooks."},
]

CORTEX_DOMAIN_KEYS = [d["key"] for d in CORTEX_DOMAIN_LENSES]
DEFAULT_DOMAIN = "general"

# ── Canvas metadata ─────────────────────────────────────────────────────────────
CANVAS_KEY = "cortex"
CANVAS_DISPLAY_NAME = "Cortex"
CANVAS_URL_PREFIX = "/cortex"

# IQE collection names the canvas exposes (mirrors args/component_registry.yaml).
IQE_COLLECTIONS = ("cortex.chat_sessions", "cortex.audit", "cortex.search_history")
