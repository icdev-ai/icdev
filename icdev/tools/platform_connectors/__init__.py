# CUI // SP-CTI
"""Unified platform connector registry (adapt-conn-01).

Provides a shared adapter registry over GitHub, Reddit, and Hacker News so
web_scanner.py and creative/source_scanner.py share one HTTP layer instead
of duplicating _safe_get() implementations.
"""
