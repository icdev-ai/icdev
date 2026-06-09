# task-257b02c294-d3 — No-Op Closure Rationale

**Task:** Remove or quarantine `network_egress` capability from `tools/seed_demo_corpus.py` line 81-82 if unauthorized
**Date:** 2026-06-09
**Status:** NO-OP — task is conditional, precondition not met

## Precondition check

The task title and description both encode an **if-conditional**:
> "If RTM confirms no authorization, edit `tools/seed_demo_corpus.py`..."

The companion task **task-257b02c294-d2** (RTM verdict) returned:

- **Verdict: AUTHORIZED** (with constraints)
- **Evidence file:** `docs/research/rtm-verdict-network-egress-urllib.md`

Because the precondition (unauthorized) is FALSE, the conditional action
("comment out or delete the `_download` function lines") does NOT execute.

## Authorization chain (from d2 verdict)

1. **D-KARL-9** (adrs.md): "Uses `urllib.request` for embedding query (no external deps)."
2. **D-FS-TIER-3** (adrs.md): "Parent ICDEV™ HTTP client uses stdlib urllib (air-gap safe) with queued retry when parent unreachable."
3. **Feature specs:** `docs/features/phase-ddc-lineage.md` (§4.4 OpenMetadata, §4.5 DataHub), `docs/features/phase-sdc-attackpath.md` (§8 Caldera), `docs/features/internal-awareness-engine.md` (probe).
4. **Governance constraint:** `args/security_gates.yaml` B310 URL-scheme gate (does NOT ban `urllib`; constrains which schemes may flow through `urlopen`).

## Constraint compliance check on live code

File: `tools/document_intelligence/seed_demo_corpus.py` (the task description's
stale `tools/seed_demo_corpus.py` path resolves to this live file; the
functional content is identical).

| Constraint (from d2 verdict) | Live code | Status |
|---|---|---|
| `urllib.request.urlopen` used | Line 82 | ✅ Compliant |
| B310 scheme allowlist (http/https) | URLs are IETF RFCs (https://www.rfc-editor.org/...) | ✅ Compliant |
| `# nosec B310` (Bandit) or `# noqa: S310` (ruff) justification | Line 82 has `# noqa: S310 (trusted IETF host)` | ✅ Compliant |
| Time-bounded | `timeout=60` on `urlopen` | ✅ Compliant |
| Retry-aware | Caller wraps in try/except (line 137-138, 138-139+) | ✅ Compliant |
| SIPA capability recording | `network_egress` recorded in `context/capabilities/` (per d2 verdict) | ✅ Compliant |

## Action taken

**No code change required.** The conditional action in the task is a no-op
when authorization holds, and authorization holds. The function
`_download` at lines 80-85 of
`tools/document_intelligence/seed_demo_corpus.py` remains as-is, with its
existing `# noqa: S310 (trusted IETF host)` justification aligned to the
RTM verdict's conditions.

## Files referenced

- `docs/research/rtm-verdict-network-egress-urllib.md` (d2 verdict)
- `tools/document_intelligence/seed_demo_corpus.py` (live code, lines 80-85, 138)
- `args/security_gates.yaml` (B310 URL-scheme gate)
- `docs/reference/adrs.md` (D-KARL-9, D-FS-TIER-3)
