# CUI // SP-CTI
"""Workflow Discipline Engine — LLM-agnostic PLAN→APPLY→UNIFY loop (Phase 66).

Adapts PAUL (Plan-Apply-Unify Loop) concepts into deterministic Python tools
that any AI coding assistant can invoke via FORGE framework.

Design decisions:
  D-WF-1: Loop state stored in DB, not files — multi-user, multi-platform
  D-WF-2: Tools are deterministic Python scripts (FORGE pattern)
  D-WF-3: Acceptance criteria tracked per-loop with PASS/FAIL/SKIP
  D-WF-4: Single next action uses weighted priority scoring (D21 pattern)
  D-WF-5: Handoff documents are markdown consumable by any AI platform
  D-WF-6: Process verification queries audit_trail (existing infrastructure)
  D-WF-7: Reconciliation records planned-vs-actual delta for NIST AU evidence
"""
