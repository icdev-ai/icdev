
"""
Tier 2 — The Design Canvas trio: DDC, ODC, NDC
Goal: Route a design need to the RIGHT design canvas — Data, Observability, or Network —
      the way ICDEV's registry-driven taxonomy does, and return None when nothing fits
      (go back to the registry; never force a fit).

ICDEV is registry-driven: canvases are declared in args/component_registry.yaml. Three of the
Design Canvases:

  * NDC — Network Design Canvas  (key `ndc`, /network, module tools.network.blueprint)
        "Topology, routing, capacity, redundancy, EOL analysis."
  * DDC — Data Design Canvas     (key `ddc`, /data, module tools.data_canvas.blueprint)
        "Data lineage, schemas, synthetic data, quality."
  * ODC — Observability Design Canvas (key `odc`, /observability, tools.observability_canvas.blueprint)
        "Logging, monitoring, distributed tracing, SRE."

The purposes below are quoted VERBATIM from the registry — the same source the Tier-3 canvas-
selection lesson uses. There is no fixed "7 canvas" model; when a need matches none of these,
the honest answer is None. This lab models that selection with the stdlib.
"""

import re

# Registry `description` for each canvas — verbatim.
CANVAS_PURPOSE = {
    "ndc": "Topology, routing, capacity, redundancy, EOL analysis.",
    "ddc": "Data lineage, schemas, synthetic data, quality.",
    "odc": "Logging, monitoring, distributed tracing, SRE.",
}
# Registry `url_prefix` for each canvas.
CANVAS_ROUTE = {"ndc": "/network", "ddc": "/data", "odc": "/observability"}
# Tie-break order (registry order of the three).
CANVAS_ORDER = ("ndc", "ddc", "odc")

# Signal keywords per canvas, grounded in each canvas's real capabilities.
CANVAS_SIGNALS = {
    "ndc": {"topology", "routing", "capacity", "redundancy", "eol", "subnet", "bgp",
            "bandwidth", "network"},
    "ddc": {"lineage", "schema", "synthetic", "dataset", "pii", "mesh", "mapping",
            "quality", "governance"},
    "odc": {"logging", "monitoring", "tracing", "sre", "observability", "mitre",
            "detection", "sigma", "alert", "siem", "runbook"},
}


def _tokens(text: str) -> set:
    """Lowercased alphanumeric word tokens of `text`."""
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


# ── Step 1: Count signal hits per canvas ──────────────────────────────────────

def match_signals(description: str) -> dict:
    """TODO: Return {"ndc": n, "ddc": n, "odc": n} — how many of each canvas's signal
    keywords appear as tokens in `description` (case-insensitive).

    Use _tokens(description) and intersect with each canvas's CANVAS_SIGNALS set;
    n is the size of that intersection.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Classify the design need ──────────────────────────────────────────

def classify_design_need(description: str) -> str | None:
    """TODO: Route a free-text design need to a canvas key, or None.

    1. scores = match_signals(description)
    2. If every score is 0 -> return None (nothing fits; go back to the registry).
    3. Otherwise return the canvas with the MOST hits. On a tie, prefer the canvas that
       comes first in CANVAS_ORDER.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Route the request ─────────────────────────────────────────────────

def route_design_request(description: str) -> dict:
    """TODO: Resolve a design need to a full routing decision.

    key = classify_design_need(description)
      * key is None -> {"canvas": None, "purpose": None, "route": None, "matched": False}
      * else        -> {"canvas": key, "purpose": CANVAS_PURPOSE[key],
                        "route": CANVAS_ROUTE[key], "matched": True}
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    for need in [
        "design the network topology and routing with redundancy",
        "map data lineage and run a pii scan on the schema",
        "set up logging, monitoring and distributed tracing with mitre detection",
        "plan the office holiday party",
    ]:
        print(f"{need[:45]:47s} -> {route_design_request(need)}")
