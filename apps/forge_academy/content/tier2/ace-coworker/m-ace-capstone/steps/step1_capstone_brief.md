---
ontology_id: icdev:mission:m-ace-capstone:step:1
step_class: icdev:design
---
# ACE Capstone: Full Co-Worker Pipeline

Your capstone: build a complete ACE co-worker pipeline that solves a real engineering problem.

## The challenge

Design and run a pipeline that:
1. Takes a GitHub repository URL as input
2. Has an `agent_developer` co-worker analyze the repo structure
3. Has a `security_analyst` co-worker run a security review
4. Has a `compliance_officer` co-worker produce a NIST 800-53 control gap assessment
5. Gates the compliance assessment with a HITL approval
6. Returns a structured report with findings from all 3 co-workers

## Success criteria

- Pipeline completes or reaches HITL within 5 minutes
- All 3 stages produce non-empty artifacts
- Compliance assessment references at least 3 specific NIST 800-53 controls
- HITL approval workflow is correctly configured

## Your task

Write the pipeline request JSON and the polling script. Then run it against the ICDEV repo itself (`C:\AI\ICDev`).
