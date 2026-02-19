# Feature — End-to-End (ATLAS Workflow)

Plan, implement, validate through the full ICDEV DevSecOps pipeline, and commit a feature in one shot. The user provides only a description — ICDEV handles everything else.

Follows the ATLAS workflow: Architect → Trace → Link → Assemble → Stress-test.

## Instructions

### Phase 1: Plan (Architect + Trace)

1. **Create GitHub Issue** — Use `gh issue create`:
   ```bash
   gh issue create --title "<concise feature title>" --body "<description from user input below>" --label "enhancement"
   ```
   Capture the issue number from the output.

2. **Generate Run ID**:
   ```bash
   python -c "import uuid; print(uuid.uuid4().hex[:8])"
   ```

3. **Create Branch**:
   ```bash
   git checkout master && git pull origin master
   git checkout -b "feature-issue-<number>-icdev-<run_id>-<concise-name>"
   ```

3b. **Resolve Classification Marking** — Determine the project's classification:
    ```bash
    python tools/compliance/resolve_marking.py --project-id "<project_id>" --json
    ```
    Parse the JSON output to capture: `marking_required`, `banner`, `code_header`, `grep_pattern`, `vision_assertion`.
    If the resolver is not available, fall back to `CUI // SP-CTI`.
    If `marking_required` is false (Public/IL2 project), skip steps 9, 18, and CUI-related assertions in 20b.

4. **Research Codebase** — Read relevant files to understand patterns, architecture, and conventions.

5. **Create Plan** — Write the plan to `specs/` directory with filename: `issue-<number>-icdev-<run_id>-icdev_planner-<descriptive-name>.md` using the `Plan Format` below.
   - IMPORTANT: Replace every `<placeholder>` with specific values.
   - Use your reasoning model: THINK HARD about feature requirements, design, and implementation.
   - Follow existing patterns and conventions. Design for extensibility.
   - If `marking_required` is true, all generated artifacts MUST include classification markings: `<resolved banner>`
   - Follow TDD: plan tests BEFORE implementation steps.
   - If the feature includes UI, add a task to create an E2E test in `.claude/commands/e2e/test_<name>.md`

6. **Commit Plan**:
   ```bash
   git add specs/
   git commit -m "icdev_planner: feat: plan for <feature name>"
   ```

7. **Post Plan to GitHub Issue**:
   ```bash
   gh issue comment <number> --body "[ICDEV-BOT] Plan created: specs/<filename>.md
   Branch: feature-issue-<number>-icdev-<run_id>-<name>"
   ```

### Phase 2: Implement (Link + Assemble)

8. **Execute the Plan** — Follow every step in the plan's `Step by Step Tasks` section, top to bottom. Write the actual code changes.

9. **Classification Markings** — If `marking_required` is true, verify every new or modified Python file includes the classification header comment (`<resolved code_header>`). Add it to any file missing the marking. If `marking_required` is false, skip this step.

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
python tools/audit/audit_logger.py --event-type "code.validation" --actor "icdev_builder" --action "Full 4-tier DevSecOps validation passed" --project-id "<project_id>"
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
       These are pre-existing violations NOT introduced by the current feature — tracked here for systematic cleanup.

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

14. **E2E Test** (if UI changes) — If an E2E test was created in `.claude/commands/e2e/`, execute it per `.claude/commands/test_e2e.md`.
    **Prefer playwright-cli** over MCP Playwright for screenshots — it is faster and more token-efficient:
    ```bash
    npx playwright screenshot http://localhost:5000/ .tmp/e2e-screenshot.png --full-page
    ```
    Then run **vision validation** on the screenshot (see step 20b).

15. **SAST Security Scan** — Run static analysis for vulnerabilities:
    ```bash
    python tools/security/sast_runner.py --project-path . --json
    ```
    **GATE: 0 critical, 0 high** (per `thresholds.sast`). Fix any critical/high findings.

