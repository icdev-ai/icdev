
# Auto-grader — Design Canvas trio: DDC / ODC / NDC routing

NET = "design the network topology and routing with redundancy"
DATA = "map data lineage and run a pii scan on the schema"
OBS = "set up logging, monitoring and distributed tracing with mitre detection"
NONE = "plan the office holiday party"

# ── match_signals ─────────────────────────────────────────────────────────────
assert match_signals(NET) == {"ndc": 4, "ddc": 0, "odc": 0}, match_signals(NET)
assert match_signals(DATA) == {"ndc": 0, "ddc": 3, "odc": 0}, match_signals(DATA)
assert match_signals(OBS) == {"ndc": 0, "ddc": 0, "odc": 5}, match_signals(OBS)
assert match_signals(NONE) == {"ndc": 0, "ddc": 0, "odc": 0}

# ── classify_design_need ──────────────────────────────────────────────────────
assert classify_design_need(NET) == "ndc"
assert classify_design_need(DATA) == "ddc"
assert classify_design_need(OBS) == "odc"
# nothing fits -> None (registry-driven: don't force a fit)
assert classify_design_need(NONE) is None
# tie between ndc (routing) and ddc (quality) -> CANVAS_ORDER prefers ndc
assert classify_design_need("quality routing") == "ndc"

# ── route_design_request ──────────────────────────────────────────────────────
r = route_design_request(NET)
assert r == {
    "canvas": "ndc",
    "purpose": "Topology, routing, capacity, redundancy, EOL analysis.",
    "route": "/network",
    "matched": True,
}, f"unexpected: {r}"

r_data = route_design_request(DATA)
assert r_data["canvas"] == "ddc" and r_data["route"] == "/data"
assert r_data["purpose"] == "Data lineage, schemas, synthetic data, quality."

r_obs = route_design_request(OBS)
assert r_obs["canvas"] == "odc" and r_obs["route"] == "/observability"

# no match -> explicit unmatched decision
r_none = route_design_request(NONE)
assert r_none == {"canvas": None, "purpose": None, "route": None, "matched": False}

print("PASS: Design Canvas trio routing (NDC/DDC/ODC) with registry-verbatim purposes verified.")
