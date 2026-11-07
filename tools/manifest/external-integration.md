# External Integration (RICOAS Phase 4)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## External Integration (RICOAS Phase 4)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SharePoint DOM Selectors | tools/sharepoint/selectors.py | Centralized CSS/XPath selector constants for the Selenium fallback scraper (Phase F/P4.2). One file = one point of failure when SharePoint DOM drifts. Last-verified version in module docstring. | (import) | Module-level string constants |
| Jira Connector | tools/integration/jira_connector.py | Bidirectional Jira sync — SAFe items map to Jira issue types (Epic/Story/Sub-task) | --project-id, --configure, --push, --pull, --json | Sync results |
| ServiceNow Connector | tools/integration/servicenow_connector.py | Bidirectional ServiceNow sync — requirements map to ServiceNow incidents/requests/changes | --project-id, --configure, --push, --pull, --json | Sync results |
| GitLab Connector | tools/integration/gitlab_connector.py | Bidirectional GitLab sync — SAFe items map to GitLab epics/issues/merge requests | --project-id, --configure, --push, --pull, --json | Sync results |
| DOORS Exporter | tools/integration/doors_exporter.py | Export requirements as ReqIF 1.2 for DOORS NG import | --session-id, --export-reqif, --output-path, --json | ReqIF file path |
| Approval Manager | tools/integration/approval_manager.py | Approval workflows for requirements packages, COA selection, boundary acceptance | --session-id, --submit, --review, --status, --json | Approval status |
| Traceability Builder | tools/requirements/traceability_builder.py | Full RTM: requirement > SysML > code > test > control > UAT with coverage analysis | --project-id, --build-rtm, --gap-analysis, --json | RTM + coverage % |
| MCP Integration Server | tools/mcp/integration_server.py | MCP server for integration tools (10 tools: Jira, ServiceNow, GitLab, DOORS, approval, RTM) | stdio | JSON-RPC responses |

| Open WebUI Configurator | tools/openwebui/configure.py | Make ICDEV the only backend Open WebUI can reach: --check names every bypass (direct Ollama/OpenAI, user direct connections, foreign tool servers), --plan is read-only, --apply writes the governed config via the Open WebUI admin API (owui-api-03, D-OWUI-1) | --icdev-url, --check, --plan, --apply, --with-tools, --env, --json | Findings / redacted writes JSON |
| Open WebUI Gateway Refresh | tools/openwebui/refresh_gateway.sh | Refresh a governed demo's ICDEV gateway to origin/main: export to src.new, smoke-test on a spare port with the demo key (/v1/models, one local chat, MCP nist_lookup), and only then swap and restart; a failed check leaves the live gateway untouched (owui-ship-05/09) | GATEWAY_DIR, GATEWAY_SERVICE, SMOKE_PORT, ICDEV_KEY_FILE, --force | "refreshed <old> -> <new>" or SMOKE FAILED |
| Open WebUI Per-Role Check | tools/openwebui/verify_roles.sh | Each demo user's own Keycloak token straight against the gateway: models, chat, admin-only kanban, read and write MCP tools, plus tampered/forged/no-auth 401 controls; enables the client's password grant for its own run only and turns it off in an EXIT trap (owui-idp-02/ship-09) | GATEWAY_URL, KEYCLOAK_URL, REALM, USERS_FILE, OIDC_ENV_FILE, KEYCLOAK_ADMIN_FILE | Per-role verdict lines; never a secret |