16. **Secret Detection** — Scan for leaked secrets, API keys, tokens:
    ```bash
    python tools/security/secret_detector.py --project-path . --json
    ```
    **GATE: 0 secrets detected** (per `merge_gates.block_on: secrets_detected`). Remove any detected secrets.
    Note: If a `.secrets.baseline` file exists, compare against it to filter known false positives.

17. **Dependency Audit** — Check for known vulnerabilities in dependencies:
    ```bash
    python tools/security/dependency_auditor.py --project-path . --json
    ```
    **GATE: 0 critical, 0 high** (per `thresholds.dependency`).

18. **CUI Marking Verification** — Confirm all new/modified `.py` files have CUI markings:
    ```bash
    grep -rL "<resolved grep_pattern>" <list of new/modified .py files>
    ```
    **GATE: missing_cui_markings blocks merge**. Add markings to any file missing them.

19. **SBOM Generation** — Regenerate the software bill of materials:
    ```bash
    python tools/compliance/sbom_generator.py --project <project_id>
    ```
    **GATE: sbom_not_generated blocks deployment**.

20. **Integration Smoke Test** — Verify all CLI tools are importable and respond to `--help` after refactoring:
    ```bash
    python tools/testing/smoke_test.py --json
    ```
    **GATE: 0 import failures across all CLI tools**. This catches broken imports from variable renames, removed imports, or refactored modules.
    Use `--quick` for compile-only (faster), or full mode to also test `--help` responses.

20b. **Vision Validation** (if UI changes / E2E screenshots taken) — Run computer vision analysis on E2E screenshots:
    ```bash
    python tools/testing/screenshot_validator.py \
        --image .tmp/e2e-screenshot.png \
        --assert "<resolved vision_assertion>" \
        --assert "No error dialogs or stack traces visible" \
        --assert "<feature-specific assertion>" \
        --json
    ```
    **GATE: All assertions must pass with confidence ≥ 0.5**. Uses Ollama LLaVA locally (air-gap safe) with fallback to Bedrock/OpenAI.
    This is the sign-off gate — vision confirms what the human would see.

20c. **CLI Fuzz Test** (if CLI tools were modified) — Fuzz-test modified CLI tools with malformed inputs:
    ```bash
    python tools/testing/fuzz_cli.py --tools <list of modified CLI .py files> --json
    ```
    **GATE: 0 crashes (SIGSEGV/SIGABRT) or unhandled tracebacks**. Tools must fail gracefully with argparse errors, not Python tracebacks.
    Use `--discover` to fuzz all tools, or `--tools` for targeted testing.

20d. **Acceptance Criteria Validation (V&V)** — Validate the plan's acceptance criteria against actual evidence:
    ```bash
    python tools/testing/acceptance_validator.py \
        --plan specs/<plan_file> \
        --test-results .tmp/test_runs/<run_id>/state.json \
        --base-url <app_url if applicable> \
        --pages <pages from plan acceptance criteria> \
        --json
    ```
    **GATE: 0 failed criteria, 0 error pages, plan must have acceptance criteria** (per `acceptance_validation`).
    This is the "did we build what was asked?" gate. It maps acceptance criteria to test evidence
    and checks rendered pages for error patterns (500s, tracebacks, JS errors, TemplateNotFound).
    Unlike E2E/vision which are conditional on UI changes, this step is **always required**.

---

#### Tier 2: ATO & Compliance Impact (always run when project has a project_id)

Assess whether this feature affects the project's Authority to Operate (ATO) and System Security Plan (SSP).

20. **NIST 800-53 Control Mapping + Crosswalk** — Map the commit to NIST controls and auto-cascade to all mapped frameworks (FedRAMP, CMMC, 800-171, CJIS, HIPAA, SOC 2, PCI DSS, ISO 27001, NIST 800-207):
    ```bash
    python tools/compliance/control_mapper.py --activity "code.commit" --project-id "<project_id>"
    ```
    Then verify crosswalk coverage:
    ```bash
    python tools/compliance/crosswalk_engine.py --project-id "<project_id>" --coverage
    ```

