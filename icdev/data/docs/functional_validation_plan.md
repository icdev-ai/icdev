# CUI // SP-CTI

# Functional Validation Plan

**Date:** 2026-05-16
**Scope:** Validate the 5 identified functional requirements for ICDEV™ platform infrastructure enablement.
**Method:** Artifact existence checks, static analysis, end-to-end computation verification, and BDD scenario execution.

---

## Requirement 1 — Environment Setup Artifacts Are Present

**Description:** The platform must provide a reproducible environment template, dependency manifest, and health-check tooling so that new instances can be bootstrapped consistently.

**Validation Steps:**
1. **File Existence Check:** Verify `.env.example` exists in the project root and contains all mandatory variables (`ICDEV_STORAGE_BACKEND`, `OLLAMA_BASE_URL`, `ICDEV_LLM_PROVIDER`, `ICDEV_AUTO_COMMIT`).
2. **Dependency Manifest Check:** Verify `requirements.txt` exists and lists core dependencies (`flask`, `pytest`, `pyyaml`, `jinja2`).
3. **Health Check Tool Check:** Verify `tools/testing/health_check.py` exists and executes without unhandled exceptions (`python tools/testing/health_check.py --json`).
4. **Schema Validation:** Parse the JSON output of the health check and assert `status` == `ok`.
5. **Cross-Platform Guard:** Confirm `tempfile.gettempdir()`, `pathlib.Path`, and `encoding='utf-8'` are used in the health check script (no hard-coded Unix paths).

---

## Requirement 2 — CI/CD Pipeline Configuration Is In Place

**Description:** The platform must contain a pipeline config generator and a declarative CI/CD args file to support automated build, test, and deploy workflows.

**Validation Steps:**
1. **Generator Existence:** Verify `tools/ci/pipeline_config_generator.py` exists and exposes a callable entry point (check `__name__ == '__main__'` block or CLI argparse).
2. **Config File Existence:** Verify `args/cicd_config.yaml` exists and is valid YAML (`yaml.safe_load` succeeds).
3. **Config Content Check:** Confirm `args/cicd_config.yaml` defines at minimum `stages`, `gates`, and `triggers` keys.
4. **Generator Smoke Test:** Run `python tools/ci/pipeline_config_generator.py --dry-run` (or equivalent) and assert exit code 0.
5. **Upstream Integration:** Verify `.github/workflows/ci_cd_pipeline.yml` references the generated config or uses compatible stage names.

---

## Requirement 3 — Security Hardening Artifacts Are Present

**Description:** The platform must include a pipeline security generator and a security gates configuration to enforce DevSecOps controls at build time.

**Validation Steps:**
1. **Generator Existence:** Verify `tools/devsecops/pipeline_security_generator.py` exists and has no syntax errors (`python -m py_compile` passes).
2. **Gates Config Existence:** Verify `args/security_gates.yaml` exists and is valid YAML.
3. **Gate Condition Coverage:** Confirm `args/security_gates.yaml` contains blocking conditions for at least `CAT1 STIG`, `critical/high vulns`, `failed tests`, and `missing markings`.
4. **Static Security Scan:** Run `python -m bandit -r tools/devsecops/ --severity-level medium` and assert zero findings in the security generator itself.
5. **SBOM / Container Policy:** Verify the security gates config references `SBOM` regeneration and `non-root` / `read-only rootfs` container requirements.

---

## Requirement 4 — Compliance Scaffolding Is In Place

**Description:** The platform must provide a classification manager and a control mapper to automate CUI/SECRET marking and NIST 800-53 / FedRAMP / CMMC crosswalk population.

**Validation Steps:**
1. **Classification Manager Existence:** Verify `tools/compliance/classification_manager.py` exists and exposes `apply_markings()` or equivalent API.
2. **Control Mapper Existence:** Verify `tools/compliance/control_mapper.py` exists and exposes `map_control()` or equivalent API.
3. **Functional Unit Test:** Import both modules in a Python REPL and call their primary functions with sample data; assert no `ImportError` or unhandled exception.
4. **Crosswalk Auto-Populate:** Verify `control_mapper.py` calls the crosswalk engine (or imports from it) so that implementing a NIST 800-53 control auto-populates FedRAMP and CMMC mappings.
5. **Classification Marking Check:** Verify `classification_manager.py` returns strings containing `CUI` for IL4/IL5 inputs and `SECRET` for IL6 inputs.

---

## Requirement 5 — Diplomatic Activity Tracker (DAT) Capability

**Description:** The MCIP system must provide a full Diplomatic Activity Tracker including ingestion, DTI calculation, update scheduling, dashboard rendering, and blueprint integration.

**Validation Steps:**
1. **Ingestion Engine Existence:** Verify `tools/dat/ingestion_engine.py` exists and exposes a `run()` or `ingest()` function.
2. **DTI Calculator Existence:** Verify `tools/dat/dti_calculator.py` exists and exposes `compute_dti_from_manifest()`.
3. **End-to-End DTI Computation:** Invoke `compute_dti_from_manifest()` with the canonical sample manifest:
   ```python
   sample = {
       'cables': [{'tension_level': 'high', 'received_at': '2026-05-16T00:00:00+00:00'}],
       'schedules': [{'emergency': True, 'veto_cast': False, 'walkout': False}],
       'metadata': [{'escalation_flag': True, 'communication_breakdown': False, 'frequency_delta': -0.5}],
   }
   ```
   Assert the returned score is a float in the closed range `[0.0, 1.0]`.
4. **Update Runner Existence:** Verify `tools/dat/dti_update_runner.py` exists and is executable as a standalone script.
5. **Dashboard Template Existence:** Verify `tools/dashboard/templates/strategos/dat.html` exists and contains valid Jinja2 syntax (`jinja2.Environment().parse()` succeeds).
6. **Scheduler Config Existence:** Verify `tools/dat/icdev_dat_scheduler_task.xml` exists and is well-formed XML.
7. **Args Config Existence:** Verify `args/dat_config.yaml` exists and defines at minimum `sources`, `schedule`, and `thresholds` keys.
8. **Blueprint Module Existence:** Verify `tools/strategos/dat.py` exists and imports cleanly from `icdev.tools.strategos.dat`.

---

## Consolidated Acceptance Runbook

| Step | Command / Action | Expected Result |
|------|------------------|-----------------|
| 1 | `python -c "import os; assert os.path.exists('.env.example')"` | Pass |
| 2 | `python tools/testing/health_check.py --json` | `status: ok` |
| 3 | `python -m py_compile tools/ci/pipeline_config_generator.py` | Exit 0 |
| 4 | `python -c "import yaml; yaml.safe_load(open('args/cicd_config.yaml'))"` | No exception |
| 5 | `python -m bandit -r tools/devsecops/ --severity-level medium` | Zero findings |
| 6 | `python -c "from tools.compliance.classification_manager import *"` | No ImportError |
| 7 | `python -c "from tools.compliance.control_mapper import *"` | No ImportError |
| 8 | `python -c "from tools.dat.dti_calculator import compute_dti_from_manifest; s=...; assert 0.0 <= compute_dti_from_manifest(s) <= 1.0"` | Pass |
| 9 | `behave features/functional_infrastructure.feature` | 6 scenarios passed |

---

*End of plan.*
