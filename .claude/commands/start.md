# Start — Launch ICDEV™ Dashboard, SaaS Portal, and Poll Trigger

## Option Exercise Status — READY FOR DECISION

> **CLEANED** · Lint gate clear (ruff F401 resolved) · `pmo-opt-9cabf1a414`

- **Contract:** W911NF-DEMO-24-C-0042 — Option 2 ($4.5M ceiling, deadline 2026-07-15)
- **Recommendation:** **GO** — exercise the option (health GREEN 94.0, CPI 0.99, SPI 0.98, CPARS Exceptional)
- **Code hygiene:** ✅ Unused-import lint issue resolved (`icdev/tools/ace/problem_classifier.py`, F401 — commit `b9a21e98c`)
- **Decision indicator:** 🟢 **READY FOR DECISION** — no open lint/build blockers; option exercise may proceed

## Variables

PORTAL_PORT: 8443

## Workflow

> **Windows PowerShell note:** All commands below use PowerShell syntax. Set `$env:PYTHONPATH = "C:\AI\ICDev"` before any `python` call that imports `tools.*` or `icdev.*`. Use `2>$null` (not `2>/dev/null`), `Start-Sleep -Seconds N` (not `sleep N`), `Start-Process` (not `nohup`), and `Stop-Process` (not `pkill`).

0. **Check what is already running before killing anything** (autonomy-id-03):
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/genesis/supervisor_status.py
   ```
   If the supervisor is **UP**, skip the teardown entirely and go to step 1 — its
   children are healthy and step 8 will defer to it.

   > **Do NOT run `taskkill /f /im python.exe`.** It has already taken out
   > unrelated tooling on this machine once, and — because the supervisor is
   > itself `python.exe` — it kills the one process that would have restarted
   > everything else, turning a "clean start" into six dead services and no
   > supervisor. If a specific process must go, confirm its exact command line
   > with `Get-CimInstance Win32_Process -Filter "ProcessId=<id>"` first, then
   > `Stop-Process -Id <that exact PID> -Force`. Never by name, never by filter.

   Only if the supervisor is **DOWN** and you need a clean slate, stop its
   children by verified PID:
   ```powershell
   python tools/genesis/supervisor_status.py --json | ConvertFrom-Json |
     ForEach-Object { $_.children } | ForEach-Object { $_.pids } |
     ForEach-Object { Get-CimInstance Win32_Process -Filter "ProcessId=$_" } |
     Format-Table ProcessId, CommandLine -AutoSize
   ```
   Review that list, then stop only the PIDs you intend to.

0. Clear stale PostgreSQL backend connections left over from killed processes (prevents lock pile-up on `kanban_tasks`).
   Write and run a temp script:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   @'
import os
from dotenv import load_dotenv
load_dotenv()
try:
    import psycopg2, psycopg2.extras
    db_url = os.environ.get("ICDEV_DATABASE_URL")
    dbname = None
    if db_url:
        conn = psycopg2.connect(db_url, connect_timeout=5, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        # No DATABASE_URL — build the DSN from ICDEV_PG_* (this repo's convention).
        host = os.environ.get("ICDEV_PG_HOST", "127.0.0.1")
        if host == "localhost":
            host = "127.0.0.1"  # avoid IPv6 connect penalty
        dbname = os.environ.get("ICDEV_PG_DB", "icdev")
        conn = psycopg2.connect(
            host=host,
            port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
            dbname=dbname,
            user=os.environ.get("ICDEV_PG_USER", "icdev"),
            password=os.environ.get("ICDEV_PG_PASSWORD", ""),
            connect_timeout=5,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    conn.autocommit = True
    cur = conn.cursor()
    # Conservative cleanup: ONLY stale 'idle in transaction' backends (>30s) in
    # this DB. These are orphaned leaks holding ACCESS SHARE -> the kanban_tasks
    # lock storm. Active queries and other sessions' fresh transactions are left
    # untouched. This releases PG-side backends orphaned by a previous unclean
    # shutdown; it does not require anything to have been killed first.
    base = ("SELECT pid FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
            "AND state IN ('idle in transaction', 'idle in transaction (aborted)') "
            "AND (xact_start IS NULL OR xact_start < now() - interval '30 seconds')")
    if dbname:
        cur.execute(base + " AND datname = %s", (dbname,))
    else:
        cur.execute(base)
    pids = [r["pid"] for r in cur.fetchall()]
    for pid in pids:
        try:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        except Exception:
            pass
    conn.close()
    print(f"PG cleanup: terminated {len(pids)} stale idle-in-transaction backend(s)")
except Exception as e:
    print(f"PG cleanup skipped ({e})")
'@ | Set-Content .tmp\pg_cleanup.py
   python .tmp\pg_cleanup.py
   ```