21. **ATO Boundary Impact Assessment** — Determine if this feature changes the ATO boundary (GREEN/YELLOW/ORANGE/RED tier):
    - If the feature introduces new components, data flows, external connections, or classification changes, assess boundary impact
    - **GREEN**: No boundary change — proceed
    - **YELLOW**: Minor adjustment — SSP addendum needed, note in plan
    - **ORANGE**: Significant change — SSP revision required, ISSO review needed
    - **RED**: ATO-invalidating — **STOP. Generate alternative COAs before proceeding.**

22. **SSP Currency Check** — Verify the System Security Plan is current:
    - Confirm SSP exists and is not stale
    - If this feature introduces new NIST controls, flag SSP for update
    **GATE: ssp_not_current blocks deployment**.

23. **STIG Compliance** — Check for STIG violations:
    ```bash
    python tools/compliance/stig_checker.py --project-id "<project_id>"
    ```
    **GATE: 0 CAT1 open** (per `thresholds.stig.max_cat1_open: 0`). WARN on CAT2 > 20.

24. **FIPS 199 Security Categorization** — Verify categorization is current:
    ```bash
    python tools/compliance/fips199_categorizer.py --project-id "<project_id>" --gate
    ```
    **GATE: no_categorization_for_ato_project blocks**. Verify IL6 projects have CNSSI 1253 overlay.

25. **FIPS 200 Minimum Security** — Validate all 17 security areas:
    ```bash
    python tools/compliance/fips200_validator.py --project-id "<project_id>" --gate --json
    ```
    **GATE: 0 areas not_satisfied** (per `fips200.thresholds.max_not_satisfied_areas: 0`).

26. **POAM Review** — Check for overdue Plan of Action & Milestones:
    **GATE: poam_has_overdue_items blocks deployment**.

---

#### Tier 3: Framework-Specific Gates (auto-detected per project)

Run the compliance detector to identify which frameworks apply, then validate each one. Only applicable frameworks are assessed — skip those that don't apply.

27. **Auto-Detect Applicable Frameworks**:
    ```bash
    python tools/compliance/compliance_detector.py --project-id "<project_id>" --json
    ```
    This returns the list of applicable frameworks based on data types (CUI, PHI, PCI, CJIS, etc.).

28. **FedRAMP Assessment** (if applicable):
    ```bash
    python tools/compliance/fedramp_assessor.py --project-id "<project_id>" --baseline moderate
    ```
    **GATE: 0 other_than_satisfied on high-priority controls** (per `fedramp.blocking`). Encryption must be FIPS 140-2.

29. **CMMC Assessment** (if applicable):
    ```bash
    python tools/compliance/cmmc_assessor.py --project-id "<project_id>" --level 2
    ```
    **GATE: 0 not_met Level 2 practices** (per `cmmc.blocking`). Evidence must be current within 90 days.

30. **cATO Evidence Freshness** (if applicable):
    ```bash
    python tools/compliance/cato_monitor.py --project-id "<project_id>" --check-freshness
    ```
    **GATE: 0 expired evidence on critical controls** (per `cato.blocking`). Readiness must be ≥50%.

31. **HIPAA Assessment** (if PHI data detected):
    ```bash
    python tools/compliance/hipaa_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 not_satisfied on Administrative/Technical Safeguards**. Encryption FIPS 140-2 required for PHI.

32. **PCI DSS Assessment** (if PCI data detected):
    ```bash
    python tools/compliance/pci_dss_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 not_satisfied on Requirements 3-4 (data protection), 6 (secure dev), 10 (logging)**.

