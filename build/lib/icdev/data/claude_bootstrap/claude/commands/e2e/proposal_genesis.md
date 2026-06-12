# E2E Test: Proposal Genesis — Autonomous Capture Pipeline Dashboard

Verify the Proposal Genesis dashboard loads correctly with daemon status, Phase A reflexes table, quality scores, and audit trail.

## Prerequisites
- Flask dashboard running on http://localhost:5050
- Database initialized with Proposal Genesis tables (pg_ prefix)

## Screenshot Output
All screenshots MUST be saved to `playwright/screenshots/` directory using the Playwright `browser_take_screenshot` tool. Use descriptive filenames with the pattern: `{feature}-{view}-{viewport}.png`
Example: `browser_take_screenshot` with filename `playwright/screenshots/proposal-genesis-desktop-1920x1080.png`

## Steps

### Page Load & Navigation
1. Navigate to http://localhost:5050/login
2. Enter API key into the login form and submit
3. Navigate to http://localhost:5050/proposal-genesis
4. Wait for the page to fully load
5. Assert the page contains heading "Proposal Genesis — Autonomous Capture Pipeline"
6. Assert the intro panel contains text "Capture-to-Delivery Automation"
7. Assert the intro steps show: "Discover Opps", "Extract Reqs", "Map & Draft", "Quality Check"

### Summary Stat Cards
8. Assert stat cards exist: "Daemon Status", "Active Opportunities", "Shall Statements", "Pending Drafts", "Avg Quality Score", "Pulse Links"
9. Assert "Daemon Status" shows either "ENABLED" or "DISABLED"

### Phase A Reflexes Table
10. Assert the reflex table has headers: Reflex, Phase, Tier, Status, Last Run, Successes, Failures, Last Metric, Action
11. Assert Phase A reflexes are present (discover, extract, map, draft, polish)
12. Assert tier badges appear (GREEN or YELLOW)
13. Assert status badges show ACTIVE, DISABLED, or TRIPPED
14. Assert each row has a "Run" button

### Refresh Status Button
15. Click "Refresh Status" button
16. Wait 2 seconds for API response
17. Assert the stat cards update (daemon status refreshes)
18. Assert no errors appear

### Run Full Pipeline Button
19. Assert "Run Full Pipeline" button exists with warning styling
20. Do NOT click (long-running operation) — verify presence only

### Quality Scores Table
21. Scroll to "Quality Scores (R8 Polish)" section
22. Assert quality table has headers: Opportunity, Draft ID, Composite, Grammar, Readability, Tone, Plagiarism, AI Detection, Created
23. Click "Refresh" button under quality scores
24. Wait 1 second for API response
25. Assert the table updates (shows scores or "No quality scores yet" placeholder)

### Audit Trail Table
26. Scroll to "Audit Trail" section
27. Assert audit table has headers: Timestamp, Reflex, Action, Opportunity, Details
28. Click "Refresh" button under audit trail
29. Wait 1 second for API response
30. Assert the table updates (shows events or "No audit events yet" placeholder)

### Run Single Reflex (interactive test)
31. Click the "Run" button on the "polish" reflex row (lightweight, no external deps)
32. Wait for the "Reflex Output" section to become visible
33. Assert the output panel displays JSON content
34. Assert the run button text returns to "Run"

### Responsive Testing
35. Resize viewport to desktop (1920x1080), screenshot full page as `proposal-genesis-desktop-1920x1080.png`
36. Resize viewport to tablet (768x1024), screenshot full page as `proposal-genesis-tablet-768x1024.png`
37. Resize viewport to mobile (375x812), screenshot full page as `proposal-genesis-mobile-375x812.png`

### Error Checks
38. Check browser console for errors (should be 0)
39. Check network requests for any 4xx/5xx responses (should be 0)

## Expected Results
- All Phase A reflexes render with correct tier and status badges
- Interactive buttons (Refresh Status, Quality Refresh, Audit Refresh, Run) trigger API calls and update UI
- No console errors
- No failed network requests
- Responsive layout works at all 3 viewports
