# Bandit baseline — `kanban/aca-hyg-06` (task `aca-hyg-06-d1`)

**CUI // SP-CTI**

Read-only scan. No source file was modified to produce this baseline. Machine-readable
counterpart: [`aca-hyg-06-bandit-baseline.json`](aca-hyg-06-bandit-baseline.json).

- Scanned from worktree `.tmp/worktrees/aca-hyg-06-d1`, branch `kanban/aca-hyg-06-d1`,
  which is at `origin/main` (`git log origin/main..HEAD` → empty). The baseline therefore
  **is** the `main` baseline.
- Raw full-detail JSON is intentionally *not* committed (2.8 MB of B608 noise). It is
  regenerable with the exact commands below.

## Verdict — the 3 reported issues are pre-existing, not introduced

`kanban/aca-hyg-06` carries a single commit, `b5ae433cc` *"test(aadc): third state leak —
a fixture that replaces get_connection outlives itself"*, and it touches **five test files
and nothing else**:

```
tests/_aadc_canvas.py               | 67 +++++++++++++
tests/test_aadc_model_layer.py      | 10 +++++
tests/test_penta_aadc_compliance.py | 84 ++++++++++-----
tests/test_penta_aadc_p2.py         | 14 ++---
tests/test_penta_aadc_routes.py     |  6 +--
```

Bandit over exactly those five files reports **66 findings, all `LOW`/`B101`** (`assert`
in pytest — expected and unavoidable in a test file) and **zero `MEDIUM` or `HIGH`**.

The branch adds no `tools/` and no `apps/` code, so every `MEDIUM`+ finding below exists
identically on `origin/main`. Any bandit issue attributed to this branch is inherited,
not introduced.

## Scan 1 — CI gate scan: `tools/`, medium severity and above

```bash
python -m bandit -r tools/ --severity-level medium -f json -o <out>.json
```

**1367 findings. 0 HIGH severity.** Exit code 1 (bandit exits non-zero whenever any
finding is reported; this is not a new regression).

| Severity | Count |
|---|---|
| HIGH | 0 |
| MEDIUM | 1367 |

| Confidence | Count |
|---|---|
| HIGH | 119 |
| MEDIUM | 1109 |
| LOW | 139 |

| Test | Count | Meaning |
|---|---|---|
| `B608` | 1248 | Possible SQL injection via string-built query |
| `B310` | 113 | `urllib.urlopen` — audit URL scheme |
| `B314` | 5 | `xml.etree` parsing (XXE class) |
| `B704` | 1 | Markup/`\|safe` XSS surface |

Highest-density files (all pre-existing, none touched by this branch):

| Count | File |
|---|---|
| 56 | `tools/network/routes/pages.py` |
| 53 | `tools/network/routes/crud.py` |
| 48 | `tools/network/routes/topology_ops.py` |
| 44 | `tools/network/routes/import_io.py` |
| 41 | `tools/network/routes/topology.py` |
| 38 | `tools/network/routes/misc.py` |
| 36 | `tools/workflow_canvas/blueprint.py` |
| 33 | `tools/network/routes/peering_inventory.py` |
| 32 | `tools/network/routes/twin_migration.py` |
| 30 | `tools/network/routes/analytics.py` |

The B608 mass is dominated by the FORGE-idiomatic pattern of interpolating
module-constant table/column names into otherwise parameterised SQL. Many such sites
already carry `# nosec B608` with a justification; the remainder are unannotated. That is
a standing hygiene backlog, out of scope for `aca-hyg-06`.

## Scan 2 — FORGE Academy: `apps/forge_academy/`, all severities

The academy lives at `apps/forge_academy/`, **not** `tools/academy/` — the documented CI
command (`bandit -r tools/`) never scans it. Scanned separately:

```bash
python -m bandit -r apps/forge_academy -f json -o <out>.json
```

**1620 findings: 1615 LOW, 5 MEDIUM, 0 HIGH.**

The 1615 LOW are almost entirely `B101` (`assert`) and `B311` (`random`) inside
`apps/forge_academy/content/tier*/…/step*_test.py` and `step*_starter.py` — i.e. inside
*course material*, which is graded learner code, not platform runtime.

All 5 MEDIUM findings, in full:

| Test | Sev/Conf | Location | Issue |
|---|---|---|---|
| `B608` | MEDIUM/MEDIUM | `apps/forge_academy/content_loader.py:1642` | Possible SQL injection vector through string-based query construction |
| `B608` | MEDIUM/MEDIUM | `apps/forge_academy/content_loader.py:2284` | Possible SQL injection vector through string-based query construction |
| `B608` | MEDIUM/MEDIUM | `apps/forge_academy/db.py:1086` | Possible SQL injection vector through string-based query construction |
| `B608` | MEDIUM/MEDIUM | `apps/forge_academy/db.py:1094` | Possible SQL injection vector through string-based query construction |
| `B608` | MEDIUM/MEDIUM | `apps/forge_academy/oracle/db.py:106` | Possible SQL injection vector through string-based query construction |

Two runtime notes worth carrying forward (not fixed here — read-only task):

- `apps/forge_academy/code_runner.py` is the only academy module with a subprocess
  surface (`B404` at line 31, `B603` at line 249). Both are LOW severity and expected for
  a sandboxed grader. `code_runner.py` executes learner-supplied Python and is already
  recorded in [`docs/security/sandbox-coverage.md`](sandbox-coverage.md), so no new
  sandbox decision is owed.
- `apps/forge_academy/db.py` was re-authored for PostgreSQL by `aca-hyg-05`
  (`ea9d25511`); line numbers above are against `origin/main` as of this scan.

## Scan 3 — files changed by `kanban/aca-hyg-06`

```bash
python -m bandit tests/_aadc_canvas.py tests/test_aadc_model_layer.py \
  tests/test_penta_aadc_compliance.py tests/test_penta_aadc_p2.py \
  tests/test_penta_aadc_routes.py -f json -o <out>.json
```

**66 findings: 66 LOW (`B101`), 0 MEDIUM, 0 HIGH.**

## Reproducing

Run from a clean worktree at `origin/main`. Write scratch output to an absolute Windows
path — a bash `> /tmp/…` redirect does not land where Python's `open('/tmp/…')` reads.

```powershell
$ws = "<worktree>"
python -m bandit -r "$ws\tools" --severity-level medium -f json -o "$ws\.tmp\bandit\baseline-medium.json" -q
python -m bandit -r "$ws\apps\forge_academy" -f json -o "$ws\.tmp\bandit\academy-all.json" -q
```

Both exit `1` when findings exist; that is bandit reporting, not a scan failure.
