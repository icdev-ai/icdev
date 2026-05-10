"""
Tier 2 DevOps Mission 3: GitOps Automation Agent
Goal: Build a GitOps agent that detects drift between desired state (Git)
      and actual state (deployed), then generates a reconciliation plan.

GitOps rule: Git is the single source of truth.
Drift = any difference between what's in Git and what's deployed.
"""

# ── State Definitions ─────────────────────────────────────────────────────────

DESIRED_STATE = {
    "services": {
        "api-gateway":  {"image": "icdev/api-gateway:2.1.0",  "replicas": 3, "env": "prod"},
        "auth-service": {"image": "icdev/auth-service:1.8.2", "replicas": 2, "env": "prod"},
        "rag-service":  {"image": "icdev/rag-service:3.0.1",  "replicas": 2, "env": "prod"},
        "db-proxy":     {"image": "icdev/db-proxy:1.2.0",     "replicas": 1, "env": "prod"},
    }
}

ACTUAL_STATE = {
    "services": {
        "api-gateway":  {"image": "icdev/api-gateway:2.0.9",  "replicas": 3, "env": "prod"},  # version drift
        "auth-service": {"image": "icdev/auth-service:1.8.2", "replicas": 1, "env": "prod"},  # replica drift
        "rag-service":  {"image": "icdev/rag-service:3.0.1",  "replicas": 2, "env": "prod"},  # in sync
        # db-proxy missing entirely — not deployed
    }
}

DRIFT_PRIORITY = {"image": "HIGH", "replicas": "MEDIUM", "missing": "HIGH", "extra": "LOW"}


# ── Step 1: Drift Detector ────────────────────────────────────────────────────

def detect_drift(desired: dict, actual: dict) -> list[dict]:
    """TODO: Compare desired vs actual service states and return drift items.

    For each service in desired["services"]:
    1. If service not in actual["services"]:
       Add drift item: {"service": name, "type": "missing", "priority": "HIGH",
                        "desired": desired_config, "actual": None}
    2. If service in both: compare each field (image, replicas, env):
       If field value differs:
         Add drift item: {"service": name, "type": field_name, "priority": DRIFT_PRIORITY[field_name],
                          "desired": desired_value, "actual": actual_value}

    Also check for services in actual but not in desired:
       Add drift item: {"service": name, "type": "extra", "priority": "LOW",
                        "desired": None, "actual": actual_config}

    Return list of all drift items (empty if no drift).
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Reconciliation Planner ───────────────────────────────────────────

def plan_reconciliation(drift_items: list[dict]) -> list[dict]:
    """TODO: Generate reconciliation actions for each drift item.

    For each drift item, generate an action:
    - type "missing":  {"action": "deploy", "service": name, "reason": f"Deploy {name} — not found in cluster"}
    - type "image":    {"action": "update",  "service": name, "reason": f"Update image: {actual} → {desired}"}
    - type "replicas": {"action": "scale",   "service": name, "reason": f"Scale replicas: {actual} → {desired}"}
    - type "extra":    {"action": "review",  "service": name, "reason": f"Review {name} — not in desired state, may need removal"}
    - type "env":      {"action": "update",  "service": name, "reason": f"Update env: {actual} → {desired}"}

    Return list of action dicts sorted by priority: HIGH first, then MEDIUM, then LOW.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: GitOpsAgent ───────────────────────────────────────────────────────

class GitOpsAgent:
    """Detects and plans remediation for GitOps drift."""

    def reconcile(self, desired: dict, actual: dict) -> dict:
        """TODO: Run full drift detection and reconciliation planning.

        1. detect_drift(desired, actual) → drift_items
        2. plan_reconciliation(drift_items) → actions
        3. Return:
        {
            "in_sync": len(drift_items) == 0,
            "drift_count": len(drift_items),
            "high_priority": count of drift items with priority "HIGH",
            "drift": drift_items,
            "actions": actions,
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    agent = GitOpsAgent()
    report = agent.reconcile(DESIRED_STATE, ACTUAL_STATE)
    print(f"In sync: {report['in_sync']}")
    print(f"Drift items: {report['drift_count']}")
    print(f"High priority: {report['high_priority']}")
    print("\nDrift detected:")
    for d in report["drift"]:
        print(f"  [{d['priority']}] {d['service']}: {d['type']} (desired={d['desired']}, actual={d['actual']})")
    print("\nReconciliation plan:")
    for a in report["actions"]:
        print(f"  {a['action'].upper()} {a['service']}: {a['reason']}")
