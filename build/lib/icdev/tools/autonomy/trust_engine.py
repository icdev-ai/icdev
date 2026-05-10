#!/usr/bin/env python3
# CUI // SP-CTI
"""Bayesian Trust Engine — Beta distribution trust scoring with Thompson Sampling.

Each action category has its own Beta(α, β) distribution. Trust grows with
successful outcomes and decays with failures. Thompson Sampling provides
controlled exploration — occasionally attempting actions above the current
mean when uncertainty is high.

Architecture Decisions:
    D-AE-1: Beta distributions per action category (conjugate prior for Bernoulli)
    D-AE-2: Thompson Sampling for explore/exploit (stdlib random.betavariate)
    D-AE-3: Per-category trust ceiling (spawn_child can never reach 1.0)
    D-AE-4: Safety invariant check before every action (unconditional block)
    D-AE-5: Append-only observation log (NIST AU-2)

Usage:
    python tools/autonomy/trust_engine.py --status --json
    python tools/autonomy/trust_engine.py --check create_tool --json
    python tools/autonomy/trust_engine.py --observe create_tool --success --json
    python tools/autonomy/trust_engine.py --observe create_tool --failure --json
    python tools/autonomy/trust_engine.py --sample create_tool --json
    python tools/autonomy/trust_engine.py --reset create_tool --json
"""

import argparse
import json
import random
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.common.helpers import now_iso  # noqa: E402
from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402


def _get_connection():
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def _gen_id(prefix: str = "ao") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _load_config() -> Dict[str, Any]:
    """Load autonomy config from YAML."""
    config_path = BASE_DIR / "args" / "autonomy_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------
