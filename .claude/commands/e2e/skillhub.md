# E2E Test: SkillHub Skill Browser

Verify the SkillHub Skill Browser page loads correctly and all features work.

## Prerequisites
- Flask dashboard running on http://localhost:5077
- Database initialized

## Steps

1. Navigate to http://localhost:5077/skillhub
2. Wait for the page to fully load
3. Screenshot the SkillHub page at desktop viewport (1920x1080)
4. Verify the page title or heading contains "SkillHub" or "Skill Browser"
5. Assert the CUI banner "CUI // SP-CTI" is visible

6. Verify the search form exists with an input field and search button
7. Type "system architect" into the search input
8. Click the Search button
9. Wait for search results to appear (up to 30 seconds — live API call)
10. Screenshot the search results
11. Assert at least 1 search result is displayed

12. Verify the Import Queue section exists
13. Check if there are any items in the import queue table
14. If items exist, verify each row has action buttons (Promote, Unpromote, Install, Trust, Rate, or Update?)
15. Screenshot the import queue section

16. Check console for JavaScript errors — report any found
17. Check network requests for 500-level errors — report any found

## Expected Results
- SkillHub page loads without errors
- Search returns results from the live SkillHub API
- Import queue displays with proper action buttons based on status
- No JavaScript console errors
- No 500-level network errors
- CUI banners present

## Screenshots
- `playwright/screenshots/skillhub-main-1920x1080.png`
- `playwright/screenshots/skillhub-search-results-1920x1080.png`
- `playwright/screenshots/skillhub-import-queue-1920x1080.png`
