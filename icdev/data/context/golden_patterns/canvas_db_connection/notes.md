# Golden pattern — canvas `init_db` uses `get_canvas_connection()`

**Why this pattern exists:** CLAUDE.md carries this as an explicit guardrail
because it has caused repeated failures. It is a good golden-pattern candidate
for the same reason: the wrong version looks completely correct, and fails only
at runtime against PostgreSQL.

## The rule

`get_connection()` attaches a row-level-security predicate that references
`classification` and `tenant_id`. Canvas-specific tables (`aac_*`, `dsoc_*`,
`ccc_*`, and friends) do not have those columns — they are canvas-local, not
tenant-scoped platform tables.

So a canvas `db/init_db.py` that calls `get_connection()` raises
`UndefinedColumn` on **every query**, not just writes. The canvas appears
completely broken while every individual line looks idiomatic.

Use `get_canvas_connection()` in canvas init files. The canonical example in the
tree is `tools/ai_augmentation/db/init_db.py`.

## Common mistakes this pattern prevents

- Copying a platform module's `from tools.db.storage import get_connection`
  import into a canvas, which is the usual way this arrives.
- Adding `classification`/`tenant_id` columns to a canvas table to make the RLS
  predicate pass. That mislabels canvas-local data as tenant-scoped and is the
  wrong fix for the right symptom.
- Concluding the canvas needs its own database. It does not; it needs the
  connection that does not attach the predicate.

## Checking your work

If a canvas raises `UndefinedColumn: column "classification" does not exist`,
this is the cause — look at the connection helper before the schema.
