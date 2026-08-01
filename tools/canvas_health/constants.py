# CUI // SP-CTI — ICDEV Canvas Health Constants
"""Canvas Health module-level constants.

Status vocabulary and issue labels for the per-canvas QA rollup rendered at
/canvas-health. Kept here rather than inline so the template, the blueprint and
any future gate all agree on one spelling.
"""

# ── Status vocabulary ─────────────────────────────────────────────────────────
# Ordered worst-first; the page sorts by this so broken canvases surface at top.
STATUS_RED = "red"       # a security problem: canvas needs an RLS bypass
STATUS_AMBER = "amber"   # incomplete: missing blueprint, E2E spec or IQE adapter
STATUS_GREEN = "green"   # all static completeness checks pass

STATUS_ORDER = (STATUS_RED, STATUS_AMBER, STATUS_GREEN)

# ── Issue labels ──────────────────────────────────────────────────────────────
ISSUE_NO_BLUEPRINT = "no blueprint"
ISSUE_NO_E2E = "no E2E spec"
ISSUE_NO_IQE = "no IQE adapter"
ISSUE_RLS_BYPASS = "RLS bypass needed"

# Only an RLS bypass is severe enough to make a canvas red; the rest are amber.
RED_ISSUES = frozenset({ISSUE_RLS_BYPASS})
