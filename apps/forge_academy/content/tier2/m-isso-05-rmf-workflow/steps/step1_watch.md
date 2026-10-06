---
ontology_id: icdev:mission:m-isso-05-rmf-workflow:step:1
step_class: icdev:Lesson
---

# RMF Workflow Setup

The Risk Management Framework (RMF) isn't a one-time event — it's a continuous cycle. ICDEV automates the workflow handoffs, evidence collection triggers, and SSP section drafts so your team focuses on decisions, not document assembly.

## What You'll See

An illustrative RMF run for ICDEV-Prod, scoped to the 47 controls under review this cycle:

**Step 1: Categorize**
System Security Plan Section 1.1 auto-populated from existing asset inventory. Impact level confirmed: Moderate (FIPS 199, via `tools/compliance/fips199_categorizer.py`). 47 controls in scope for this review cycle out of the NIST SP 800-53 Rev 5 Moderate baseline.

**Step 2: Select Controls**
47 controls → 12 organization-defined parameter values filled in from the organization's tailoring decisions. Control tailoring complete in 38 seconds. Overlays applied: DoD IL4 + FedRAMP Moderate.

**Step 3: Implement (evidence collection trigger)**
ICDEV queued 47 evidence collection tasks. 39 of 47 automated (scan/config pull). 8 require manual evidence (policy reviews, interviews). Automated tasks begin immediately — estimated 6h to complete.

**Step 4: SSP Section Draft**
Control narratives drafted for all 47 controls using your existing tool outputs as source material (`tools/compliance/ssp_generator.py`). Average draft length: 287 words per control. Human review flagged as required for 8 controls with gaps.

**The result:** What previously took 3 months of ISSO time is now a 6-hour automated run with 8 targeted human touchpoints.
