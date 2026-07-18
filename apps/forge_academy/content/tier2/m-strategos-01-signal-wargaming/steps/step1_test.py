
# Auto-grader — Strategos: signal scoring, prioritization, wargaming

# ── score_signal ──────────────────────────────────────────────────────────────
assert score_signal({"domain": "military", "raw_score": 0.9, "source_grade": "A", "age_days": 0}) == 0.9
# economic(0.8) x grade B(0.8) x one half-life(0.5): 0.9*0.64*0.5
assert score_signal({"domain": "economic", "raw_score": 0.9, "source_grade": "B", "age_days": 30}) == 0.288
# information(0.5) x grade C(0.6)
assert score_signal({"domain": "information", "raw_score": 0.5, "source_grade": "C", "age_days": 0}) == 0.15
# unknown domain -> 0.5 weight
assert score_signal({"domain": "cyber", "raw_score": 1.0, "source_grade": "A", "age_days": 0}) == 0.5
# unknown / unreliable grade -> 0.0
assert score_signal({"domain": "military", "raw_score": 1.0, "source_grade": "Z", "age_days": 0}) == 0.0
# missing age defaults to 0 (no decay)
assert score_signal({"domain": "military", "raw_score": 1.0, "source_grade": "A"}) == 1.0

# ── prioritize_signals ────────────────────────────────────────────────────────
sigs = [
    {"domain": "military", "raw_score": 0.9, "source_grade": "A", "age_days": 0},     # 0.9
    {"domain": "economic", "raw_score": 0.9, "source_grade": "B", "age_days": 30},    # 0.288
    {"domain": "information", "raw_score": 0.5, "source_grade": "C", "age_days": 0},   # 0.15
]
top = prioritize_signals(sigs, 2)
assert len(top) == 2
assert [s["domain"] for s in top] == ["military", "economic"], f"order wrong: {[s['domain'] for s in top]}"
assert top[0]["score"] == 0.9 and top[1]["score"] == 0.288
# inputs not mutated
assert "score" not in sigs[0], "score_signal/prioritize must not mutate inputs"
assert prioritize_signals(sigs, 0) == []
# top_n larger than list returns all, still sorted
assert len(prioritize_signals(sigs, 99)) == 3

# ── score_coa ─────────────────────────────────────────────────────────────────
assert score_coa({"feasibility": 0.9, "impact": 0.8, "risk": 0.3}) == 0.62
assert score_coa({"feasibility": 1.0, "impact": 1.0, "risk": 0.0}) == 0.8
# all-risk COA clamps at 0.0 (never negative)
assert score_coa({"feasibility": 0.0, "impact": 0.0, "risk": 1.0}) == 0.0

# ── lanchester_square ─────────────────────────────────────────────────────────
r = lanchester_square(1.0, 10, 1.0, 8)
assert r["attacker_power"] == 100 and r["defender_power"] == 64
assert r["winner"] == "attacker"
assert lanchester_square(1.0, 5, 4.0, 5)["winner"] == "defender", "few strong vs many weak: square law favors mass"
assert lanchester_square(1.0, 10, 4.0, 5)["winner"] == "draw"

print("PASS: Strategos signal scoring, prioritization, and Lanchester wargaming verified.")
