
"""
Tier 2 — NSA ZIG: Zero Trust maturity scoring
Goal: Score a target's Zero Trust posture across the NSA ZIG 7 pillars, roll the pillars
      up to one weighted maturity score, name the maturity level, and find the weakest
      pillar to invest in next.

ICDEV implements the NSA **ZIG** (Zero Trust Implementation Guide, January 2026) at
/security/zig inside the Security Design Canvas (registry key `sdc`). ZIG defines **7
pillars**, **42 target capabilities**, and 91 activities (tools/security_canvas/constants.py:
ZIG_PILLARS / ZIG_CAPABILITIES / ZIG_MATURITY_LEVELS). The pillar scorer
(tools/security_canvas/zig_pillar_scorer.py::score_pillar) blends how many activities are
complete with how many capabilities are implemented, and aggregate_zig_score() rolls the
7 weighted pillars into an overall posture. This lab reproduces that scoring with the stdlib.
"""

# The NSA ZIG 7 pillars and their aggregation weights (sum to 1.0).
# (slug -> weight; the User/identity pillar carries the most weight.)
PILLAR_WEIGHTS = {
    "user":        0.20,   # Identity & Access Management (ICAM)
    "device":      0.15,   # Endpoint Security
    "network":     0.15,   # Network Segmentation & Isolation
    "application": 0.15,   # Secure Software Development & Runtime
    "data":        0.15,   # Data Protection & Governance
    "visibility":  0.10,   # Monitoring & Threat Detection
    "automation":  0.10,   # Speed, Scale & Orchestrated Response
}

# ZIG maturity bands (level, score_min). Checked high-to-low.
MATURITY_BANDS = (
    ("advanced",     0.75),
    ("intermediate", 0.50),
    ("basic",        0.25),
    ("preparation",  0.0),
)


# ── Step 1: Score one pillar ──────────────────────────────────────────────────

def pillar_score(activities_done: int, activities_total: int,
                 caps_impl: int, caps_total: int) -> float:
    """TODO: Score a single ZIG pillar (mirrors zig_pillar_scorer.score_pillar).

    activity_rate   = activities_done / activities_total   (0.0 if total == 0)
    capability_rate = caps_impl / caps_total               (0.0 if total == 0)
    score = 0.6 * activity_rate + 0.4 * capability_rate
    Round to 4 decimals.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Name the maturity level ───────────────────────────────────────────

def maturity_level(score: float) -> str:
    """TODO: Map a 0..1 score to its ZIG maturity level.

    Walk MATURITY_BANDS high-to-low and return the first level whose score_min the
    score meets or exceeds:
        >= 0.75 -> "advanced"; >= 0.50 -> "intermediate"; >= 0.25 -> "basic";
        else -> "preparation".
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Aggregate the 7 pillars ───────────────────────────────────────────

def aggregate_zig_score(pillar_scores: dict) -> float:
    """TODO: Weighted roll-up of pillar scores into one overall posture.

    pillar_scores maps pillar slug -> score (0..1). For each pillar present, use its
    PILLAR_WEIGHTS weight. Return:
        sum(weight * score) / sum(weight)      over the pillars provided
    so a partial set of pillars still normalizes correctly. Empty input -> 0.0.
    Round to 4 decimals.
    """
    # YOUR CODE HERE
    pass


# ── Step 4: Find the weakest pillar ───────────────────────────────────────────

def weakest_pillar(pillar_scores: dict) -> str | None:
    """TODO: Return the pillar slug with the LOWEST score (invest here next).

    On a tie, prefer the pillar that comes first in PILLAR_WEIGHTS order.
    Empty input -> None.
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    scores = {
        "user": pillar_score(6, 7, 6, 7),
        "device": pillar_score(2, 7, 1, 7),
        "data": pillar_score(4, 7, 3, 7),
    }
    print("pillar scores:", scores)
    overall = aggregate_zig_score(scores)
    print("overall:", overall, "->", maturity_level(overall))
    print("invest next:", weakest_pillar(scores))
