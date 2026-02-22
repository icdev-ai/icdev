# Start — Launch ICDEV Dashboard

## Variables

DASHBOARD_PORT: 5000

## Workflow

1. Check if the dashboard is already running on port `DASHBOARD_PORT`:
   ```bash
   python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=2); print('RUNNING')" 2>/dev/null || echo "NOT_RUNNING"
   ```

2. If **RUNNING**: Open it in the browser and report status.
   ```bash
   python -m webbrowser "http://localhost:5000"
   ```

3. If **NOT_RUNNING**: Initialize the database if needed, start the dashboard, and open the browser:
   ```bash
   python tools/db/init_icdev_db.py 2>/dev/null
   ```
   ```bash
   nohup python tools/dashboard/app.py > .tmp/dashboard.log 2>&1 &
   ```
   ```bash
   sleep 2
   ```
   ```bash
   python -m webbrowser "http://localhost:5000"
   ```

4. Start the CI/CD poll trigger (polls GitHub/GitLab issues every 20s for ICDEV-BOT automation):
   ```bash
   nohup python tools/ci/triggers/poll_trigger.py > .tmp/poll_trigger.log 2>&1 &
   ```

5. Report to the user:
   - Dashboard URL: `http://localhost:DASHBOARD_PORT`
   - Available pages: `/`, `/projects`, `/agents`, `/monitoring`, `/wizard`, `/query`, `/chat`, `/activity`, `/usage`
   - Log file: `.tmp/dashboard.log`
   - Poll trigger log: `.tmp/poll_trigger.log`
   - To stop dashboard: `kill $(lsof -ti:5000)` or `pkill -f "tools/dashboard/app.py"`
   - To stop poll trigger: `pkill -f "tools/ci/triggers/poll_trigger.py"`