33. **CJIS Assessment** (if CJIS data detected):
    ```bash
    python tools/compliance/cjis_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 not_satisfied on Policy Areas 4 (audit), 5 (access control), 6 (identification), 10 (encryption)**.

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
    **GATE: All applicable frameworks must pass individual gates. 0 framework failures allowed.**

---

#### Tier 4: Architecture & Governance Gates (conditional)

Run these gates when the feature touches architecture, infrastructure, security controls, or DoD/IC requirements.

37. **DevSecOps Pipeline Security** (if project has DevSecOps profile):
    ```bash
    python tools/devsecops/profile_manager.py --project-id "<project_id>" --assess --json
    ```
    **GATE: 0 critical policy-as-code violations, 0 missing attestations, 0 secrets in pipeline** (per `devsecops.blocking`).

38. **ZTA Posture Check** (if feature involves network, API endpoints, auth, or service communication):
    ```bash
    python tools/devsecops/zta_maturity_scorer.py --project-id "<project_id>" --all --json
    ```
    - Verify new endpoints require authentication (AC-3)
    - Verify no default-allow network policies introduced
    - Verify mTLS not bypassed for service communication
    **GATE: ZTA maturity ≥ Advanced (0.34) for IL4+, no pillar at 0.0** (per `zero_trust.thresholds`).

39. **NIST 800-207 ZTA Compliance** (if applicable):
    ```bash
    python tools/compliance/nist_800_207_assessor.py --project-id "<project_id>" --gate
    ```

40. **MOSA Modularity** (if DoD/IC project at IL4+):
    ```bash
    python tools/compliance/mosa_assessor.py --project-id "<project_id>" --gate
    ```
    **GATE: 0 external interfaces without ICD, 0 circular deps, modularity ≥ 0.6** (per `mosa.blocking`).

41. **Supply Chain Risk** (if new dependencies introduced):
    ```bash
    python tools/supply_chain/cve_triager.py --project-id "<project_id>" --sla-check --json
    ```
    **GATE: 0 critical SCRM risks unmitigated, 0 expired ISAs, 0 overdue critical CVE SLAs, 0 Section 889 prohibited vendors** (per `supply_chain.blocking`).

42. **Secure by Design (CISA SbD)** (if applicable):
    ```bash
    python tools/compliance/sbd_assessor.py --project-id "<project_id>" --domain all
    ```
    **GATE: 0 critical SbD requirements not_satisfied** (per `sbd.blocking`).

43. **IV&V Compliance (IEEE 1012)** (if applicable):
    ```bash
    python tools/compliance/ivv_assessor.py --project-id "<project_id>" --process-area all
    ```
    **GATE: 0 critical IV&V findings open** (per `ivv.blocking`).

---

### Phase 3.5: Auto-Issue Creation for Blockers

If any gate in Tiers 1-4 produces a **blocker** finding that cannot be auto-fixed inline (e.g., requires multi-file refactoring, architectural changes, or domain expertise), create a tracking issue instead of blocking the entire workflow:

43a. **Auto-Create Bug Issues for Blockers** — For each blocker that requires separate resolution:
    1. Check for an existing open issue covering the same blocker (avoid duplicates):
       ```bash
       gh issue list --label "bug" --label "v&v-blocker" --state open --limit 5
       ```
    2. If NO existing issue covers this blocker, auto-create one:
       ```bash
       gh issue create --title "bug: <blocker description>" \
         --label "bug" --label "v&v-blocker" --label "<tier>" \
         --body "$(cat <<'BLOCKER_EOF'
       ## V&V Blocker — Auto-Created

       **Discovered during**: Issue #<current_issue_number> validation (run_id: <run_id>)
       **Gate**: <gate name> (Tier <N>)
       **Severity**: blocker

       ### Description
       <what failed and why>

       ### Evidence
       <paste relevant gate output — counts, file list, error messages>

       ### Suggested Fix
       <concrete steps to resolve — commands, files to modify, patterns to follow>

       ### Acceptance Criteria
       - [ ] Gate passes with 0 findings
       - [ ] Full test suite still passes (121+ tests)
       - [ ] No regressions introduced

       Run `/bug` against this issue to auto-fix.
       BLOCKER_EOF
       )"
       ```
    3. Record the auto-created issue numbers in the validation report under the relevant tier.
    4. Post a summary comment on the current issue listing all auto-created blocker issues.

    **Examples of auto-issue blockers:**
    - CUI markings missing on N files → `bug: Add CUI markings to N Python files`
    - No BDD features/ directory → `bug: Create BDD features/ with .feature files for core capabilities`
    - SAST finding requiring refactoring → `bug: Resolve SAST finding <type> in <module>`
    - Dashboard page returning errors → `bug: Fix <page> rendering error on dashboard`
    - Import failures in N tools → `bug: Fix broken imports in N tools after refactoring`

    **Auto-fix vs. Auto-issue decision:**
    - If the fix is < 10 lines and isolated to the current feature's files → **fix inline** (don't create issue)
    - If the fix touches pre-existing code across multiple modules → **create issue** (track separately)
    - If the fix requires domain knowledge or architectural decision → **create issue** (needs review)

### Phase 4: Commit & Close

44. **Write Validation Report** — Create the audit evidence artifact at `audit/issue-<number>-icdev-<run_id>-validation-report.md` using the `Validation Report Format` below. Include actual output/metrics from every gate. Commit it with the implementation.

45. **Commit Implementation** — Stage all changed files including the validation report and commit:
    ```bash
    git add <changed files> audit/
    git commit -m "icdev_builder: feat: <concise description of what was built>"
    ```

46. **Post Completion to GitHub Issue** — Include actual results from each gate (not just pass/fail):
    ```bash
    gh issue comment <number> --body "[ICDEV-BOT] Feature complete — all ICDEV DevSecOps gates passed.
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
    ✓ Smoke test — <N tools tested, N passed, 0 import failures>
    ✓ Vision validation — <N assertions passed via LLaVA/Claude (or N/A: no UI)>
    ✓ CLI fuzz test — <N tools fuzzed, 0 crashes (or N/A: no CLI changes)>
    ✓ Acceptance V&V — <N criteria verified, 0 failed, N pages checked, 0 with errors>

    Tier 2 — ATO & Compliance Impact:
    ✓ NIST control mapping — <list controls: AC-3, AU-2, etc.>
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
# <resolved banner> (omit if marking_required is false)
# Validation Report: <feature/bug/chore name>

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
- **Details**: 0 violations on feature files (<N> auto-fixed, if any)
- **Pre-existing Debt**: <N remaining violations in other files / "0 — codebase clean">
- **Auto-Chore Created**: <#<issue_number> — "chore: Resolve N ruff violations" / "N/A — existing issue #<N> already open" / "N/A — 0 remaining">

