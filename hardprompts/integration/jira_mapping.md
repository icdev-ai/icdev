# Jira Integration Mapping Prompt

## Role
You are mapping ICDEV SAFe decomposition items to Jira issue types.

## Mapping Rules
- Epic → Jira Epic (with ICDEV ID in description)
- Capability → Jira Epic with "Capability" label
- Feature → Jira Story (with acceptance criteria)
- Story → Jira Sub-task (linked to parent Feature)
- Enabler → Jira Task with "Enabler" label

## Field Mapping
- title → summary
- description → description (prepend CUI marking)
- acceptance_criteria → custom field (configured per org)
- t_shirt_size → custom field
- priority → Jira priority (P1→Highest, P2→High, P3→Medium, P4→Low)
- wsjf_score → custom field

## Sync Rules
- Push creates new issues or updates existing (by ID mapping)
- Pull updates status and comments
- Never delete Jira issues from ICDEV
- Conflict resolution: last-write-wins with audit trail
