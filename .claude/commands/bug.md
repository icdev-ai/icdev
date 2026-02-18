# Bug Fix — End-to-End (ATLAS Workflow)

Plan, implement, validate through the full ICDEV DevSecOps pipeline, and commit a bug fix in one shot. The user provides only a description — ICDEV handles everything else.

Follows the ATLAS workflow: Architect → Trace → Link → Assemble → Stress-test.

## Instructions

### Phase 1: Plan (Architect + Trace)

1. **Create GitHub Issue** — Use `gh issue create`:
   ```bash
   gh issue create --title "<concise bug title>" --body "<description from user input below>" --label "bug"
   ```
   Capture the issue number from the output.

2. **Generate Run ID**:
   ```bash
   python -c "import uuid; print(uuid.uuid4().hex[:8])"
   ```

3. **Create Branch**:
   ```bash
   git checkout master && git pull origin master
   git checkout -b "bug-issue-<number>-icdev-<run_id>-<concise-name>"
   ```

4. **Research Codebase** — Read relevant files to understand the bug. Try to reproduce it and identify the root cause.

5. **Create Plan** — Write the plan to `specs/` directory with filename: `issue-<number>-icdev-<run_id>-icdev_planner-<descriptive-name>.md` using the `Plan Format` below.
   - IMPORTANT: Replace every `<placeholder>` with specific values.
   - Use your reasoning model: THINK HARD about the bug, its root cause, and the minimal fix.
   - Be surgical — fix the bug at hand, don't fall off track. Minimal changes only.
   - All generated artifacts MUST include CUI markings: `CUI // SP-CTI`
   - Follow TDD: write a failing test that proves the bug, then fix it.
   - If the bug affects UI, add a task to create an E2E test in `.claude/commands/e2e/test_<name>.md`

6. **Commit Plan**:
   ```bash
   git add specs/
   git commit -m "icdev_planner: fix: plan for <bug name>"
   ```

7. **Post Plan to GitHub Issue**:
   ```bash
   gh issue comment <number> --body "[ICDEV-BOT] Fix plan created: specs/<filename>.md
   Branch: bug-issue-<number>-icdev-<run_id>-<name>"
   ```

### Phase 2: Implement (Link + Assemble)

8. **Execute the Plan** — Follow every step in the plan's `Step by Step Tasks` section, top to bottom. Write the minimal code changes to fix the bug.

9. **CUI Markings** — Verify every new or modified Python file includes:
   ```python
   # CUI // SP-CTI
   ```

### Phase 3: Validate — Full ICDEV DevSecOps Pipeline (Stress-test)

Run the complete ICDEV validation pipeline across all 4 tiers. Every **GATE** must pass before committing. Fix failures and re-run until clean. Reference: `args/security_gates.yaml`

**IMPORTANT — Audit Trail**: For every gate below, capture the **actual command output** (JSON where available). After all gates pass, write a structured validation report to:
```
audit/issue-<number>-icdev-<run_id>-validation-report.md
```
This file is the auditor's evidence artifact. It must include:
- Timestamp (ISO 8601) for each gate execution
- Exact command run
- Result: pass/fail/N/A
- Key metrics from output (e.g., "SAST: 0 critical, 0 high, 3 medium", "Tests: 14 passed, 0 failed", "STIG: 0 CAT1, 2 CAT2")
- For any gate that was N/A, state why (e.g., "No features/ directory", "No DevSecOps profile configured")

Also log the validation event to the audit trail:
```bash
python tools/audit/audit_logger.py --event-type "code.validation" --actor "icdev_builder" --action "Full 4-tier DevSecOps validation passed — bug fix" --project-id "<project_id>"
```

---

#### Tier 1: Universal Gates (always run)

These gates apply to every commit regardless of project type.

10. **Syntax Validation** — Compile-check every changed Python file:
    ```bash
    python -m py_compile <file>
    ```