0. Reset any stuck IN PROGRESS tasks back to backlog (orphaned by a previous
   unclean shutdown). Skip this if the supervisor was UP in step 0 — those
   tasks have live workers, and resetting them re-dispatches work already in
   flight.
   Write a temp script then run it (avoids PowerShell quote-escaping issues with `python -c`):
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   @'
from tools.db.storage import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    sql = "UPDATE kanban_tasks SET status='backlog', updated_at=datetime('now') WHERE status='in_progress'"
    cur.execute(sql)
    n = cur.rowcount
    conn.commit()
    print(f"Reset {n} stuck in_progress task(s) to backlog")
'@ | Set-Content .tmp\reset_tasks.py
   python .tmp\reset_tasks.py
   ```

0. Read the dashboard port from `.env` (uses `ICDEV_DASHBOARD_PORT`, defaults to 5050):
   ```powershell
   $DASHBOARD_PORT = python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('ICDEV_DASHBOARD_PORT', '5050'))"
   ```
   Use `$DASHBOARD_PORT` for all subsequent dashboard URL references.

1. Check if the dashboard is already running on port `$DASHBOARD_PORT`:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   $dashStatus = python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('ICDEV_DASHBOARD_PORT','5050'); import urllib.request; urllib.request.urlopen(f'http://localhost:{p}/health', timeout=2); print('RUNNING')" 2>$null
   if (-not $dashStatus) { $dashStatus = "NOT_RUNNING" }
   echo $dashStatus
   ```

2. If **RUNNING**: Open it in the browser and report status.
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('ICDEV_DASHBOARD_PORT','5050'); import webbrowser; webbrowser.open(f'http://localhost:{p}')"
   ```

3. If **NOT_RUNNING**: Initialize the database if needed, start the dashboard, and open the browser:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/db/init_icdev_db.py 2>$null
   ```
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   $proc = Start-Process python -ArgumentList "tools/dashboard/app.py" -RedirectStandardOutput ".tmp/dashboard.log" -RedirectStandardError ".tmp/dashboard_err.log" -WindowStyle Hidden -PassThru
   echo "Dashboard PID: $($proc.Id)"
   ```
   ```powershell
   Start-Sleep -Seconds 3
   ```
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python -c "import os; from dotenv import load_dotenv; load_dotenv(); p=os.getenv('ICDEV_DASHBOARD_PORT','5050'); import webbrowser; webbrowser.open(f'http://localhost:{p}')"
   ```

4. Check if the SaaS API Gateway / Portal is already running on port `PORTAL_PORT`:
   ```powershell
   $portalStatus = python -c "import urllib.request; urllib.request.urlopen('http://localhost:8443/health', timeout=2); print('RUNNING')" 2>$null
   if (-not $portalStatus) { $portalStatus = "NOT_RUNNING" }
   echo $portalStatus
   ```

5. If **NOT_RUNNING**: Initialize the platform database if needed, start the API gateway, and open the portal:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/saas/platform_db.py --init 2>$null
   ```
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   $gw = Start-Process python -ArgumentList "tools/saas/api_gateway.py", "--port", "8443", "--debug" -RedirectStandardOutput ".tmp/api_gateway.log" -RedirectStandardError ".tmp/api_gateway_err.log" -WindowStyle Hidden -PassThru
   echo "API Gateway PID: $($gw.Id)"
   Start-Sleep -Seconds 3
   ```

6. Open the portal in the browser:
   ```powershell
   Start-Process "http://localhost:8443/portal/"
   ```

