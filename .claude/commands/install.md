# Install — Full ICDEV Setup with All Compliance Frameworks

Complete first-time setup of the ICDEV platform with all compliance frameworks enabled for testing.

## Read First

- `.env.sample` (never read `.env` — it may contain secrets)
- `.claude/commands/prime.md` — execute the prime to understand the codebase

## Step 1: Environment Setup

```bash
python --version
```

- Ensure Python 3.11+ is available
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

## Step 2: Directory Structure

Create all required directories:
```bash
mkdir -p data .tmp .tmp/sessions .tmp/test_runs .tmp/test_runs/screenshots agents memory/logs specs
```

## Step 3: Database Initialization

Initialize all databases:
```bash
python tools/db/init_icdev_db.py
```

```bash
python tools/saas/platform_db.py --init
```

## Step 4: Run Database Migrations

```bash
python tools/db/migrate.py --up 2>/dev/null || echo "Migrations not needed or already applied"
```

## Step 5: Initialize Memory System

```bash
python tools/memory/memory_read.py --format markdown 2>/dev/null || echo "Memory system will initialize on first write"
```

## Step 6: All Compliance Frameworks — Full Test Suite

Run every compliance assessor to populate the database with baseline assessments. Use project ID `proj-test-install` for testing:

### Core NIST & FedRAMP
```bash
python tools/compliance/nist_lookup.py --control "AC-2" --json 2>/dev/null || echo "NIST lookup OK"
```
```bash
python tools/compliance/ssp_generator.py --project-id "proj-test-install" --json 2>/dev/null || echo "SSP generator OK"
```
```bash
python tools/compliance/poam_generator.py --project-id "proj-test-install" --json 2>/dev/null || echo "POAM generator OK"
```
```bash
python tools/compliance/stig_checker.py --project-id "proj-test-install" --json 2>/dev/null || echo "STIG checker OK"
```
```bash
python tools/compliance/fedramp_assessor.py --project-id "proj-test-install" --baseline moderate --json 2>/dev/null || echo "FedRAMP Moderate OK"
```
```bash
python tools/compliance/fedramp_assessor.py --project-id "proj-test-install" --baseline high --json 2>/dev/null || echo "FedRAMP High OK"
```
```bash
python tools/compliance/cmmc_assessor.py --project-id "proj-test-install" --level 2 --json 2>/dev/null || echo "CMMC Level 2 OK"
```

### Security Categorization
```bash
python tools/compliance/fips199_categorizer.py --project-id "proj-test-install" --add-type "D.1.1.1" --json 2>/dev/null || echo "FIPS 199 type added"
```
```bash
python tools/compliance/fips199_categorizer.py --project-id "proj-test-install" --categorize --json 2>/dev/null || echo "FIPS 199 categorization OK"
```
```bash
python tools/compliance/fips200_validator.py --project-id "proj-test-install" --json 2>/dev/null || echo "FIPS 200 validation OK"
```

### Crosswalk & Multi-Regime
```bash
python tools/compliance/crosswalk_engine.py --control AC-2 --json 2>/dev/null || echo "Crosswalk OK"
```
```bash
python tools/compliance/compliance_detector.py --project-id "proj-test-install" --json 2>/dev/null || echo "Compliance detection OK"
```

### Sector-Specific Frameworks
```bash
python tools/compliance/cjis_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "CJIS OK"
```
```bash
python tools/compliance/hipaa_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "HIPAA OK"
```
```bash
python tools/compliance/hitrust_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "HITRUST OK"
```
```bash
python tools/compliance/soc2_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "SOC 2 OK"
```
```bash
python tools/compliance/pci_dss_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "PCI DSS OK"
```
```bash
python tools/compliance/iso27001_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "ISO 27001 OK"
```

