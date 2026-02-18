# E2E Test: Security Scan Results Display

Verify security scan results are properly displayed in the dashboard.

## Prerequisites
- Flask dashboard running on http://localhost:5000
- At least one project with security scan results

## Steps

1. Navigate to http://localhost:5000
2. Wait for the dashboard to load
3. Assert the dashboard shows an active alerts section

4. Navigate to the monitoring page via navigation bar
5. Wait for the monitoring page to load
6. Screenshot the monitoring overview

7. Assert the page shows health check status indicators
8. Assert the page shows active alerts (if any)
9. Verify metric display areas are present

10. Navigate back to dashboard
11. Click on the audit trail section or navigate to /audit
12. Wait for the audit trail page to load
13. Screenshot the audit trail page

14. Assert audit entries are displayed in a table format
15. Assert each audit entry shows: timestamp, event type, actor, action
16. Verify CUI banner "CUI // SP-CTI" is present

## Expected Results
- Dashboard shows security summary
- Monitoring page displays health checks and metrics
- Audit trail shows timestamped entries
- All pages have CUI banners

## CUI Verification
- Check header and footer CUI banners on every page visited
