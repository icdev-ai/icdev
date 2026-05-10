"""
Tier 3 Mission 5: Canvas Selection
Goal: Build a CanvasSelector that recommends the right ICDEV design canvas
      based on an app description.
"""

# ── Canvas Definitions ────────────────────────────────────────────────────────

CANVASES = {
    "NDC": {
        "name": "Network Data Canvas",
        "description": "Network topology, traffic analysis, infrastructure monitoring",
        "keywords": [
            "network", "packet", "traffic", "topology", "firewall", "router", "switch",
            "bandwidth", "latency", "infrastructure", "connectivity", "subnet", "vlan",
            "interface", "port", "protocol", "tcp", "udp", "ip", "dns", "dhcp",
        ],
    },
    "SDC": {
        "name": "Signal Data Canvas",
        "description": "SIGINT, sensor streams, time-series signal processing",
        "keywords": [
            "signal", "sensor", "sigint", "frequency", "spectrum", "waveform", "stream",
            "time-series", "timeseries", "telemetry", "rf", "radar", "sonar", "satellite",
            "emission", "intercept", "geolocation", "antenna", "broadcast",
        ],
    },
    "PDC": {
        "name": "Pattern Data Canvas",
        "description": "Behavioral patterns, anomaly detection, threat intelligence",
        "keywords": [
            "pattern", "anomaly", "behavior", "threat", "detection", "classification",
            "cluster", "outlier", "baseline", "deviation", "indicator", "ioc", "ttp",
            "malware", "intrusion", "correlation", "alert", "signature", "rule",
        ],
    },
    "BDC": {
        "name": "Business Data Canvas",
        "description": "Finance, contracts, procurement, business intelligence",
        "keywords": [
            "contract", "finance", "procurement", "budget", "invoice", "vendor",
            "proposal", "rfp", "acquisition", "cost", "revenue", "spend", "award",
            "gsa", "sam", "naics", "solicitation", "bid", "price", "payment",
        ],
    },
    "DDC": {
        "name": "Domain Data Canvas",
        "description": "Domain-specific knowledge bases, expertise systems",
        "keywords": [
            "knowledge", "domain", "expertise", "rag", "document", "policy",
            "regulation", "standard", "compliance", "stig", "nist", "fedramp",
            "framework", "guide", "manual", "procedure", "reference", "catalog",
        ],
    },
    "ODC": {
        "name": "Operations Data Canvas",
        "description": "Workflows, task automation, operational procedures",
        "keywords": [
            "workflow", "pipeline", "automation", "task", "schedule", "queue",
            "orchestration", "job", "process", "step", "stage", "cicd", "deploy",
            "build", "release", "monitor", "alert", "incident", "runbook",
        ],
    },
    "IDC": {
        "name": "Intelligence Data Canvas",
        "description": "Multi-source fusion, strategic analysis, decision support",
        "keywords": [
            "intelligence", "fusion", "analysis", "strategic", "decision", "report",
            "assessment", "estimate", "all-source", "collection", "product", "brief",
            "dashboard", "summary", "insight", "forecast", "risk", "threat assessment",
        ],
    },
}


# ── Step 1: Canvas Scorer ─────────────────────────────────────────────────────

def score_canvas(canvas_code: str, description: str) -> float:
    """TODO: Score how well a description matches a canvas.

    1. Get the canvas keyword list from CANVASES[canvas_code]["keywords"]
    2. Convert description to lowercase
    3. Count how many keywords appear in the description
    4. Return count / len(keywords) as the score (0.0 to 1.0)
       If keywords list is empty, return 0.0

    Do NOT raise — return 0.0 for unknown canvas codes.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Canvas Explainer ──────────────────────────────────────────────────

def explain_canvas(canvas_code: str) -> str:
    """TODO: Return the human-readable description for a canvas code.

    Return CANVASES[canvas_code]["description"] for known codes.
    Return "Unknown canvas" for codes not in CANVASES.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: CanvasSelector ────────────────────────────────────────────────────

class CanvasSelector:
    """Recommends the right ICDEV design canvas for an app description."""

    def select(self, description: str) -> dict:
        """TODO: Select the best canvas for a given description.

        1. Call score_canvas(code, description) for each canvas code in CANVASES
        2. Find the canvas with the highest score
        3. Tie-breaking: if multiple canvases tie for highest, prefer "IDC"
        4. Calculate confidence: highest_score (already 0.0–1.0)
        5. If all scores are 0.0, default to "IDC" with confidence=0.0
        6. Return:
           {
               "canvas": canvas_code,           ← e.g. "NDC"
               "name": CANVASES[canvas_code]["name"],
               "confidence": float,             ← highest score, 0.0–1.0
               "reasoning": f"Matched {N} keyword(s) from {canvas_code} canvas",
               "all_scores": {code: score for each canvas},
           }
        """
        # YOUR CODE HERE
        pass

    def rank_canvases(self, description: str) -> list[dict]:
        """TODO: Return all canvases ranked by score (highest first).

        For each canvas, compute score_canvas(code, description).
        Return list of {"canvas": code, "name": ..., "score": float}
        sorted by score descending. Ties: IDC before others.
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    selector = CanvasSelector()

    print("=== Canvas Selector ===\n")

    descriptions = [
        ("Network monitoring", "Monitor packet loss across DoD network segments and routing tables"),
        ("Compliance docs", "Build a RAG system over STIG documents and NIST 800-53 controls"),
        ("Pipeline agent", "Automate the CI/CD pipeline with build, test, deploy stages"),
        ("Threat analysis", "Detect anomalies and classify threat patterns in user behavior"),
        ("Contract intel", "Analyze SAM.gov contracts and track vendor procurement spending"),
    ]

    for label, desc in descriptions:
        result = selector.select(desc)
        print(f"{label}: {result['canvas']} ({result['confidence']:.0%}) — {result['reasoning']}")