### Unit Tests (pytest)
- **Command**: `python -m pytest tests/ -v --tb=short`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> passed, 0 failed, 0 errors, <N%> coverage
- **Output Summary**:
  ```
  <paste key lines from pytest output: test count, pass/fail, duration>
  ```

### BDD Tests (behave)
- **Command**: `python -m behave features/`
- **Timestamp**: <ISO 8601>
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

### Classification Markings
- **Timestamp**: <ISO 8601>
- **Result**: <PASS / N/A>
- **Details**: <resolved banner> verified on <N> new/modified .py files (or "N/A — Public/IL2 project, no marking required")
- **Files Verified**: <list files>

### SBOM Generation
- **Command**: `python tools/compliance/sbom_generator.py --project <project_id>`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: SBOM generated, <N> components cataloged

### Integration Smoke Test
- **Command**: `python tools/testing/smoke_test.py --json`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> tools tested, <N> passed, 0 import failures

### Vision Validation
- **Command**: `python tools/testing/screenshot_validator.py --image <screenshot> --assert "..." --json`
- **Timestamp**: <ISO 8601>
- **Result**: <PASS / N/A>
- **Details**: <N assertions passed, model: llava:13b / N/A — no UI changes>
- **Screenshot**: <path to screenshot file>

### CLI Fuzz Test
- **Command**: `python tools/testing/fuzz_cli.py --tools <modified tools> --json`
- **Timestamp**: <ISO 8601>
- **Result**: <PASS / N/A>
- **Details**: <N tools fuzzed, N strategies, 0 crashes / N/A — no CLI changes>

### Acceptance Criteria Validation (V&V)
- **Command**: `python tools/testing/acceptance_validator.py --plan <plan_file> --base-url <url> --pages <pages> --json`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> criteria verified, 0 failed, <N> unverified, <N> pages checked (0 with errors)

