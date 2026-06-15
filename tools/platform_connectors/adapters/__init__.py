#!/usr/bin/env python3
# CUI // SP-CTI
"""Platform-specific adapter implementations.

Each adapter targets a single backend (e.g. github_rest, hackernews_firebase,
stackoverflow_v2, reddit_json, youtube_data_api, youtube_ytdlp).

Adapters in the same platform group are tried in priority order; the first
successful response wins (multi-backend routing with fallbacks).
"""
