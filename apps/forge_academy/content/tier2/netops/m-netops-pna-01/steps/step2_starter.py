"""PNA predictor runner — BGP + Capacity with kanban integration.

The Academy sandbox cannot import `tools.*` and has no network, so this starter ships
stub predictors that return the documented output shape. Use them as-is.
"""


# ── Provided: sandbox stubs for the PNA predictors ────────────────────────────

def bgp_predict(as_number: int, prefix: str, lookback_hours: int = 24) -> dict:
    """Stub BGP instability predictor for one AS/prefix."""
    return {
        "instability_probability": 0.73,
        "confidence": 0.85,
        "horizon_hours": 4,
        "risk_level": "high",
        "features": {"as_number": as_number, "lookback_hours": lookback_hours},
        "recommended_action": f"Pre-stage backup route for prefix {prefix}",
    }


def capacity_predict_top_n(n: int = 3, lookback_hours: int = 24) -> list[dict]:
    """Stub capacity predictor: one prediction per link, highest utilization first."""
    links = [("core-sw-01:Gi0/1", 0.92), ("edge-rtr-01:Gi0/2", 0.81), ("dist-sw-02:Te1/1", 0.64)]
    return [
        {
            "link": link,
            "utilization": util,
            "risk_level": "high" if util >= 0.8 else "medium",
            "recommended_action": f"Add capacity or rebalance traffic on {link}",
        }
        for link, util in links[:n]
    ]


# ── Your code ─────────────────────────────────────────────────────────────────

def run_pna_analysis() -> list:
    """Run BGP and Capacity predictors and surface high-risk findings.

    Return a list of (predictor_name, prediction_dict) tuples, e.g.
        [("BGP", {...}), ("Capacity", {...}), ("Capacity", {...}), ("Capacity", {...})]
    """
    results = []

    # TODO: Run the BGP predictor for AS 64512, prefix "10.0.0.0/8", 24h lookback:
    #   bgp_result = bgp_predict(as_number=64512, prefix="10.0.0.0/8", lookback_hours=24)
    #   results.append(("BGP", bgp_result))

    # TODO: Run the Capacity predictor for the top-3 links and append each one:
    #   for link in capacity_predict_top_n(n=3, lookback_hours=24):
    #       results.append(("Capacity", link))

    # TODO: Print a risk summary: each predictor's risk_level and recommended_action

    # TODO: For each high-risk finding, build a kanban backlog card
    #   {"title": ..., "status": "backlog", "description": recommended_action}
    #   and print it (the sandbox has no network, so do not POST it)

    return results


if __name__ == "__main__":
    run_pna_analysis()