11. **Code Quality (Ruff)** — Lint the codebase:
    ```bash
    ruff check . --fix
    ```
    Fix any remaining violations **in files you created or modified** before proceeding.

    **Auto-Chore: Pre-existing Lint Debt** — After `--fix`, if remaining violations exist in files NOT touched by this task (pre-existing debt):
    1. Count remaining violations:
       ```bash
       ruff check . --statistics 2>&1
       ```
    2. Check for an existing open cleanup issue (avoid duplicates):
       ```bash
       gh issue list --label "chore" --label "ruff-cleanup" --state open --limit 1
       ```
    3. If NO existing issue found AND remaining violations > 0, auto-create a chore issue:
       ```bash
       gh issue create --title "chore: Resolve <N> pre-existing ruff lint violations" \
         --label "chore" --label "ruff-cleanup" \
         --body "$(cat <<'RUFF_EOF'
       ## Pre-existing Ruff Lint Violations

       Discovered during validation of issue #<current_issue_number>.
       These are pre-existing violations NOT introduced by the current bug fix — tracked here for systematic cleanup.

       ### Violation Breakdown
       $(ruff check . --statistics 2>&1)

       ### Recommended Fix Strategy
       1. **E701/E702** (multiple statements on one line) — Refactor compact one-liners to multi-line
       2. **E402** (import not at top) — Review `sys.path.insert` patterns; suppress where intentional with `# noqa: E402`
       3. **F841** (unused variable) — Remove or prefix with `_` if intentional
       4. **F401** (unused import) — Run `ruff check . --fix --unsafe-fixes` for safe removals
       5. **E741** (ambiguous variable name) — Rename `l`/`O`/`I` to descriptive names

       ### Commands
       - `ruff check . --fix --unsafe-fixes` — Fix all auto-fixable (including unsafe)
       - `ruff check . --select E701,E702 --statistics` — Audit one-liners
       - `ruff check . --select F841 --statistics` — Audit unused vars

       Run `/chore` against this issue to systematically resolve all violations.
       RUFF_EOF
       )"
       ```
    4. Note the auto-created chore issue number in the validation report under Tier 1 > Code Quality.
    This does NOT block the current workflow — pre-existing violations are tracked for separate cleanup.

12. **Unit Tests (pytest)** — Run the test suite:
    ```bash
    python -m pytest tests/ -v --tb=short
    ```
    If tests fail, fix and re-run. Do NOT skip failing tests.

13. **BDD Tests (behave)** — If `features/` directory exists:
    ```bash
    python -m behave features/
    ```

14. **E2E Test** (if UI changes) — Execute per `.claude/commands/test_e2e.md`.

15. **SAST Security Scan** — Run static analysis for vulnerabilities:
    ```bash
    python tools/security/sast_runner.py --project-dir . --json
    ```
    **GATE: 0 critical, 0 high** (per `thresholds.sast`). Fix any critical/high findings.

16. **Secret Detection** — Scan for leaked secrets, API keys, tokens:
    ```bash
    python tools/security/secret_detector.py --project-dir . --json
    ```
    **GATE: 0 secrets detected** (per `merge_gates.block_on: secrets_detected`).

17. **Dependency Audit** — Check for known vulnerabilities in dependencies:
    ```bash
    python tools/security/dependency_auditor.py --project-dir . --json
    ```
    **GATE: 0 critical, 0 high** (per `thresholds.dependency`).

18. **CUI Marking Verification** — Confirm all new/modified `.py` files have CUI markings:
    ```bash
    grep -rL "CUI // SP-CTI" <list of new/modified .py files>
    ```
    **GATE: missing_cui_markings blocks merge**.

19. **SBOM Generation** — Regenerate the software bill of materials:
    ```bash
    python tools/compliance/sbom_generator.py --project-dir .
    ```
    **GATE: sbom_not_generated blocks deployment**.

---

#### Tier 2: ATO & Compliance Impact (always run when project has a project_id)

Assess whether this bug fix affects the project's Authority to Operate (ATO) and System Security Plan (SSP).

20. **NIST 800-53 Control Mapping + Crosswalk** — Map to SI-2 (Flaw Remediation) at minimum, plus any other affected controls. Auto-cascade to all mapped frameworks:
    ```bash
    python tools/compliance/control_mapper.py --activity "code.commit" --project-id "<project_id>"
    ```
    Then verify crosswalk coverage:
    ```bash
    python tools/compliance/crosswalk_engine.py --project-id "<project_id>" --coverage
    ```

21. **ATO Boundary Impact Assessment** — Determine if the bug fix changes the ATO boundary:
    - If the fix modifies authentication, encryption, network boundaries, or data flows, assess boundary impact
    - **GREEN**: No boundary change — proceed
    - **YELLOW**: Minor adjustment — SSP addendum needed
    - **ORANGE**: Significant change — SSP revision required, ISSO review needed
    - **RED**: ATO-invalidating — **STOP. Generate alternative COAs.**

22. **SSP Currency Check** — Verify the System Security Plan is current:
    - If this fix addresses a security control gap, flag SSP for update
    **GATE: ssp_not_current blocks deployment**.

23. **STIG Compliance** — Check for STIG violations:
    ```bash
    python tools/compliance/stig_checker.py --project-id "<project_id>"
    ```
    **GATE: 0 CAT1 open** (per `thresholds.stig.max_cat1_open: 0`).

24. **FIPS 199 Security Categorization** — Verify categorization is current:
    ```bash
    python tools/compliance/fips199_categorizer.py --project-id "<project_id>" --gate
    ```
    **GATE: no_categorization_for_ato_project blocks**.

25. **FIPS 200 Minimum Security** — Validate all 17 security areas:
    ```bash
    python tools/compliance/fips200_validator.py --project-id "<project_id>" --gate --json
    ```
    **GATE: 0 areas not_satisfied**.

26. **POAM Review** — Check for overdue Plan of Action & Milestones:
    **GATE: poam_has_overdue_items blocks deployment**.

---

#### Tier 3: Framework-Specific Gates (auto-detected per project)

Run the compliance detector to identify which frameworks apply, then validate each one.

27. **Auto-Detect Applicable Frameworks**:
    ```bash
    python tools/compliance/compliance_detector.py --project-id "<project_id>" --json
    ```

28. **FedRAMP Assessment** (if applicable):
    ```bash
    python tools/compliance/fedramp_assessor.py --project-id "<project_id>" --baseline moderate
    ```
    **GATE: 0 other_than_satisfied on high-priority controls**. Encryption FIPS 140-2 required.

29. **CMMC Assessment** (if applicable):
    ```bash
    python tools/compliance/cmmc_assessor.py --project-id "<project_id>" --level 2
    ```
    **GATE: 0 not_met Level 2 practices**. Evidence current within 90 days.

30. **cATO Evidence Freshness** (if applicable):
    ```bash
    python tools/compliance/cato_monitor.py --project-id "<project_id>" --check-freshness
    ```
    **GATE: 0 expired evidence on critical controls**. Readiness ≥50%.

31. **HIPAA Assessment** (if PHI data detected):
    ```bash
    python tools/compliance/hipaa_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 not_satisfied on Administrative/Technical Safeguards**.

