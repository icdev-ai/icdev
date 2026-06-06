# CUI // SP-CTI

# eqo-sipa-s1 — SIPA malware-signature scanner silently disabled by gitignored quarantine tree

**Type:** fix (critical) · **Source:** eqo-vv-01 V&V defect S1

## Symptom

`tools/integrity/scanners.py::run_signature_scan` persisted **0 findings** on real
assessments even when a planted reverse shell / `exec(base64.b64decode(...))`
dropper sat in the quarantined tree.

## Root cause

1. The quarantine staging root is `<repo>/.tmp/integrity_quarantine` — and `.tmp/`
   is **gitignored** (`.gitignore:19`).
2. `_detect_signatures` calls the shared `run_semgrep(staged_dir, rules_dir)` with a
   **directory** target.
3. Semgrep honors `.gitignore` by default → it walks the gitignored staged tree,
   finds **zero files**, and returns `[]` (an empty list, **not** `None`).
4. `run_signature_scan` only falls through to the deterministic regex fallback when
   `_detect_signatures` returns `None`. An empty list is treated as "scanned clean,"
   so the regex fallback **never ran** and the malware was silently masked.

Unit tests missed it because they monkeypatch `_detect_signatures`, bypassing the
real Semgrep + `.gitignore` interaction.

**Repro (pre-fix):** plant `exec(base64.b64decode(...))` under
`.tmp/integrity_quarantine/<id>/`; `run_semgrep` → 0 hits, regex fallback → 1 hit,
`run_signature_scan` persists 0.

## Fix

- `tools/aiify/pattern_classifier.py::run_semgrep` — new keyword `no_git_ignore: bool
  = False`; when set, `--no-git-ignore` is added to the Semgrep CLI so it scans files
  matching a `.gitignore` rule. Backward compatible (default off) for all other
  callers.
- `tools/integrity/scanners.py::_detect_signatures` — passes `no_git_ignore=True`.
  SIPA always scans a quarantined tree under a gitignored path, so this is mandatory.

**Verified (post-fix):** the same dropper under the gitignored tree → Semgrep now
returns 1 hit (`decode_then_exec`), and `run_signature_scan` persists a
`known_bad_signature` finding (critical).

## Test (non-monkeypatched regression)

`tests/test_integrity_scanners.py::test_signature_scan_detects_payload_in_gitignored_tree`
plants a real dropper under the repo's gitignored `.tmp/` tree and runs the **full**
signature scan **without** monkeypatching `_detect_signatures`, so the
Semgrep + `.gitignore` path is exercised end to end. It asserts the staged tree is
actually gitignored (premise check via `git check-ignore`, fails loudly otherwise),
then asserts `engine == "semgrep"` and `findings_persisted >= 1`. Skipped when the
Semgrep binary is absent (the regex fallback walks the tree directly and never
honored `.gitignore`, so it cannot reproduce the bug). This test fails against the
pre-fix code and passes with `--no-git-ignore`.

## Scope

Two files changed + one regression test. No DB, route, or config changes. All 35
signature/scanner tests pass; `ruff` clean.
