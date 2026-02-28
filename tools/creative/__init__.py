#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV Creative Engine — customer-centric feature opportunity discovery.

Automates competitor gap analysis, customer pain point discovery, and feature
opportunity scouting from public review sites, community forums, and GitHub issues.
Outputs ranked feature specs with justification. Supports daemon and on-demand CLI.

Pipeline: DISCOVER -> EXTRACT -> SCORE -> RANK -> GENERATE

Architecture Decisions:
    D351 — Separate from Innovation Engine (different domain, scoring, sources)
    D352 — Source adapters via function registry dict (web_scanner pattern)
    D353 — Competitor auto-discovery is advisory-only (human must confirm)
    D354 — Pain extraction is deterministic keyword/regex (air-gap safe)
    D355 — 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25)
    D356 — Feature specs are template-based (no LLM, reproducible)
    D357 — All tables append-only except creative_competitors (UPDATE for status transitions)
    D358 — Reuses _safe_get(), _get_db(), _now(), _audit() helpers
    D359 — Daemon mode respects quiet hours from config
    D360 — High-scoring signals cross-register to innovation_signals
"""
