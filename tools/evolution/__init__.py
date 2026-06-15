# CUI // SP-CTI
"""NOVA SELA — Skill & Goal Self-Evolution Engine.

Evolves ICDEV's own .agents/skills/ and hardprompts/ using GEPA-style
text mutation + LLM judge fitness scoring.

Pipeline:
  eval_builder   → build eval dataset from kanban history
  fitness        → multi-dimensional LLM judge scoring
  artifact_evolver → orchestrate mutation → gate → promote
"""
