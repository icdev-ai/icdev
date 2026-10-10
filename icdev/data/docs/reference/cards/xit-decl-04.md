# Every table has ONE owner: core | it | ft (xit-decl-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/db/schema_ownership.py --check                   # CI; exit 1 on an unowned/duplicate/foreign/stale table
python tools/db/schema_ownership.py --regenerate              # after editing args/schema_ownership_rules.yaml
python tools/db/schema_ownership.py --table kanban_tasks      # owner + where it is declared
python tools/workflow/coherence_checker.py --check schema_ownership --gate
```

2,096 distinct tables are created under tools/ (init_icdev_db.py's 527 in ONE
string, fourteen canvas init_db modules, kanban, trading, 430 migrations) and
nothing said which belonged to the kernel both ICDEV parents install, to
ICDEV[IT], or to the trading domain leaving for the private ICDEV[FT] repo.
The manifests (icdev/core/schema/tables.yaml = core, tools/db/schema/
tables.yaml = it + ft) are GENERATED from args/schema_ownership_rules.yaml --
change a rule, never a manifest line; the check fails on a stale manifest.
args/schema_ownership_gate.yaml says which owners a migration HERE may touch;
the removal PR drops ft, and then an ad_* table cannot come back. `rls_exempt`
(in the rules, flowing into the manifests) is the generalisation of
get_canvas_connection and is EMPTY at adoption. icdev/core/sensitivity.py is
the ONE sensitivity seam (labels low->high, default, egress_restricted,
dominates(), rls_exempt_tables()) read from icdev_domain.yaml; the four
hard-coded classification ladders in the tree are rewired onto it by
xit-core-*, one PR each, asserted behaviour-identical for IT.
