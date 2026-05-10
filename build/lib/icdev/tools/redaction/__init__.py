# CUI // SP-CTI
"""ICDEV™ Redaction & Anonymization Module.

Provides PII detection and anonymization for sensitive government proposal data.
Uses Microsoft Presidio (NLP-based) + custom GovCon recognizers (regex/deny-list).

Key components:
    detector.py          — Presidio analyzer wrapper + custom recognizers
    anonymizer.py        — Anonymization engine with IL-aware operators
    govcon_recognizers.py — Contract#, CAGE, program name, pricing recognizers
    govcon_sanitizer.py  — Pre-LLM hook for proposal_drafting / requirement_extraction
    pulse_sanitizer.py   — Hardened Pulse case study sanitization
    registry.py          — Conversation-scoped real<->surrogate mapping
    db_scanner.py        — Scan proposal tables for unprotected sensitive fields

Configuration:
    args/redaction_config.yaml  — Global entity config, thresholds, IL-aware operators
    args/redaction_govcon.yaml  — GovCon-specific: program names, contract patterns, pricing
"""

__version__ = "1.0.0"
