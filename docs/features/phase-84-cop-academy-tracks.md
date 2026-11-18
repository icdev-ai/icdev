<!-- CUI // SP-CTI -->
# FORGE Academy tracks per Community of Practice, with xAPI out to ICDEV[OS] (icx-cop-01)

Card 7 of the CoE / CoP programme. Owner decision (2026-11-17, "CoP home: split"): ICDEV[IT] hosts the CoP hubs
and the Academy learning paths; ICDEV[OS] holds the marked knowledge and the signed contribution workflow, and its CoP
charters link these learning paths by id (osx-cop-01). There is no cloud-vendor training or certification content:
the owner said skip it.

## What a learner sees

`/idp/cop-tracks` (Canvases > Developer Portal > CoP Learning Paths; also linked from the portal and the FORGE Academy
dashboard) lists four learning paths, one per Community of Practice, each three missions on this platform's own
patterns:

| Path (activity id `urn:icdev:academy:path:<id>`) | CoP | Missions |
|---|---|---|
| `cop-engineering-foundations` | engineering (AIDLC) | red first; the standards register and exemptions that expire; golden paths and scorecards |
| `cop-ai-data-foundations` | AI and data | the governed router; agents propose, people decide, citations checked; the twin (unverified is never passed) |
| `cop-application-foundations` | application | the five rules; declared workflow steps; pages that name routes by endpoint id |
| `cop-cloud-foundations` | cloud | marking; the append-only, hash-chained audit; PostgreSQL first and scaffolded migrations |

Each mission cites the files a learner reads (a test holds every citation to a file that exists) and asks three
questions. Answers are graded on the server against the key in `args/academy_cop_tracks.yaml`; no route, page or MCP
tool returns it. Every attempt is appended to the learner's own hash-chained record (`cop_academy_events`,
`learner:<pseudonym>`): `attempted` when not passed, `completed` when passed, with the answers' digest, not the answers.

## xAPI out to ICDEV[OS]

Statements are derived from that ledger alone, one per learner and path (xAPI 1.0.3 statement shape):

* actor: a pseudonymous `account` (`homePage` `https://icdev.ai/academy`, a random name kept once in
  `cop_academy_learners`), never a name or a mailbox;
* verb: ADL `completed` (every mission of the path as declared now passed; `result.completion` and `success` true) or
  `progressed` (`result.completion` false, so ICDEV[OS] never reads it as complete);
* object: the path's activity id; timestamp: the pass that made it so; id: a UUIDv5 over path, account, verb and time.

They leave as one `icos-cop-academy/v1` batch, ICDEV[OS]'s schema as drafted in osx-cop-01 (contract 1.194.0, not yet
merged there), vendored and pinned at `context/icos/cop-academy-statements.schema.json`. Its `content_digest` is
SHA-256 over the canonical JSON of the batch without the digest, as ICDEV[OS] recomputes it.

Publishing is a person's declared step (`decl-wft-cop-academy-xapi-publish`): anyone, or an agent as a draft, prepares
the batch (recorded whole at its digest, deciding nothing); a person who did not prepare it publishes it or refuses,
with a reason, bound to that digest. The batch schema admits no extra field, so the publication travels beside it
(`icdev-cop-academy-publication/v1`, `context/icos/cop-academy-publication.schema.json`, PROPOSED for ICDEV[OS] to pin).
`tools/idp/cop_xapi_export.py --published --out <dir>` writes both; `--verify <dir> --ledger` checks both schemas, the
digest, the separation and the ledger; the page's "Verify the published export" does the same.

## The five rules

Page `/idp/cop-tracks`; declared template `args/workflow_hitl_templates/cop-academy-xapi-publish.yaml`; the Developer
Portal twin observes `cop.academy` (nothing published is unverified; statements a published batch lacks, a batch
waiting on a person, too old for ICDEV[OS] or off its contract, and one published by its preparer are findings, each
threaded to `cop_academy:<event id>`); platform model `args/platform_model/cop.yaml` PM-COP-01..05 traced to
`tests/test_icx_cop_01_*.py`; MCP `cop_academy` (read, never an answer) and `cop_academy_xapi_prepare` (draft only).
E2E: `tests/e2e/cop_tracks.spec.ts` (cop01a-learner, cop01a-preparer, cop01a-publisher; both themes, axe clean).
