# E2E Test: File Sync Dashboard

Verify the File Sync dashboard page renders correctly with stat grid, job table, activity log, and create modal.

## Prerequisites
- Flask dashboard running on http://localhost:5050
- Database initialized (`python tools/db/init_icdev_db.py`)
- Valid API key for dashboard login

## Steps

1. Navigate to http://localhost:5050/filesync
2. Wait for the page to fully load
3. Verify the page title contains "File Sync"

### Stat Grid
4. Assert 6 stat cards are visible: Total Jobs, Active, Completed, Failed, Conflicts, Transferred
5. Verify stat values are numeric (0 or greater)

### Action Buttons
6. Assert "New Sync Job" button is visible
7. Assert "Run All" button is visible
8. Assert "Refresh" button is visible

### Sync Jobs Table
9. Assert "Sync Jobs" heading is visible
10. Assert table headers: Name, Source, Dest, Mode, Status, Last Run, Files, Actions
11. Assert empty state message: "No sync jobs configured" (when no jobs exist)
12. Assert search box and "Export CSV" button are present

### Recent Sync Activity Table
13. Assert "Recent Sync Activity" heading is visible
14. Assert table headers: Time, Job, Action, Path, Bytes, Duration, Detail
15. Assert empty state message: "No sync activity yet." (when no activity)
16. Assert search box and "Export CSV" button are present

### Create Sync Job Modal
17. Click "New Sync Job" button
18. Assert modal opens with heading "Create Sync Job"
19. Assert form fields: Job Name, Source Path, Source Provider, Destination Path, Destination Provider, Sync Mode, Conflict Strategy
20. Assert Source Provider dropdown has options: Local, SFTP, AWS S3, Azure Blob, GCS
21. Assert Sync Mode dropdown has options: Push, Pull, Bidirectional
22. Assert Conflict Strategy dropdown has options: Last Write Wins, Source Wins, Rename Both, Skip
23. Assert "Cancel" and "Create" buttons are visible
24. Click "Cancel" to close modal
25. Assert modal is hidden

### Screenshots
26. Set viewport to 1920x1080 (desktop)
27. Screenshot full page: `playwright/screenshots/filesync-desktop-1920x1080.png`
28. Set viewport to 768x1024 (tablet)
29. Screenshot full page: `playwright/screenshots/filesync-tablet-768x1024.png`
30. Set viewport to 375x812 (mobile)
31. Screenshot full page: `playwright/screenshots/filesync-mobile-375x812.png`

### Console & Network
32. Check browser console for errors — assert 0 errors
33. Check network requests — assert 0 failed requests (all 200 OK)