32. **PCI DSS Assessment** (if PCI data detected):
    ```bash
    python tools/compliance/pci_dss_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 not_satisfied on Requirements 3-4, 6, 10**.

33. **CJIS Assessment** (if CJIS data detected):
    ```bash
    python tools/compliance/cjis_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 not_satisfied on Policy Areas 4, 5, 6, 10**.

34. **SOC 2 Assessment** (if applicable):
    ```bash
    python tools/compliance/soc2_assessor.py --project-id "<project_id>" --gate
    ```

35. **ISO 27001 Assessment** (if applicable):
    ```bash
    python tools/compliance/iso27001_assessor.py --project-id "<project_id>" --gate
    ```

36. **Multi-Regime Gate** — Unified pass/fail across all detected frameworks:
    ```bash
    python tools/compliance/multi_regime_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: All applicable frameworks must pass. 0 framework failures.**

---

#### Tier 4: Architecture & Governance Gates (conditional)

Run these gates when the bug fix touches architecture, infrastructure, security controls, or DoD/IC requirements.

37. **DevSecOps Pipeline Security** (if project has DevSecOps profile):
    ```bash
    python tools/devsecops/profile_manager.py --project-id "<project_id>" --assess --json
    ```
    **GATE: 0 critical policy-as-code violations, 0 missing attestations**.

38. **ZTA Posture Check** (if fix involves network, API endpoints, auth, or service communication):
    ```bash
    python tools/devsecops/zta_maturity_scorer.py --project-id "<project_id>" --all --json
    ```
    - Verify no default-allow policies introduced
    - Verify mTLS not bypassed
    **GATE: ZTA maturity ≥ Advanced (0.34) for IL4+, no pillar at 0.0**.

39. **NIST 800-207 ZTA Compliance** (if applicable):
    ```bash
    python tools/compliance/nist_800_207_assessor.py --project-id "<project_id>" --gate
    ```

40. **MOSA Modularity** (if DoD/IC project at IL4+):
    ```bash
    python tools/compliance/mosa_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 external interfaces without ICD, 0 circular deps, modularity ≥ 0.6**.

