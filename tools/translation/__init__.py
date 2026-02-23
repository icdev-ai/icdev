#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Cross-Language Translation Module (Phase 43).

Provides LLM-assisted cross-language code translation across all 30
directional pairs of the 6 first-class languages (Python, Java,
JavaScript/TypeScript, Go, Rust, C#).

Architecture: 5-phase hybrid pipeline (D242):
  1. Extract (deterministic) - AST/regex -> IR JSON
  2. Type-Check (deterministic) - signature compatibility
  3. Translate (LLM) - chunk-based with feature maps
  4. Assemble (deterministic) - scaffold target project
  5. Validate+Repair (deterministic+LLM) - 8-check validation
"""

__version__ = "0.1.0"
