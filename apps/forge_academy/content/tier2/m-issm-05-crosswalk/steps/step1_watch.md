---
ontology_id: icdev:mission:m-issm-05-crosswalk:step:1
step_class: icdev:Lesson
---

# Cross-Framework Crosswalk — FedRAMP + CMMC + RMF

Your organization operates across multiple compliance regimes simultaneously. FedRAMP for cloud services. CMMC Level 2 for defense contracts. NIST RMF for federal systems. The control sets overlap — but no human can track the mapping manually. ICDEV's crosswalk engine does it automatically.

## What You'll See

Watch ICDEV perform a cross-framework crosswalk for ICDEV-Prod:

**Control Mapping Results**
```
NIST 800-53 IA-2 → FedRAMP Moderate IA-2 → NIST 800-171 3.5.1 → CMMC IA.L2-3.5.1
NIST 800-53 AC-2 → FedRAMP Moderate AC-2 → NIST 800-171 3.1.1 → CMMC AC.L2-3.1.1
NIST 800-53 AU-2 → FedRAMP Moderate AU-2 → NIST 800-171 3.3.1 → CMMC AU.L2-3.3.1
```

CMMC 2.0 practice IDs follow the NIST SP 800-171 requirement they come from (`<family>.L<level>-<800-171 id>`). The older CMMC 1.0 style (`AC.1.001`) has been retired.

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

*(The counts above are an illustrative scenario.)*

## Try it in ICDEV

The mappings above come straight from ICDEV's crosswalk engine (`context/compliance/control_crosswalk.json`):

```bash
python tools/compliance/crosswalk_engine.py --control IA-2
python tools/compliance/crosswalk_engine.py --framework fedramp --baseline moderate
```