## Tier 2 — ATO & Compliance Impact

### NIST 800-53 Control Mapping
- **Command**: `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "<project_id>"`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Controls Mapped**: <list: AC-3, AU-2, SI-2, etc.>
- **Crosswalk Cascade**: <list frameworks updated via crosswalk_engine.py>

### ATO Boundary Impact
- **Timestamp**: <ISO 8601>
- **Result**: <GREEN / YELLOW / ORANGE / RED>
- **Rationale**: <explain why — e.g., "No new components, data flows, or classification changes">
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
- **Detected**: <list of applicable frameworks, or "none detected">

<For each applicable framework, include a subsection with command, timestamp, result, and key metrics. Mark non-applicable frameworks as N/A with reason.>

### Multi-Regime Gate
- **Command**: `python tools/compliance/multi_regime_assessor.py --project-id "<project_id>" --gate`
- **Timestamp**: <ISO 8601>
- **Result**: PASS
- **Details**: <N> frameworks assessed, all passed

## Tier 4 — Architecture & Governance Gates

<For each applicable gate (DevSecOps, ZTA, NIST 800-207, MOSA, Supply Chain, SbD, IV&V), include command, timestamp, result, and key metrics. Mark non-applicable gates as N/A with reason.>

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
- **Action**: Full 4-tier DevSecOps validation passed
- **Project ID**: <project_id>
- **Logged At**: <ISO 8601>

# <resolved banner> (omit if marking_required is false)
```

## Plan Format

```md
# <resolved banner> (omit if marking_required is false)
# Feature: <feature name>

## Metadata
issue_number: `{issue_number}`
run_id: `{run_id}`

## Feature Description
<describe the feature in detail, including its purpose and value>

## User Story
As a <type of user>
I want to <action/goal>
So that <benefit/value>

## Solution Statement
<describe the proposed solution approach>

## ATO Impact Assessment
- **Boundary Impact**: <GREEN/YELLOW/ORANGE/RED>
- **New NIST Controls**: <list any new controls introduced by this feature>
- **SSP Impact**: <none / addendum / revision required>
- **Data Classification Change**: <yes/no — if yes, describe>

## Relevant Files
<list files relevant to the feature with bullet point descriptions>

### New Files
<list any new files that need to be created>

## Implementation Plan
### Phase 1: Foundation
<foundational work needed>

### Phase 2: Core Implementation
<main implementation work>

### Phase 3: Integration & Testing
<integration with existing functionality and test creation>

## Step by Step Tasks
IMPORTANT: Execute every step in order, top to bottom.

<list step by step tasks as h3 headers plus bullet points. Start with tests (TDD).>

## Testing Strategy
### Unit Tests
<unit tests needed>

### BDD Tests
<Gherkin feature files if applicable>

### Edge Cases
<edge cases to test>

## Acceptance Criteria
<specific, measurable criteria for completion>

## Validation Commands
- `python -m py_compile <file>` - Syntax check
- `ruff check .` - Lint check
- `python -m pytest tests/ -v --tb=short` - Unit tests
- `python -m behave features/` - BDD tests (if applicable)
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
<list any NIST controls relevant to this feature>

## Notes
<additional context, future considerations>

# <resolved banner> (omit if marking_required is false)
```

## Feature
$ARGUMENTS

## Report
- Summarize what was done: issue created, branch created, plan written, code implemented, all ICDEV DevSecOps gates passed.
- Include the GitHub issue number and URL.
- Include the path to the plan file in `specs/`.
- Include the path to the validation report in `audit/`.
- List the files that were created or modified.
- Include the commit hash.
- Include a **4-tier gate summary table** with actual results (not just pass/fail — include metrics like "14 tests passed", "0 critical, 0 high, 3 medium", "AC-3, AU-2, SI-2 mapped").
- Include ATO boundary impact assessment result (GREEN/YELLOW/ORANGE/RED) with rationale.
- Tell the user: "Run `/pull_request` to open a PR, or `/test` for a full validation suite."
