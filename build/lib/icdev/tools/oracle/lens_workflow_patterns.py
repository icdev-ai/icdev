# CUI // SP-CTI
"""Oracle Workflow Pattern Lens — Mine frequent multi-step tool sequences.

Re-exports WorkflowPatternLens from tools.oracle.lenses.lens_workflow_patterns.

Analyzes audit_trail and kanban_tasks to surface:
  - Frequent 3–5 step sequential event patterns (sliding window, hash counting)
  - Tool pairs with >80% co-occurrence rate (composition candidates)
  - Tasks that fail then succeed (backlog→in_progress→backlog→done) as
    self-healing candidates

All mining is deterministic — zero LLM calls, scanner-tier.
"""

from tools.oracle.lenses.lens_workflow_patterns import WorkflowPatternLens  # noqa: F401

__all__ = ["WorkflowPatternLens"]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import logging
    import sys

    logging.basicConfig(level=logging.WARNING)
    lens = WorkflowPatternLens()
    preds = lens.run()
    print(json.dumps([p.to_dict() for p in preds], indent=2, default=str))
    print(f"\n# {len(preds)} prediction(s) generated", file=sys.stderr)
