# Start — Launch ICDEV™ Dashboard, SaaS Portal, and Poll Trigger

## Variables

PORTAL_PORT: 8443

## Workflow

0. Kill all running Python processes to ensure a clean start:
   ```bash
   taskkill /f /im python.exe 2>/dev/null; echo "Cleared Python processes"
   ```

0. Reset any stuck IN PROGRESS tasks back to backlog (orphaned by the taskkill above):
   ```bash
   python -c "
from tools.db.storage import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    cur.execute(\"UPDATE kanban_tasks SET status='backlog', updated_at=datetime('now') WHERE status='in_progress'\")
    n = cur.rowcount
    conn.commit()
    print(f'Reset {n} stuck in_progress task(s) to backlog')
"
   ```

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

8. Always start the Kanban Scheduler fresh (kill any stale instance first, then launch via module):
   ```bash
   pkill -f "tools.genesis.kanban_scheduler" 2>/dev/null; pkill -f "kanban_scheduler" 2>/dev/null; sleep 1; nohup python -m tools.genesis.kanban_scheduler > .tmp/kanban_scheduler.log 2>&1 & echo "Kanban scheduler PID: $!"
   ```

9. Check if the Genesis daemon (failure_triage, oracle_triage, awareness, heal, and 20+ other reflexes) is running:
   ```bash
   python -c "
import os, pathlib
pid_file = pathlib.Path('.tmp/genesis/daemon.pid')
if pid_file.exists():
    pid = pid_file.read_text().strip()
    try:
        os.kill(int(pid), 0)
        print('RUNNING', pid)
    except Exception:
        print('NOT_RUNNING')
else:
    print('NOT_RUNNING')
" 2>/dev/null || echo NOT_RUNNING
   ```

   If **NOT_RUNNING**: start it (Task Scheduler will handle it on next reboot; start manually now):
   ```bash
   mkdir -p .tmp/genesis && nohup python tools/genesis/daemon.py > .tmp/genesis_daemon.log 2>&1 &
   ```

