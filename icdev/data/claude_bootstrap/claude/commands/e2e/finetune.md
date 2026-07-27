# E2E Test: Fine-Tuning Dashboard

Verify the Fine-Tuning dashboard pages at `/finetune` render correctly, display system status, and support dataset/job/model management.

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

### Navigate to Fine-Tuning Overview
8. Navigate to `http://localhost:5050/finetune`
9. Assert the page title contains "Fine-Tuning"
10. Assert the heading "Fine-Tuning" is visible

### Stat Grid
11. Assert the stat grid container is visible
12. Assert "Datasets" stat card is visible
13. Assert "Training Jobs" stat card is visible
14. Assert "Models" stat card is visible
15. Assert "Active Overrides" stat card is visible

### Recent Jobs Section
16. Assert a "Recent Training Jobs" section is visible
17. Assert a table or empty state message is visible

### Navigation Links
18. Assert links to sub-pages are visible (Datasets, Jobs, Models)

### Datasets Page
19. Navigate to `http://localhost:5050/finetune/datasets`
20. Assert the heading contains "Datasets"
21. Assert a "Create Dataset" button or link is visible
22. Assert a dataset table or empty state is visible

### Jobs Page
23. Navigate to `http://localhost:5050/finetune/jobs`
24. Assert the heading contains "Training Jobs"
25. Assert a table or empty state is visible

### Models Page
26. Navigate to `http://localhost:5050/finetune/models`
27. Assert the heading contains "Model Versions"
28. Assert a table or empty state is visible

### Label Page
29. Navigate to `http://localhost:5050/finetune/label`
30. Assert the heading contains "Labeling"
31. Assert dataset selector dropdown is visible
32. Assert batch action buttons are visible (Approve, Reject)

### Evaluate Page
33. Navigate to `http://localhost:5050/finetune/evaluate`
34. Assert the heading contains "Evaluations"
35. Assert a table or empty state is visible

### Screenshots
36. Navigate to `http://localhost:5050/finetune`
37. Take screenshot at desktop viewport (1920x1080) - save as `playwright/screenshots/finetune-desktop.png`
38. Resize to tablet viewport (768x1024)
39. Take screenshot - save as `playwright/screenshots/finetune-tablet.png`
40. Resize to mobile viewport (375x812)
41. Take screenshot - save as `playwright/screenshots/finetune-mobile.png`

### Console Error Check
42. Check browser console for errors
43. Assert no JavaScript errors on any finetune page
44. Assert no 500-level HTTP errors in network requests

## Expected Results
- All 9 finetune pages load without errors
- Stat grids show numeric values (may be 0 for empty database)
- Tables render with proper headers even when empty
- CUI banners visible on all pages
- No console errors or 500 responses
- Screenshots captured at 3 viewports
