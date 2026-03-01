//////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D — Authorized DoD Personnel Only
//////////////////////////////////////////////////////////////////

# STIG Checklist: Web Application Security STIG

**Project:** BDD Test Project (proj-test)
**STIG ID:** webapp
**STIG Version:** 2.0
**Target Type:** app
**Assessment Date:** 2026-03-01 04:15 UTC
**Assessed By:** ICDEV STIG Checker (automated)
**Classification:** CUI // SP-CTI

---

## Summary

| Severity | Open | Not A Finding | Not Applicable | Not Reviewed | Total |
|----------|------|---------------|----------------|--------------|-------|
| CAT1 | 2 | 0 | 0 | 2 | 4 |
| CAT2 | 0 | 0 | 0 | 7 | 7 |
| CAT3 | 1 | 0 | 0 | 2 | 3 |
| **Total** | | | | | **14** |

---

## Detailed Findings

### V-222602: The application must not store sensitive information in URL parameters

**Rule ID:** SV-222602r879587
**Severity:** CAT1
**Status:** Open

**Comments:** Potential sensitive URL params in: C:\Users\schuo\Downloads\ICDev\node_modules\ufo\dist\index.d.ts, C:\Users\schuo\Downloads\ICDev\tools\saas\portal\static\portal.js

---

### V-222604: The application must implement input validation on all user-controllable input

**Rule ID:** SV-222604r879589
**Severity:** CAT1
**Status:** Not Reviewed

**Comments:** Validation patterns detected; manual verification of completeness needed.

---

### V-222607: The application must enforce approved authorizations for access to resources

**Rule ID:** SV-222607r879592
**Severity:** CAT1
**Status:** Not Reviewed

**Comments:** Authorization patterns detected; verify enforcement completeness manually.

---

### V-222609: The application must use FIPS 140-2/140-3 validated cryptographic modules

**Rule ID:** SV-222609r879594
**Severity:** CAT1
**Status:** Open

**Comments:** Deprecated crypto found in: C:\Users\schuo\Downloads\ICDev\.claude\hooks\pre_tool_use.py, C:\Users\schuo\Downloads\ICDev\.claude\hooks\user_prompt_submit.py, C:\Users\schuo\Downloads\ICDev\.tmp\e2e-logistics\context\agentic\capability_registry.yaml, C:\Users\schuo\Downloads\ICDev\.tmp\e2e-logistics\src\app.py, C:\Users\schuo\Downloads\ICDev\.tmp\e2e-logistics\tools\a2a\agent_registry.py

---

### V-222612: The application must set the Secure and HttpOnly flags on session cookies

**Rule ID:** SV-222612r879597
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** Cookie security patterns detected; verify all cookies are covered.

---

### V-222614: The application must implement security headers to prevent common attacks

**Rule ID:** SV-222614r879599
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** Security header patterns found (54 matches); verify all required headers.

---

### V-222617: The application must protect against Cross-Site Request Forgery (CSRF)

**Rule ID:** SV-222617r879602
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** CSRF protection patterns detected; verify coverage of all state-changing endpoints.

---

### V-222620: The application must generate audit records for security-relevant events

**Rule ID:** SV-222620r879605
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** Logging patterns detected; verify all security events are captured per AU-2.

---

### V-222623: The application must enforce password complexity requirements

**Rule ID:** SV-222623r879608
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** Requires manual assessment.

---

### V-222626: The application must configure session timeout and management controls

**Rule ID:** SV-222626r879611
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** Requires manual assessment.

---

### V-222629: The application must protect CUI data at rest using encryption

**Rule ID:** SV-222629r879614
**Severity:** CAT2
**Status:** Not Reviewed

**Comments:** Requires manual assessment.

---

### V-222632: The application must display a DoD-approved banner before granting access

**Rule ID:** SV-222632r879617
**Severity:** CAT3
**Status:** Not Reviewed

**Comments:** Requires manual assessment.

---

### V-222635: The application must not expose detailed error messages to users

**Rule ID:** SV-222635r879620
**Severity:** CAT3
**Status:** Open

**Comments:** Debug mode or detailed error exposure detected.

---

### V-222638: The application must implement file upload restrictions

**Rule ID:** SV-222638r879623
**Severity:** CAT3
**Status:** Not Reviewed

**Comments:** Requires manual assessment.

---

//////////////////////////////////////////////////////////////////
CUI // SP-CTI | Department of Defense
//////////////////////////////////////////////////////////////////
