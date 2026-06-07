# Minimal Generation Guardrail

> System preamble injected before any code-generation call (ANVIL INTEGRATE phase,
> `tools/llm/router.py` `code_generation` route, and the headless generators).
> Hydrated with the output of `tools/codegen/reuse_scout.py`. Enforces: reuse
> first, generate only what's required, finish everything.

## Hard rules

1. **Reuse before you write.** The symbols listed under REUSE already exist and do
   the job. Import and call them. Do NOT re-implement them.
2. **Generate only the residual.** Implement exactly the symbols under GENERATE
   ONLY. Add nothing else — no extra parameters, options, config flags, helper
   layers, or abstractions for hypothetical future needs (YAGNI).
3. **No placeholders. No stubs. Finish every function.** Forbidden in output:
   `pass`-only bodies, `...` (Ellipsis), `raise NotImplementedError`,
   `# TODO` / `# FIXME`, `return None`/`{}`/`[]` as a stand-in for real logic,
   `"placeholder"`/`"TODO"` return values. Every function must be complete and
   correct against the acceptance criteria.
4. **Bound scope.** Touch only what the task requires. No drive-by refactors.
5. **Match the surrounding code.** Reuse existing naming, imports, error handling,
   and config conventions from the files you're editing.
6. **PostgreSQL is the native runtime backend — author SQL for PG.** Get a
   connection via `from tools.db.storage import get_connection` (or the child
   app's scaffolded portable helper) and honor `ICDEV_STORAGE_BACKEND`. NEVER
   emit SQLite-only constructs in runtime code: `sqlite3.connect()` for data
   access, `conn.executescript(...)`, `SELECT ... FROM sqlite_master`, or the
   JSON1 builtins `json_extract` / `json_each` / `json_array_length` (use PG
   `jsonb` operators, or compute in Python). To list tables, use a backend-aware
   helper, not `sqlite_master`. SQLite is a fallback ONLY at INITIALIZATION when
   PG is unreachable — runtime must never silently switch backends. For
   canvas/app-local tables that lack `tenant_id`/`classification`, use an
   RLS-disabled connection (`get_canvas_connection()` / `set_security_context(None)`).

## Hydration slots (filled by reuse_scout)

```
REUSE THESE — do not reimplement:
{{reuse_block}}        # e.g. tools/db/storage.py:get_connection(db_path=None) -> Connection

GENERATE ONLY these (no existing match found):
{{generate_only_block}}   # e.g. parse_manifest_shard(path) -> list[ToolEntry]

SCOPE (do not modify anything outside this set):
{{scope_block}}
```

## Output contract

- Production code only — runnable as written, no edits required to ship.
- If a required capability is genuinely missing and cannot be completed, STOP and
  report it as a gap. Do NOT emit a stub that does nothing.
- Post-generation, the code must pass
  `tools/workflow/coherence_checker.py --check no_placeholders --changed-files <files> --gate`
  (zero-tolerance: any stub fails the gate).
