---
ontology_id: icdev:mission:m-docgen-02-portfolio-artifact:step:1
step_class: icdev:Lesson
---
# DocGen Artifacts as Portfolio Evidence

A published DocGen session leaves behind real, inspectable artifacts. Each published artifact is:

1. **Recorded** as a row on its session (`GET /docgen/api/sessions/<id>/artifacts`), downloadable as HTML, PDF and, where available, DOCX
2. **Classified**: the export carries the session's classification marking (CUI by default)
3. **Quality-gated**: it only exists because WriteGuard passed and the TRUST publish gates (placeholders, citations, claims) passed or were overridden with a recorded reason
4. **Traceable**: the session keeps its uploads, analyses and conflict resolutions, so a reviewer can see what the document was built from

## What this does and does not do for your certification

DocGen artifacts are **not** linked to your Academy profile automatically, and they do not count toward a certificate on their own. FORGE certificates (see `/academy/my-certificates`) are earned through the Academy itself:

| Certificate | Requirements |
|-------------|--------------|
| FORGE AI Foundation | Tier 1 complete + your full role Tier 2 track + the 20-question adaptive assessment |
| FORGE AI Practitioner (FAP) | Foundation + an AADC design score of 80 or higher + 1 GameDay scenario completed |
| FORGE AI Expert | Practitioner + the Tier 3 capstone + a top-50% GameDay finish |

What DocGen artifacts *are* good for is evidence you can show a human: a reviewer, an assessor, or your manager. A published SSP with its source trail is a stronger demonstration of hands-on proficiency than a description of one.

## Your task

Open `/academy/my-certificates` and note which certificate you are closest to and which requirement is still open. Then list the DocGen artifacts you would put in front of a reviewer as supporting evidence, and what each one demonstrates.
