# E2E Test: Genesis v2.0 Autonomous Research Lab Dashboard

Verify the Genesis v2.0 dashboard loads correctly with daemon status, 14 reflexes table, GKP promoter stats, and feedback-driven priorities.

## Prerequisites
- Flask dashboard running on http://localhost:5050
- Database initialized with Genesis reflex state

## Screenshot Output
All screenshots MUST be saved to `playwright/screenshots/` directory using the Playwright `browser_take_screenshot` tool. Use descriptive filenames with the pattern: `{feature}-{view}-{viewport}.png`
Example: `browser_take_screenshot` with filename `playwright/screenshots/genesis-desktop-1920x1080.png`

## Steps

### Page Load & Navigation
1. Navigate to http://localhost:5050/login
2. Enter API key "sparkpilot" into the login form and submit
3. Navigate to http://localhost:5050/genesis
4. Wait for the page to fully load
5. Assert the page contains heading "Genesis v2.0 — Autonomous Research Lab"
6. Assert the intro panel contains text "Continuous Self-Improvement Engine"
7. Assert the intro steps show: "Monitor Reflexes", "Review GKPs", "Promote Knowledge"

### Daemon Status Cards
8. Assert stat cards exist: "Daemon Status", "Active Reflexes", "Circuit Breakers Open", "Audit Events (24h)"
9. Assert "Active Reflexes" shows "14"
10. Assert "Circuit Breakers Open" shows "0"

### 14 Reflexes Table
11. Assert the reflex table has headers: Reflex, Tier, Schedule, Status, Last Run, Successes, Failures, Last Metric, Action
12. Assert 14 rows are present in the table (one per reflex)
13. Assert GREEN tier badges appear for: research, scout, audit, comply, ingest, market, report, docs
14. Assert YELLOW tier badges appear for: publish, test, learn, heal
15. Assert ORANGE tier badge appears for: evolve, experiment
16. Assert all reflexes show "ACTIVE" status (no TRIPPED or DISABLED)
17. Assert each row has a "Run" button

### Refresh Status Button
18. Click "Refresh Status" button
19. Wait 2 seconds for API response
20. Assert the stat cards update (values remain consistent)
21. Screenshot the reflexes section

### GKP Promoter Stats
22. Scroll to "Knowledge Bridge (GKP Promoter)" section
23. Assert stat cards exist: "Total GKPs", "Promoted", "Pending Review", "Rejected"
24. Click "Load Stats" button
25. Wait 1 second for API response
26. Assert stat cards populate with numeric values

### Feedback-Driven Priorities
27. Scroll to "Feedback-Driven Priorities" section
28. Click "Check Priorities" button
29. Wait 1 second for API response
30. Assert 14 priority cards appear (one per reflex)
31. Assert each card shows a priority level (NORMAL, BOOST, or REDUCE)
32. Assert each card shows a reason text

### Run Reflex (interactive test)
33. Click the "Run" button on the "scout" reflex row
34. Assert the "Reflex Output" section becomes visible
35. Assert the output panel shows JSON with "success" field
36. Wait for the run to complete (button text returns to "Run")

### Responsive Testing
37. Resize viewport to desktop (1920x1080), screenshot full page as `genesis-desktop-1920x1080.png`
38. Resize viewport to tablet (768x1024), screenshot full page as `genesis-tablet-768x1024.png`
39. Resize viewport to mobile (375x812), screenshot full page as `genesis-mobile-375x812.png`

### Error Checks
40. Check browser console for errors (should be 0)
41. Check network requests for any 4xx/5xx responses (should be 0)

## Expected Results
- All 14 reflexes render with correct tier badges (GREEN/YELLOW/ORANGE)
- Interactive buttons (Refresh, Load Stats, Check Priorities, Run) all trigger API calls and update UI
- No console errors
- No failed network requests
- Responsive layout works at all 3 viewports
