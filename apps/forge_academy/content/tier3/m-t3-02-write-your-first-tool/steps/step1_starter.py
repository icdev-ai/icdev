
"""
Tier 3 Mission 2: Write Your First ICDEV Tool
Goal: Build a compliance evidence collector tool following the ICDEV tool contract.

ICDEV Tool Contract:
  - Does one job
  - Returns {"status": "ok"|"error"|"partial", "data": {...}, "error": None|"msg"}
  - Handles its own errors (never raises)
  - Deterministic
"""

from datetime import date

# ── Evidence Database (simulated) ─────────────────────────────────────────────

EVIDENCE_DB = {
    ("ICDEV-Prod", "IA-2"): [
        {"type": "config", "description": "MFA enforced via Duo Security for all privileged accounts"},
        {"type": "scan",   "description": "Nessus scan 2026-04-30: IA-2 compliant, no findings"},
        {"type": "policy", "description": "System Security Plan Section 3.5.1 documents IA-2 implementation"},
    ],
    ("ICDEV-Prod", "AC-2"): [
        {"type": "config", "description": "IAM role-based access control configured in AWS GovCloud"},
        {"type": "log",    "description": "Account review log 2026-04-01: 47 accounts reviewed, 3 disabled"},
    ],
    ("ICDEV-Dev", "IA-2"): [
        {"type": "config", "description": "MFA enforced, but only for external access — internal lacks MFA"},
        {"type": "finding", "description": "STIG V-220706: Internal privileged access lacks MFA (CAT II)"},
    ],
    ("ICDEV-Dev", "AC-2"): [
        {"type": "config", "description": "RBAC configured"},
        {"type": "scan",   "description": "Nessus 2026-04-28: AC-2 compliant"},
    ],
    ("ICDEV-Test", "IA-2"): [],  # No evidence — test environment not yet assessed
}

COMPLIANT_TYPES = {"config", "scan", "policy", "log"}
NON_COMPLIANT_TYPES = {"finding"}

TOOL_META = {
    "tool": "compliance_evidence_collector",
    "version": "1.0",
}


# ── Step 1: Evidence Lookup ───────────────────────────────────────────────────

def lookup_evidence(system_name: str, control_id: str) -> list[dict]:
    """TODO: Look up evidence items for a (system_name, control_id) pair.

    Look up (system_name, control_id) in EVIDENCE_DB.
    Return the list of evidence items (empty list if not found).
    Do NOT raise — return [] for unknown systems/controls.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Compliance Status Deriver ────────────────────────────────────────

def derive_compliance_status(evidence: list[dict]) -> str:
    """TODO: Derive compliance status from evidence items.

    Rules (in priority order):
    1. If evidence is empty → return "partial" (insufficient evidence)
    2. If ANY evidence has type in NON_COMPLIANT_TYPES → return "non-compliant"
    3. If ALL evidence has type in COMPLIANT_TYPES → return "compliant"
    4. Otherwise → return "partial"
    """
    # YOUR CODE HERE
    pass


# ── Step 3: collect_evidence ──────────────────────────────────────────────────

def collect_evidence(system_name: str, control_id: str) -> dict:
    """TODO: Main tool function — collect and return structured evidence.

    Follow the ICDEV tool contract:
    1. Look up evidence using lookup_evidence(system_name, control_id)
    2. Derive compliance status using derive_compliance_status(evidence)
    3. Build and return the result dict:
       {
           "status": "ok" if evidence exists else "partial",
           "data": {
               "control": control_id,
               "system": system_name,
               "evidence": evidence,
               "compliance_status": <derived status>,
               "evidence_count": len(evidence),
               "evidence_date": str(date.today()),
           },
           "error": None,
           "meta": TOOL_META,
       }

    Edge case: if (system_name, control_id) is not in EVIDENCE_DB at all
    (i.e., the key is completely missing, not just empty), return:
       {"status": "partial", "data": {"system": system_name, "control": control_id,
        "message": "No evidence records found — system may not have been assessed."}, "error": None, "meta": TOOL_META}
    """
    # YOUR CODE HERE
    pass


# ── Step 4: run_batch ─────────────────────────────────────────────────────────

def run_batch(requests: list[dict]) -> dict:
    """TODO: Process multiple evidence collection requests.

    Each request is {"system": str, "control": str}.
    Call collect_evidence(system, control) for each.

    Return:
    {
        "status": "ok",
        "results": [list of collect_evidence results],
        "summary": {
            "total": len(requests),
            "compliant": count of results with data.compliance_status == "compliant",
            "non_compliant": count with status == "non-compliant",
            "partial": count with status == "partial" or data.compliance_status in ("partial", ...),
        },
        "meta": TOOL_META,
    }
    """
    # YOUR CODE HERE
    pass


# Test
if __name__ == "__main__":
    print("=== Compliance Evidence Collector ===\n")

    # Single evidence collection
    result = collect_evidence("ICDEV-Prod", "IA-2")
    print(f"ICDEV-Prod / IA-2:")
    print(f"  Status: {result['status']}")
    print(f"  Compliance: {result['data']['compliance_status']}")
    print(f"  Evidence items: {result['data']['evidence_count']}")

    result2 = collect_evidence("ICDEV-Dev", "IA-2")
    print(f"\nICDEV-Dev / IA-2:")
    print(f"  Status: {result2['status']}")
    print(f"  Compliance: {result2['data']['compliance_status']}")

    result3 = collect_evidence("ICDEV-Unknown", "AC-3")
    print(f"\nICDEV-Unknown / AC-3:")
    print(f"  Status: {result3['status']}")

    # Batch run
    batch = run_batch([
        {"system": "ICDEV-Prod",  "control": "IA-2"},
        {"system": "ICDEV-Dev",   "control": "IA-2"},
        {"system": "ICDEV-Test",  "control": "IA-2"},
    ])
    print(f"\nBatch results:")
    print(f"  Total: {batch['summary']['total']}")
    print(f"  Compliant: {batch['summary']['compliant']}")
    print(f"  Non-compliant: {batch['summary']['non_compliant']}")
    print(f"  Partial: {batch['summary']['partial']}")
