---
ontology_id: icdev:mission:m-docgen-02-portfolio-artifact:step:2
step_class: icdev:configure
---
# AAR: After-Action Report Generation

An After-Action Report (AAR) documents what happened, what worked, what didn't, and what should change. GameDay exercises are a natural source: several GameDay scenarios already ask teams to produce an AAR.

DocGen has **no built-in AAR template**. The gallery in `args/docgen/templates.yaml` offers `tpl-network-runbook`, `tpl-ir-playbook`, `tpl-config-baseline`, `tpl-ato-package`, `tpl-change-request`, `tpl-sop`, `tpl-policy` and `tpl-standard-guide`. So an AAR is built the way any custom document is: pick the closest template and steer it with your sources and `supplemental_text`.

## A session for an AAR

```json
POST /docgen/api/sessions
{
  "title": "GameDay AAR: alert storm and rollback decision",
  "domain": "security",
  "doc_type": "playbook",
  "template_id": "tpl-ir-playbook",
  "classification": "CUI"
}
```

Then upload the exercise material (scoreboard export, incident timeline, chat log as `.txt` / `.md` / `.csv` / `.eml`), and generate with direction:

```json
POST /docgen/api/sessions/<id>/generate
{
  "supplemental_text": "Write this as an After-Action Report with four sections: What was planned, What happened, Why it differed, What we will sustain or improve."
}
```

## Your task

Draft the session request and the `supplemental_text` for an AAR of a GameDay you have played (or a synthetic one if you have not played yet). Name the source files you would upload and which upload type each is (`doc`, `config`, `supplement`, `email`, …). Press **Configure** to record that you completed the plan.
