
# Auto-grader — NSA ZIG Zero Trust maturity scoring

# ── pillar_score (0.6*activity_rate + 0.4*capability_rate) ────────────────────
assert pillar_score(10, 10, 5, 5) == 1.0
assert pillar_score(5, 10, 2, 4) == 0.5
assert pillar_score(0, 0, 0, 0) == 0.0, "zero totals must not divide by zero"
assert pillar_score(3, 4, 1, 5) == 0.53
# activities weighted heavier than capabilities (0.6 vs 0.4)
assert pillar_score(10, 10, 0, 10) == 0.6
assert pillar_score(0, 10, 10, 10) == 0.4

# ── maturity_level (ZIG bands) ────────────────────────────────────────────────
assert maturity_level(1.0) == "advanced"
assert maturity_level(0.75) == "advanced", "0.75 is the advanced floor"
assert maturity_level(0.74) == "intermediate"
assert maturity_level(0.50) == "intermediate"
assert maturity_level(0.49) == "basic"
assert maturity_level(0.25) == "basic"
assert maturity_level(0.24) == "preparation"
assert maturity_level(0.0) == "preparation"

# ── aggregate_zig_score (weighted, normalized) ────────────────────────────────
all_half = {p: 0.5 for p in ["user", "device", "network", "application", "data", "visibility", "automation"]}
assert aggregate_zig_score(all_half) == 0.5, "uniform 0.5 across all 7 pillars -> 0.5"
# user pillar (0.20) at 1.0, device (0.15) at 0.0 -> 0.20 / 0.35
assert aggregate_zig_score({"user": 1.0, "device": 0.0}) == 0.5714
# single pillar normalizes to its own score
assert aggregate_zig_score({"user": 1.0}) == 1.0
assert aggregate_zig_score({}) == 0.0

# ── weakest_pillar ────────────────────────────────────────────────────────────
assert weakest_pillar({"user": 0.9, "device": 0.3, "data": 0.5}) == "device"
# tie -> first in PILLAR_WEIGHTS order (user before device)
assert weakest_pillar({"user": 0.5, "device": 0.5}) == "user"
assert weakest_pillar({}) is None

print("PASS: ZIG pillar scoring, maturity banding, weighted aggregate, and gap detection verified.")
