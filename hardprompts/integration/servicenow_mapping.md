# ServiceNow Integration Mapping Prompt

## Role
You are mapping ICDEV SAFe items to ServiceNow Agile Development 2.0 records.

## Mapping Rules
- Epic → rm_epic table
- Feature/Story → rm_story table with category field
- Enabler → rm_story with "Enabler" category

## Sync Rules
- Push creates ServiceNow records with ICDEV reference
- Pull syncs state, assignment, and sprint information
- Honor ServiceNow business rules and ACLs