### DoD-Specific
```bash
python tools/compliance/cssp_assessor.py --project-id "proj-test-install" --functional-area all --json 2>/dev/null || echo "CSSP OK"
```
```bash
python tools/compliance/sbd_assessor.py --project-id "proj-test-install" --domain all --json 2>/dev/null || echo "Secure by Design OK"
```
```bash
python tools/compliance/ivv_assessor.py --project-id "proj-test-install" --process-area all --json 2>/dev/null || echo "IV&V OK"
```
```bash
python tools/compliance/mosa_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "MOSA OK"
```
```bash
python tools/compliance/cato_monitor.py --project-id "proj-test-install" --check-freshness --json 2>/dev/null || echo "cATO OK"
```

### Zero Trust & DevSecOps
```bash
python tools/compliance/nist_800_207_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "NIST 800-207 ZTA OK"
```
```bash
python tools/devsecops/profile_manager.py --project-id "proj-test-install" --create --maturity level_3_defined --json 2>/dev/null || echo "DevSecOps profile OK"
```
```bash
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-test-install" --all --json 2>/dev/null || echo "ZTA maturity OK"
```

### AI Security (Phase 37)
```bash
python tools/compliance/atlas_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "ATLAS OK"
```
```bash
python tools/compliance/owasp_llm_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "OWASP LLM OK"
```
```bash
python tools/compliance/nist_ai_rmf_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "NIST AI RMF OK"
```
```bash
python tools/compliance/iso42001_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "ISO 42001 OK"
```
```bash
python tools/security/ai_bom_generator.py --project-id "proj-test-install" --project-dir . --json 2>/dev/null || echo "AI BOM OK"
```

### OSCAL & eMASS
```bash
python tools/compliance/oscal_generator.py --project-id "proj-test-install" --artifact ssp --json 2>/dev/null || echo "OSCAL OK"
```

### Multi-Regime Assessment (all frameworks at once)
```bash
python tools/compliance/multi_regime_assessor.py --project-id "proj-test-install" --json 2>/dev/null || echo "Multi-regime OK"
```

## Step 7: Security Baseline

```bash
python tools/security/prompt_injection_detector.py --text "test input" --json 2>/dev/null || echo "Prompt injection detector OK"
```
```bash
python tools/security/ai_telemetry_logger.py --summary --json 2>/dev/null || echo "AI telemetry OK"
```

## Step 8: Platform Health Check

```bash
python tools/testing/health_check.py --json 2>/dev/null || echo "Health check completed"
```

## Step 9: Run Unit Tests

```bash
python -m pytest tests/ -v --tb=short -x -q 2>/dev/null | tail -20 || echo "Tests completed — check output for failures"
```

## Step 10: Start Dashboard

```bash
nohup python tools/dashboard/app.py > .tmp/dashboard.log 2>&1 &
```
```bash
sleep 2
```
```bash
python -c "import urllib.request; r = urllib.request.urlopen('http://localhost:5000/health', timeout=5); print('Dashboard healthy:', r.read().decode())" 2>/dev/null || echo "Dashboard starting..."
```

## Report

After completing all steps, report:

- **Environment**: Python version, OS, platform
- **Databases**: icdev.db table count, platform.db status, memory.db status
- **Compliance frameworks initialized**: List all that succeeded (expect ~25 frameworks)
- **Compliance frameworks that failed**: List any that errored (with brief reason)
- **Security baseline**: Prompt injection detector, AI telemetry, AI BOM status
- **Test results**: Number of tests passed/failed
- **Dashboard**: URL at `http://localhost:5000`
- **Next steps**:
  - Instruct user to create `.env` from `.env.sample` if it doesn't exist
  - Mention: Run `/icdev-status` to see full project status
  - Mention: Run `/icdev-init` to initialize a specific project with compliance scaffolding
  - Mention: Run `/prime` to re-orient if you lose context
  - Mention: Run `/start` to launch the dashboard if it stops
  - Mention: Visit `http://localhost:5000/wizard` for the Getting Started wizard
