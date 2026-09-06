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

8b. **ICDEV[FT] and ICDEV[RT] — each under ITS OWN supervisor, never beside a live one.**

   `C:\ai\icdev_ft\supervise_ft.py` serves `launch_ft.py` on 127.0.0.1:5200 and
   `C:\ai\icdev_rt\supervise_rt.py` serves `launch_rt.py` on 127.0.0.1:5300. Each
   polls origin/main, redeploys fast-forward only, and rolls back on a failed
   deep-health probe — the same shape as step 8, in a different checkout. Both
   need `PYTHONPATH=C:\AI\ICDev` for the `tools.*` tree, and FT needs
   PostgreSQL accepting connections (step 0's cleanup already proved it; on a
   fresh boot PG reports "the database system is starting up" for a minute and
   the FT child fails its first probe — the supervisor retries, so wait, do not
   start a second one). A second supervisor beside a live one fights it for the
   port, so the LISTENER is checked first, exactly as step 1 does for 5050:
   ```powershell
   $env:PYTHONPATH = "C:\AI\ICDev"
   foreach ($svc in @(
       @{ name = "icdev_ft"; dir = "C:\ai\icdev_ft"; script = "supervise_ft.py"; port = 5200 },
       @{ name = "icdev_rt"; dir = "C:\ai\icdev_rt"; script = "supervise_rt.py"; port = 5300 })) {
     $up = Get-NetTCPConnection -State Listen -LocalPort $svc.port -ErrorAction SilentlyContinue
     if ($up) { "$($svc.name): already serving on $($svc.port) (pid $($up.OwningProcess)) -- not starting a second supervisor"; continue }
     New-Item -ItemType Directory -Force "$($svc.dir)\.tmp" | Out-Null
     $p = Start-Process python -ArgumentList $svc.script -WorkingDirectory $svc.dir -RedirectStandardOutput "$($svc.dir)\.tmp\supervisor.log" -RedirectStandardError "$($svc.dir)\.tmp\supervisor_err.log" -WindowStyle Hidden -PassThru
     "$($svc.name): supervisor PID $($p.Id)"
   }
   Start-Sleep -Seconds 20
   foreach ($port in 5200, 5300) {
     try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$port/api/v1/health/deep"; "port $($port): RUNNING (HTTP $($r.StatusCode))" }
     catch { "port $($port): NOT RUNNING yet -- read <checkout>\.tmp\supervisor.log; FT waits for PostgreSQL" }
   }
   ```
   Stop them with `/stop` — `shutdown_dashboard.py` stops both pairs by verified
   pid (supervisor first, then its child) and `--keep-ft` / `--keep-rt` keep one.
   Never `Stop-Process` a `launch_*.py` child by name: its supervisor restarts it.

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
<!-- Derived from the app url_map by tools/dashboard/nav_paths.py.
     Regenerate: python tools/dashboard/nav_paths.py --write  (mfx-sib-02) -->
