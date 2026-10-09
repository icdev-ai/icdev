# Golden pattern — SQL placeholders are `%s`, never `?`

**Why this pattern exists:** the coherence gate currently reports **49**
`execute()` calls using a bare `?` placeholder. This is not hypothetical drift;
it is the single largest open violation class on the board, which is exactly
what makes it worth a reference implementation.

## The rule

ICDEV runs PostgreSQL as its primary backend. `psycopg2` uses `%s` for every
parameter, of every type. SQLite's `?` is not accepted and raises
`ProgrammingError` at runtime — not at import, not in review, but the first time
that specific line executes.

That failure mode is what makes it expensive: a `?` on a rarely-taken branch can
sit in the tree for months and then fail in production on the one path nobody
exercised. Three audit trails were found empty for exactly this reason.

## Common mistakes this pattern prevents

- Using `?` because the surrounding file predates the PostgreSQL migration.
- Assuming `translate_sql` will rewrite it. It will not: it is an init/seed
  fallback, never load-bearing at runtime.
- Using `%s` for strings but `%d` for integers. There is only ever `%s` — the
  driver handles typing.
- Writing raw `sqlite3.connect()` to sidestep the issue. That bypasses the
  storage layer, its RLS predicate and its tenant scoping, and the pre-tool hook
  blocks it under `tools/`.

## Checking your work

```bash
python tools/quality/sensor.py --json | python -c "import json,sys; print(sum(1 for v in json.load(sys.stdin)['violations'] if v['rule']=='canvas_placeholder_style'))"
```