41. **Supply Chain Risk** (if dependencies changed):
    ```bash
    python tools/supply_chain/cve_triager.py --project-id "<project_id>" --sla-check --json
    ```
    **GATE: 0 critical SCRM risks, 0 expired ISAs, 0 overdue critical CVE SLAs, 0 Section 889 vendors**.

42. **Secure by Design (CISA SbD)** (if applicable):
    ```bash
    python tools/compliance/sbd_assessor.py --project-id "<project_id>" --domain all
    ```
    **GATE: 0 critical SbD requirements not_satisfied**.

43. **IV&V Compliance (IEEE 1012)** (if applicable):
    ```bash
    python tools/compliance/ivv_assessor.py --project-id "<project_id>" --process-area all
    ```
    **GATE: 0 critical IV&V findings open**.

---

### Phase 4: Commit & Close

44. **Write Validation Report** — Create the audit evidence artifact at `audit/issue-<number>-icdev-<run_id>-validation-report.md` using the `Validation Report Format` below. Include actual output/metrics from every gate. Commit it with the implementation.

45. **Commit Implementation** — Stage all changed files including the validation report and commit:
    ```bash
    git add <changed files> audit/
    git commit -m "icdev_builder: fix: <concise description of what was fixed>"
    ```

46. **Post Completion to GitHub Issue** — Include actual results from each gate:
    ```bash
    gh issue comment <number> --body "[ICDEV-BOT] Bug fix complete — all ICDEV DevSecOps gates passed.
    Plan: specs/<filename>.md
    Commit: $(git rev-parse --short HEAD)
    Validation Report: audit/issue-<number>-icdev-<run_id>-validation-report.md

    Tier 1 — Universal Gates:
    ✓ Syntax check — <N files compiled, 0 errors>
    ✓ Ruff lint — <0 violations (or N fixed)>
    ✓ Unit tests — <N passed, 0 failed, N% coverage>
    ✓ BDD tests — <N scenarios passed (or N/A: no features/ dir)>
    ✓ E2E tests — <N passed (or N/A: no UI changes)>
    ✓ SAST scan — <0 critical, 0 high, N medium, N low>
    ✓ Secret detection — <0 findings across N files scanned>
    ✓ Dependency audit — <0 critical, 0 high, N packages audited>
    ✓ CUI markings — <verified on N files>
    ✓ SBOM — <generated, N components cataloged>

    Tier 2 — ATO & Compliance Impact:
    ✓ NIST control mapping — SI-2 + <additional controls>
    ✓ Crosswalk cascade — <N frameworks updated: FedRAMP, CMMC, etc.>
    ✓ ATO boundary impact — <GREEN/YELLOW/ORANGE> — <rationale>
    ✓ SSP currency — <current / flagged for update — reason>
    ✓ STIG — <0 CAT1, N CAT2, N CAT3>
    ✓ FIPS 199 — <categorized: impact level>
    ✓ FIPS 200 — <17/17 areas satisfied, N gap controls>
    ✓ POAM — <0 overdue, N total items>

    Tier 3 — Framework-Specific:
    ✓ Applicable frameworks — <list detected>
    ✓ FedRAMP — <score% satisfied (or N/A)>
    ✓ CMMC — <score% met (or N/A)>
    ✓ cATO — <readiness%, 0 expired (or N/A)>
    ✓ HIPAA — <gate result (or N/A)>
    ✓ PCI DSS — <gate result (or N/A)>
    ✓ CJIS — <gate result (or N/A)>
    ✓ SOC 2 — <gate result (or N/A)>
    ✓ ISO 27001 — <gate result (or N/A)>
    ✓ Multi-regime gate — <all N frameworks passed>

    Tier 4 — Architecture & Governance:
    ✓ DevSecOps — <maturity level, 0 violations (or N/A)>
    ✓ ZTA posture — <maturity score, 7 pillars assessed (or N/A)>
    ✓ NIST 800-207 — <gate result (or N/A)>
    ✓ MOSA — <modularity score, 0 circular deps (or N/A)>
    ✓ Supply chain — <0 critical SCRM, 0 overdue CVE SLAs (or N/A)>
    ✓ SbD — <score% satisfied (or N/A)>
    ✓ IV&V — <0 critical findings (or N/A)>"
    ```

