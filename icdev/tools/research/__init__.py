#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""
ICDEV™ Industry Research Engine — deep industry vertical research for child app generation.

Architecture:
  D-RES-1:  Session-based lifecycle (created → scoping → ... → child_app_triggered)
  D-RES-2:  Declarative vertical configs in context/research/verticals/*.json
  D-RES-3:  SOURCE_SCANNERS function registry dict
  D-RES-4:  6-dimension weighted challenge scoring (D21)
  D-RES-5:  Append-only tables except research_sessions/research_verticals (D6)
  D-RES-9:  Template-based dossier (no LLM, air-gap safe)
  D-RES-10: Dossier → child app fitness via HITL approval
  D-RES-13: Parent-only (excluded from child apps)

Pipeline: SCOPE → LANDSCAPE → REGULATE → COMMUNITY → ACADEMIC → BUILD_BUY → SYNTHESIZE → DOSSIER
"""

__version__ = "1.0.0"
__all__ = [
    "research_engine",
    "session_manager",
    "vertical_loader",
    "source_scanner",
    "challenge_scorer",
    "regulatory_mapper",
    "capability_mapper",
    "build_buy_analyzer",
    "dossier_generator",
    "trend_detector",
]
