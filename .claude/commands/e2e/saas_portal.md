# E2E Test: SaaS Portal Authentication and Pages

Verify the ICDEV SaaS tenant portal login flow, session management, logout, and that all portal pages load correctly with CUI banners and sidebar navigation.

## Prerequisites
- SaaS API gateway running with portal blueprint registered
- Platform database initialized with at least one tenant and user
- Portal accessible at http://localhost:8443/portal or http://localhost:5000/portal

## Steps

### Login Flow
1. Navigate to /portal/login
2. Wait for the login page to fully load
3. Screenshot the login page
4. Assert the CUI banner "CUI // SP-CTI" is visible at top and bottom
5. Assert the login form contains an API Key input field
6. Assert a "Sign In" submit button is present
7. Assert the page title contains "ICDEV" or "Portal"
8. Assert classification text is present (IL4/IL5, NIST)

### Login with API Key
9. Enter a test API key into the API Key field
10. Click the "Sign In" button
11. Wait for page to load after form submission
12. Screenshot the post-login result
13. Assert redirect to /portal/ dashboard or error message displayed on login page
14. Assert CUI banner is present on the result page

### Logout
15. Navigate to /portal/logout
16. Wait for the redirect
17. Screenshot the post-logout page
18. Assert the URL contains /portal/login
19. Assert the login form is shown again
20. Assert CUI banner is present on the login page

### Unauthenticated Access
21. Navigate to /portal/ without session
22. Assert redirect to /portal/login
23. Navigate to /portal/projects without session
24. Assert redirect to /portal/login
25. Navigate to /portal/compliance without session
26. Assert redirect to /portal/login

### Portal Pages (authenticated or redirect verification)
27. Navigate to /portal/projects — verify CUI banner and "project" content
28. Screenshot the projects page
29. Navigate to /portal/compliance — verify CUI banner and compliance terms
30. Screenshot the compliance page
31. Navigate to /portal/team — verify CUI banner and team/user terms
32. Screenshot the team page
33. Navigate to /portal/settings — verify CUI banner and settings terms
34. Screenshot the settings page
35. Navigate to /portal/keys — verify CUI banner and API key terms
36. Screenshot the keys page
37. Navigate to /portal/usage — verify CUI banner and usage/token terms
38. Screenshot the usage page
39. Navigate to /portal/audit — verify CUI banner and audit trail terms
40. Screenshot the audit page

### Sidebar Navigation
41. On any authenticated portal page, verify sidebar navigation contains: Dashboard, Projects, Compliance, Team, Settings, API Keys, Usage, Audit Trail
42. Verify Sign Out link is present in sidebar footer
43. Screenshot the sidebar

## Expected Results
- Login page loads with CUI banners, API key form, and classification text
- Login with valid API key redirects to dashboard
- Logout clears session and returns to login page
- Unauthenticated access to protected pages redirects to login
- All portal pages load with CUI banners
- Sidebar navigation is consistent across all pages

## CUI Verification
- Check that both header and footer CUI banners are present on every page
- Verify banner text matches exactly: "CUI // SP-CTI"