## Validation Report Format

Write this file to `audit/issue-<number>-icdev-<run_id>-validation-report.md`. This is the auditor's evidence artifact — include actual tool output, not just pass/fail.

```md
# CUI // SP-CTI
# Validation Report: Bug Fix — <bug name>

## Metadata
- **Issue**: #<number>
- **Run ID**: <run_id>
- **Branch**: <branch name>
- **Commit**: <commit hash>
- **Validated By**: icdev_builder (automated)
- **Validation Date**: <ISO 8601 timestamp>
- **Plan**: specs/<plan filename>.md

## Tier 1 — Universal Gates

### Syntax Validation
- **Command**: `python -m py_compile <files>`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> files compiled successfully, 0 syntax errors

### Code Quality (Ruff)
- **Command**: `ruff check . --fix`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 0 violations on bug fix files (<N> auto-fixed, if any)
- **Pre-existing Debt**: <N remaining violations in other files / "0 — codebase clean">
- **Auto-Chore Created**: <#<issue_number> — "chore: Resolve N ruff violations" / "N/A — existing issue #<N> already open" / "N/A — 0 remaining">

### Unit Tests (pytest)
- **Command**: `python -m pytest tests/ -v --tb=short`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> passed, 0 failed, 0 errors, <N%> coverage
- **Output Summary**:
  ```
  <paste key lines from pytest output>
  ```

### BDD Tests (behave)
- **Result**: <PASS / N/A>
- **Details**: <N scenarios passed / "N/A — no features/ directory">

### E2E Tests
- **Result**: <PASS / N/A>
- **Details**: <N tests passed / "N/A — no UI changes">

### SAST Security Scan
- **Command**: `python tools/security/sast_runner.py --project-dir . --json`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 0 critical, 0 high, <N> medium, <N> low across <N> files
- **Output Summary**:
  ```json
  <paste JSON summary from SAST output>
  ```

### Secret Detection
- **Command**: `python tools/security/secret_detector.py --project-dir . --json`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 0 secrets detected across <N> files scanned

### Dependency Audit
- **Command**: `python tools/security/dependency_auditor.py --project-dir . --json`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 0 critical, 0 high vulnerabilities across <N> packages

### CUI Markings
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: CUI // SP-CTI verified on <N> new/modified .py files
- **Files Verified**: <list files>

### SBOM Generation
- **Command**: `python tools/compliance/sbom_generator.py --project-dir .`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: SBOM generated, <N> components cataloged

## Tier 2 — ATO & Compliance Impact

### NIST 800-53 Control Mapping
- **Command**: `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "<project_id>"`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Controls Mapped**: SI-2 (Flaw Remediation) + <additional controls>
- **Crosswalk Cascade**: <list frameworks updated>

### ATO Boundary Impact
- **Timestamp**: <ISO 8601>
- **Result**: <GREEN / YELLOW / ORANGE / RED>
- **Rationale**: <explain why>
- **SSP Action Required**: <none / addendum / revision>

### STIG Compliance
- **Command**: `python tools/compliance/stig_checker.py --project-id "<project_id>"`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 0 CAT1, <N> CAT2, <N> CAT3

### FIPS 199 Categorization
- **Command**: `python tools/compliance/fips199_categorizer.py --project-id "<project_id>" --gate`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <impact level, CNSSI 1253 status if applicable>

### FIPS 200 Minimum Security
- **Command**: `python tools/compliance/fips200_validator.py --project-id "<project_id>" --gate --json`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 17/17 security areas satisfied, <N> gap controls

### POAM Review
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: 0 overdue items, <N> total POAM items

## Tier 3 — Framework-Specific Gates

### Applicable Frameworks
- **Command**: `python tools/compliance/compliance_detector.py --project-id "<project_id>" --json`
- **Detected**: <list or "none detected">

<For each applicable framework, include subsection with command, timestamp, result, key metrics. Mark non-applicable as N/A with reason.>

### Multi-Regime Gate
- **Command**: `python tools/compliance/multi_regime_assessor.py --project-id "<project_id>" --gate`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> frameworks assessed, all passed

## Tier 4 — Architecture & Governance Gates

<For each applicable gate, include command, timestamp, result, key metrics. Mark non-applicable as N/A with reason.>

## Summary

| Tier | Gates Run | Passed | Failed | N/A |
|------|-----------|--------|--------|-----|
| Tier 1: Universal | <N> | <N> | 0 | <N> |
| Tier 2: ATO & Compliance | <N> | <N> | 0 | <N> |
| Tier 3: Framework-Specific | <N> | <N> | 0 | <N> |
| Tier 4: Architecture & Gov | <N> | <N> | 0 | <N> |
| **Total** | **<N>** | **<N>** | **0** | **<N>** |

**Overall Result**: PASS — All gates satisfied. Ready for commit.

## Audit Trail Entry
- **Event**: code.validation
- **Actor**: icdev_builder
- **Action**: Full 4-tier DevSecOps validation passed — bug fix
- **Project ID**: <project_id>
- **Logged At**: <ISO 8601>

# CUI // SP-CTI
```

