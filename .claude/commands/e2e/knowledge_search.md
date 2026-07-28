# E2E Test: Knowledge Search (RAG) Dashboard

Verify the Knowledge Search page at `/knowledge-search` renders correctly, displays RAG system status, and supports semantic search.

## Prerequisites
- Dashboard running on port 5000
- API key for authentication

## Steps

### Login
1. Navigate to `http://localhost:5050/login`
2. Fill in the API key field with the test API key
3. Click "Login" button
4. Assert redirect to home page

### CUI Banner Verification
5. Assert the CUI banner is visible at the top of the page
6. Assert the CUI banner contains "CUI" text
7. Assert the CUI banner is visible at the bottom of the page

### Navigate to Knowledge Search
8. Navigate to `http://localhost:5050/knowledge-search`
9. Assert the page title contains "Knowledge Search"
10. Assert the heading "Knowledge Search" is visible
11. Assert the subtitle mentions "RAG-powered search"

### Stat Grid
12. Assert the stat grid container is visible
13. Assert "Total Chunks" stat card is visible
14. Assert "Source Types" stat card is visible
15. Assert "Active Tiers" stat card is visible
16. Assert "Backend" stat card is visible
17. Assert "Status" stat card is visible (shows Online/Offline)

### Search Controls
18. Assert the search input field is visible with placeholder text
19. Assert the source type filter dropdown is visible
20. Assert the top-K input field is visible with default value "5"
21. Assert the "Search" button is visible

### Example Queries
22. Assert example query links are visible
23. Assert at least one example query link is clickable

### Search Functionality
24. Fill in the search input with "FedRAMP AC-2 implementation patterns"
25. Click the "Search" button
26. Wait for search results to appear (or "No results found" message)
27. If results appear, assert each result card has:
    - Source type label
    - Relevance score
    - Content preview
    - Source ID and table attribution

### Source Filter
28. Select a source type from the dropdown (if options available)
29. Fill in search input with "compliance"
30. Click "Search" button
31. Wait for results
32. Assert results (if any) match the selected source type

### Top-K Control
33. Clear the search input
34. Set top-K value to "3"
35. Fill in search input with "security"
36. Click "Search"
37. Assert at most 3 results appear

### Example Query Click
38. Click on the first example query link
39. Assert the search input is populated with the example query text
40. Assert search results appear (or no results message)

### Enter Key Search
41. Clear the search input
42. Click on the search input
43. Type "supply chain" and press Enter
44. Assert search results panel becomes visible

### Source Distribution Chart
45. If chart section exists, assert SVG element is present
46. Assert chart has appropriate bars or "no data" indicator

### Tier Breakdown
47. If tier breakdown section exists, assert tier cards are visible
48. Assert tier labels include "hot", "warm", or "cold"

### Recent Searches Table
49. Assert "Recent Searches" heading is visible
50. Assert the recent searches table is visible
51. Assert table has columns: Query Hash, Results, Top Score, Re-ranked, Duration, Time
52. If searches have been performed, assert at least one row in the table

### Console Check
53. Check browser console for errors — assert no JavaScript errors

### Responsive Design
54. Resize browser to desktop (1440x900) and screenshot as `playwright/screenshots/knowledge-search-desktop.png`
55. Resize browser to tablet (768x1024) and screenshot as `playwright/screenshots/knowledge-search-tablet.png`
56. Resize browser to mobile (375x812) and screenshot as `playwright/screenshots/knowledge-search-mobile.png`

### Breadcrumb Navigation
57. Assert breadcrumb navigation is visible
58. Click on "Home" breadcrumb and assert redirect to home page

## Expected Results
- Page loads with HTTP 200
- Stat grid displays RAG system status (may show "--" if no data)
- Search input accepts text and returns results or "No results found"
- Source filter dropdown populated with available source types
- Top-K control limits result count
- Example queries auto-populate search and trigger search
- Enter key triggers search
- Recent searches table shows search history
- No JavaScript console errors
- Responsive layout adapts to all viewports

## CUI Verification
- Top banner displays CUI marking
- Bottom banner displays CUI marking
- Page content between banners

## Screenshots
- `playwright/screenshots/knowledge-search-desktop.png`
- `playwright/screenshots/knowledge-search-tablet.png`
- `playwright/screenshots/knowledge-search-mobile.png`
