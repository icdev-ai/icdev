# E2E Test: SignalForge Trading Dashboard

Verify all 4 SignalForge dashboard pages load correctly with CUI banners, sub-navigation, tables, and API endpoints.

## Prerequisites
- Flask dashboard running on http://localhost:5050 (or configured port)
- Database initialized with auth tables (dashboard_users, dashboard_api_keys)
- Admin API key created via `python tools/dashboard/auth.py create-admin`
- SignalForge trade_journal tables auto-created on first access

## Steps

### Login
1. Navigate to http://localhost:5050/login
2. Enter the admin API key in the login form
3. Submit the login form
4. Verify redirect to dashboard (not back to /login)

### Main Dashboard (/signalforge)
5. Navigate to http://localhost:5050/signalforge
6. Wait for the page to fully load
7. Assert the page title contains "SignalForge"
8. Assert the CUI banner "CUI // SP-CTI" is visible at the top of the page
9. Assert the CUI banner "CUI // SP-CTI" is visible at the bottom of the page
10. Verify breadcrumbs show "Home > SignalForge"
11. Verify sub-navigation tabs exist: Dashboard, Backtest, Journal, Model
12. Verify stat grid displays 8 metrics: Total Trades, Win Rate, R:R Ratio, Profit Factor, Total PnL, Max Drawdown, Open Trades, Long/Short counts
13. Verify "Equity Curve" section heading exists
14. Verify "Recent Trades" section heading exists
15. Verify "Audit Trail Integrity" section with "Verify Hash Chain" button exists
16. Screenshot the full page as signalforge-dashboard.png

### Backtest Results (/signalforge/backtest)
17. Click "Backtest" tab in sub-navigation
18. Wait for the page to load
19. Assert the page title contains "Backtest"
20. Verify breadcrumbs show "Home > SignalForge > Backtest"
21. Verify "Target Metrics" table exists with 7 rows (Win Rate, R:R, Profit Factor, Sharpe, Features, Model Params, Max DD)
22. Verify "Risk Limits" table exists with 9 parameters
23. Verify CLI instructions code block is present
24. Verify table search/filter/CSV export controls are available
25. Screenshot the full page as signalforge-backtest.png

### Trade Journal (/signalforge/journal)
26. Click "Journal" tab in sub-navigation
27. Wait for the page to load
28. Assert the page title contains "Trade Journal"
29. Verify breadcrumbs show "Home > SignalForge > Journal"
30. Verify "CFTC Reg AT compliant" text is present
31. Verify empty state message with CLI command for generating signals
32. Screenshot the full page as signalforge-journal.png

### Model Analysis (/signalforge/model)
33. Click "Model" tab in sub-navigation
34. Wait for the page to load
35. Assert the page title contains "Model Analysis"
36. Verify breadcrumbs show "Home > SignalForge > Model"
37. Verify "Feature Set (25 Features)" table with 15 rows
38. Verify "Model Configuration" table with 10 parameters (Type, Classes, max_depth, n_estimators, learning_rate, etc.)
39. Verify "Architecture Decision: XGBoost vs PPO (NT8-RL)" comparison table with 8 rows
40. Verify "Train a Model" section with CLI instructions
41. Test table search: type "VWAP" in feature table search box, verify only 1 row shown ("Showing 1 of 15 rows")
42. Clear search, verify all 15 rows restored
43. Screenshot the full page as signalforge-model.png

### API Endpoints
44. Navigate to http://localhost:5050/api/signalforge/summary
45. Verify JSON response contains "status": "success" and "summary" object with total_trades, win_rate, avg_rr, profit_factor, total_pnl, max_drawdown keys
46. Navigate to http://localhost:5050/api/signalforge/verify
47. Verify JSON response contains "status": "success" and "verification" object with valid: true
48. Navigate to http://localhost:5050/api/signalforge/equity-curve
49. Verify JSON response contains "status": "success" and "curve" array
50. Navigate to http://localhost:5050/api/signalforge/risk
51. Verify JSON response contains risk limit parameters

### Console Error Check
52. Check browser console for errors
53. Assert no SignalForge-specific errors (ignore generic live.js polling errors for missing general tables)

## Expected Results
- All 4 pages render without SignalForge-specific errors
- CUI banners visible on every page (top and bottom)
- Sub-navigation tabs work correctly across all pages
- Tables support search, sort, filter, and CSV export
- API endpoints return valid JSON with correct structure
- Hash chain verification returns valid: true
