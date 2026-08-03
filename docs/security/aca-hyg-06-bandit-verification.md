# Bandit verification — `aca-hyg-06` chain complete (task `aca-hyg-06-d4-d5`)

**CUI // SP-CTI**

Read-only scan. No source file was modified to produce this verification. Machine-readable
counterpart: [`aca-hyg-06-bandit-verification.json`](aca-hyg-06-bandit-verification.json).
Baseline it is measured against:
[`aca-hyg-06-bandit-baseline.md`](aca-hyg-06-bandit-baseline.md) (task `aca-hyg-06-d1`,
commit `15ef81bac`, 2026-07-31).

## Verdict — the chain introduced zero MEDIUM+ findings

**Scoped to what `aca-hyg-06` actually changed: PASS.** Bandit over the union of every file
the chain's five commits touched reports **606 findings, all LOW, and zero MEDIUM or HIGH**:

| Severity | Count |
|---|---|
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 606 (`B101` 602, `B105` 3, `B106` 1) |

Both findings the chain set out to remove are gone:

- `B314` in `tests/test_ecr_sso.py` — fixed by `2d88f8582` (`aca-hyg-06-d2`), now parses via
  `defusedxml`. No `B314` remains in that file.
- `B108` in `tests/test_proposal_genesis.py` — fixed by `b257547e1` (`aca-hyg-06-d3`). No
  `B108` remains in that file.

Chain commits and the files each touched:

| Commit | Task | Files |
|---|---|---|
| `b5ae433cc` | `aca-hyg-06` | 5 aadc test files |
| `2d88f8582` | `-d2` | `tests/test_ecr_sso.py`, `tools/auth/saml.py`, `icdev/tools/auth/saml.py`, sandbox-coverage doc |
| `b257547e1` | `-d3` | `tests/test_proposal_genesis.py` |
| `23e4ec809` | `-d4-d2` | `tests/test_penta_aadc_compliance.py` |
| `52aea5ed0` | `-d4-d3` | `tests/test_penta_aadc_compliance.py` |

Scanned from a clean detached worktree at `bfc2f3dfd` (`origin/main`). Branch
`kanban/aca-hyg-06-d4-d5` carries **zero commits ahead of `origin/main`**
(`git log origin/main..HEAD` → empty), so the branch adds no code of its own.

## Whole-repo drift since the baseline — +16 MEDIUM, none from this chain

The repo-wide counts are **not** at or below baseline. Every added finding is attributable
to unrelated work merged into `main` after `15ef81bac`, and **zero are HIGH severity**.

### Scan 1 — CI gate scan: `tools/`, medium and above

```bash
python -m bandit -r tools/ --severity-level medium -f json -o <out>.json
```

**1377 findings (baseline 1367, delta +10). 0 HIGH.**

| Test | Baseline | Current | Delta |
|---|---|---|---|
| `B608` | 1248 | 1249 | +1 |
| `B310` | 113 | 114 | +1 |
| `B314` | 5 | 13 | **+8** |
| `B704` | 1 | 1 | 0 |
| **Total** | **1367** | **1377** | **+10** |

| Confidence | Baseline | Current |
|---|---|---|
| HIGH | 119 | 128 |
| MEDIUM | 1109 | 1110 |
| LOW | 139 | 139 |

The `B314` jump is one module, not a regression in reviewed code: `tools/bom/` did not exist
at baseline and arrived carrying 9 stdlib `xml.etree` parse sites.

| Count | File | Status |
|---|---|---|
| 7 | `tools/bom/forensics.py` | new module, remediation in flight |
| 2 | `tools/bom/extract_grid.py` | new module, remediation in flight |
| 1 each | `tools/geoint/geoint_ingestor.py:128`, `tools/network/diagram_analysis.py:135`, `tools/osint/osint_ingestor.py:211`, `tools/testing/flaky_tracker.py:95` | pre-existing |

Baseline held 5 `B314` in `tools/`; 4 of those remain and 1 was fixed elsewhere, so the
arithmetic is 5 − 1 + 9 = 13.

**The `tools/bom/` findings are already being fixed.** A concurrent session holds
uncommitted `defusedxml` conversions for both files. Scanning that working tree instead of
committed `HEAD` gives **0 medium+ for `tools/bom/`, down from 9** — verified, not assumed.
No new card is owed for it.

