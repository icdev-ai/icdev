# Start — Launch ICDEV Dashboard, SaaS Portal, and Poll Trigger

## Variables

DASHBOARD_PORT: 5000
PORTAL_PORT: 8443

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

4. Check if the SaaS API Gateway / Portal is already running on port `PORTAL_PORT`:
   ```bash
   python -c "import urllib.request; urllib.request.urlopen('http://localhost:8443/health', timeout=2); print('RUNNING')" 2>/dev/null || echo "NOT_RUNNING"
   ```

5. If **NOT_RUNNING**: Initialize the platform database if needed, start the API gateway, and open the portal:
   ```bash
   python tools/saas/platform_db.py --init 2>/dev/null
   ```
   ```bash
   nohup python tools/saas/api_gateway.py --port 8443 --debug > .tmp/api_gateway.log 2>&1 &
   ```
   ```bash
   sleep 2
   ```

6. Open the portal in the browser:
   ```bash
   python -m webbrowser "http://localhost:8443/portal/"
   ```

7. Start the CI/CD poll trigger (polls GitHub/GitLab issues every 20s for ICDEV-BOT automation):
   ```bash
   nohup python tools/ci/triggers/poll_trigger.py > .tmp/poll_trigger.log 2>&1 &
   ```

8. Report to the user:
   - **Dashboard**: `http://localhost:DASHBOARD_PORT`
     - Pages: `/`, `/projects`, `/projects/<id>`, `/agents`, `/monitoring`, `/events`, `/activity`, `/usage`, `/wizard`, `/query`, `/chat`, `/chat/<id>`, `/quick-paths`, `/batch`, `/diagrams`, `/cicd`, `/gateway`, `/phases`, `/dev-profiles`, `/children`, `/profile`, `/translations`, `/translations/<id>`, `/traces`, `/provenance`, `/xai`, `/oscal`, `/prod-audit`, `/ai-transparency`, `/ai-accountability`, `/code-quality`, `/fedramp-20x`, `/evidence`, `/lineage`, `/proposals`, `/proposals/<id>`, `/proposals/<id>/sections/<id>`, `/govcon`, `/govcon/requirements`, `/govcon/capabilities`, `/login`, `/logout`
     - Log: `.tmp/dashboard.log`
   - **SaaS Portal**: `http://localhost:PORTAL_PORT/portal/`
     - API docs: `http://localhost:PORTAL_PORT/api/v1/docs`
     - Health: `http://localhost:PORTAL_PORT/health`
     - Log: `.tmp/api_gateway.log`
   - **Poll Trigger**: `.tmp/poll_trigger.log`
   - To stop dashboard: `kill $(lsof -ti:5000)` or `pkill -f "tools/dashboard/app.py"`
   - To stop portal: `kill $(lsof -ti:8443)` or `pkill -f "tools/saas/api_gateway.py"`
   - To stop poll trigger: `pkill -f "tools/ci/triggers/poll_trigger.py"`
