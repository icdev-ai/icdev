# CUI // SP-CTI
"""ICDEV™ Slide Deck Generator — dynamic agentic PPTX generation.

Adapts GenSlide's Orchestrator → Content Agents → Builder pipeline using
ICDEV's FORGE pattern:
  - LLM Router (multi-provider) instead of LangGraph + GPT-4o
  - Native ICDEV source connectors (canvases, kanban, genesis, capabilities)
  - Full dashboard canvas at /slides
  - Genesis daemon reflex for weekly auto-generation
"""
from __future__ import annotations
