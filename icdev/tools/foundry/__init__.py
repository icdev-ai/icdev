# CUI // SP-CTI
"""ACF — Autonomous Capability Foundry (``tools/foundry``).

A 0->1 product factory that autonomously invents, designs, decomposes, and ships
brand-new ICDEV capabilities. Distinct from Oracle/Genesis reflexes (which improve
EXISTING tasks incrementally) — ACF creates net-new products.

This package is intentionally side-effect free on import. Submodules:

* ``novelty_gate`` — dedup a candidate concept vs the existing capability catalog
  (canvas registry + tool manifests + goal workflows) and reject incremental
  rehashes. THE differentiator from Oracle.
* ``learner`` — capture build outcomes from emitted kanban tasks into
  ``foundry_outcomes`` + ``foundry_concepts.status``, and tune
  ``args/foundry_config.yaml -> scoring.weights`` from shipped vs failed
  contrast (acf-learn-01).
* ``heuristic_learner`` — slower human-merged scorer-weight proposals
  (acf-ada-07), sibling to ``learner``.

Upstream siblings (``constants``, ``db.init_db``, ``synthesizer`` …) are added by
their own kanban tasks; ``novelty_gate`` degrades gracefully when they are absent.
"""

from __future__ import annotations

__all__ = ["novelty_gate", "learner", "heuristic_learner"]