## Plan Format

```md
# CUI // SP-CTI
# Bug: <bug name>

## Metadata
issue_number: `{issue_number}`
run_id: `{run_id}`

## Bug Description
<describe the bug in detail, including symptoms and expected vs actual behavior>

## Root Cause Analysis
<analyze and explain the root cause of the bug>

## Solution Statement
<describe the proposed fix — minimal, targeted changes only>

## ATO Impact Assessment
- **Boundary Impact**: <GREEN/YELLOW/ORANGE/RED>
- **Affected NIST Controls**: SI-2 (Flaw Remediation) + <any other affected controls>
- **SSP Impact**: <none / addendum / revision required>
- **Data Classification Change**: <yes/no>

## Relevant Files
<find and list files relevant to the bug with bullet point descriptions>

### New Files
<list any new files that need to be created, if any>

## Step by Step Tasks
IMPORTANT: Execute every step in order, top to bottom.

<list step by step tasks as h3 headers plus bullet points. Start with a failing test that proves the bug (TDD). Then fix it. Keep changes minimal.>

## Validation Commands
- `python -m py_compile <file>` - Syntax check
- `ruff check .` - Lint check
- `python -m pytest tests/ -v --tb=short` - Unit tests
- `python tools/security/sast_runner.py --project-dir . --json` - SAST scan
- `python tools/security/secret_detector.py --project-dir . --json` - Secret detection
- `python tools/security/dependency_auditor.py --project-dir . --json` - Dependency audit
- `python tools/compliance/sbom_generator.py --project-dir .` - SBOM
- `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "<project_id>"` - NIST mapping
- `python tools/compliance/crosswalk_engine.py --project-id "<project_id>" --coverage` - Crosswalk
- `python tools/compliance/stig_checker.py --project-id "<project_id>"` - STIG check
- `python tools/compliance/fips199_categorizer.py --project-id "<project_id>" --gate` - FIPS 199
- `python tools/compliance/fips200_validator.py --project-id "<project_id>" --gate` - FIPS 200
- `python tools/compliance/compliance_detector.py --project-id "<project_id>" --json` - Framework detection
- `python tools/compliance/multi_regime_assessor.py --project-id "<project_id>" --gate` - Multi-regime
- `python tools/devsecops/zta_maturity_scorer.py --project-id "<project_id>" --all --json` - ZTA
- `python tools/compliance/mosa_assessor.py --project-id "<project_id>" --gate` - MOSA

## NIST 800-53 Controls
- SI-2 Flaw Remediation — bug fix addresses identified software flaw
<list any additional NIST controls relevant to this fix>

## Notes
<additional context>

# CUI // SP-CTI
```

## Bug
$ARGUMENTS

## Report
- Summarize what was done: issue created, branch created, plan written, fix implemented, all ICDEV DevSecOps gates passed.
- Include the GitHub issue number and URL.
- Include the path to the plan file in `specs/`.
- Include the path to the validation report in `audit/`.
- List the files that were modified.
- Include the commit hash.
- Include a **4-tier gate summary table** with actual results (not just pass/fail — include metrics like "14 tests passed", "0 critical, 0 high, 3 medium", "SI-2 mapped").
- Include ATO boundary impact assessment result (GREEN/YELLOW/ORANGE/RED) with rationale.
- Tell the user: "Run `/pull_request` to open a PR, or `/test` for a full validation suite."
