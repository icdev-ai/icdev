# E2E Test: Industry Research Engine Dashboard

Verify the Research Engine dashboard page loads correctly, displays stat grid, vertical dropdown, session creation form, and sessions table.

## Prerequisites
- Flask dashboard running on http://localhost:5050
- Database initialized with research tables (`research_sessions`, `research_verticals`, etc.)
- Verticals loaded via `python tools/research/vertical_loader.py --load`
- Dashboard API key available for login

## Steps

### 1. Login and Navigate
1. Navigate to http://localhost:5050/login
2. Enter API key and submit
3. Verify redirect to dashboard home
4. Click "Research" link in the navigation bar
5. Wait for the research page to load
6. Verify page title contains "Industry Research"
7. Screenshot the research page (desktop 1440x900)

### 2. CUI Banner Verification
8. Assert the CUI banner "CUI // SP-CTI" is visible at the top of the page
9. Assert the CUI banner "CUI // SP-CTI" is visible at the bottom of the page
10. Assert inline CUI markings are present in the main content area

### 3. Page Structure Verification
11. Assert the heading "Industry Research Engine" is visible
12. Assert the breadcrumb shows "Home > Research"
13. Assert the stat grid contains 4 cards: "Total Sessions", "Active Sessions", "Verticals Loaded", "Dossiers Generated"
14. Assert "Verticals Loaded" shows a non-zero value (6 expected)

### 4. Session Creation Form
15. Assert the "Start New Research Session" form is visible
16. Assert the "Session Name" text input is present with placeholder
17. Assert the "Vertical" dropdown contains options (Cybersecurity, Defense & Intelligence, Financial Technology, Healthcare & Life Sciences, Logistics & Supply Chain, Trading & Financial Markets)
18. Assert the "Focus Areas" textarea is present
19. Assert the "Start Research" button is present

### 5. Form Validation
20. Click "Start Research" without filling any fields
21. Handle the alert dialog "Select a vertical."
22. Verify the form was not submitted (no new session in table)

### 6. Session Creation
23. Fill in "Session Name" with "E2E Test Research Session"
24. Select "Cybersecurity" from the Vertical dropdown
25. Fill in "Focus Areas" with "zero trust adoption\ncloud security posture"
26. Click "Start Research"
27. Wait for success message "Session created: rsess-..."
28. Screenshot the page showing the success message

### 7. Sessions Table Verification
29. Assert the sessions table shows the new session row
30. Assert the row contains: Name="E2E Test Research Session", Vertical="Cybersecurity", Status="created"
31. Assert the pipeline status badges are visible (created, scoping, scanning, synthesizing, dossier_ready)
32. Assert a "Run" button is present in the Actions column for sessions with status "created"
33. Assert the search box and "Export CSV" button are present
34. Assert "Showing 1 row" text is visible

### 8. Database Validation
35. Query `research_sessions` table for the created session
36. Verify `name`, `vertical_id`, `status`, `pipeline_stage`, `focus_areas` match expected values
37. Verify `created_at` timestamp is recent

### 9. Responsive Layout Testing
38. Resize viewport to tablet (768x1024)
39. Screenshot the research page at tablet viewport
40. Verify stat grid wraps correctly (3+1 layout)
41. Verify form is still usable

42. Resize viewport to mobile (375x812)
43. Screenshot the research page at mobile viewport
44. Verify stat cards stack vertically
45. Verify form inputs are accessible

46. Resize viewport back to desktop (1440x900)

### 10. View Dossier Verification (requires completed session)
47. If a session with status "dossier_ready" or "reviewed" exists, assert "View Dossier" button is visible
48. Click "View Dossier" button
49. Wait for the "Research Dossier" section to appear below the sessions table
50. Assert the section navigation sidebar contains section links (Executive Summary, Vertical Overview, Challenge Analysis, etc.)
51. Assert the dossier content area shows markdown content with CUI markings
52. Assert the "API Response" panel shows raw JSON data
53. Screenshot the dossier viewer

### 11. Retry Button Verification (requires errored session)
54. If a session with status "error" exists, assert "Retry" button is visible
55. Click "Retry" button
56. Verify no JS errors in console

### 12. Console Error Check
57. Check browser console for errors
58. Verify no research-page-specific errors (SSE polling errors from live.js are pre-existing and acceptable)

## Expected Results
- Research page loads without errors (HTTP 200)
- CUI // SP-CTI banners visible on all viewports
- Navigation bar includes "Research" link with active state
- Stat grid shows correct counts (0 sessions initially, 6 verticals)
- Vertical dropdown populated with 6 industry verticals
- Form validation prevents submission without vertical selection
- Session creation succeeds and appears in table with pipeline badges
- Pipeline badges use correct statuses: created, scoping, scanning, synthesizing, dossier_ready
- "View Dossier" button appears for sessions with status dossier_ready or reviewed
- Dossier viewer renders section navigation + markdown content with CUI markings
- "Retry" button appears for sessions with status error
- Session persisted correctly in SQLite database
- Page renders correctly at desktop, tablet, and mobile viewports
- No research-specific console errors

## CUI Verification
- Check that both header and footer CUI banners are present
- Check inline CUI markings in main content area
- Verify banner text matches exactly: "CUI // SP-CTI"

## Screenshots
- `playwright/screenshots/research-desktop-1440x900.png` — Desktop layout
- `playwright/screenshots/research-tablet-768x1024.png` — Tablet layout
- `playwright/screenshots/research-mobile-375x812.png` — Mobile layout
- `playwright/screenshots/research-session-created.png` — After session creation