<!-- BEGIN GENERATED start-pages | DO NOT HAND-EDIT -->
- Pages: `/`, `/academy`, `/academy/achievements`, `/academy/arena`, `/academy/certificate/<cert_key>`, `/academy/guild`, `/academy/instructor`, `/academy/instructor/learner/<int:user_id>`, `/academy/leaderboard`, `/academy/mission/<slug>`, `/academy/missions`, `/academy/my-certificates`, `/academy/oracle`, `/academy/org-readiness`, `/academy/patterns`, `/academy/patterns/<pattern_id>`, `/academy/profile`, `/academy/skill-tree`, `/academy/verify/<token>`, `/academy/workflow-builder`, `/activity`, `/admin`, `/admin/`, `/admin/users`, `/agentic-ai/`, `/agentic-ai/analytics`, `/agentic-ai/artifacts/<design_id>`, `/agentic-ai/assessments/<design_id>`, `/agentic-ai/ato/<design_id>`, `/agentic-ai/canvas`, `/agentic-ai/canvas/<design_id>`, `/agentic-ai/canvas/<design_id>/ft-link`, `/agentic-ai/canvas/<design_id>/kanban-status`, `/agentic-ai/canvas/<design_id>/ops-config`, `/agentic-ai/canvas/<design_id>/versions`, `/agentic-ai/canvas/<design_id>/versions/diff`, `/agentic-ai/deploy-gate/<design_id>`, `/agentic-ai/exec-summary/<design_id>`, `/agentic-ai/findings`, `/agentic-ai/impact-graph`, `/agentic-ai/impact/<design_id>`, `/agentic-ai/lifecycle/<design_id>`, `/agentic-ai/monitoring`, `/agentic-ai/patterns/<design_id>`, `/agentic-ai/quick-start`, `/agentic-ai/red-team/<design_id>`, `/agentic-ai/review/<design_id>`, `/agentic-ai/risks/<design_id>`, `/agentic-ai/scorecard/<design_id>`, `/agentic-ai/snippets`, `/agentic-ai/solutions`, `/agentic-ai/templates`, `/agents`, `/ai-accountability`, `/ai-augmentation/`, `/ai-augmentation/<path:subpath>`, `/ai-builder`, `/ai-handoff`, `/ai-ify/`, `/ai-ify/posture`, `/ai-learning`, `/ai-ml/`, `/ai-ml/assessments/<assessment_id>`, `/ai-ml/canvas/<design_id>`, `/ai-ml/canvas/new`, `/ai-ml/model-catalog`, `/ai-ml/modernize`, `/ai-ml/snippets`, `/ai-ml/templates`, `/ai-observatory`, `/ai-patterns`, `/ai-roi`, `/ai-skills`, `/ai-transparency`, `/ai-wizard`, `/analysis`, `/analytics`, `/ask-icdev`, `/ato-compliance`, `/ato-package`, `/auth/saml/<provider_id>/login`, `/auth/saml/<provider_id>/metadata`, `/auth/saml/oidc/<provider_id>/callback`, `/auth/saml/oidc/<provider_id>/login`, `/autonomous-coder/`, `/autoresearch`, `/batch`, `/bi_dashboard/`, `/bi_dashboard/<dashboard_id>`, `/boundary/`, `/boundary/ask`, `/boundary/assessments`, `/boundary/ato-compliance`, `/boundary/ato-package`, `/boundary/canvas/<design_id>`, `/boundary/canvas/new`, `/boundary/cato`, `/boundary/cato-health`, `/boundary/compliance-debt`, `/boundary/compliance-hub`, `/boundary/compliance/<design_id>`, `/boundary/control-inheritance`, `/boundary/fedramp-20x`, `/boundary/isa-tracker`, `/boundary/mosa`, `/boundary/oscal`, `/boundary/poam`, `/boundary/pps-matrix/<design_id>`, `/boundary/remediation/<design_id>`, `/boundary/runbooks`, `/boundary/sops`, `/boundary/templates`, `/boundary/twin/<design_id>`, `/cache-savings`, `/canvas-compliance`, `/canvas-kg`, `/capture/evidence`, `/capture/strategy`, `/cato`, `/ccc`, `/ccc/capacity`, `/ccc/circuits`, `/ccc/cross-connects`, `/ccc/dwdm`, `/ccc/loa`, `/ccc/orders`, `/chat`, `/chat/<session_id>`, `/children`, `/cicd`, `/clawhub`, `/code-quality`, `/compliance`, `/compliance-debt`, `/components-map`, `/connector-forge`, `/control-inheritance`, `/cortex/`, `/cortex/metrics`, `/coworker`, `/coworker/`, `/coworker/<instance_id>`, `/coworker/<instance_id>/resume`, `/coworker/evals`, `/coworker/evals/trends`, `/coworker/live/<instance_id>`, `/coworker/profiles/new`, `/coworker/roles`, `/coworker/sessions`, `/coworker/sessions/<session_id>`, `/coworker/trust`, `/coworkers`, `/coworkers/`, `/coworkers/c/<path:coworker_id>`, `/coworkers/status`, `/cpmp`, `/cpmp/<contract_id>`, `/cpmp/<contract_id>/deliverables/<deliverable_id>`, `/cpmp/cor`, `/cpmp/cor/<contract_id>`, `/cpmp/deliverables`, `/cpmp/reports`, `/dashboard/compliance-view`, `/dashboard/executive-view`, `/dashboard/pm-view`, `/dat`, `/data/`, `/data/ask`, `/data/assessments`, `/data/canvas/<design_id>`, `/data/canvas/new`, `/data/contracts`, `/data/csp`, `/data/domains`, `/data/explore`, `/data/geoint`, `/data/governance`, `/data/lineage`, `/data/mapping/`, `/data/mapping/<session_id>`, `/data/mapping/new`, `/data/mesh`, `/data/osint`, `/data/pipeline-ops`, `/data/products`, `/data/quality`, `/data/query`, `/data/remediation/<design_id>`, `/data/runbooks`, `/data/runbooks/<runbook_id>`, `/data/sops`, `/data/sops/<sop_id>`, `/data/templates`, `/data/twin/<design_id>`, `/delta-review`, `/delta-review/`, `/demo-runner/`, `/dev-profiles`, `/devops/`, `/devops/ask`, `/devops/canvas/<pipe_id>`, `/devops/canvas/new`, `/devops/runbooks`, `/devops/runbooks/<runbook_id>`, `/devops/sops`, `/devops/twin`, `/devops/twin/<pipe_id>`, `/devops/twin/<pipe_id>/delta`, `/diagrams`, `/digital-twin`, `/docgen/`, `/docgen/<session_id>`, `/docgen/<session_id>/conflicts`, `/docgen/<session_id>/review`, `/docgen/new`, `/document-intelligence/`, `/document-intelligence/acoic`, `/document-intelligence/analytics`, `/document-intelligence/collections`, `/document-intelligence/doc/<doc_id>`, `/document-intelligence/docdrift`, `/document-intelligence/explorer`, `/document-intelligence/freshness`, `/document-intelligence/generate`, `/document-intelligence/handoff`, `/document-intelligence/notebook`, `/document-intelligence/notebook/<collection_id>`, `/document-intelligence/review`, `/document-intelligence/search`, `/document-intelligence/techwriter`, `/document-intelligence/templates`, `/dsoc`, `/dsoc/bgp-security`, `/dsoc/flowspec`, `/dsoc/mitigations`, `/dsoc/rtbh`, `/dsoc/scrubbing`, `/dsoc/threats`, `/events`, `/evidence`, `/favicon.ico`, `/fedramp-20x`, `/filesync`, `/finetune`, `/finetune/datasets`, `/finetune/datasets/<dataset_id>`, `/finetune/evaluate`, `/finetune/jobs`, `/finetune/jobs/<job_id>`, `/finetune/label`, `/finetune/models`, `/finetune/models/<model_id>`, `/forge-academy`, `/forge-academy/`, `/forge-academy/<path:rest>`, `/foundry`, `/foundry/`, `/foundry/<concept_id>`, `/gameday`, `/gameday/ai-league`, `/gameday/ai-league/ops`, `/gameday/ai-league/team/<team_key>`, `/gameday/leaderboard/<int:session_id>`, `/gameday/scenarios`, `/gameday/scenarios/builder`, `/gameday/session/<int:session_id>/facilitate`, `/gameday/session/<int:session_id>/play`, `/gameday/session/<int:session_id>/register`, `/gameday/session/<int:session_id>/registrations`, `/gameday/session/<int:session_id>/results`, `/gameday/session/<int:session_id>/simulate`, `/gameday/simulation`, `/gateway`, `/genesis`, `/geosigint/`, `/geosigint/a2ad`, `/geosigint/amphibious`, `/geosigint/island-chain`, `/geosigint/militia`, `/geosigint/semiconductor`, `/geosigint/static/geosigint/<path:filename>`, `/geosigint/strait-crossing`, `/geospatial`, `/geospatial/static/<path:filename>`, `/geospatial/table`, `/govcon`, `/govcon/capabilities`, `/govcon/requirements`, `/govlift`, `/govlift/audit`, `/govlift/executor`, `/govlift/migrations/<migration_id>`, `/govlift/stig`, `/govlift/stig/<check_id>`, `/govlift/waves`, `/govlift/waves/<wave_id>`, `/govlift/workloads`, `/govlift/workloads/<workload_id>`, `/health`, `/health/canvases`, `/health/live`, `/health/ready`, `/iac`, `/idp/`, `/idp/catalog`, `/idp/component/<key>`, `/idp/evidence`, `/idp/scorecards`, `/il5`, `/infra/`, `/infra/ask`, `/infra/assessments`, `/infra/canvas/<design_id>`, `/infra/canvas/new`, `/infra/emit`, `/infra/remediation/<design_id>`, `/infra/runbooks`, `/infra/sops`, `/infra/templates`, `/infra/twin`, `/innovation`, `/innovation/`, `/innovation/<int:idea_id>`, `/innovation/dashboard`, `/innovation/new`, `/intake/prd/<session_id>/view`, `/intake/requirements/<session_id>`, `/integrity`, `/integrity/`, `/integrity/<int:assessment_id>`, `/iqe`, `/kanban`, `/knowledge-graph`, `/knowledge-search`, `/leads`, `/lineage`, `/login`, `/logout`, `/logs`, `/logs/`, `/mcip`, `/mcp-wrapper`, `/me/`, `/me/briefing/today`, `/me/challenges`, `/me/customers`, `/me/integrations`, `/me/learn`, `/me/objectives`, `/me/profile`, `/me/relationships`, `/me/retro`, `/me/search`, `/metrics`, `/migration`, `/migration-canvas/`, `/migration-canvas/assessments`, `/migration-canvas/canvas/<design_id>`, `/migration-canvas/canvas/new`, `/migration-canvas/compliance-wizard`, `/migration-canvas/network-migration/`, `/migration-canvas/network-migration/<session_id>`, `/migration-canvas/network-migration/<session_id>/port-diagram`, `/migration-canvas/network-migration/new`, `/migration-canvas/projects`, `/migration-canvas/projects/<project_id>`, `/migration-canvas/server-migration/`, `/migration-canvas/server-migration/<sid>`, `/migration-canvas/server-migration/<sid>/inventory/import`, `/migration-canvas/server-migration/<sid>/waves`, `/migration-canvas/server-migration/new`, `/migration-canvas/sops`, `/migration-canvas/templates`, `/migration-cost`, `/migration-intel`, `/migration-intel/`, `/mission-canvas/`, `/mission-canvas/detail/<mission_id>`, `/monitoring`, `/mosa`, `/ndc/sops`, `/network/`, `/network/advisory-history`, `/network/ask`, `/network/budget`, `/network/cables`, `/network/canvas/<topo_id>`, `/network/canvas/new`, `/network/capacity`, `/network/charts`, `/network/circuits`, `/network/cloud-topology`, `/network/collect`, `/network/compliance-audit`, `/network/compliance/<topo_id>`, `/network/config-review`, `/network/conflicts`, `/network/connectivity`, `/network/cross-connects`, `/network/customers`, `/network/demo-runner`, `/network/design-patterns`, `/network/design-rules`, `/network/device-profiles`, `/network/diagram-analysis`, `/network/discovery`, `/network/documents`, `/network/enterprise`, `/network/exceptions`, `/network/exceptions/file`, `/network/executive-dashboard`, `/network/facilities`, `/network/fcc`, `/network/global`, `/network/global/canvas`, `/network/hardware-profiles`, `/network/ingestion`, `/network/innovation`, `/network/intelligence`, `/network/ipam`, `/network/labs`, `/network/logout`, `/network/map`, `/network/migration-hub`, `/network/migration-phases`, `/network/migration-phases/<topo_id>`, `/network/migration-wizard`, `/network/montecarlo/<topo_id>`, `/network/netbox`, `/network/network/predictive-analytics`, `/network/network/vulnerability-intelligence`, `/network/nqe-translator`, `/network/partners`, `/network/peering`, `/network/poam`, `/network/port-mapping`, `/network/pps/<topo_id>`, `/network/projects`, `/network/projects/<pid>/presentation`, `/network/projects/<proj_id>`, `/network/projects/<project_id>`, `/network/projects/compare`, `/network/projects/diff`, `/network/replacements`, `/network/runbooks`, `/network/simulation/<sim_id>`, `/network/sops`, `/network/stencils`, `/network/subnet-calc`, `/network/template/<tpl_id>/edit`, `/network/templates`, `/network/twin/<topo_id>`, `/network/versions/<topo_id>`, `/network/wave-planner`, `/network/what-if`, `/network/wizard`, `/news`, `/noc`, `/noc/alarms`, `/noc/incidents`, `/noc/looking-glass`, `/noc/maintenance`, `/noc/mops`, `/noc/rfcs`, `/noc/sla`, `/notifications`, `/observability/`, `/observability/ask`, `/observability/assessments`, `/observability/canvas/<design_id>`, `/observability/canvas/new`, `/observability/coverage/<design_id>`, `/observability/kill-chain`, `/observability/mitre`, `/observability/mitre/<tid>`, `/observability/remediation/<design_id>`, `/observability/runbooks`, `/observability/sops`, `/observability/templates`, `/observability/twin/<design_id>`, `/ontology`, `/ops`, `/ops/incidents`, `/ops/llm`, `/ops/models`, `/ops/runbooks`, `/ops/self-healing`, `/ops/slos`, `/ops/topology`, `/options`, `/oracle`, `/orchestration`, `/oscal`, `/phases`, `/platform-health`, `/pmc`, `/pmc/ix`, `/pmc/peers`, `/pmc/peers/<peer_id>`, `/pmc/policies`, `/pmc/requests`, `/pmc/rpki`, `/pmc/transit`, `/poam`, `/portal/`, `/portal/ai-accountability`, `/portal/ai-ify`, `/portal/ai-transparency`, `/portal/audit`, `/portal/chat`, `/portal/cmmc`, `/portal/code-quality`, `/portal/compliance`, `/portal/keys`, `/portal/knowledge-search`, `/portal/login`, `/portal/logout`, `/portal/notifications`, `/portal/oscal`, `/portal/prod-audit`, `/portal/profile`, `/portal/projects`, `/portal/settings`, `/portal/static/<path:filename>`, `/portal/team`, `/portal/translations`, `/portal/translations/<job_id>`, `/portal/usage`, `/portal/zig`, `/pr-intel`, `/prod-audit`, `/profile`, `/projects`, `/projects/<project_id>`, `/proposal-genesis`, `/proposals`, `/proposals/<opp_id>`, `/proposals/<opp_id>/compliance/gaps`, `/proposals/<opp_id>/language`, `/proposals/<opp_id>/ptw`, `/proposals/<opp_id>/sections/<sec_id>`, `/proposals/reviews-dashboard`, `/provenance`, `/pulse`, `/pulse/post/<post_id>`, `/quality-scores`, `/quality/`, `/quality/assessments`, `/quality/canvas/<design_id>`, `/quality/canvas/new`, `/quality/remediation/<design_id>`, `/quality/runbooks`, `/quality/snippets`, `/quality/sops`, `/quality/templates`, `/query`, `/quick-paths`, `/research`, `/review-board`, `/rfi`, `/rfi/`, `/rfi/<session_id>`, `/rfi/<session_id>/preview`, `/robots.txt`, `/safety`, `/safety/circuit-breaker`, `/sandbox`, `/sbd`, `/security-scan`, `/security/`, `/security/ai-accountability`, `/security/ai-transparency`, `/security/artifacts/<design_id>`, `/security/ask`, `/security/assessment/<assessment_id>`, `/security/attackpath`, `/security/canvas/<design_id>`, `/security/canvas/new`, `/security/compare`, `/security/demo`, `/security/posture`, `/security/prod-audit`, `/security/remediation/<design_id>`, `/security/runbooks`, `/security/runbooks/<runbook_id>`, `/security/sbd`, `/security/sops`, `/security/stig-manager`, `/security/templates`, `/security/twin/<design_id>`, `/security/zig`, `/security/zig/`, `/security/zig/assessment`, `/security/zig/phase`, `/security/zig/pillar/<pillar_slug>`, `/security/zig/portfolio`, `/security/zig/roadmap`, `/settings`, `/simulate`, `/simulate/`, `/simulate/chat`, `/simulation`, `/skillhub`, `/slides/`, `/slides/<int:deck_id>`, `/slides/<int:deck_id>/present`, `/slides/new`, `/slides/templates`, `/slides/templates/<int:template_id>`, `/sre`, `/standards-catalog/`, `/stig-manager`, `/strategos/`, `/strategos/airspace`, `/strategos/airspace/`, `/strategos/bda`, `/strategos/bda/`, `/strategos/briefs`, `/strategos/briefs/<brief_id>`, `/strategos/commander`, `/strategos/commander/`, `/strategos/cyber`, `/strategos/darkweb`, `/strategos/darkweb/`, `/strategos/dat`, `/strategos/dat/`, `/strategos/ew`, `/strategos/f3ead`, `/strategos/f3ead/`, `/strategos/ghost`, `/strategos/hitl`, `/strategos/indicators`, `/strategos/indicators/`, `/strategos/info`, `/strategos/intel-brief`, `/strategos/intel-brief/`, `/strategos/interdiction`, `/strategos/intsum`, `/strategos/intsum/`, `/strategos/ipb`, `/strategos/ipb/`, `/strategos/isr-planner`, `/strategos/isr-planner/`, `/strategos/iw`, `/strategos/kg`, `/strategos/leadership-brief`, `/strategos/leadership-brief/`, `/strategos/map`, `/strategos/maritime`, `/strategos/mett-tc`, `/strategos/mett-tc/`, `/strategos/opord`, `/strategos/opord/`, `/strategos/oracle`, `/strategos/orbat`, `/strategos/osint`, `/strategos/pir`, `/strategos/red-cell`, `/strategos/red-cell/`, `/strategos/signals`, `/strategos/simulate`, `/strategos/sources`, `/strategos/sources/`, `/strategos/supply`, `/strategos/sync-matrix`, `/strategos/sync-matrix/`, `/strategos/war-council`, `/strategos/wargame`, `/studio/app-builder`, `/studio/automations`, `/studio/cases`, `/studio/dashboards`, `/studio/forms`, `/studio/marketplace`, `/studio/workflows`, `/supply_chain`, `/system-graph`, `/system-graph/`, `/traces`, `/translations`, `/translations/<job_id>`, `/twin-observatory/`, `/updates`, `/usage`, `/war-endurance`, `/wizard`, `/workflow`, `/workflow-canvas/`, `/workflow-canvas/forms`, `/workflow-canvas/forms/<form_id>`, `/workflow-canvas/forms/<form_id>/edit`, `/workflow-canvas/forms/new`, `/workflow-canvas/my-tasks`, `/workflow-canvas/processify`, `/workflow-canvas/templates`, `/workflow-canvas/workflows`, `/workflow-canvas/workflows/<workflow_id>`, `/workflow-canvas/workflows/<workflow_id>/edit`, `/workflow-canvas/workflows/new`, `/workflow/`, `/workflow/hitl`, `/workflow/teams`, `/writeguard`, `/xai`, `/zta/`, `/zta/cls-posture`, `/zta/lac-simulator`, `/zta/lac/audit`, `/zta/lac/scenarios`, `/zta/lac/scenarios/<scenario_id>`
<!-- END GENERATED start-pages -->
     - Log: `.tmp/dashboard.log`
   - **SaaS Portal**: `http://localhost:PORTAL_PORT/portal/`
     - API docs: `http://localhost:PORTAL_PORT/api/v1/docs`
     - Health: `http://localhost:PORTAL_PORT/health`
     - Log: `.tmp/api_gateway.log`
   - **Poll Trigger**: `.tmp/poll_trigger.log`
   - **ICDEV[FT]**: `http://127.0.0.1:5200` under `C:\ai\icdev_ft\supervise_ft.py` — log `C:\ai\icdev_ft\.tmp\supervisor.log`
   - **ICDEV[RT]**: `http://127.0.0.1:5300` under `C:\ai\icdev_rt\supervise_rt.py` — log `C:\ai\icdev_rt\.tmp\supervisor.log`
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
     so the children orphan rather than stop -- then the ICDEV[FT] and ICDEV[RT]
     supervisors, each before its child, and verifies pids and ports
     5050/5200/5300 afterwards (`--keep-ft` / `--keep-rt` keep one). Agent
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
