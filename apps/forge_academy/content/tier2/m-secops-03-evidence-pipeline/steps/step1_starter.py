
"""
Tier 2 SecOps-Eng Mission 3: Compliance Evidence Pipeline
Goal: Build a multi-stage evidence pipeline that scans, collects, and scores
      compliance evidence across multiple controls and systems.

This builds on the ICDEV tool contract you learned in T3-02 and the STIG parsing
from SecOps-02. Now you wire them into a pipeline.
"""


# ── Evidence Sources ──────────────────────────────────────────────────────────

SCAN_RESULTS = {
    ("ICDEV-Prod", "IA-2"): {"status": "pass", "scanner": "Nessus", "date": "2026-04-30"},
    ("ICDEV-Prod", "AC-2"): {"status": "pass", "scanner": "Nessus", "date": "2026-04-30"},
    ("ICDEV-Prod", "AU-2"): {"status": "pass", "scanner": "Nessus", "date": "2026-04-30"},
    ("ICDEV-Dev",  "IA-2"): {"status": "fail", "scanner": "Nessus", "date": "2026-04-30",
                              "finding": "V-220706: Internal MFA not enforced (CAT II)"},
    ("ICDEV-Dev",  "AC-2"): {"status": "pass", "scanner": "Nessus", "date": "2026-04-28"},
    ("ICDEV-Dev",  "AU-2"): {"status": "fail", "scanner": "Nessus", "date": "2026-04-28",
                              "finding": "V-220739: Audit log retention < 3 years (CAT III)"},
}

POLICY_DOCS = {
    "IA-2": {"doc": "SSP Section 3.5.1", "status": "current", "last_review": "2026-03-01"},
    "AC-2": {"doc": "SSP Section 3.1.2", "status": "current", "last_review": "2026-03-01"},
    "AU-2": {"doc": "SSP Section 3.3.1", "status": "outdated", "last_review": "2024-12-01"},
}

CONTROL_WEIGHTS = {"IA-2": 1.0, "AC-2": 0.9, "AU-2": 0.8}


# ── Step 1: Evidence Collector ────────────────────────────────────────────────

def collect_scan_evidence(system: str, control: str) -> dict:
    """TODO: Retrieve scan evidence for a (system, control) pair.

    Look up (system, control) in SCAN_RESULTS.
    If found: return {"type": "scan", "source": result["scanner"],
                      "status": result["status"], "date": result["date"],
                      "finding": result.get("finding")}
    If not found: return {"type": "scan", "source": "none", "status": "not_scanned",
                          "date": None, "finding": None}
    Do not raise.
    """
    # YOUR CODE HERE
    pass


def collect_policy_evidence(control: str) -> dict:
    """TODO: Retrieve policy documentation for a control.

    Look up control in POLICY_DOCS.
    If found: return {"type": "policy", "document": doc["doc"],
                      "status": doc["status"], "last_review": doc["last_review"]}
    If not found: return {"type": "policy", "document": None, "status": "missing",
                          "last_review": None}
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Evidence Scorer ───────────────────────────────────────────────────

def score_evidence(scan: dict, policy: dict, control: str) -> float:
    """TODO: Score evidence quality for a control (0.0 to 1.0).

    Scoring rules:
    - Scan pass + policy current:  1.0
    - Scan pass + policy outdated: 0.7
    - Scan pass + policy missing:  0.5
    - Scan fail (any policy):      0.2
    - Not scanned + policy current: 0.4
    - Not scanned + policy missing: 0.0

    Apply control weight from CONTROL_WEIGHTS (default 1.0 if not in dict):
    final_score = base_score * weight

    Return the weighted score (0.0 to 1.0).
    """
    # YOUR CODE HERE
    pass


# ── Step 3: EvidencePipeline ──────────────────────────────────────────────────

class EvidencePipeline:
    """Multi-stage compliance evidence pipeline."""

    def run(self, systems: list[str], controls: list[str]) -> dict:
        """TODO: Run the full evidence pipeline for a list of systems and controls.

        For each (system, control) combination:
        1. Collect scan evidence: collect_scan_evidence(system, control)
        2. Collect policy evidence: collect_policy_evidence(control)
        3. Score evidence: score_evidence(scan, policy, control)
        4. Build evidence record:
           {"system": system, "control": control,
            "scan": scan_result, "policy": policy_result,
            "score": score, "compliant": score >= 0.7}

        Return:
        {
            "run_date": str(date.today()),
            "systems": systems,
            "controls": controls,
            "records": [all evidence records],
            "summary": {
                "total": total records,
                "compliant": count where record["compliant"] == True,
                "non_compliant": count where record["compliant"] == False,
                "avg_score": average of all scores (round to 3 decimal places),
            }
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    pipeline = EvidencePipeline()
    result = pipeline.run(
        systems=["ICDEV-Prod", "ICDEV-Dev"],
        controls=["IA-2", "AC-2", "AU-2"],
    )
    print(f"Run date: {result['run_date']}")
    print(f"Total records: {result['summary']['total']}")
    print(f"Compliant: {result['summary']['compliant']}")
    print(f"Non-compliant: {result['summary']['non_compliant']}")
    print(f"Avg score: {result['summary']['avg_score']}")
    for rec in result["records"]:
        status = "✓" if rec["compliant"] else "✗"
        print(f"  {status} {rec['system']}/{rec['control']}: {rec['score']:.2f}")
