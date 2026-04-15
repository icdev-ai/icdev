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

