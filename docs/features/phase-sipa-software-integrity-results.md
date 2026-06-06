# SIPA — Software Integrity & Provenance Assessor: Verified V&V Results

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Module | SIPA (`tools/integrity/`) |
| Surface | `/integrity` canvas + `/api/integrity/*` |
| Status | V&V verified |
| Date | 2026-06-05 |
| Task | sipa-vv-04-d5-d3 |
| Author | ICDEV™ Architect Agent |

---

## 1. Purpose

This document records the **specific, verified behaviors** SIPA produces for the two
end-of-pipeline dispositions the V&V suite exercises:

- **ALLOW** — a disclosed, low-risk artifact is cleared for use.
- **QUARANTINE** — an artifact whose exercised capabilities exceed its disclosed
  purpose is staged, scanned, and withheld (HITL-gated, never executed).

Every finding below is asserted by a passing test. SIPA is **static-only** (AST +
signature scan + intent reconciliation); fixtures are staged into quarantine and
assessed **without ever being run**.

The disposition boundary is score-driven (`tools/integrity/scoring.py`):

| Risk score | Verdict |
|------------|---------|
| `score < 40` (`review_at`) | **ALLOW** |
| `40 <= score < 70` | REVIEW |
| `score >= 70` (`quarantine_at`) | **QUARANTINE** |

A forced-quarantine override fires regardless of score when an undisclosed
`process_exec` (or other forced-reason) capability is reconciled.

---

## 2. Verified ALLOW behavior

**Fixture (benign):** the `tinylog` package — a `README.md` that discloses
"reads and writes log files on disk … no network, no subprocesses" plus a
`tinylog.py` that only touches the filesystem via `pathlib`.

**Driver:** `engine.assess(...)` in Mode B (`provenance_blind`), auto-resolved
because no provenance handle is supplied.

**Verified findings** (`tests/test_integrity_engine.py::test_assess_benign_is_allow`):

| Property | Verified value |
|----------|----------------|
| `verdict` | `allow` |
| `risk_score` | `< 40` (under `review_at`) |
| `mode` | `provenance_blind` |
| `status` | `assessed` |
| `capabilities_count` | `>= 1` — the **disclosed filesystem capability** |
| `undisclosed_capability` findings | **0** (disclosed purpose matches exercised behavior) |

**Persistence (append-only):**

- `integrity_assessments` row transitions to `status='assessed'` with
  `mode='provenance_blind'`, `verdict='allow'`.
- An `integrity_verdicts` row is written with `verdict='allow'`,
  `decided_by='engine'` (the verdict log is append-only — re-assessing the same
  source appends a new row, never rewrites the prior disposition).

**Gate / CLI behavior** (`test_main_benign_gate_exits_zero`,
`test_gate_exit_code_default_policy`):

- `engine.gate_exit_code("allow") == GATE_OK (0)`.
- `engine.main([... , "--gate"])` exits **0** — an ALLOW verdict never blocks the
  pipeline.
- `--json` summary carries `verdict="allow"`, `mode="provenance_blind"`, plus
  `risk_score` and `assessment_id`.

---

## 3. Verified QUARANTINE behavior

**Fixture (planted backdoor):** the committed
`tests/e2e/fixtures/integrity_backdoor_pkg/` — a `README.md` that *claims*
"pure formatting, no network, no subprocess" while `formatter.py` hides three
**undisclosed** capabilities behind benign-looking helpers:

- `network_egress` — outbound socket to a hardcoded RFC1918 host (`_beacon`).
- `obfuscation` — a base64-packed payload (`_sync`).
- `dynamic_code` — `exec(base64.b64decode(...))` of the decoded blob (`_sync`).

The host is non-routable and the port is **never opened** by the assessor.

**Driver:** the REAL SIPA pipeline (`engine.assess(..., mode="provenance_blind")`)
plus the REAL Flask blueprint test client — the executable counterpart of
`integrity_backdoor_quarantine.spec.ts`.

**Verified findings** (`tests/e2e/test_integrity_backdoor_quarantine.py`):

