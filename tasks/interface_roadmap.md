# Interface Requirement Implementation Roadmap

**Classification:** CUI // SP-CTI  
**Date:** 2026-05-16  
**Source:** `reports/interface_gap_analysis.md` (task-23f28e7c55-d3)  
**Scope:** 2 interface requirements with incomplete automated verification coverage.

---

## Issue 1: IR-1 — REST API Interface Automated Verification Missing

**Problem:** All operational artifacts for the REST API interface are present and the server is live, but 3 of 4 BDD steps in `features/interface_requirement_validation.feature` are undefined, preventing automated verification on every build.

**Gaps:**
| Gap ID | Undefined Step |
|--------|----------------|
| GAP-1 | `When the REST API interface is verified` |
| GAP-2 | `Then the dashboard API server responds at "http://localhost:5050/"` |
| GAP-3 | `Then the ICD generator tool exists at "tools/mosa/icd_generator.py"` |

**Precise Steps to Resolve:**

1. **Add the `When` step** in `features/steps/functional_infrastructure_steps.py` (or a new `features/steps/interface_requirement_steps.py`):
   ```python
   @when('the REST API interface is verified')
   def step_verify_rest_api(context):
       context.check_paths = [
           'tools/mosa/icd_generator.py',
           'args/mosa_config.yaml',
       ]
       context.server_url = 'http://localhost:5050/'
   ```

2. **Add the `Then` endpoint probe step:**
   ```python
   import urllib.request

   @then('the dashboard API server responds at "{url}"')
   def step_dashboard_api_responds(context, url):
       try:
           req = urllib.request.Request(url, method='HEAD')
           resp = urllib.request.urlopen(req, timeout=5)
           assert resp.status == 200, f"Expected HTTP 200, got {resp.status}"
       except Exception as exc:
           assert False, f"Dashboard API not reachable at {url}: {exc}"
   ```

3. **Add the `Then` artifact existence step:**
   ```python
   @then('the ICD generator tool exists at "{path}"')
   def step_icd_generator_tool_exists(context, path):
       full = os.path.join(context.project_root, path)
       assert os.path.exists(full), f"ICD generator not found: {full}"
   ```

4. **Align existing wording** — The feature file uses `ICD generator tool` while existing step definitions use `ICD generator`. Either update the feature file to match `@then('the ICD generator exists at "{path}"')`, or add the new step above to cover the exact wording.

5. **Run Behave verification**:
   ```bash
   behave features/interface_requirement_validation.feature
   ```

---

## Issue 2: IR-2 — A2A Multi-Agent Communication Interface Automated Verification Missing

**Problem:** All required artifacts, configuration gates, and database schema elements for A2A are in place, but all 4 BDD steps for this scenario are undefined, preventing automated verification on every build.

**Gaps:**
| Gap ID | Undefined Step |
|--------|----------------|
| GAP-4 | `When the A2A interface specification is verified` |
| GAP-5 | `Then the MOSA code enforcer exists at "tools/mosa/mosa_code_enforcer.py"` |
| GAP-6 | `Then the security gates config defines interface coverage at "args/security_gates.yaml"` |
| GAP-7 | `Then the ICD documents table exists in the database schema` |

**Precise Steps to Resolve:**

1. **Add the `When` step** in the same step definition file:
   ```python
   @when('the A2A interface specification is verified')
   def step_verify_a2a_interface(context):
       context.check_paths = [
           'tools/mosa/mosa_code_enforcer.py',
           'args/security_gates.yaml',
       ]
       context.required_table = 'icd_documents'
   ```

2. **Add the `Then` artifact existence step:**
   ```python
   @then('the MOSA code enforcer exists at "{path}"')
   def step_mosa_code_enforcer_exists(context, path):
       full = os.path.join(context.project_root, path)
       assert os.path.exists(full), f"MOSA code enforcer not found: {full}"
   ```

3. **Add the `Then` gate coverage step:**
   ```python
   @then('the security gates config defines interface coverage at "{path}"')
   def step_security_gates_interface_coverage(context, path):
       full = os.path.join(context.project_root, path)
       assert os.path.exists(full), f"Security gates config not found: {full}"
       with open(full, 'r', encoding='utf-8') as fh:
           content = fh.read()
       assert 'interface_coverage_below_80_pct' in content, (
           "Interface coverage gate not defined in security_gates.yaml"
       )
       assert 'external_interface_without_icd' in content, (
           "External interface ICD gate not defined in security_gates.yaml"
       )
   ```

4. **Add the `Then` database schema step:**
   ```python
   from icdev.tools.db.storage import get_connection

   @then('the ICD documents table exists in the database schema')
   def step_icd_documents_table_exists(context):
       conn = get_connection()
       cur = conn.cursor()
       cur.execute(
           "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
           ('icd_documents',)
       )
       row = cur.fetchone()
       conn.close()
       assert row is not None, "Table 'icd_documents' not found in database schema"
   ```

5. **Run Behave verification**:
   ```bash
   behave features/interface_requirement_validation.feature
   ```

---

## Cross-Cutting Step (recommended after both issues are resolved)

6. **Add the interface feature to CI/CD** in `args/cicd_config.yaml` or the project's test pipeline so that missing step definitions are caught automatically on every pull request.

---

*Roadmap produced by ICDEV™ ANVIL Build Workflow — Roadmap phase (task-23f28e7c55-d4).*
