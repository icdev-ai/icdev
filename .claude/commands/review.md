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
- IMPORTANT: Issue Severity Guidelines:
  - `skippable` — non-blocker but still a problem
  - `tech_debt` — non-blocker but creates technical debt
  - `blocker` — must be fixed before release, harms user experience
- All generated artifacts MUST include CUI markings: `CUI // SP-CTI`
- Verify CUI markings are present on all generated artifacts
- Check NIST 800-53 control compliance where applicable
- IMPORTANT: Return ONLY the JSON array with review results

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
    "cui_markings_verified": "boolean - true if CUI markings present on all artifacts",
    "nist_controls_checked": ["list of NIST control IDs verified"]
}
```
