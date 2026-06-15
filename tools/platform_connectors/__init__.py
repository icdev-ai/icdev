#!/usr/bin/env python3
# CUI // SP-CTI
"""Unified Platform Connector Registry — single-entry internet access layer for AI agents.

Consolidates fragmented per-engine platform fetches from:
  - tools/research/source_scanner.py  (Reddit, arXiv, GitHub, StackOverflow, news)
  - tools/innovation/web_scanner.py   (GitHub, NVD, HN, StackOverflow, SAM.gov)
  - tools/creative/source_scanner.py
  - tools/regulatory_foresight/source_scanner.py
  - tools/tech_radar/source_scanner.py

Into a shared adapter registry with:
  - Multi-backend routing (primary + ordered fallbacks)
  - Zero-config where possible (free APIs prioritized)
  - Health monitoring (connector_cli.py --doctor)
  - Graceful degradation on individual source failures

Patterns: D66 (ABC), D146 (circuit breaker), D-RES-3 (function registry).
"""

from tools.platform_connectors.registry import PlatformConnectorRegistry, get_registry

__all__ = ["PlatformConnectorRegistry", "get_registry"]
