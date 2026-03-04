# Review

Follow the `Instructions` below to **review work done against a specification file** to ensure implemented features match requirements. Use the spec file to understand requirements and git diff to understand changes.

## Variables

run_id: $1
spec_file: $2
agent_name: $3 if provided, otherwise use 'review_agent'

## Instructions

- Check current git branch using `git branch`
- Run `git diff origin/main` to see all changes made in current branch
- Find and read the spec file to understand requirements

### Adversarial Review Protocol (BMAD-adapted)

**CRITICAL: You MUST adopt a deliberately skeptical stance.** Assume the implementation contains problems — your job is to find them. A review that finds zero issues is incomplete and must be re-examined.

- **Minimum Issue Requirement:** Every review MUST surface at least **3 issues** across all severity levels. If you find fewer than 3, re-examine using these lenses:
  1. **Requirements Coverage Audit** — Are ALL spec requirements implemented? Check each one.
  2. **Missing Edge Cases** — What happens with empty input, concurrent access, network failure, auth expiry?
  3. **Security Deep Dive** — SQL injection, XSS, hardcoded secrets, missing input validation, improper error exposure?
  4. **Consistency Check** — Does the code follow existing patterns? Naming conventions? Error handling style?
  5. **Testability Gap** — Are there untested code paths? Missing test cases for error scenarios?
  6. **Documentation Gap** — Missing CUI markings, unclear variable names, complex logic without comments?

- **Issue Severity Guidelines:**
  - `skippable` — non-blocker but still a problem (style, minor inconsistency)
  - `tech_debt` — non-blocker but creates technical debt (missing tests, poor naming, dead code)
  - `blocker` — must be fixed before release, harms user experience or security

- **Severity Distribution Expectation:** A healthy review typically surfaces 1-2 blockers, 2-3 tech_debt, and 2-4 skippable issues. If you only find skippable issues, dig deeper — the implementation likely has hidden problems.

- All generated artifacts MUST include CUI markings: `CUI // SP-CTI`
- Verify CUI markings are present on all generated artifacts
- Check NIST 800-53 control compliance where applicable
- IMPORTANT: Return ONLY the JSON object with review results

## Report

- IMPORTANT: Return results exclusively as a JSON object:

```json
{
    "success": "boolean - true if NO BLOCKING issues, false if BLOCKING issues exist",
    "review_summary": "string - 2-4 sentences describing what was built and spec conformance",
    "review_issues": [
        {
            "review_issue_number": "number",
            "issue_description": "string",
            "issue_resolution": "string",
            "issue_severity": "string - skippable|tech_debt|blocker"
        }
    ],
    "adversarial_review": {
        "total_issues_found": "number - must be >= 3",
        "re_examination_performed": "boolean - true if initial pass found < 3 issues and reviewer dug deeper",
        "lenses_applied": ["list of adversarial lenses used: requirements_coverage|edge_cases|security|consistency|testability|documentation"]
    },
    "cui_markings_verified": "boolean - true if CUI markings present on all artifacts",
    "nist_controls_checked": ["list of NIST control IDs verified"]
}
```