Highest-density files are unchanged from baseline (`tools/network/routes/pages.py` 56,
`crud.py` 53, `topology_ops.py` 48, …) — the standing `B608` hygiene backlog, still out of
scope for `aca-hyg-06`.

### Scan 2 — FORGE Academy: `apps/forge_academy/`, all severities

```bash
python -m bandit -r apps/forge_academy -f json -o <out>.json
```

**1627 findings: 1616 LOW, 11 MEDIUM, 0 HIGH** (baseline 1620: 1615 LOW, 5 MEDIUM).

The 5 baseline MEDIUM all persist (line numbers shifted); **6 new MEDIUM `B608` arrived in
`apps/forge_academy/instructor.py`**, added 2026-08-01 by `13cbed699`
*"feat(academy): instructor and cohort workflow (aca-trn-04)"* — a sibling epic, not this
chain.

| Test | Sev/Conf | Location | Origin |
|---|---|---|---|
| `B608` | MEDIUM/MEDIUM | `content_loader.py:1642` | baseline |
| `B608` | MEDIUM/MEDIUM | `content_loader.py:2295` | baseline (was :2284) |
| `B608` | MEDIUM/MEDIUM | `db.py:1212` | baseline (was :1086) |
| `B608` | MEDIUM/MEDIUM | `db.py:1220` | baseline (was :1094) |
| `B608` | MEDIUM/MEDIUM | `oracle/db.py:106` | baseline |
| `B608` | MEDIUM/LOW | `instructor.py:141` | new (`aca-trn-04`) |
| `B608` | MEDIUM/MEDIUM | `instructor.py:220` | new (`aca-trn-04`) |
| `B608` | MEDIUM/LOW | `instructor.py:288` | new (`aca-trn-04`) |
| `B608` | MEDIUM/MEDIUM | `instructor.py:419` | new (`aca-trn-04`) |
| `B608` | MEDIUM/LOW | `instructor.py:445` | new (`aca-trn-04`) |
| `B608` | MEDIUM/LOW | `instructor.py:712` | new (`aca-trn-04`) |

**All 6 are false positives, checked at the source rather than waved through.** Each is a
concatenation of `_tenant_clause()` (`instructor.py:79`), which interpolates only its
`alias` argument and binds the tenant value as a parameter:

```python
if tenant_id:
    params.append(tenant_id)
    return f"{alias}.tenant_id=%s"
return f"({alias}.tenant_id IS NULL OR {alias}.tenant_id='')"
```

Every call site passes a hardcoded literal alias (`"u"`, `"a"`) — grep for
`_tenant_clause(` matches only `_tenant_clause("…`, with no non-literal caller. No
user-controlled value reaches the SQL string. This is the FORGE-idiomatic
constant-interpolation pattern the baseline already documents; the fix owed is a
`# nosec B608` + `# noqa: S608` annotation, not a query rewrite.

### Scan 3 — the 5 test files changed by `kanban/aca-hyg-06`

```bash
python -m bandit tests/_aadc_canvas.py tests/test_aadc_model_layer.py \
  tests/test_penta_aadc_compliance.py tests/test_penta_aadc_p2.py \
  tests/test_penta_aadc_routes.py -f json -o <out>.json
```

**69 findings: 69 LOW (`B101`), 0 MEDIUM, 0 HIGH** (baseline 66 LOW). The +3 are `assert`
statements added by the `-d4-d3` fixture fix — expected and unavoidable in a test file.

## Follow-up owed (neither blocks this task)

1. `apps/forge_academy/instructor.py` — annotate the 6 `B608` sites. Belongs to the
   `aca-trn` epic that introduced them.
2. `tools/bom/{forensics,extract_grid}.py` — 9 `B314`. Fix is already written and in flight
   in another session; land it. No card needed.

## Reproducing

Run from a clean worktree at `origin/main`. Write scratch output to an absolute Windows path
— a bash `> /tmp/…` redirect does not land where Python's `open('/tmp/…')` reads.

```powershell
$ws = "<worktree>"
python -m bandit -r "$ws\tools" --severity-level medium -f json -o "<scratch>\tools-medium.json" -q
python -m bandit -r "$ws\apps\forge_academy" -f json -o "<scratch>\academy-all.json" -q
```

Both exit `1` when findings exist; that is bandit reporting, not a scan failure. Scans here
used bandit 1.9.3 on Python 3.14.0, without `-c bandit.yaml`, matching the baseline's
invocation so the two are directly comparable.
