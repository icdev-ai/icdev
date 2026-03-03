# E2E Test: Activity Feed and Usage Tracking Pages

Verify the ICDEV dashboard activity page loads with SSE connection indicator and activity entries, and the usage page displays cost breakdowns with period selection.

## Prerequisites
- Flask dashboard running on http://localhost:5000
- Database initialized with audit trail and usage records

## Steps

### Activity Page
1. Navigate to http://localhost:5000/activity
2. Wait for the page to fully load
3. Screenshot the activity page
4. Assert the CUI banner "CUI // SP-CTI" is visible at top and bottom

### SSE Connection Indicator
5. Assert connection status indicators are present (connected, live, real-time, streaming, status)
6. Check for SSE connection status UI element (.connection-status, .sse-status, .live-indicator)
7. Screenshot the connection indicator area

### Activity Entries
8. Assert activity-related terms are present (activity, event, action, timestamp, audit, log, feed)
9. Check for table rows or list items displaying activity entries
10. If entries exist, verify the first entry is visible
11. If no entries, verify empty state message is displayed (no activity, no entries, empty)
12. Screenshot the activity entries

### Activity Navigation
13. Navigate to http://localhost:5000/
14. Click on the "Activity" navigation link
15. Wait for the activity page to load
16. Assert the URL contains /activity
17. Screenshot the activity page via navigation
18. Assert CUI banner is present

### Usage Page
19. Navigate to http://localhost:5000/usage
20. Wait for the page to fully load
21. Screenshot the usage page
22. Assert the CUI banner "CUI // SP-CTI" is visible at top and bottom

### Cost Breakdown
23. Assert cost/usage-related terms are present (cost, usage, token, api call, total, provider, billing)
24. Assert summary cards or metric displays are visible
25. Check for breakdown tables or charts
26. Assert numeric values are present on the page (usage counts, costs)
27. Screenshot the cost breakdown section

### Period Selector
28. Check for period/date selector elements (period, date, range, month, week, day, filter)
29. Check for select dropdown, date picker, or filter button elements
30. If a select dropdown is present, verify it has multiple options
31. If filter buttons are present, verify they are visible and clickable
32. Screenshot the period selector

### Usage Navigation
33. Navigate to http://localhost:5000/
34. Click on the "Usage" navigation link
35. Wait for the usage page to load
36. Assert the URL contains /usage
37. Screenshot the usage page via navigation
38. Assert CUI banner is present

## Expected Results
- Activity page loads with SSE connection indicator
- Activity entries display in table or list format with event details
- Empty state shown gracefully when no activity exists
- Usage page loads with cost/token usage breakdown
- Period selector allows filtering by time range
- Summary cards show numeric usage metrics
- Navigation from dashboard works for both pages
- CUI banners present on all pages

## CUI Verification
- Check header and footer CUI banners on every page visited
- Verify banner text matches exactly: "CUI // SP-CTI"
