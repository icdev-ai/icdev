# E2E Test: Agents and Monitoring Pages

Verify the ICDEV dashboard agents page displays the agent grid with status indicators, and the monitoring page displays health checks, status icons, and metric areas.

## Prerequisites
- Flask dashboard running on http://localhost:5000
- Database initialized with agent registry data

## Steps

### Agents Page
1. Navigate to http://localhost:5000/agents
2. Wait for the page to fully load
3. Screenshot the agents page
4. Assert the CUI banner "CUI // SP-CTI" is visible at top and bottom
5. Assert the page contains all 8 core agent names: Orchestrator, Architect, Builder, Compliance, Security, Infrastructure, Knowledge, Monitor
6. Assert agent card or grid elements are visible
7. Assert status indicators are present (status, health, port, active/idle)
8. Check for agent port numbers (8443-8458 range)
9. Check for extended agents: MBSE, Modernization, Requirements, Supply Chain, Simulation

### Agents Navigation
10. Navigate to http://localhost:5000/
11. Click on the "Agents" navigation link
12. Wait for the agents page to load
13. Assert the URL contains /agents
14. Screenshot the agents page via navigation
15. Assert CUI banner is present

### Monitoring Page
16. Navigate to http://localhost:5000/monitoring
17. Wait for the monitoring page to load
18. Screenshot the monitoring overview
19. Assert CUI banner "CUI // SP-CTI" is visible
20. Assert the page shows health-related terms (health, status, metric, monitor, alert)
21. Assert status icon elements are visible (badges, indicators, status dots)
22. Assert health indicator terms are present (healthy, degraded, offline, up, down, warning, critical)

### Monitoring Metrics
23. Assert metric display areas are present (cards, panels, chart containers)
24. Assert metric-related terms are present (metric, count, rate, latency, uptime, error)
25. Check for alert-related elements (alert, notification, warning, active alerts)
26. Screenshot the metrics area

### Monitoring Navigation
27. Navigate to http://localhost:5000/
28. Click on the "Monitoring" navigation link (may appear as "Monitor")
29. Wait for the monitoring page to load
30. Assert the URL contains /monitoring
31. Screenshot the monitoring page via navigation
32. Assert CUI banner is present

## Expected Results
- Agents page loads with all 8 core agents displayed
- Agent status indicators show health/port information
- Monitoring page loads with health checks and metric displays
- Status icons are visible with appropriate health indicators
- Navigation from dashboard works for both pages
- CUI banners present on all pages

## CUI Verification
- Check header and footer CUI banners on every page visited
- Verify banner text matches exactly: "CUI // SP-CTI"
