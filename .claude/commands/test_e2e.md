# ICDEV E2E Test Runner

Execute end-to-end (E2E) tests using Playwright browser automation (MCP Server). If any errors occur and assertions fail, mark the test as failed and explain exactly what went wrong.

## Variables

run_id: $1 if provided, otherwise generate a random 8 character hex string
agent_name: $2 if provided, otherwise use 'e2e_test_runner'
e2e_test_file: $3
application_url: $4 if provided, otherwise use http://localhost:5000

## Instructions

- Read the `e2e_test_file`
- Digest the `User Story` (if present) to understand what we're validating
- IMPORTANT: Execute the `Test Steps` detailed in the `e2e_test_file` using Playwright browser automation
- Review any `Success Criteria` and if any fail, mark the test as failed and explain exactly what went wrong
- Review steps that say '**Verify**...' or '**Assert**...' — if they fail, mark the test as failed
- Capture screenshots as specified
- IMPORTANT: Return results in the format requested by the `Output Format`
- Initialize Playwright browser in headless mode (per playwright-mcp-config.json)
- Use the `application_url` (default: http://localhost:5000 for ICDEV dashboard)
- Allow time for async operations and element visibility
- IMPORTANT: After taking each screenshot, save it to `Screenshot Directory` with descriptive names
- Capture and report any errors encountered
- If you encounter an error, mark the test as failed immediately and explain what went wrong and on what step
- **CUI Verification:** On every page, verify the CUI // SP-CTI banner is visible at top and bottom

## Setup

Ensure the ICDEV dashboard or target application is running before executing tests.

## Screenshot Directory

.tmp/test_runs/<run_id>/<agent_name>/screenshots/<test_name>/*.png

Each screenshot should be saved with a descriptive name reflecting what is being captured.

## Report

- Exclusively return the JSON output as specified below
- Capture any unexpected errors
- IMPORTANT: Ensure all screenshots are saved in the `Screenshot Directory`

### Output Format

```json
{
  "test_name": "Test Name Here",
  "status": "passed|failed",
  "screenshots": [
    ".tmp/test_runs/<run_id>/<agent_name>/screenshots/<test_name>/01_initial_state.png",
    ".tmp/test_runs/<run_id>/<agent_name>/screenshots/<test_name>/02_after_action.png"
  ],
  "error": null
}
```
