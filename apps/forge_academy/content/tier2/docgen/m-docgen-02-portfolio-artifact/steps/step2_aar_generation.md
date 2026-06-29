---
ontology_id: icdev:mission:m-docgen-02-portfolio-artifact:step:2
step_class: icdev:configure
---
# AAR: After-Action Report Generation

An After-Action Report (AAR) documents what happened, what worked, what didn't, and what should change. DocGen can generate AARs from structured exercise data — GameDay tournament results, incident reports, or sprint retrospectives.

## AAR session config

```json
POST /api/docgen/sessions
{
  "doc_type": "aar",
  "source": {
    "type": "gameday_tournament",
    "tournament_id": "t-abc123"
  },
  "template": "mil_aar_std",
  "il_level": "IL4",
  "cert_token": "your-academy-cert-token"
}
```

## Your task

Generate an AAR from any past GameDay tournament (or a synthetic one if you haven't played yet). Use `doc_type: "aar"` and `template: "mil_aar_std"`. Include your `cert_token` so the artifact links to your portfolio.