7. Start the CI/CD poll trigger (polls GitHub/GitLab issues every 20s for ICDEV™-BOT automation):
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   $pt = Start-Process python -ArgumentList "tools/ci/triggers/poll_trigger.py" -RedirectStandardOutput ".tmp/poll_trigger.log" -RedirectStandardError ".tmp/poll_trigger_err.log" -WindowStyle Hidden -PassThru
   echo "Poll trigger PID: $($pt.Id)"
   ```

8. **Kanban Scheduler, PR Watcher and Genesis Daemon — defer to the supervisor** (autonomy-id-03).

   These three are **supervised children**, not standalone services.
   `tools/genesis/launch.py` -> `launcher.main()` holds a pid lock, starts six
   services, and restarts any that die on a 30s loop. Starting one beside a live
   supervisor creates a duplicate that is reaped and **exits silently** — and the
   `-RedirectStandardOutput` that started it **truncates the log you would then
   read**, so a healthy supervised pair looks like a total failure. Measured
   2026-08-20: manually started PIDs were dead inside 20s while the supervisor's
   own children were alive and dispatching work.

   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/genesis/supervisor_status.py --ensure
   python tools/genesis/supervisor_status.py
   ```

   `--ensure` starts **the supervisor** when none is running, and **defers**
   when one is (or when its state cannot be determined — starting on uncertainty
   is how duplicates begin). It never starts an individual child.

   > **Never `Stop-Process` these by name or `Where-Object` filter.** That is what
   > produced three concurrent `pr_watcher` processes racing on auto-merge. Let
   > the supervisor own its children; it stops them by verified PID.

9. **Prove liveness from the record, never from `.tmp/*.log`.**

   The supervisor and `/start` write to **different paths** — the supervisor logs
   the genesis daemon to `.tmp/genesis/daemon.log`, while `/start` used
   `.tmp/genesis_daemon.log`. Tailing the second while the first is being written
   shows nothing, and "no log output" reads as "the daemon is dead". An empty log
   is not evidence.

   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   python tools/genesis/supervisor_status.py          # supervisor + children + code identity
   python -m tools.coordination.code_identity         # which code each process is running
   python tools/awareness/code_staleness.py           # is any of it superseded?
   ```

   Reflex liveness comes from `genesis_reflex_state.last_run_at`, not from a log
   file. Note: `failure_triage` and `oracle_triage` are NOT daemon-dispatched —
   see `tests/test_reflex_registration.py` EXEMPT.

10. Report to the user:
   > **Note:** The Kanban Scheduler is always explicitly restarted by `/start` using `python -m tools.genesis.kanban_scheduler`.
   > **Note:** The Genesis Daemon auto-starts at logon via Windows Task Scheduler (ICDEV-Genesis-Daemon task). Manual override: `python tools/genesis/daemon.py`
   - **Dashboard**: `http://localhost:DASHBOARD_PORT`
