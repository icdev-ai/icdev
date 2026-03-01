# ATO Acceleration Workflow

> Pursue first Authorization to Operate (ATO) across multiple compliance frameworks simultaneously.

## Purpose

Guide a project from zero compliance to ATO-ready across FedRAMP, CMMC, and DoD IL requirements using the ICDEV multi-framework compliance engine.

## Prerequisites

- Project created with `--impact-level` and `--target-frameworks`
- Database initialized with Phase 17C tables (42 tables)
- Control crosswalk and framework catalogs loaded

## Workflow Steps

### Phase 1: Framework Selection & Baseline Assessment

1. **Select target frameworks** based on contract/mission requirements
   ```bash
   python tools/project/project_create.py --name "my-app" --impact-level IL5 --target-frameworks "fedramp-high,cmmc-l2"
   ```

2. **Run crosswalk gap analysis** to understand total control surface
   ```bash
   python tools/compliance/crosswalk_engine.py --project-id X --target fedramp-high --gap-analysis
   python tools/compliance/crosswalk_engine.py --project-id X --target cmmc --gap-analysis
   ```

3. **Compute baseline coverage** across all frameworks
   ```bash
   python tools/compliance/crosswalk_engine.py --project-id X --coverage
   ```

### Phase 2: Control Implementation

4. **Map NIST 800-53 controls** — each implementation satisfies multiple frameworks via crosswalk
   ```bash
   python tools/compliance/control_mapper.py --project-id X --activity code.commit
   python tools/compliance/crosswalk_engine.py --project-id X --map-control AC-2
   ```

5. **Track PI compliance** — align control implementation with SAFe PIs
   ```bash
   python tools/compliance/pi_compliance_tracker.py --project-id X --start-pi PI-24.1 --start-date 2024-01-15 --end-date 2024-04-15
   ```

### Phase 3: Framework-Specific Assessments

6. **Run FedRAMP assessment**
   ```bash
   python tools/compliance/fedramp_assessor.py --project-id X --baseline moderate
   python tools/compliance/fedramp_report_generator.py --project-id X --baseline moderate
   ```

7. **Run CMMC assessment**
   ```bash
   python tools/compliance/cmmc_assessor.py --project-id X --level 2
   python tools/compliance/cmmc_report_generator.py --project-id X --level 2
   ```

8. **Run existing assessments** (STIG, CSSP, SbD, IV&V)
   ```bash
   python tools/compliance/stig_checker.py --project-id X
   python tools/compliance/cssp_assessor.py --project-id X
   python tools/compliance/sbd_assessor.py --project-id X
   python tools/compliance/ivv_assessor.py --project-id X
   ```

### Phase 4: Artifact Generation

9. **Generate OSCAL artifacts** — machine-readable SSP, POA&M, Assessment Results
   ```bash
   python tools/compliance/oscal_generator.py --project-id X --artifact all
   ```

10. **Generate human-readable artifacts** — SSP, POA&M, control matrix
    ```bash
    python tools/compliance/ssp_generator.py --project-id X
    python tools/compliance/poam_generator.py --project-id X
    ```

### Phase 5: System of Record Sync

11. **Sync to eMASS** — push controls, POA&M, artifacts, test results
    ```bash
    python tools/compliance/emass/emass_sync.py --project-id X --mode hybrid
    ```

12. **Sync to Xacta** (if applicable)
    ```bash
    python tools/compliance/xacta/xacta_sync.py --project-id X --mode hybrid
    ```

### Phase 6: Continuous Monitoring (cATO)

13. **Establish cATO evidence baseline**
    ```bash
    python tools/compliance/cato_monitor.py --project-id X --readiness
    ```

14. **Schedule automated evidence collection**
    ```bash
    python tools/compliance/cato_scheduler.py --project-id X --run-due
    ```

15. **Monitor evidence freshness**
    ```bash
    python tools/compliance/cato_monitor.py --project-id X --check-freshness
    ```

## Gate Criteria

| Gate | Criteria |
|------|----------|
| FedRAMP | 0 other_than_satisfied on high-priority controls |
| CMMC | 0 not_met on Level 2 practices |
| STIG | 0 CAT1 findings |
| CSSP | 0 critical requirements not_satisfied |
| SbD | 0 critical not_satisfied |
| IV&V | 0 critical findings |
| cATO | 0 expired evidence on critical controls |

## Success Criteria

- [ ] All target framework assessments complete
- [ ] OSCAL artifacts generated and validated
- [ ] eMASS/Xacta synced with current data
- [ ] cATO evidence baseline established
- [ ] Gate evaluation: PASS on all frameworks
- [ ] PI compliance velocity tracked
- [ ] Compliance score meets threshold (≥80%)

## Edge Cases

- **Air-gapped environment**: Use `--mode export` for eMASS/Xacta sync
- **Framework overlap**: Crosswalk engine deduplicates — implement once, satisfy many
- **IL6 SECRET**: Classification manager auto-applies SECRET markings
- **IATO vs ATO**: Start with IATO (interim), build toward full ATO or cATO
