
"""
Tier 3 Mission 5: Canvas Selection
Goal: Build a CanvasSelector that recommends the right ICDEV Design Canvas
      based on an app description.

ICDEV is registry-driven: the authoritative canvas list lives in
args/component_registry.yaml (key / display_name / description) and is loaded by
tools/config/component_registry.py. The seven Design Canvases below are a
representative subset — their names and purposes are copied from that registry,
NOT invented. When a description matches nothing, do not guess: return the "NONE"
sentinel and consult the registry.
"""

# ── Canvas Definitions (subset of args/component_registry.yaml) ────────────────

CANVASES = {
    "NDC": {
        "name": "Network Design Canvas",
        "description": "Topology, routing, capacity, redundancy, EOL analysis",
        "keywords": [
            "network", "topology", "routing", "route", "router", "switch", "subnet",
            "vlan", "bandwidth", "redundancy", "redundant", "capacity", "link",
            "wan", "lan", "bgp", "ospf", "circuit", "eol", "connectivity", "uplink",
        ],
    },
    "SDC": {
        "name": "Security Design Canvas",
        "description": "Threat model, hardening, STIGs, attack-path analysis",
        "keywords": [
            "security", "threat", "hardening", "harden", "stig", "attack",
            "attack-path", "vulnerability", "cve", "exploit", "malware", "intrusion",
            "penetration", "zero-trust", "mitigation", "cwe", "waf", "encryption",
            "patch",
        ],
    },
    "PDC": {
        "name": "Pipeline Design Canvas",
        "description": "CI/CD pipeline management, worktree isolation, GitLab integration",
        "keywords": [
            "pipeline", "ci/cd", "cicd", "build", "gitlab", "worktree", "stage",
            "artifact", "release", "merge", "commit", "runner", "job", "deploy",
        ],
    },
    "BDC": {
        "name": "Boundary & Supply Chain Canvas",
        "description": "ATO boundary impact, supply chain SCRM, ISA lifecycle",
        "keywords": [
            "boundary", "ato", "supply chain", "supply-chain", "scrm", "isa",
            "interconnection", "accreditation", "sbom", "provenance", "vendor",
            "third-party", "c-scrm", "authorization boundary",
        ],
    },
    "DDC": {
        "name": "Data Design Canvas",
        "description": "Data lineage, schemas, synthetic data, quality",
        "keywords": [
            "data", "lineage", "schema", "synthetic", "dataset", "quality", "etl",
            "normalization", "records", "ingestion", "catalog", "governance",
            "database", "column",
        ],
    },
    "ODC": {
        "name": "Observability Design Canvas",
        "description": "Logging, monitoring, distributed tracing, SRE",
        "keywords": [
            "observability", "logging", "log", "monitoring", "monitor", "tracing",
            "trace", "metrics", "sre", "slo", "sli", "telemetry", "span", "uptime",
            "dashboard", "incident", "alerting",
        ],
    },
    "IDC": {
        "name": "Infrastructure Design Canvas",
        "description": "Cloud, IaC, Terraform, K8s manifest management",
        "keywords": [
            "infrastructure", "cloud", "iac", "terraform", "kubernetes", "k8s",
            "manifest", "container", "aws", "azure", "gcp", "helm", "provision",
            "provisioning", "vpc", "cluster", "node",
        ],
    },
}

# Returned when a description matches no canvas — the cue to consult the registry.
NO_MATCH = {
    "canvas": "NONE",
    "name": "No confident match",
    "confidence": 0.0,
    "reasoning": "No keywords matched — consult args/component_registry.yaml (the registry is the source of truth)",
}


# ── Step 1: Canvas Scorer ─────────────────────────────────────────────────────

def score_canvas(canvas_code: str, description: str) -> float:
    """TODO: Score how well a description matches a canvas.

    1. Get the canvas keyword list from CANVASES[canvas_code]["keywords"]
    2. Convert description to lowercase
    3. Count how many keywords appear in the (lowercased) description
    4. Return count / len(keywords) as the score (0.0 to 1.0)
       If keywords list is empty, return 0.0

    Do NOT raise — return 0.0 for unknown canvas codes.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Canvas Explainer ──────────────────────────────────────────────────

def explain_canvas(canvas_code: str) -> str:
    """TODO: Return the human-readable purpose for a canvas code.

    Return CANVASES[canvas_code]["description"] for known codes.
    Return "Unknown canvas" for codes not in CANVASES.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: CanvasSelector ────────────────────────────────────────────────────

class CanvasSelector:
    """Recommends the right ICDEV Design Canvas for an app description."""

    def select(self, description: str) -> dict:
        """TODO: Select the best canvas for a given description.

        1. Call score_canvas(code, description) for each canvas code in CANVASES
        2. Find the canvas with the highest score
        3. Tie-breaking: if multiple canvases tie for highest, prefer the one
           defined FIRST in CANVASES (registry order — dict insertion order)
        4. Calculate confidence: highest_score (already 0.0–1.0)
        5. If ALL scores are 0.0, do NOT guess — return a copy of NO_MATCH with
           an "all_scores" key added (canvas="NONE", confidence=0.0)
        6. Otherwise return:
           {
               "canvas": canvas_code,           ← e.g. "NDC"
               "name": CANVASES[canvas_code]["name"],
               "confidence": float,             ← highest score, 0.0–1.0
               "reasoning": f"Matched keyword(s) from {canvas_code} canvas",
               "all_scores": {code: score for each canvas},
           }
        """
        # YOUR CODE HERE
        pass

    def rank_canvases(self, description: str) -> list[dict]:
        """TODO: Return all canvases ranked by score (highest first).

        For each canvas, compute score_canvas(code, description).
        Return list of {"canvas": code, "name": ..., "score": float}
        sorted by score descending. Ties: keep registry (insertion) order.
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    selector = CanvasSelector()

    print("=== Canvas Selector ===\n")

    descriptions = [
        ("Network design", "Design redundant WAN routing and size link capacity across subnets"),
        ("Security design", "Threat model the system, apply STIG hardening, and map attack-path exposure to CVEs"),
        ("Pipeline", "Build a GitLab CI/CD pipeline with worktree isolation and build/deploy stages"),
        ("Data design", "Model the data schema, track data lineage, and generate synthetic datasets"),
        ("Observability", "Add distributed tracing, logging, and SLO monitoring dashboards for SRE"),
        ("Infrastructure", "Provision cloud infrastructure with Terraform and Kubernetes manifests on AWS"),
        ("Boundary", "Assess the ATO boundary impact and supply chain SCRM for a new vendor ISA"),
        ("No match", "the quick brown fox jumped over the lazy dog"),
    ]

    for label, desc in descriptions:
        result = selector.select(desc)
        print(f"{label}: {result['canvas']} ({result['confidence']:.0%}) — {result['reasoning']}")
