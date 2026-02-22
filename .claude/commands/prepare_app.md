# Prepare Application — Pre-Test/Review Setup

Setup the ICDEV environment for E2E tests or review validation.

## Variables

DASHBOARD_PORT: 5000

## Setup Steps

1. **Initialize database** (idempotent — safe to run repeatedly):
   ```bash
   python tools/db/init_icdev_db.py 2>/dev/null || echo "DB already initialized"
   ```

2. **Initialize platform database** (for SaaS/portal tests):
   ```bash
   python tools/saas/platform_db.py --init 2>/dev/null || echo "Platform DB already initialized"
   ```

3. **Check if dashboard is already running**:
   ```bash
   python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=2); print('RUNNING')" 2>/dev/null || echo "NOT_RUNNING"
   ```

4. **If NOT_RUNNING — start the dashboard**:
   ```bash
   nohup python tools/dashboard/app.py > .tmp/dashboard.log 2>&1 &
   ```
   ```bash
   sleep 3
   ```

5. **Verify dashboard is healthy**:
   ```bash
   python -c "import urllib.request; r = urllib.request.urlopen('http://localhost:5000/health', timeout=5); print(r.read().decode())" 2>/dev/null || echo "WARNING: Dashboard health check failed"
   ```

6. **Verify SaaS portal** (if testing SaaS pages):
   ```bash
   python -c "import urllib.request; r = urllib.request.urlopen('http://localhost:8443/health', timeout=2); print(r.read().decode())" 2>/dev/null || echo "SaaS portal not running (optional — start with: python tools/saas/api_gateway.py --port 8443)"
   ```

## Notes

- Dashboard runs on port 5000 by default
- SaaS API gateway runs on port 8443 (optional, only for SaaS-specific tests)
- DB files are created in `data/` directory
- Dashboard logs go to `.tmp/dashboard.log`
- To stop: `pkill -f "tools/dashboard/app.py"` or `kill $(lsof -ti:5000)`
- All setup is idempotent — safe to run multiple times