10. Report to the user:
   > **Note:** The Kanban Scheduler is always explicitly restarted by `/start` using `python -m tools.genesis.kanban_scheduler`.
   > **Note:** The Genesis Daemon auto-starts at logon via Windows Task Scheduler (ICDEV-Genesis-Daemon task). Manual override: `python tools/genesis/daemon.py`
   - **Dashboard**: `http://localhost:DASHBOARD_PORT`
     - Pages: `/`, `/demo-runner/`, `/ai-observatory`, `/projects`, `/projects/<id>`, `/agents`, `/orchestration`, `/monitoring`, `/ai-wizard`, `/ai-patterns`, `/ai-skills`, `/events`, `/activity`, `/usage`, `/wizard`, `/query`, `/chat`, `/chat/<id>`, `/quick-paths`, `/batch`, `/simulation`, `/diagrams`, `/cicd`, `/gateway`, `/phases`, `/dev-profiles`, `/children`, `/profile`, `/translations`, `/translations/<id>`, `/traces`, `/provenance`, `/xai`, `/oscal`, `/prod-audit`, `/ai-transparency`, `/ai-accountability`, `/code-quality`, `/fedramp-20x`, `/evidence`, `/lineage`, `/poam`, `/proposals`, `/proposals/<id>`, `/proposals/<id>/sections/<id>`, `/govcon`, `/govcon/requirements`, `/govcon/capabilities`, `/cpmp`, `/cpmp/<id>`, `/cpmp/<id>/deliverables/<did>`, `/cpmp/cor`, `/cpmp/cor/<id>`, `/research`, `/autoresearch`, `/knowledge-search`, `/knowledge-graph`, `/components-map`, `/ask-icdev`, `/network/ask`, `/security/ask`, `/security/demo`, `/devops/ask`, `/boundary/ask`, `/data/ask`, `/observability/ask`, `/infra/ask`, `/finetune`, `/finetune/datasets`, `/finetune/datasets/<id>`, `/finetune/label`, `/finetune/jobs`, `/finetune/jobs/<id>`, `/finetune/models`, `/finetune/models/<id>`, `/finetune/evaluate`, `/proposal-genesis`, `/filesync`, `/clawhub`, `/studio/app-builder`, `/studio/workflows`, `/studio/forms`, `/studio/cases`, `/studio/automations`, `/studio/dashboards`, `/studio/marketplace`, `/ndc/sops`, `/data/sops`, `/data/sops/<id>`, `/notifications`, `/login`, `/logout`, `/health`, `/analytics`, `/ato-compliance`, `/ato-package`, `/canvas-compliance`, `/cato`, `/compliance`, `/compliance-debt`, `/connector-forge`, `/control-inheritance`, `/genesis`, `/iac`, `/il5`, `/kanban`, `/leads`, `/mcp-wrapper`, `/migration`, `/migration-canvas/network-migration/`, `/network/migration-phases/<topo_id>`, `/network/migration-hub`, `/network/projects/<project_id>`, `/migration-cost`, `/migration-intel`, `/migration-intel/`, `/mosa`, `/oracle`, `/platform-health`, `/pr-intel`, `/pulse`, `/pulse/post/<id>`, `/review-board`, `/sandbox`, `/safety`, `/sbd`, `/security-scan`, `/sre`, `/stig-manager`, `/writeguard`, `/digital-twin`, `/fathomdesk`, `/news`, `/options`, `/machine-momentum`, `/strategos`, `/strategos/orbat`, `/strategos/ghost`, `/strategos/iw`, `/strategos/wargame`, `/strategos/kg`, `/strategos/hitl`, `/strategos/commander`, `/strategos/intsum`, `/strategos/ipb`, `/strategos/indicators`, `/strategos/f3ead`, `/strategos/bda`, `/strategos/opord`, `/strategos/red-cell`, `/strategos/mett-tc`, `/strategos/sync-matrix`, `/strategos/isr-planner`, `/strategos/sources`, `/innovation`, `/innovation/new`, `/innovation/<id>`, `/innovation/dashboard`, `/academy/certificate/<level>`, `/academy/verify/<token>`, `/academy/my-certificates`, `/api/academy/learning-path`, `/academy/org-readiness`, `/academy/patterns`, `/academy/patterns/<id>`, `/ai-ml/modernize`, `/simulate/chat`, `/<context_id>/close`, `/<context_id>/intervene`, `/<context_id>/messages`, `/<context_id>/send`, `/<context_id>/state`, `/<diagram_id>`, `/<project_id>/audit-trail`, `/<project_id>/compliance`, `/<project_id>/status`, `/<trace_id>`, `/active-models`, `/activities`, `/alerts`, `/amendments/<amendment_id>/diff`, `/analysis`, `/analyze`, `/app-builder/extract`, `/app-builder/sessions`, `/app-builder/sessions/<session_id>/build`, `/app-builder/sessions/<session_id>/refine`, `/appeals`, `/artifacts`, `/artifacts/<int:artifact_id>`, `/assess`, `/assessments`, `/audit`, `/automations`, `/automations/<auto_id>`, `/automations/<auto_id>/simulate`, `/automations/<auto_id>/toggle`, `/automations/actions`, `/automations/operators`, `/automations/runs`, `/automations/templates`, `/automations/triggers`, `/benchmarks`, `/bid-decisions`, `/burndown`, `/by-provider`, `/calculate`, `/cancel/<run_id>`, `/capture-plans`, `/case-study-links`, `/cases`, `/cases/<case_id>`, `/cases/<case_id>/transition`, `/cases/templates`, `/cases/types`, `/cases/types/<type_id>`, `/cases/types/<type_id>/board`, `/cat1`, `/catalog`, `/catalog/<control_id>`, `/catalog/stats`, `/cdrl-generations`, `/certifications`, `/chain/process-alert`, `/chain/slo-breach`, `/chains`, `/checklist`, `/clins/<clin_id>`, `/collaboration`, `/collect`, `/comparison`, `/competitors/leaderboard`, `/competitors/profile/<vendor>`, `/competitors/scan`, `/compliance/<item_id>`, `/conflicts`, `/conflicts/<conflict_id>/resolve`, `/containers`, `/contexts`, `/contexts/<context_id>`, `/contract-health`, `/contracts`, `/contracts/<contract_id>`, `/contracts/<contract_id>/clins`, `/contracts/<contract_id>/cpars`, `/contracts/<contract_id>/cpars/predict`, `/contracts/<contract_id>/cpars/trend`, `/contracts/<contract_id>/deliverables`, `/contracts/<contract_id>/evm`, `/contracts/<contract_id>/evm/forecast`, `/contracts/<contract_id>/evm/ipmdar`, `/contracts/<contract_id>/evm/periods`, `/contracts/<contract_id>/evm/scurve`, `/contracts/<contract_id>/generate-cdrl/<deliverable_id>`, `/contracts/<contract_id>/generate-due`, `/contracts/<contract_id>/health`, `/contracts/<contract_id>/negative-events`, `/contracts/<contract_id>/negative-events/auto-detect`, `/contracts/<contract_id>/negative-events/ndaa-thresholds`, `/contracts/<contract_id>/sb-compliance`, `/contracts/<contract_id>/small-business`, `/contracts/<contract_id>/status`, `/contracts/<contract_id>/subcontractors`, `/contracts/<contract_id>/subcontractors/noncompliance`, `/contracts/<contract_id>/wbs`, `/controls`, `/controls-summary`, `/cor/contracts`, `/cor/contracts/<contract_id>`, `/cor/contracts/<contract_id>/cpars`, `/cor/contracts/<contract_id>/deliverables`, `/cor/contracts/<contract_id>/evm`, `/coverage`, `/cpars-predictions`, `/cpars/<assessment_id>`, `/critiques`, `/crm-accounts`, `/crm-accounts/<account_id>`, `/crm-contacts`, `/crm-contacts/<contact_id>`, `/crm-interactions`, `/csps`, `/dashboard`, `/dashboards`, `/dashboards/<dash_id>`, `/dashboards/role-defaults`, `/dashboards/widgets`, `/datasets`, `/datasets/<dataset_id>`, `/datasets/<dataset_id>/batch-label`, `/datasets/<dataset_id>/examples/<int:example_id>/label`, `/deliverables/<deliverable_id>`, `/deliverables/<deliverable_id>/status`, `/detect`, `/dev-profiles/api/create`, `/dev-profiles/api/list`, `/dev-profiles/api/resolve/<scope>/<scope_id>`, `/dev-profiles/api/templates`, `/diagnostics`, `/domains`, `/dora`, `/drafts/<draft_id>/approve`, `/drafts/<draft_id>/reject`, `/drift`, `/engagement-scores`, `/entities`, `/estimate`, `/evaluations`, `/exceptions`, `/execute`, `/expiring-atos`, `/export`, `/feed`, `/feedback`, `/filter-options`, `/findings`, `/findings/<find_id>`, `/findings/<finding_id>`, `/forms`, `/forms/<form_id>`, `/forms/<form_id>/submissions`, `/forms/field-types`, `/forms/templates`, `/frameworks`, `/freshness`, `/from-opportunity/<opp_id>`, `/gap`, `/gaps`, `/gaps/heatmap`, `/gaps/recommendations`, `/generate`, `/gpu-status`, `/graph`, `/history`, `/hyperparam-results`, `/incidents`, `/incidents/<incident_id>`, `/incidents/<incident_id>/close`, `/incidents/<incident_id>/escalate`, `/incidents/<incident_id>/postmortem`, `/incidents/<incident_id>/resolve`, `/incidents/<incident_id>/triage`, `/incidents/health`, `/incidents/mttr`, `/intake/requirements/<session_id>`, `/intake/prd/<session_id>/view`, `/inventory`, `/jobs`, `/jobs/<job_id>`, `/jobs/<job_id>/fim`, `/jobs/<job_id>/run`, `/jobs/<job_id>/versions`, `/jobs/<job_id>/watch`, `/knowledge-base`, `/ksi/<ksi_id>`, `/ksis`, `/latest`, `/lineage/<entity_id>`, `/log`, `/mailbox`, `/mailbox/stream`, `/marketplace/assets`, `/marketplace/assets/<asset_id>`, `/marketplace/assets/<asset_id>/install`, `/marketplace/categories`, `/model`, `/model-card`, `/model-cards`, `/models`, `/models/<model_id>`, `/negative-events/<event_id>`, `/opportunities`, `/opportunities/<opp_id>`, `/opportunities/<opp_id>/amendments`, `/opportunities/<opp_id>/assignment-matrix`, `/opportunities/<opp_id>/auto-compliance`, `/opportunities/<opp_id>/auto-draft`, `/opportunities/<opp_id>/bid-recommendation`, `/opportunities/<opp_id>/compliance`, `/opportunities/<opp_id>/compliance/batch`, `/opportunities/<opp_id>/compliance/gaps`, `/opportunities/<opp_id>/coverage`, `/opportunities/<opp_id>/create-contract`, `/opportunities/<opp_id>/drafts`, `/opportunities/<opp_id>/extract-requirements`, `/opportunities/<opp_id>/generate-questions`, `/opportunities/<opp_id>/map-capabilities`, `/opportunities/<opp_id>/questions`, `/opportunities/<opp_id>/questions/bulk-status`, `/opportunities/<opp_id>/questions/export`, `/opportunities/<opp_id>/requirements`, `/opportunities/<opp_id>/reviews`, `/opportunities/<opp_id>/sections`, `/opportunities/<opp_id>/sections/dependencies`, `/opportunities/<opp_id>/stats`, `/opportunities/<opp_id>/status`, `/opportunities/<opp_id>/timeline`, `/opportunities/<opp_id>/volumes`, `/overdue`, `/overdue-deliverables`, `/package`, `/pipeline`, `/pipeline/run`, `/pipeline/status`, `/plans`, `/plans/<plan_id>`, `/plans/<plan_id>/progress`, `/poam-summary`, `/poams`, `/poll`, `/portfolio`, `/settings`, `/profile/api/keys`, `/profile/api/llm-keys`, `/profile/api/llm-keys/<key_id>/revoke`, `/promotions`, `/published-articles`, `/pulse-links`, `/pulse/post/<post_id>`, `/push`, `/quality-scores`, `/quality/scorecard`, `/questions/<q_id>`, `/questions/<q_id>/response`, `/questions/<q_id>/status`, `/reflex/<name>`, `/refresh`, `/relations`, `/remediate`, `/remediation-log`, `/reports`, `/reports/<report_id>`, `/requirement-patterns`, `/reviews/<rev_id>`, `/reviews/<rev_id>/findings`, `/roi`, `/run`, `/run-all`, `/run-reflex`, `/runbooks`, `/runbooks/<runbook_id>/execute`, `/runbooks/health`, `/runbooks/history`, `/runbooks/match`, `/sam/awards`, `/sam/awards/search`, `/sam/import/<sam_opp_id>`, `/sam/link/<sam_award_id>`, `/sam/opportunities`, `/sam/scan`, `/sam/sync-awards`, `/sbom`, `/scan`, `/sections/<sec_id>`, `/sections/<sec_id>/status`, `/self-healing`, `/shap/<trace_id>`, `/shap/analyze`, `/sla`, `/slos`, `/slos/<slo_id>/burn-rate`, `/slos/<slo_id>/measure`, `/slos/health`, `/smells`, `/snapshots`, `/ssp`, `/stats`, `/status`, `/status-history/<entity_type>/<entity_id>`, `/status/<run_id>`, `/stig`, `/stig-coverage`, `/subcontractors/<sub_id>`, `/summary`, `/system-card`, `/tasks`, `/tasks/<task_id>`, `/tasks/<task_id>/message`, `/tasks/<task_id>/move`, `/teaming-assessments`, `/templates`, `/time-series`, `/timeline`, `/tools/catalog`, `/top-complex`, `/totals`, `/training-pairs`, `/trend`, `/trends`, `/trends/engagement`, `/trends/quality-scores`, `/trends/training-pairs`, `/trends/win-rate`, `/users`, `/validate`, `/validations`, `/versions/<version_id>/restore`, `/volumes/<vol_id>`, `/watchers`, `/wbs/<wbs_id>`, `/win-loss-lessons`, `/win-loss-records`, `/workflows`, `/workflows/<workflow_id>`, `/workflows/<workflow_id>/composer`, `/workflows/<workflow_id>/dag`
     - Log: `.tmp/dashboard.log`
   - **SaaS Portal**: `http://localhost:PORTAL_PORT/portal/`
     - API docs: `http://localhost:PORTAL_PORT/api/v1/docs`
     - Health: `http://localhost:PORTAL_PORT/health`
     - Log: `.tmp/api_gateway.log`
   - **Poll Trigger**: `.tmp/poll_trigger.log`
   - **Kanban Scheduler**: `.tmp/kanban_scheduler.log` (promotes backlog → in_progress, dispatches to Claude CLI every 60s)
   - **Genesis Daemon**: `.tmp/genesis_daemon.log` (failure_triage every 30m, oracle_triage/awareness every 3h, heal every 5m, 20+ reflexes)
   - To stop dashboard: `kill $(lsof -ti:$DASHBOARD_PORT)` or `pkill -f "tools/dashboard/app.py"`
   - To stop portal: `kill $(lsof -ti:8443)` or `pkill -f "tools/saas/api_gateway.py"`
   - To stop poll trigger: `pkill -f "tools/ci/triggers/poll_trigger.py"`
   - To stop kanban scheduler: `pkill -f "tools/genesis/kanban_scheduler.py"`
   - To stop genesis daemon: `python -c "import os,pathlib; os.kill(int(pathlib.Path('.tmp/genesis/daemon.pid').read_text().strip()), 15)"`

## Kanban Auto-Pickup

Check `.tmp/kanban/` for pending task prompt files (`task-*.md`). For each file found:

1. Read the prompt file to understand the task
2. Execute the task as described
3. When complete, move the task to "done" via: `POST http://localhost:5050/api/kanban/tasks/<task_id>/move` with `{"status": "done"}`
4. Send a Telegram completion notification via: `python -c "from tools.notifications.adapters.telegram import send; send('Task Completed', '<task_title> — done', severity='success')"`
5. Delete the prompt file

Process tasks in priority order (critical first). If a task fails, keep the prompt file and move on to the next one.

**IMPORTANT:** Do NOT ask for user permission or confirmation. Just execute each task autonomously.
