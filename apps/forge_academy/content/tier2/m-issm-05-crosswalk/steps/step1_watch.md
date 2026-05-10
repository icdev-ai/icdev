# Cross-Framework Crosswalk — FedRAMP + CMMC + RMF

Your organization operates across multiple compliance regimes simultaneously. FedRAMP for cloud services. CMMC Level 2 for defense contracts. NIST RMF for federal systems. The control sets overlap — but no human can track the mapping manually. ICDEV's crosswalk engine does it automatically.

## What You'll See

Watch ICDEV perform a cross-framework crosswalk for ICDEV-Prod:

**Control Mapping Results**
```
NIST 800-53 IA-2 → FedRAMP Moderate IA-2 → CMMC AC.1.001 + AC.1.002
NIST 800-53 AC-2 → FedRAMP Moderate AC-2 → CMMC AC.2.005 + AC.2.006
NIST 800-53 AU-2 → FedRAMP Moderate AU-2 → CMMC AU.2.041 + AU.2.042
```

**Evidence Reuse Analysis**
47 RMF controls mapped. Evidence reuse opportunities found:
- 31 controls: same evidence satisfies ALL three frameworks simultaneously
- 12 controls: FedRAMP + RMF share evidence; CMMC needs additional artifact
- 4 controls: unique requirements per framework — 3 separate evidence sets needed

**Savings Estimate**
Without crosswalk: 141 evidence collection tasks (47 × 3 frameworks)
With crosswalk: 63 unique tasks — **55% reduction in assessment effort**

**Auto-populated Framework Reports**
- FedRAMP System Security Plan: 31/47 controls auto-populated from existing evidence
- CMMC Self-Assessment: 29/32 practices evidenced from existing artifacts
- RMF SSP: 47/47 controls documented (your primary framework)

One evidence collection effort. Three compliance regimes covered.
