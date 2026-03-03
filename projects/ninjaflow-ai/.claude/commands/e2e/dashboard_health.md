# E2E Test: Dashboard Health Check

Verify the ICDEV web dashboard loads correctly with CUI banners and core navigation.

## Prerequisites
- Flask dashboard running on http://localhost:5000
- Database initialized with at least one project

## Steps

1. Navigate to http://localhost:5000
2. Wait for the page to fully load
3. Verify the page title contains "ICDEV"
4. Screenshot the full dashboard page

5. Assert the CUI banner "CUI // SP-CTI" is visible at the top of the page
6. Assert the CUI banner "CUI // SP-CTI" is visible at the bottom of the page

7. Verify the navigation bar contains links: Projects, Agents, Compliance, Security, Monitoring, Audit
8. Click on the "Projects" navigation link
9. Wait for the projects page to load
10. Screenshot the projects list page

11. Assert the projects page displays a table or list
12. Verify the CUI banner is present on the projects page

13. Navigate back to the dashboard
14. Click on the "Agents" navigation link
15. Wait for the agents page to load
16. Screenshot the agents page

17. Assert the agents page shows the 8-agent grid (Orchestrator, Architect, Builder, Compliance, Security, Infrastructure, Knowledge, Monitor)

## Expected Results
- Dashboard loads without errors
- CUI // SP-CTI banners are visible on every page
- Navigation links work correctly
- Projects page shows project data
- Agents page shows all 8 agents

## CUI Verification
- Check that both header and footer CUI banners are present
- Verify banner text matches exactly: "CUI // SP-CTI"
