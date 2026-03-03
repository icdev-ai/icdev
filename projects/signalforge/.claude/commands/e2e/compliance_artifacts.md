# E2E Test: Compliance Artifact Generation

Verify compliance artifacts (SSP, POAM, STIG, SBOM) can be viewed through the dashboard.

## Prerequisites
- Flask dashboard running on http://localhost:5000
- At least one project with compliance artifacts generated

## Steps

1. Navigate to http://localhost:5000/compliance
2. Wait for the compliance page to load
3. Screenshot the compliance overview page

4. Assert the page displays a control family coverage matrix
5. Assert STIG findings summary is visible (CAT1/CAT2/CAT3 counts)
6. Assert SSP/POAM document status is shown

7. Navigate to http://localhost:5000/projects
8. Click on the first project in the list
9. Wait for the project detail page to load
10. Click on the "Compliance" tab

11. Screenshot the project compliance tab
12. Assert the compliance tab shows SSP status
13. Assert the compliance tab shows POAM status
14. Assert the compliance tab shows STIG findings
15. Assert the compliance tab shows SBOM status

16. Verify CUI banner "CUI // SP-CTI" is present at top and bottom

## Expected Results
- Compliance overview page loads with control matrix
- STIG findings are categorized by severity
- Project compliance tab shows all artifact statuses
- CUI banners present on all pages

## CUI Verification
- Check header and footer CUI banners on every page visited
