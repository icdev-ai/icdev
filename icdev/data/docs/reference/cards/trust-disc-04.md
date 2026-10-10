# Substrate probe — does the thing you are about to design against HAVE ROWS? (trust-disc-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/capability_consumption.py --probe-plan <plan.md> --substrate-gate  # BEFORE writing code
python tools/awareness/capability_consumption.py --probe-substrate kg_ontology            # one table
python tools/awareness/capability_consumption.py --probe-substrate kg_nodes.ontology_id   # one column
python tools/awareness/capability_consumption.py --probe-diff origin/main --json          # what the branch reads
python tools/awareness/capability_consumption.py --substrates                             # the curated declared list
```

empty (writer never ran) / absent (migration never ran) / column_unpopulated (rows exist,
column 100% NULL) are never merged — they send you to different fixes. On a database with
no operating history everything reports UNMEASURABLE and the gate exits 0.
Gate consumer: coherence_checker.py --check substrate_liveness (warn).
