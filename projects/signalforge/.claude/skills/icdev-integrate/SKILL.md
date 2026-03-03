---
description: "Sync requirements with Jira, ServiceNow, GitLab, and DOORS NG"
---

# ICDEV External Integration

Bidirectional sync with Jira, ServiceNow, and GitLab. Export to DOORS NG via ReqIF. Manage approval workflows and build full traceability.

## Usage
/icdev-integrate <project-id> [--jira|--servicenow|--gitlab|--doors|--approve|--rtm]

## Workflow

### Push to External System
1. Verify integration is configured
2. Push decomposed SAFe items with --dry-run first
3. Review mapping, then push for real
4. Verify sync status

### Pull Updates
1. Pull status/comment changes from external system
2. Review updates applied to SAFe items

### DOORS NG Export
1. Export session requirements as ReqIF 1.2
2. Import into DOORS NG

### Approval
1. Submit requirements package for review
2. Track pending approvals
3. Record reviewer decisions

## MCP Tools Used
- configure_jira, sync_jira, configure_servicenow, sync_servicenow
- configure_gitlab, sync_gitlab
- export_reqif, submit_approval, review_approval, build_traceability

## Example
```
/icdev-integrate proj-123 --jira --push --session sess-abc
```