- Pages: `/`, `/demo-runner/`, `/ai-observatory`, `/idp/`, `/idp/catalog`, `/idp/scorecards`, `/idp/component/<key>`, `/idp/evidence`, `/projects`, `/projects/<id>`, `/agents`, `/orchestration`, `/monitoring`, `/monitoring/forecast`, `/ai-wizard`, `/ai-patterns`, `/ai-skills`, `/events`, `/activity`, `/usage`, `/wizard`, `/query`, `/chat`, `/chat/<id>`, `/quick-paths`, `/batch`, `/simulation`, `/diagrams`, `/cicd`, `/gateway`, `/phases`, `/dev-profiles`, `/children`, `/profile`, `/translations`, `/translations/<id>`, `/traces`, `/provenance`, `/xai`, `/oscal`, `/boundary/oscal`, `/prod-audit`, `/security/prod-audit`, `/ai-transparency`, `/security/ai-transparency`, `/security/stig-manager`, `/security/sbd`, `/ai-accountability`, `/code-quality`, `/fedramp-20x`, `/evidence`, `/lineage`, `/poam`, `/proposals`, `/proposals/<id>`, `/proposals/<id>/sections/<id>`, `/proposals/<opp_id>/compliance/gaps`, `/proposals/<opp_id>/language`, `/proposals/<opp_id>/ptw`, `/proposals/reviews-dashboard`, `/govcon`, `/govcon/requirements`, `/govcon/capabilities`, `/cpmp`, `/cpmp/<id>`, `/cpmp/<id>/deliverables/<did>`, `/cpmp/deliverables`, `/cpmp/reports`, `/cpmp/cor`, `/cpmp/cor/<id>`, `/research`, `/autoresearch`, `/knowledge-search`, `/knowledge-graph`, `/components-map`, `/ask-icdev`, `/network/ask`, `/security/ask`, `/security/demo`, `/security/`, `/security/posture`, `/security/compare`, `/security/sops`, `/security/attackpath`, `/security/runbooks`, `/security/twin/<design_id>`, `/security/zig/portfolio`, `/security/zig/`, `/security/zig/pillar/user`, `/security/zig/pillar/device`, `/security/zig/pillar/network`, `/security/zig/pillar/application`, `/security/zig/pillar/data`, `/security/zig/pillar/visibility`, `/security/zig/pillar/automation`, `/security/zig/phase`, `/security/zig/assessment`, `/security/zig/roadmap`, `/devops/`, `/devops/canvas/new`, `/devops/canvas/<id>`, `/devops/runbooks`, `/devops/runbooks/<id>`, `/devops/sops`, `/devops/twin`, `/devops/twin/<pipe_id>`, `/devops/twin/<id>/delta`, `/devops/ask`, `/boundary/ask`, `/data/ask`, `/observability/`, `/observability/canvas/new`, `/observability/canvas/<design_id>`, `/observability/templates`, `/observability/assessments`, `/observability/coverage/<design_id>`, `/observability/remediation/<design_id>`, `/observability/sops`, `/observability/runbooks`, `/observability/mitre`, `/observability/mitre/<tid>`, `/observability/kill-chain`, `/observability/twin/<design_id>`, `/observability/ask`, `/infra/ask`, `/finetune`, `/finetune/datasets`, `/finetune/datasets/<id>`, `/finetune/label`, `/finetune/jobs`, `/finetune/jobs/<id>`, `/finetune/models`, `/finetune/models/<id>`, `/finetune/evaluate`, `/proposal-genesis`, `/filesync`, `/clawhub`, `/studio/app-builder`, `/studio/workflows`, `/studio/forms`, `/studio/cases`, `/studio/automations`, `/studio/dashboards`, `/studio/marketplace`, `/workflow-canvas/`, `/workflow-canvas/forms`, `/workflow-canvas/forms/new`, `/workflow-canvas/forms/<id>`, `/workflow-canvas/forms/<id>/edit`, `/workflow-canvas/workflows`, `/workflow-canvas/workflows/new`, `/workflow-canvas/workflows/<id>`, `/workflow-canvas/workflows/<id>/edit`, `/workflow-canvas/templates`, `/workflow-canvas/processify`, `/workflow-canvas/my-tasks`, `/workflow-canvas/digitize`, `/ndc/sops`, `/data/sops`, `/data/sops/<id>`, `/data/mapping/`, `/data/mapping/new`, `/data/mapping/<session_id>`, `/data/`, `/data/canvas/new`, `/data/quality`, `/data/query`, `/data/csp`, `/data/pipeline-ops`, `/data/geoint`, `/data/osint`, `/data/domains`, `/data/products`, `/data/contracts`, `/data/governance`, `/data/mesh`, `/data/lineage`, `/data/twin/<design_id>`, `/data/assessments`, `/data/templates`, `/data/runbooks`, `/ai-ify/`, `/ai-ify/posture`, `/integrity`, `/integrity/<id>`, `/notifications`, `/login`, `/logout`, `/health`, `/analytics`, `/ato-compliance`, `/boundary/ato-compliance`, `/boundary/cato-health`, `/ato-package`, `/canvas-compliance`, `/canvas-kg`, `/cato`, `/compliance`, `/compliance-debt`, `/connector-forge`, `/control-inheritance`, `/foundry`, `/foundry/<id>`, `/genesis`, `/iac`, `/il5`, `/kanban`, `/leads`, `/mcp-wrapper`, `/migration`, `/migration-canvas/network-migration/`, `/network/migration-phases/<topo_id>`, `/network/migration-hub`, `/network/projects/<project_id>`, `/network/labs`, `/network/conflicts`, `/network/advisory-history`, `/network/poam`, `/network/exceptions`, `/migration-cost`, `/migration-intel`, `/migration-intel/`, `/mosa`, `/oracle`, `/platform-health`, `/pr-intel`, `/pulse`, `/pulse/post/<id>`, `/skillhub`, `/review-board`, `/sandbox`, `/safety`, `/sbd`, `/security-scan`, `/sre`, `/stig-manager`, `/writeguard`, `/digital-twin`, `/news`, `/options`, `/machine-momentum`, `/strategos`, `/strategos/orbat`, `/strategos/ghost`, `/strategos/iw`, `/strategos/wargame`, `/strategos/kg`, `/strategos/hitl`, `/strategos/commander`, `/strategos/intsum`, `/strategos/ipb`, `/strategos/indicators`, `/strategos/f3ead`, `/strategos/bda`, `/strategos/opord`, `/strategos/red-cell`, `/strategos/mett-tc`, `/strategos/sync-matrix`, `/strategos/isr-planner`, `/strategos/sources`, `/dat`, `/innovation`, `/innovation/new`, `/innovation/<id>`, `/innovation/dashboard`, `/academy/certificate/<level>`, `/academy/verify/<token>`, `/academy/my-certificates`, `/api/academy/learning-path`, `/academy/org-readiness`, `/academy/instructor`, `/academy/instructor/learner/<id>`, `/academy/patterns`, `/academy/patterns/<id>`, `/ai-ml/modernize`, `/simulate/chat`, `/<context_id>/close`, `/<context_id>/intervene`, `/<context_id>/messages`, `/<context_id>/send`, `/<context_id>/state`, `/<diagram_id>`, `/<project_id>/audit-trail`, `/<project_id>/compliance`, `/<project_id>/status`, `/<trace_id>`, `/active-models`, `/activities`, `/alerts`, `/amendments/<amendment_id>/diff`, `/analysis`, `/analyze`, `/app-builder/extract`, `/app-builder/sessions`, `/app-builder/sessions/<session_id>/build`, `/app-builder/sessions/<session_id>/refine`, `/appeals`, `/artifacts`, `/artifacts/<int:artifact_id>`, `/assess`, `/assessments`, `/audit`, `/automations`, `/automations/<auto_id>`, `/automations/<auto_id>/simulate`, `/automations/<auto_id>/toggle`, `/automations/actions`, `/automations/operators`, `/automations/runs`, `/automations/templates`, `/automations/triggers`, `/benchmarks`, `/bid-decisions`, `/burndown`, `/by-provider`, `/calculate`, `/cancel/<run_id>`, `/capture-plans`, `/case-study-links`, `/cases`, `/cases/<case_id>`, `/cases/<case_id>/transition`, `/cases/templates`, `/cases/types`, `/cases/types/<type_id>`, `/cases/types/<type_id>/board`, `/cat1`, `/catalog`, `/catalog/<control_id>`, `/catalog/stats`, `/cdrl-generations`, `/certifications`, `/chain/process-alert`, `/chain/slo-breach`, `/chains`, `/checklist`, `/clins/<clin_id>`, `/collaboration`, `/collect`, `/comparison`, `/competitors/leaderboard`, `/competitors/profile/<vendor>`, `/competitors/scan`, `/compliance/<item_id>`, `/conflicts`, `/conflicts/<conflict_id>/resolve`, `/containers`, `/contexts`, `/contexts/<context_id>`, `/contract-health`, `/contracts`, `/contracts/<contract_id>`, `/contracts/<contract_id>/clins`, `/contracts/<contract_id>/cpars`, `/contracts/<contract_id>/cpars/predict`, `/contracts/<contract_id>/cpars/trend`, `/contracts/<contract_id>/deliverables`, `/contracts/<contract_id>/evm`, `/contracts/<contract_id>/evm/forecast`, `/contracts/<contract_id>/evm/ipmdar`, `/contracts/<contract_id>/evm/periods`, `/contracts/<contract_id>/evm/scurve`, `/contracts/<contract_id>/generate-cdrl/<deliverable_id>`, `/contracts/<contract_id>/generate-due`, `/contracts/<contract_id>/health`, `/contracts/<contract_id>/negative-events`, `/contracts/<contract_id>/negative-events/auto-detect`, `/contracts/<contract_id>/negative-events/ndaa-thresholds`, `/contracts/<contract_id>/sb-compliance`, `/contracts/<contract_id>/small-business`, `/contracts/<contract_id>/status`, `/contracts/<contract_id>/subcontractors`, `/contracts/<contract_id>/subcontractors/noncompliance`, `/contracts/<contract_id>/wbs`, `/controls`, `/controls-summary`, `/cor/contracts`, `/cor/contracts/<contract_id>`, `/cor/contracts/<contract_id>/cpars`, `/cor/contracts/<contract_id>/deliverables`, `/cor/contracts/<contract_id>/evm`, `/coverage`, `/cpars-predictions`, `/cpars/<assessment_id>`, `/critiques`, `/crm-accounts`, `/crm-accounts/<account_id>`, `/crm-contacts`, `/crm-contacts/<contact_id>`, `/crm-interactions`, `/csps`, `/dashboard`, `/dashboards`, `/dashboards/<dash_id>`, `/dashboards/role-defaults`, `/dashboards/widgets`, `/datasets`, `/datasets/<dataset_id>`, `/datasets/<dataset_id>/batch-label`, `/datasets/<dataset_id>/examples/<int:example_id>/label`, `/deliverables/<deliverable_id>`, `/deliverables/<deliverable_id>/status`, `/detect`, `/dev-profiles/api/create`, `/dev-profiles/api/list`, `/dev-profiles/api/resolve/<scope>/<scope_id>`, `/dev-profiles/api/templates`, `/diagnostics`, `/domains`, `/dora`, `/drafts/<draft_id>/approve`, `/drafts/<draft_id>/reject`, `/drift`, `/engagement-scores`, `/entities`, `/estimate`, `/evaluations`, `/exceptions`, `/execute`, `/expiring-atos`, `/export`, `/feed`, `/feedback`, `/filter-options`, `/findings`, `/findings/<find_id>`, `/findings/<finding_id>`, `/forms`, `/forms/<form_id>`, `/forms/<form_id>/submissions`, `/forms/field-types`, `/forms/templates`, `/frameworks`, `/freshness`, `/from-opportunity/<opp_id>`, `/gap`, `/gaps`, `/gaps/heatmap`, `/gaps/recommendations`, `/generate`, `/gpu-status`, `/graph`, `/history`, `/hyperparam-results`, `/incidents`, `/incidents/<incident_id>`, `/incidents/<incident_id>/close`, `/incidents/<incident_id>/escalate`, `/incidents/<incident_id>/postmortem`, `/incidents/<incident_id>/resolve`, `/incidents/<incident_id>/triage`, `/incidents/health`, `/incidents/mttr`, `/intake/requirements/<session_id>`, `/intake/prd/<session_id>/view`, `/inventory`, `/jobs`, `/jobs/<job_id>`, `/jobs/<job_id>/fim`, `/jobs/<job_id>/run`, `/jobs/<job_id>/versions`, `/jobs/<job_id>/watch`, `/knowledge-base`, `/ksi/<ksi_id>`, `/ksis`, `/latest`, `/lineage/<entity_id>`, `/log`, `/mailbox`, `/mailbox/stream`, `/marketplace/assets`, `/marketplace/assets/<asset_id>`, `/marketplace/assets/<asset_id>/install`, `/marketplace/categories`, `/model`, `/model-card`, `/model-cards`, `/models`, `/models/<model_id>`, `/negative-events/<event_id>`, `/opportunities`, `/opportunities/<opp_id>`, `/opportunities/<opp_id>/amendments`, `/opportunities/<opp_id>/assignment-matrix`, `/opportunities/<opp_id>/auto-compliance`, `/opportunities/<opp_id>/auto-draft`, `/opportunities/<opp_id>/bid-recommendation`, `/opportunities/<opp_id>/compliance`, `/opportunities/<opp_id>/compliance/batch`, `/opportunities/<opp_id>/compliance/gaps`, `/opportunities/<opp_id>/coverage`, `/opportunities/<opp_id>/create-contract`, `/opportunities/<opp_id>/drafts`, `/opportunities/<opp_id>/extract-requirements`, `/opportunities/<opp_id>/generate-questions`, `/opportunities/<opp_id>/map-capabilities`, `/opportunities/<opp_id>/questions`, `/opportunities/<opp_id>/questions/bulk-status`, `/opportunities/<opp_id>/questions/export`, `/opportunities/<opp_id>/requirements`, `/opportunities/<opp_id>/reviews`, `/opportunities/<opp_id>/sections`, `/opportunities/<opp_id>/sections/dependencies`, `/opportunities/<opp_id>/stats`, `/opportunities/<opp_id>/status`, `/opportunities/<opp_id>/timeline`, `/opportunities/<opp_id>/volumes`, `/overdue`, `/overdue-deliverables`, `/package`, `/pipeline`, `/pipeline/run`, `/pipeline/status`, `/plans`, `/plans/<plan_id>`, `/plans/<plan_id>/progress`, `/poam-summary`, `/poams`, `/poll`, `/portfolio`, `/settings`, `/profile/api/keys`, `/profile/api/llm-keys`, `/profile/api/llm-keys/<key_id>/revoke`, `/promotions`, `/published-articles`, `/pulse-links`, `/pulse/post/<post_id>`, `/push`, `/quality-scores`, `/quality/scorecard`, `/questions/<q_id>`, `/questions/<q_id>/response`, `/questions/<q_id>/status`, `/reflex/<name>`, `/refresh`, `/relations`, `/remediate`, `/remediation-log`, `/reports`, `/reports/<report_id>`, `/requirement-patterns`, `/reviews/<rev_id>`, `/reviews/<rev_id>/findings`, `/roi`, `/run`, `/run-all`, `/run-reflex`, `/runbooks`, `/runbooks/<runbook_id>/execute`, `/runbooks/health`, `/runbooks/history`, `/runbooks/match`, `/sam/awards`, `/sam/awards/search`, `/sam/import/<sam_opp_id>`, `/sam/link/<sam_award_id>`, `/sam/opportunities`, `/sam/scan`, `/sam/sync-awards`, `/sbom`, `/scan`, `/sections/<sec_id>`, `/sections/<sec_id>/status`, `/self-healing`, `/shap/<trace_id>`, `/shap/analyze`, `/sla`, `/slos`, `/slos/<slo_id>/burn-rate`, `/slos/<slo_id>/measure`, `/slos/health`, `/smells`, `/snapshots`, `/ssp`, `/stats`, `/status`, `/status-history/<entity_type>/<entity_id>`, `/status/<run_id>`, `/stig`, `/stig-coverage`, `/subcontractors/<sub_id>`, `/summary`, `/system-card`, `/tasks`, `/tasks/<task_id>`, `/tasks/<task_id>/message`, `/tasks/<task_id>/move`, `/teaming-assessments`, `/templates`, `/time-series`, `/timeline`, `/tools/catalog`, `/top-complex`, `/totals`, `/training-pairs`, `/trend`, `/trends`, `/trends/engagement`, `/trends/quality-scores`, `/trends/training-pairs`, `/trends/win-rate`, `/users`, `/validate`, `/validations`, `/versions/<version_id>/restore`, `/volumes/<vol_id>`, `/watchers`, `/wbs/<wbs_id>`, `/win-loss-lessons`, `/win-loss-records`, `/workflows`, `/workflows/<workflow_id>`, `/workflows/<workflow_id>/composer`, `/workflows/<workflow_id>/dag`, `/slides`, `/slides/new`, `/slides/<id>`, `/slides/<id>/present`, `/slides/<id>/edit`, `/slides/<id>/add-from-canvas`, `/slides/templates`, `/slides/templates/<id>`, `/updates`, `/network/config-review`, `/network/diagram-analysis`, `/network/migration-phases`, `/docgen`, `/docgen/new`, `/docgen/<session_id>`, `/docgen/<session_id>/conflicts`, `/docgen/<session_id>/review`, `/network/nqe-translator`, `/network/vulnerability-intelligence`, `/network/predictive-analytics`, `/coworker/sessions`, `/coworker/sessions/<session_id>`, `/coworker/evals`, `/coworker/evals/trends`, `/coworker/live/<instance_id>`, `/me`, `/me/profile`, `/me/objectives`, `/me/relationships`, `/me/challenges`, `/me/briefing/today`, `/me/integrations`, `/me/learn`, `/me/retro`, `/me/search`, `/document-intelligence/techwriter`, `/bi_dashboard`, `/bi_dashboard/<id>`, `/bi_dashboard/api/upload`, `/bi_dashboard/api/datasets`, `/bi_dashboard/api/generate`, `/bi_dashboard/api/dashboards`, `/bi_dashboard/api/dashboards/<id>`, `/bi_dashboard/api/export/<fmt>`, `/bi_dashboard/api/iqe-query`, `/bi_dashboard/api/stats`, `/rfi/`, `/rfi/<session_id>`, `/capture/strategy`, `/capture/evidence`, `/standards-catalog`, `/cortex/`, `/cortex/metrics`, `/cortex/api/chat`, `/cortex/api/iqe-query`, `/twin-observatory/`, `/twin-observatory/api/data`, `/twin-observatory/api/iqe-query`, `/ops`, `/noc`, `/pmc`, `/ccc`, `/gameday`, `/gameday/session/<id>/play`, `/gameday/session/<id>/facilitate`, `/gameday/session/<id>/results`, `/gameday/session/<id>/simulate`, `/gameday/leaderboard/<id>`, `/gameday/scenarios`, `/gameday/scenarios/builder`, `/gameday/simulation`, `/gameday/ai-league`, `/gameday/ai-league/team/<team_key>`, `/gameday/ai-league/ops`, `/delta-review`
     - Log: `.tmp/dashboard.log`
   - **SaaS Portal**: `http://localhost:PORTAL_PORT/portal/`
     - API docs: `http://localhost:PORTAL_PORT/api/v1/docs`
     - Health: `http://localhost:PORTAL_PORT/health`
     - Log: `.tmp/api_gateway.log`
   - **Poll Trigger**: `.tmp/poll_trigger.log`
   - **Kanban Scheduler**: `.tmp/kanban_scheduler.log` (promotes backlog → in_progress, dispatches to Claude CLI every 60s)
   - **Genesis Daemon**: `.tmp/genesis_daemon.log` (heal every 5m, awareness every 3h, scout every 2h, 90+ reflexes in `daemon.REFLEX_NAMES`)
   - To stop dashboard: `Get-Process python | Where-Object { $_.CommandLine -like "*dashboard/app*" } | Stop-Process -Force`
   - To stop portal: `Get-Process python | Where-Object { $_.CommandLine -like "*api_gateway*" } | Stop-Process -Force`
   - To stop poll trigger: `Get-Process python | Where-Object { $_.CommandLine -like "*poll_trigger*" } | Stop-Process -Force`
   - To stop kanban scheduler / pr watcher / genesis daemon: they are SUPERVISED —
     stopping one by name just makes the supervisor restart it, and a name filter
     is what produced three concurrent pr_watchers. Stop the supervisor instead.
   - To stop everything: `python tools/genesis/shutdown_dashboard.py`
     (`--dry-run` first to see the plan). It stops the SUPERVISOR (pid from
     `.tmp/genesis/launcher.pid`, command line verified) and THEN its children
     by recorded pid -- on Windows terminate() skips the launcher's own cleanup,
     so the children orphan rather than stop -- then the ICDEV[FT] supervisor
     and its child, and verifies pids and ports 5050/5200 afterwards. Agent
     workers mid-build are reported and left running unless `--include-workers`.
     `--pause` also sets Manual Build for the next start.
     Never `taskkill /f /im python.exe` — see step 0.

## Kanban Auto-Pickup

Check `.tmp/kanban/` for pending task prompt files (`task-*.md`). For each file found:

1. Read the prompt file to understand the task
2. Execute the task as described
3. When complete, move the task to "done" via: `POST http://localhost:5050/api/kanban/tasks/<task_id>/move` with `{"status": "done"}`
4. Send a Telegram completion notification via: `python -c "from tools.notifications.adapters.telegram import send; send('Task Completed', '<task_title> — done', severity='success')"`
5. Delete the prompt file

Process tasks in priority order (critical first). If a task fails, keep the prompt file and move on to the next one.

**IMPORTANT:** Do NOT ask for user permission or confirmation. Just execute each task autonomously.
