# Bandit baseline — full repository (task `aca-hyg-06-d4-d1`)

**CUI // SP-CTI**

Read-only scan. No source file was modified to produce this baseline. Machine-readable
counterpart: [`aca-hyg-06-d4-bandit-baseline.json`](aca-hyg-06-d4-bandit-baseline.json).

- Scanned from worktree `.tmp/worktrees/aca-hyg-06-d4-d1`, branch `kanban/aca-hyg-06-d4-d1`,
  commit `1453f378b`, which is at `origin/main` (`git log origin/main..HEAD` → empty). This
  baseline therefore **is** the `main` baseline.
- bandit 1.9.3 / CPython 3.14.0.
- **Scope difference from the earlier `aca-hyg-06-d1` baseline.** That one scanned `tools/`
  and `apps/forge_academy/` separately. This one is the whole repository (`-r .`), so the
  two are not directly comparable — read them as complements, not as a before/after.

> **Public-repo redaction.** This file reports **counts by test id and by scope only**.
> Exact file paths and line numbers are deliberately omitted: `icdev-ai/icdev` is a public
> repository, and enumerating unremediated findings publishes a vulnerability map. Full
> detail is regenerable in 3 minutes from any checkout using the commands at the bottom;
> keep the raw JSON in internal tracking, not in the repo.

## Verdict

**394 MEDIUM-or-above findings repo-wide under repo policy — 374 MEDIUM, 20 HIGH.**
Not zero, so the exact counts below are the comparison baseline.

**Zero HIGH-severity findings in platform runtime** (`tools/`, `icdev/tools/`). All 20 HIGH
sit outside runtime code, in three buckets described below. The CI SAST gate passes.

## Scan 1 — repo policy, MEDIUM and above (authoritative)

```bash
python -m bandit -r . -c bandit.yaml --severity-level medium -f json -o <out>.json -q
```

`bandit.yaml` skips B110, B404, B603, B607, B113, B608 and excludes `.tmp`, `node_modules`,
`.git`, `__pycache__`, `projects`, `playwright`. This is the configuration the platform
actually holds itself to, so it is the number to diff against.

**394 findings.** Exit code 1 (bandit exits non-zero whenever any finding is reported;
that is reporting, not scan failure).

| Severity | Count |
|---|---|
| HIGH | 20 |
| MEDIUM | 374 |

| Test | Count | Meaning |
|---|---|---|
| `B310` | 280 | `urllib.urlopen` — audit URL scheme (SSRF/`file://` class) |
| `B108` | 60 | Hardcoded temp directory (`/tmp/...`) |
| `B314` | 21 | `xml.etree` parsing (XXE class) |
| `B104` | 8 | Binding to all interfaces (`0.0.0.0`) |
| `B701` | 8 | Jinja2 `autoescape=False` |
| `B411` | 6 | `xmlrpc` import (decompression-bomb / XXE class) |
| `B501` | 5 | `requests` call with TLS verification disabled |
| `B704` | 2 | Markup / `\|safe` XSS surface |
| `B102` | 2 | `exec` used |
| `B615` | 1 | Hugging Face download without revision pin |
| `B324` | 1 | Insecure hash function |

### By scope

| Scope | MEDIUM+ | of which HIGH |
|---|---|---|
| `tests/` | 129 | 9 |
| `icdev/tools/` (mirror) | 123 | 0 |
| `tools/` | 119 | 0 |
| `data/` (generated artifacts) | 9 | 8 |
| `apps/` | 5 | 0 |
| `src/` | 5 | 0 |
| `docs/` | 3 | 3 |
| other | 1 | 0 |

`tools/` and `icdev/tools/` track each other closely (119 vs 123) as expected for a mirror
pair; the 4-finding delta is mirror drift worth a separate look, not a security finding.

### The 20 HIGH, by bucket

None are in platform runtime. Characterised without locations:

1. **8 in generated demo artifacts under `data/`** — 5 × `B501` (TLS verification disabled
   in outbound calls) and 3 × `B411`. These are LLM-generated sample artifacts committed as
   demo data for a canvas project, not code ICDEV executes. The `B501` pattern is still bad
   to ship as an exemplar, since generated artifacts get copied by users. **Recommend
   remediation** — highest-value item on this list.
2. **3 in the WordPress site-publishing scripts under `docs/`** (plus 3 MEDIUM mirrored
   copies under `icdev/data/`) — `B411`, raised at the `xmlrpc.client` *import*, which is
   how the WordPress XML-RPC API is reached. Advisory about a stdlib module's hardening
   posture against hostile servers, not an injectable surface in ICDEV. Low priority.
3. **9 in `tests/`** — 8 × `B701` (Jinja2 `autoescape=False`) and 1 × `B324` (insecure
   hash). The `B701` sites are XSS *regression* tests: disabling autoescape is the point of
   the test, which asserts the platform escapes on its own. The `B324` site is a WebSocket
   handshake test, where the algorithm is fixed by RFC 6455 and is not used as a security
   primitive. All 9 are correct-as-written; they want `# nosec` annotations with
   justifications, not code changes.

### Standing MEDIUM backlog

The 280 `B310` findings dominate and are split roughly evenly across `tools/` and its
`icdev/` mirror — the same underlying call sites counted twice. Concentrated in document
extractors, E2E canvas drivers, migration helpers, and third-party SaaS connectors, i.e.
places that fetch a URL assembled from config. Worth a scheme-allowlist helper rather than
280 individual `# nosec` comments.

The 60 `B108` hardcoded-`/tmp` findings are entirely in `tests/`, and are a portability
defect on Windows before they are a security one — a bash `/tmp` redirect and Python's
`open('/tmp/...')` resolve to different files on this platform. `tempfile.gettempdir()` is
the fix, per the cross-platform rule in `CLAUDE.md`.

## Scan 2 — CI SAST gate (the gate that actually blocks)

```bash
bandit -r tools/ -q --severity-level high --confidence-level high
```

Source: `.github/workflows/icdev-ci.yml`. **0 findings, exit code 0 — PASS.**

## Scan 3 — literal task command, no config

```bash
python -m bandit -r . -f json -o bandit_baseline.json -q
```

Recorded for traceability with the task as written. Without `-c bandit.yaml` the repo's
skips and `exclude_dirs` do not apply, so this scan also walks `.tmp/`, `projects/`,
`playwright/` and re-raises every suppressed test:

**69,931 findings — 67,028 LOW, 2,883 MEDIUM, 20 HIGH (2,903 MEDIUM+).**

The MEDIUM+ delta against Scan 1 (2,903 vs 394) is almost entirely `B608` (2,497) and
`B310` (282 vs 280) re-raised from scratch and vendored directories. The HIGH count is
identical at 20, which is the useful cross-check: the configuration suppresses noise, not
severity.

The raw output is **62 MB** and is intentionally *not* committed — it is regenerable, and
committing a full finding list to a public repo is exactly what the redaction note above
guards against.

## Reproducing

Run from a clean worktree at `origin/main`. Write scratch output to an absolute Windows
path — a bash `> /tmp/…` redirect does not land where Python's `open('/tmp/…')` reads.

```powershell
$ws  = "<worktree>"
$out = "<scratch-outside-the-worktree>"
python -m bandit -r "$ws" -c "$ws\bandit.yaml" --severity-level medium -f json -o "$out\policy-medium.json" -q
python -m bandit -r "$ws\tools" -q --severity-level high --confidence-level high
python -m bandit -r "$ws" -f json -o "$out\raw-all.json" -q
```

The first and third exit `1` when findings exist; the second exits `0` and is the gate.
