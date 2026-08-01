# E2E Test: Second Brain (My AI Assistant)

Verify the Second Brain (/me) canvas: fail-closed auth, the profile/objectives/
customers/briefing pages, integrations, and the daily briefing render correctly.

## Prerequisites
- Flask dashboard running on http://localhost:5050
- Database initialized (`python tools/db/init_icdev_db.py`) with migration 223 applied
- `ICDEV_SECOND_BRAIN_ENABLED=true`
- Authenticated dashboard session (log in, or set `ICDEV_AUTH_BYPASS=1` for a test run)

## Auth Gate (cnr-me-01)

1. Without a session (fresh incognito context), request http://localhost:5050/me/api/second-brain/profile
2. Assert HTTP 401 with JSON `{"error": "Authentication required"}`
3. Without a session, navigate to http://localhost:5050/me/profile
4. Assert redirect to `/login` (no personal data rendered)
5. Log in (or set ICDEV_AUTH_BYPASS=1) and repeat step 1 — assert HTTP 200

## Home / Briefing

6. Navigate to http://localhost:5050/me
7. Wait for the page to fully load
8. Assert the page title / heading references "My AI Assistant" or the briefing
9. Navigate to http://localhost:5050/me/briefing/today
10. Assert the briefing greeting and focus sections are visible (or an empty-state
    prompt to generate today's briefing when none exists)
11. Assert no raw attendee email addresses are rendered in meeting prep notes
    (PII masking — cnr-me-02)

## Profile

12. Navigate to http://localhost:5050/me/profile
13. Assert form fields: Full Name, Work Email, Title, Seniority, Organization,
    Industry, Org Size, Timezone, Briefing Time, Delivery Channels, Comm Style
14. Assert the inferred-persona hint updates when a Title is entered
15. Save the profile and assert a success response (HTTP 200, `ok: true`)

## Objectives / Customers / Challenges

16. Navigate to http://localhost:5050/me/objectives — assert the objectives list renders
17. Navigate to http://localhost:5050/me/customers — assert the relationships table
    and relationship-type legend render
18. Navigate to http://localhost:5050/me/challenges — assert the challenge cards render

## Integrations (msgraph — cnr-me-03)

19. Navigate to http://localhost:5050/me/integrations
20. Assert integration cards render for: Gmail, Google Calendar, Slack, Microsoft
    365 (msgraph), GitHub, GitLab, Jira, Linear, Notion
21. Assert the Microsoft 365 card offers a "Connect" action (OAuth start)

## IQE Widget

22. Assert the IQE query widget is present on the profile/briefing page
23. Submit a query (e.g. "my objectives") and assert a JSON response with `ok: true`

## Screenshots
24. Set viewport to 1920x1080 (desktop)
25. Screenshot full page: `playwright/screenshots/second_brain-desktop-1920x1080.png`
26. Set viewport to 375x812 (mobile)
27. Screenshot full page: `playwright/screenshots/second_brain-mobile-375x812.png`

## Console & Network
28. Check browser console for errors — assert 0 errors
29. Check network requests — assert 0 failed requests (all 200 OK, except the
    intentional 401 from the unauthenticated auth-gate steps)
