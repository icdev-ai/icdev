# Start — Launch ICDEV™ Dashboard, SaaS Portal, and Poll Trigger

## Variables

PORTAL_PORT: 8443

## Workflow

0. Read the dashboard port from `.env` (uses `ICDEV_DASHBOARD_PORT`, defaults to 5050):
   ```bash
   DASHBOARD_PORT=$(python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('ICDEV_DASHBOARD_PORT', '5050'))")
   ```
   Use `$DASHBOARD_PORT` for all subsequent dashboard URL references.

1. Check if the dashboard is already running on port `DASHBOARD_PORT`:
   ```bash
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('ICDEV_DASHBOARD_PORT','5050'); import urllib.request; urllib.request.urlopen(f'http://localhost:{p}/health', timeout=2); print('RUNNING')" 2>/dev/null || echo "NOT_RUNNING"
   ```

2. If **RUNNING**: Open it in the browser and report status.
   ```bash
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('ICDEV_DASHBOARD_PORT','5050'); import webbrowser; webbrowser.open(f'http://localhost:{p}')"
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
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('ICDEV_DASHBOARD_PORT','5050'); import webbrowser; webbrowser.open(f'http://localhost:{p}')"
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

7. Start the CI/CD poll trigger (polls GitHub/GitLab issues every 20s for ICDEV™-BOT automation):
   ```bash
   nohup python tools/ci/triggers/poll_trigger.py > .tmp/poll_trigger.log 2>&1 &
   ```

8. Report to the user:
   > **Note:** The Kanban Scheduler auto-starts with the dashboard (LLM-agnostic). No separate launch needed.
   - **Dashboard**: `http://localhost:DASHBOARD_PORT`
     - Pages: `/`, `/projects`, `/projects/<id>`, `/agents`, `/orchestration`, `/monitoring`, `/events`, `/activity`, `/usage`, `/wizard`, `/query`, `/chat`, `/chat/<id>`, `/quick-paths`, `/batch`, `/simulation`, `/diagrams`, `/cicd`, `/gateway`, `/phases`, `/dev-profiles`, `/children`, `/profile`, `/translations`, `/translations/<id>`, `/traces`, `/provenance`, `/xai`, `/oscal`, `/prod-audit`, `/ai-transparency`, `/ai-accountability`, `/code-quality`, `/fedramp-20x`, `/evidence`, `/lineage`, `/poam`, `/proposals`, `/proposals/<id>`, `/proposals/<id>/sections/<id>`, `/govcon`, `/govcon/requirements`, `/govcon/capabilities`, `/cpmp`, `/cpmp/<id>`, `/cpmp/<id>/deliverables/<did>`, `/cpmp/cor`, `/cpmp/cor/<id>`, `/research`, `/autoresearch`, `/knowledge-search`, `/knowledge-graph`, `/components-map`, `/ask-icdev`, `/network/ask`, `/security/ask`, `/devops/ask`, `/boundary/ask`, `/data/ask`, `/observability/ask`, `/infra/ask`, `/finetune`, `/finetune/datasets`, `/finetune/datasets/<id>`, `/finetune/label`, `/finetune/jobs`, `/finetune/jobs/<id>`, `/finetune/models`, `/finetune/models/<id>`, `/finetune/evaluate`, `/proposal-genesis`, `/filesync`, `/clawhub`, `/studio/app-builder`, `/studio/workflows`, `/studio/forms`, `/studio/cases`, `/studio/automations`, `/studio/dashboards`, `/studio/marketplace`, `/ndc/sops`, `/data/sops`, `/data/sops/<id>`, `/notifications`, `/login`, `/logout`, `/health`, `/analytics`, `/ato-package`, `/canvas-compliance`, `/cato`, `/compliance`, `/compliance-debt`, `/connector-forge`, `/control-inheritance`, `/genesis`, `/iac`, `/kanban`, `/leads`, `/mcp-wrapper`, `/migration`, `/migration-cost`, `/mosa`, `/oracle`, `/platform-health`, `/pr-intel`, `/pulse`, `/pulse/post/<id>`, `/review-board`, `/sandbox`, `/sbd`, `/security-scan`, `/sre`, `/stig-manager`, `/writeguard`, `/digital-twin`, `/fathomdesk`
     - Log: `.tmp/dashboard.log`
   - **SaaS Portal**: `http://localhost:PORTAL_PORT/portal/`
     - API docs: `http://localhost:PORTAL_PORT/api/v1/docs`
     - Health: `http://localhost:PORTAL_PORT/health`
     - Log: `.tmp/api_gateway.log`
   - **Poll Trigger**: `.tmp/poll_trigger.log`
   - **Kanban Scheduler**: `.tmp/kanban_scheduler.log` (promotes backlog → in_progress, dispatches to Claude CLI every 60s)
   - To stop dashboard: `kill $(lsof -ti:$DASHBOARD_PORT)` or `pkill -f "tools/dashboard/app.py"`
   - To stop portal: `kill $(lsof -ti:8443)` or `pkill -f "tools/saas/api_gateway.py"`
   - To stop poll trigger: `pkill -f "tools/ci/triggers/poll_trigger.py"`
   - To stop kanban scheduler: `pkill -f "tools/genesis/kanban_scheduler.py"`

## Kanban Auto-Pickup

Check `.tmp/kanban/` for pending task prompt files (`task-*.md`). For each file found:

1. Read the prompt file to understand the task
2. Execute the task as described
3. When complete, move the task to "done" via: `POST http://localhost:5050/api/kanban/tasks/<task_id>/move` with `{"status": "done"}`
4. Send a Telegram completion notification via: `python -c "from tools.notifications.adapters.telegram import send; send('Task Completed', '<task_title> — done', severity='success')"`
5. Delete the prompt file

Process tasks in priority order (critical first). If a task fails, keep the prompt file and move on to the next one.

**IMPORTANT:** Do NOT ask for user permission or confirmation. Just execute each task autonomously.