def ensure_tables() -> None:
    """Create autonomy tables."""
    conn = _get_connection()
    try:
        # Trust state — allows UPDATE (posterior evolves)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_trust_state (
                category        TEXT PRIMARY KEY,
                alpha           REAL NOT NULL DEFAULT 2.0,
                beta            REAL NOT NULL DEFAULT 8.0,
                ceiling         REAL NOT NULL DEFAULT 1.0,
                total_observations INTEGER NOT NULL DEFAULT 0,
                total_successes INTEGER NOT NULL DEFAULT 0,
                total_failures  INTEGER NOT NULL DEFAULT 0,
                current_tier    TEXT DEFAULT 'observer',
                last_updated    TEXT NOT NULL
            )
        """)
        # Observations — append-only (NIST AU)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_observations (
                id              TEXT PRIMARY KEY,
                category        TEXT NOT NULL,
                outcome         TEXT NOT NULL CHECK(outcome IN ('success', 'failure')),
                alpha_before    REAL,
                beta_before     REAL,
                alpha_after     REAL,
                beta_after      REAL,
                mean_before     REAL,
                mean_after      REAL,
                tier_before     TEXT,
                tier_after      TEXT,
                source          TEXT,
                details         TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        # Actions — append-only (NIST AU)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_actions (
                id              TEXT PRIMARY KEY,
                category        TEXT NOT NULL,
                action_type     TEXT NOT NULL,
                decision        TEXT NOT NULL CHECK(decision IN (
                    'approved', 'denied', 'explored', 'safety_blocked'
                )),
                trust_mean      REAL,
                thompson_sample REAL,
                tier_required   TEXT,
                tier_current    TEXT,
                details         TEXT,
                coherence_passed INTEGER,
                remediation_applied INTEGER,
                outcome         TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        # Behavior log — append-only (NIST AU)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS autonomy_behavior_log (
                id              TEXT PRIMARY KEY,
                signal_type     TEXT NOT NULL,
                finding_id      TEXT,
                pr_url          TEXT,
                alpha_delta     REAL DEFAULT 0.0,
                beta_delta      REAL DEFAULT 0.0,
                category        TEXT,
                details         TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ao_category ON autonomy_observations(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_aa_category ON autonomy_actions(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_abl_signal ON autonomy_behavior_log(signal_type)")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trust state management
# ---------------------------------------------------------------------------
def get_trust_state(category: str) -> Dict[str, Any]:
    """Get current Beta(α, β) for a category. Initializes from config if missing."""
    ensure_tables()
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM autonomy_trust_state WHERE category = ?",
            (category,),
        ).fetchone()
        if row:
            d = dict(row)
            d["mean"] = d["alpha"] / (d["alpha"] + d["beta"])
            d["variance"] = (d["alpha"] * d["beta"]) / ((d["alpha"] + d["beta"]) ** 2 * (d["alpha"] + d["beta"] + 1))
            return d

        # Initialize from config
        config = _load_config()
        priors = config.get("priors", {})
        prior = priors.get(category, {"alpha": 2.0, "beta": 8.0, "ceiling": 1.0})
        alpha = prior.get("alpha", 2.0)
        beta_val = prior.get("beta", 8.0)
        ceiling = prior.get("ceiling", 1.0)
        mean = alpha / (alpha + beta_val)

        conn.execute(
            "INSERT INTO autonomy_trust_state "
            "(category, alpha, beta, ceiling, current_tier, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (category, alpha, beta_val, ceiling, _mean_to_tier(mean, config), now_iso()),
        )
        conn.commit()

        return {
            "category": category,
            "alpha": alpha,
            "beta": beta_val,
            "ceiling": ceiling,
            "mean": mean,
            "variance": (alpha * beta_val) / ((alpha + beta_val) ** 2 * (alpha + beta_val + 1)),
            "total_observations": 0,
            "total_successes": 0,
            "total_failures": 0,
            "current_tier": _mean_to_tier(mean, config),
        }
    finally:
        conn.close()


def get_all_trust_states() -> List[Dict[str, Any]]:
    """Get trust state for all categories."""
    config = _load_config()
    categories = list(config.get("priors", {}).keys())
    return [get_trust_state(c) for c in categories]


def observe(category: str, success: bool, source: str = "daemon", details: Dict = None) -> Dict[str, Any]:
    """Record an observation and update the posterior.

    success=True → α += 1 (posterior shifts right)
    success=False → β += 1 (posterior shifts left)
    """
    ensure_tables()
    config = _load_config()
    state = get_trust_state(category)
    ceiling = state["ceiling"]

    alpha_before = state["alpha"]
    beta_before = state["beta"]
    mean_before = state["mean"]
    tier_before = state["current_tier"]

    # Update posterior
    if success:
        alpha_after = alpha_before + 1.0
        beta_after = beta_before
    else:
        alpha_after = alpha_before
        beta_after = beta_before + 1.0

    # Apply ceiling
    mean_after = alpha_after / (alpha_after + beta_after)
    if mean_after > ceiling:
        # Scale back to ceiling
        total = alpha_after + beta_after
        alpha_after = ceiling * total
        beta_after = total - alpha_after
        mean_after = ceiling

    tier_after = _mean_to_tier(mean_after, config)

    # Write observation (append-only)
    obs_id = _gen_id("ao")
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO autonomy_observations "
            "(id, category, outcome, alpha_before, beta_before, alpha_after, "
            "beta_after, mean_before, mean_after, tier_before, tier_after, "
            "source, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                obs_id,
                category,
                "success" if success else "failure",
                alpha_before,
                beta_before,
                alpha_after,
                beta_after,
                mean_before,
                mean_after,
                tier_before,
                tier_after,
                source,
                json.dumps(details) if details else None,
                now_iso(),
            ),
        )

        # Update trust state
        conn.execute(
            "UPDATE autonomy_trust_state SET "
            "alpha = ?, beta = ?, total_observations = total_observations + 1, "
            "total_successes = total_successes + ?, total_failures = total_failures + ?, "
            "current_tier = ?, last_updated = ? "
            "WHERE category = ?",
            (
                alpha_after,
                beta_after,
                1 if success else 0,
                0 if success else 1,
                tier_after,
                now_iso(),
                category,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "observation_id": obs_id,
        "category": category,
        "outcome": "success" if success else "failure",
        "alpha": round(alpha_after, 2),
        "beta": round(beta_after, 2),
        "mean_before": round(mean_before, 4),
        "mean_after": round(mean_after, 4),
        "tier_before": tier_before,
        "tier_after": tier_after,
        "tier_changed": tier_before != tier_after,
    }


# ---------------------------------------------------------------------------
# Thompson Sampling — decision engine
# ---------------------------------------------------------------------------
def should_act(category: str, required_tier: str = None) -> Dict[str, Any]:
    """Decide whether to take autonomous action using Thompson Sampling.

    Returns decision: approved, denied, explored, or safety_blocked.
    """
    from tools.autonomy.kill_switch import is_killed

    if is_killed():
        return {"decision": "safety_blocked", "reason": "Kill switch active"}

    config = _load_config()

    state = get_trust_state(category)
    alpha = state["alpha"]
    beta_val = state["beta"]
    mean = state["mean"]
    current_tier = state["current_tier"]

    # Determine required tier for this category
    if not required_tier:
        required_tier = _category_to_required_tier(category, config)

    required_threshold = _tier_threshold(required_tier, config)

    # Thompson Sample
    ts_config = config.get("thompson_sampling", {})
    min_obs = ts_config.get("min_observations", 3)
    sample = random.betavariate(alpha, beta_val)

    decision = "denied"
    reason = ""

    if mean >= required_threshold:
        # Exploit — mean is high enough
        decision = "approved"
        reason = f"Mean {mean:.3f} >= threshold {required_threshold}"
    elif state["total_observations"] >= min_obs and sample >= required_threshold:
        # Explore — Thompson sample exceeded threshold
        decision = "explored"
        reason = f"Thompson sample {sample:.3f} >= threshold {required_threshold} (mean={mean:.3f})"
    else:
        decision = "denied"
        reason = f"Mean {mean:.3f} and sample {sample:.3f} both below threshold {required_threshold}"

    # Log action
    action_id = _gen_id("aa")
    conn = _get_connection()
    try:
        conn.execute(
            "INSERT INTO autonomy_actions "
            "(id, category, action_type, decision, trust_mean, thompson_sample, "
            "tier_required, tier_current, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                category,
                category,
                decision,
                mean,
                sample,
                required_tier,
                current_tier,
                json.dumps({"reason": reason}),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "action_id": action_id,
        "category": category,
        "decision": decision,
        "reason": reason,
        "trust_mean": round(mean, 4),
        "thompson_sample": round(sample, 4),
        "tier_current": current_tier,
        "tier_required": required_tier,
        "threshold": required_threshold,
    }


def check_safety_invariant(action_description: str) -> Tuple[bool, str]:
    """Check if an action violates safety invariants.

    Returns (is_safe, reason).
    """
    config = _load_config()
    never_auto = config.get("never_autonomous", [])

    action_lower = action_description.lower()

    safety_checks = {
        "delete_production_data": ["drop table", "truncate", "delete from"],
        "push_to_external_repos": ["git push", "push to remote"],
        "disable_security_gates": ["disable gate", "remove gate", "weaken gate"],
        "modify_kill_switch": ["kill_switch", "kill switch", "autonomy_kill"],
        "remove_hitl_gates": ["remove hitl", "remove human", "remove approval"],
        "bypass_cui_markings": ["skip cui", "remove cui", "bypass marking"],
        "exceed_token_budget": ["exceed budget", "unlimited token"],
        "delete_vcs_repos": ["delete repo", "remove repo", "gh repo delete", "glab repo delete"],
        "delete_backups": ["delete backup", "remove backup", "rm backup", "unlink backup"],
    }

    for invariant in never_auto:
        keywords = safety_checks.get(invariant, [])
        for kw in keywords:
            if kw in action_lower:
                return False, f"Safety invariant violated: {invariant} (matched: '{kw}')"

    return True, "Safe"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mean_to_tier(mean: float, config: Dict) -> str:
    """Convert Beta mean to tier name."""
    tiers = config.get("tiers", {})
    # Sort tiers by threshold descending to find highest matching
    sorted_tiers = sorted(
        tiers.items(),
        key=lambda t: t[1].get("threshold", 0),
        reverse=True,
    )
    for tier_name, tier_config in sorted_tiers:
        if mean >= tier_config.get("threshold", 0):
            return tier_name
    return "observer"


def _tier_threshold(tier_name: str, config: Dict) -> float:
    """Get threshold for a tier."""
    tiers = config.get("tiers", {})
    return tiers.get(tier_name, {}).get("threshold", 0.0)


def _category_to_required_tier(category: str, config: Dict) -> str:
    """Map action category to required tier."""
    tier_map = {
        "auto_fix": "contributor",
        "create_tool": "builder",
        "modify_config": "builder",
        "modify_goals": "architect",
        "modify_claude_md": "architect",
        "spawn_child": "creator",
        "commit_to_main": "contributor",
        "cross_engine_feed": "architect",
    }
    return tier_map.get(category, "builder")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Bayesian Trust Engine (D-AE-1)")
    parser.add_argument("--status", action="store_true", help="Show all trust states")
    parser.add_argument("--check", type=str, help="Check if action category is approved")
    parser.add_argument("--observe", type=str, help="Record observation for category")
    parser.add_argument("--success", action="store_true", help="Observation was success")
    parser.add_argument("--failure", action="store_true", help="Observation was failure")
    parser.add_argument("--sample", type=str, help="Draw Thompson sample for category")
    parser.add_argument("--reset", type=str, help="Reset category to prior")
    parser.add_argument("--safety-check", type=str, help="Check action against safety invariants")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    ensure_tables()

    if args.status:
        states = get_all_trust_states()
        if args.json:
            print(json.dumps({"trust_states": states}, indent=2))
        else:
            for s in states:
                print(
                    f"  {s['category']:20s} Beta({s['alpha']:.1f}, {s['beta']:.1f}) "
                    f"mean={s['mean']:.3f} tier={s['current_tier']} "
                    f"obs={s['total_observations']}"
                )

    elif args.check:
        result = should_act(args.check)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"  {result['category']}: {result['decision']} "
                f"(mean={result['trust_mean']:.3f}, sample={result['thompson_sample']:.3f})"
            )

    elif args.observe:
        if not args.success and not args.failure:
            print("Error: --observe requires --success or --failure")
            sys.exit(1)
        result = observe(args.observe, success=args.success, source="cli")
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"  {result['category']}: {result['outcome']} "
                f"mean {result['mean_before']:.3f} → {result['mean_after']:.3f} "
                f"tier {result['tier_before']} → {result['tier_after']}"
            )

    elif args.sample:
        state = get_trust_state(args.sample)
        sample = random.betavariate(state["alpha"], state["beta"])
        if args.json:
            print(
                json.dumps(
                    {
                        "category": args.sample,
                        "sample": round(sample, 4),
                        "mean": round(state["mean"], 4),
                        "alpha": state["alpha"],
                        "beta": state["beta"],
                    },
                    indent=2,
                )
            )
        else:
            print(f"  {args.sample}: sample={sample:.4f} mean={state['mean']:.4f}")

    elif args.reset:
        config = _load_config()
        prior = config.get("priors", {}).get(args.reset, {"alpha": 2.0, "beta": 8.0, "ceiling": 1.0})
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE autonomy_trust_state SET alpha = ?, beta = ?, "
                "total_observations = 0, total_successes = 0, total_failures = 0, "
                "current_tier = 'observer', last_updated = ? WHERE category = ?",
                (prior["alpha"], prior["beta"], now_iso(), args.reset),
            )
            conn.commit()
        finally:
            conn.close()
        print(
            json.dumps({"reset": args.reset, "alpha": prior["alpha"], "beta": prior["beta"]}, indent=2)
            if args.json
            else f"  Reset {args.reset} to Beta({prior['alpha']}, {prior['beta']})"
        )

    elif args.safety_check:
        is_safe, reason = check_safety_invariant(args.safety_check)
        if args.json:
            print(json.dumps({"action": args.safety_check, "safe": is_safe, "reason": reason}, indent=2))
        else:
            print(f"  {'SAFE' if is_safe else 'BLOCKED'}: {reason}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