| Property | Verified value |
|----------|----------------|
| `verdict` | `quarantine` |
| `risk_score` | `>= 70` (saturates past `quarantine_at`; undisclosed caps dominate) |
| `status` | `assessed` (staged + scanned, **never executed**) |
| Required capabilities surfaced | `network_egress`, `dynamic_code`, `obfuscation` |
| "Undiscovered" flag | `>= 1` **`undisclosed_capability`** finding |
| "Known-bad"-grade flag | `>= 1` finding at **`critical`** severity |

**Detail view (V&V'd against live API + page):**

- `GET /api/integrity/assessment/<id>` → `200`, `verdict.verdict == "quarantine"`,
  and `capabilities[]` contains the full `{network_egress, dynamic_code,
  obfuscation}` set.
- `GET /integrity/<id>` (detail page) → `200` and the body carries the word
  `quarantine` plus every required capability token (raw or underscore-spaced).

**Engine-internal QUARANTINE drivers** (`test_assess_planted_backdoor_is_quarantine`):

- Capabilities persisted include the dangerous trio
  `{network_egress, process_exec, dynamic_code}`.
- Mode B reconciliation writes `undisclosed_capability` findings for everything
  (nothing disclosed); **`process_exec` is the forced-QUARANTINE driver**.
- The signature regex fallback (Semgrep absent) writes a `critical`
  `known_bad_signature` finding for the `decode_then_exec` / `reverse_shell`
  patterns. With Semgrep installed-but-quiet the literal signature may not fire,
  so it is asserted softly — the deterministic "known-bad"-grade coverage is the
  `critical` severity assertion above.
- The `integrity_verdicts` rationale carries `forced_quarantine == True`.

**Mode A (`provenance_aware`) variant**
(`test_assess_provenance_aware_emits_unauthorized_capability`): when an intake
requirement authorizes *only* network egress, the same backdoor's `process_exec`
is flagged `unauthorized_capability` (authorized `network_egress` is **not**),
and no Mode B `undisclosed_capability` findings are emitted.

**Gate / CLI behavior** (`test_main_backdoor_gate_exits_nonzero`,
`test_main_json_gate_block_includes_gate_block`):

- `engine.gate_exit_code("quarantine") == GATE_BLOCK` (non-zero).
- `engine.main([... , "--gate"])` exits **non-zero** — QUARANTINE **blocks** the
  pipeline. Without `--gate` the run still exits 0 (the gate converts the verdict
  into a failing exit code only on demand).
- `--json --gate` summary carries `verdict="quarantine"`, `gate.blocked == True`,
  `gate.exit_code == GATE_BLOCK`.

---

## 4. ALLOW vs QUARANTINE — at a glance

| Dimension | ALLOW (benign `tinylog`) | QUARANTINE (backdoor `jsonpretty`) |
|-----------|--------------------------|-------------------------------------|
| Disclosed purpose vs exercised | match | gap (claims "formatting only") |
| Risk score | `< 40` | `>= 70` |
| Undisclosed capabilities | 0 | `network_egress`, `dynamic_code`, `obfuscation` (+ `process_exec`) |
| Critical / known-bad finding | none | `>= 1` critical (`known_bad_signature`) |
| Forced quarantine | no | yes (`forced_quarantine=True`, driven by `process_exec`) |
| Engine verdict row | `allow` / `decided_by=engine` | `quarantine` / `decided_by=engine` |
| `--gate` exit code | `GATE_OK` (0) | `GATE_BLOCK` (non-zero) |
| Artifact executed? | no | no (static-only; staged into quarantine) |

---

## 5. Backing tests

- `tests/test_integrity_engine.py` — benign⇒ALLOW, backdoor⇒QUARANTINE,
  Mode A unauthorized-capability, append-only verdicts, gate exit codes, CLI/JSON.
- `tests/e2e/test_integrity_backdoor_quarantine.py` — E2E QUARANTINE through the
  real pipeline + blueprint test client (detail API + detail page).
- `tests/e2e/integrity_backdoor_quarantine.spec.ts` — the same E2E flow through a
  live browser/HTTP client when a dashboard with the integrity canvas is up.
- Fixture: `tests/e2e/fixtures/integrity_backdoor_pkg/` (`README.md` +
  `formatter.py`).

**CUI // SP-CTI**
