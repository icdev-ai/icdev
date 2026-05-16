# CUI // SP-CTI
# Chore: Infrastructure Enabler — Security Accreditation Boundary Controls

## Metadata
issue_number: `17`
run_id: `146ca522`

## Chore Description
This enabler delivers the BDD acceptance scenario and step definitions that verify the ICDEV™ platform enforces security controls per the accreditation boundary. The existing security infrastructure (SAST runner, secret detector, dependency auditor, STIG checker, security gates, ATO boundary tagger, ABAC engine, mTLS, classification enforcer, etc.) is already deployed. This chore formalizes acceptance by:

1. Writing the BDD feature scenario from the task acceptance criteria into `features/security_infrastructure.feature`
2. Adding corresponding Behave step definitions to `features/steps/security_infrastructure_steps.py`
3. Verifying that the existing infrastructure satisfies the acceptance scenario

Why it matters: Without a formal BDD scenario tied to the accreditation boundary, there is no automated gate that proves the security controls are in place on every commit. This closes that gap for the 1 security requirement this enabler supports.

## ATO Impact Assessment
- **Boundary Impact**: GREEN — no new external interfaces, no removal of controls; adds test evidence
- **Affected NIST Controls**: CA-2 (Security Assessment), CA-7 (Continuous Monitoring), SA-11 (Developer Security Testing)
- **SSP Impact**: Addendum — new continuous monitoring evidence artifact (BDD scenario) should be noted in the SSP

## Relevant Files

### Modified Files
- `features/security_scanning.feature` — existing security BDD feature (reference only, not modified)
- `features/steps/security_steps.py` — existing security step definitions (reference)

### New Files
- `features/security_infrastructure.feature` — new feature with accreditation boundary scenario
- `features/steps/security_infrastructure_steps.py` — step definitions for the new scenario

## Step by Step Tasks

### Step 1: Write BDD Feature File
Create `features/security_infrastructure.feature` containing:
- CUI marking header
- Feature description for Security Requirement Validation
- The exact acceptance criteria scenario from task-6bbb1bf3b1:
  - Given: system enforces security controls per the accreditation boundary
  - When: infrastructure enablement covers env setup, CI/CD, hardening, and compliance scaffolding
  - Then: the system behaves as specified and the requirement is satisfied
- Additional scenarios to verify boundary controls are detectable programmatically

### Step 2: Write Step Definitions
Create `features/steps/security_infrastructure_steps.py` with:
- CUI marking header
- `@given` for accreditation boundary control enforcement check
- `@when` for infrastructure enablement validation
- `@then` for requirement satisfaction check
- Use existing tools: `sast_runner.py`, `secret_detector.py`, `stig_checker.py`, `security_gates.yaml` presence check

### Step 3: Verify BDD Passes
Run `python -m behave features/security_infrastructure.feature` and confirm all scenarios pass.

### Step 4: Run Full Validation Pipeline
Execute the ICDEV™ DevSecOps validation gates.

## Validation Commands
- `python -m py_compile features/steps/security_infrastructure_steps.py`
- `ruff check features/steps/security_infrastructure_steps.py`
- `python -m pytest tests/ -v --tb=short`
- `python -m behave features/security_infrastructure.feature`
- `python tools/security/sast_runner.py --project-path . --json`
- `python tools/security/secret_detector.py --project-path . --json`
- `python tools/security/dependency_auditor.py --project-path . --json`
- `python tools/compliance/sbom_generator.py --project icdev`
- `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "icdev"`
- `python tools/compliance/crosswalk_engine.py --project-id "icdev" --coverage`
- `python tools/compliance/stig_checker.py --project-id "icdev" --json`
- `python tools/compliance/fips199_categorizer.py --project-id "icdev" --gate`
- `python tools/compliance/fips200_validator.py --project-id "icdev" --gate --json`
- `python tools/compliance/compliance_detector.py --project-id "icdev" --json`
- `python tools/compliance/multi_regime_assessor.py --project-id "icdev" --gate`
- `python tools/devsecops/zta_maturity_scorer.py --project-id "icdev" --all --json`
- `python tools/compliance/mosa_assessor.py --project-id "icdev" --gate`

## Notes
The acceptance scenario step text is intentionally broad (infrastructure enablement) rather than narrowly testing a single tool. The step definitions validate the boundary by checking that key security infrastructure artifacts exist and are operational: security gates config, SAST capability, secret detection, STIG compliance tooling, and the classification enforcer.

# CUI // SP-CTI
