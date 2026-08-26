# qa-fail-7afb236486986e8a — the ACE audit endpoint is on `main`; the deployment is 18 commits behind it

**Card:** `[QA] ACE Co-Worker Engine Lifecycle > GET /api/ace/<id>/audit returns events array`
**Run:** `qa-1787705278` **Spec:** `tests/e2e/coworker_lifecycle.spec.ts`
**Reported:** `page.evaluate: SyntaxError: Unexpected token '<', "<!DOCTYPE "... is not valid JSON`
**Resolution:** no code change — the route, and a CI-gated regression guard for it,
have been on `main` since 747392d89 (#1910). What the sweep measured is a **frozen
deployment**, and the freeze is still standing. See *What is still broken* below.

## The `<!DOCTYPE` is an unregistered URL, not a bad response body

`fetch('/api/ace/<id>/audit?limit=50').json()` parsed HTML because the live server
answers that path with Flask's global 404 page. Measured against the running
dashboard (pid 27848, `tools/dashboard/app.py --port 5050`) before touching
anything — every sibling endpoint in the same blueprint, same process, same call:

| path | status | content-type |
|---|---|---|
| `/api/ace/instances` | 200 | `application/json` |
| `/api/ace/presets` | 200 | `application/json` |
| `/api/ace/xyz/status` | 404 | `application/json` |
| `/api/ace/xyz/messages` | 200 | `application/json` |
| `/api/ace/xyz/artifacts` | 200 | `application/json` |
| **`/api/ace/xyz/audit`** | **404** | **`text/html`, 113,169 bytes** |
| `/api/ace/xyz/hitl/pending` | 200 | `application/json` |

`/status` answers a JSON 404 because it is a **registered route** refusing an
unknown instance. `/audit` answers an HTML 404 because **no URL rule matches it
at all** — the request never reaches ACE code. `api_hitl_pending` is defined
directly *after* `api_audit` in the same source file and is registered, so this
is not a partially-loaded module: it is a different, older module.

## The route is on `main`, in both trees, with a guard

`tools/ace/blueprint.py:759` and `icdev/tools/ace/blueprint.py:759` both carry
`@ace_api_bp.route("/<instance_id>/audit")` and `def api_audit(...)` at
`origin/main` (384dda8ba). `tests/test_ace_instance_page_render.py` covers it
four ways, including `test_audit_route_exists_in_both_trees`, and the file is
CI-gated via `args/ci_test_files/core.d/qa-fail-5f7cf03a0b0a4351.txt`. Run on
this tree: **4 passed**.

The endpoint has been lost before, which is why that guard exists — 407111d59
added `api_audit` to `icdev/tools/ace/blueprint.py` only, and the next
`tools/` → `icdev/` mirror sync (3d16b47a3) deleted it, shipping an Activity Log
tab whose endpoint answered 404 text/html. **That is not what happened this
time.** The mirror is intact; the deployed tree is old.

## A restart is not a deployment

| event | when |
|---|---|
| `C:\AI\ICDev` last advanced (`git reflog main`) | 2026-08-24 14:56:32 -0400 |
| 747392d89 (#1910) restores `/audit` on `origin/main` | 2026-08-25 00:10:36 -0400 |
| dashboard pid 27848 **restarted** | 2026-08-25 09:44:58 -0400 |
| QA sweep `qa-1787705278` runs, files this card | 2026-08-26 |

The process was restarted **9h34m after the fix merged** and still served the old
code, because the tree it restarted *from* had not moved since ~9h *before* the
fix landed. `local main` is 415c01d06; `origin/main` is 384dda8ba; `git
merge-base --is-ancestor 747392d89 main` → **NO**; behind by **18 commits**.
Neither `tools/ace/blueprint.py` nor `icdev/tools/ace/blueprint.py` in the
deployed working tree contains the route (0 matches in each).

## What is still broken

The freeze is real, measured, and **not cleared by this card**:

```
$ python tools/genesis/deployment_freshness.py --root C:/AI/ICDev --json
{"state": "blocked", "behind_by": 18,
 "reason": "local changes would be lost",
 "conflicts": ["args/projects.yaml"], "ref": "origin/main"}
```

`args/projects.yaml` is the one entry in `AUTO_MANAGED_FILES`, and
`restore_auto_managed_file` (autonomy-dep-04) exists to clear exactly this
freeze. Asked, dry-run, it **correctly refuses**:

```
$ python tools/awareness/restore_acts.py --apply restore_auto_managed_file \
    --target args/projects.yaml --root C:/AI/ICDev --dry-run
refused: project 'ftl' field 'briefs' is not what the writer would produce
         — a human edited an auto-registered entry in the working tree
```

That refusal is the act behaving as designed: it may only revert a diff the
writer would regenerate, never a human edit. So the repair is **not** automatic
and **not** `git checkout -- args/projects.yaml` — the `ftl` briefs are somebody's
uncommitted work. **Commit the `ftl` edit, then pull.** Until then the deployment
stays 18 behind and every fix merged since 2026-08-24 14:56 is on `main` and not
in production, this one included.

Also uncommitted in that checkout: the nine MCP config files
(`.amazonq`, `.cline`, `.codex`, `.copilot`, `.cursor`, `.gemini`, `.goose`,
`.junie`, `.windsurf`) that `companion.py --sync` rewrites. Those are
regenerable and are not what the guard named.

## Finding not fixed here, deliberately

An unregistered path under `/api/` returns Flask's **HTML** 404, so a missing API
route reports itself to any caller as `SyntaxError: Unexpected token '<'` rather
than as a 404. That is what turned "one endpoint is absent" into a full QA card to
diagnose, and it is platform-wide, not ACE-specific. A JSON 404 handler scoped to
the `/api/` prefix would make the absence legible. It is out of scope for a card
titled after one route — it would not have made this test pass, only made the
failure honest — and it touches global error handling. Carded, not smuggled in.
