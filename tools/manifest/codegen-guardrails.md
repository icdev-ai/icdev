# Codegen Guardrails (AI Code Optimization)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Keep AI-generated code lean: generate only what's required, reuse existing code,
and never ship placeholder/stub code. Pre-generation reuse discovery + a
zero-tolerance post-generation gate. See `hardprompts/karpathy_principles.md`
and `hardprompts/minimal_generation.md`.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Reuse Scout | tools/codegen/reuse_scout.py | Pre-generation reuse + minimal-scope brief. Queries the self-awareness KG (`kg_nodes`), the `tools/manifest/` shards, and `api_surface_extractor` to list existing symbols to REUSE and the residual symbols to GENERATE ONLY. Deterministic, air-gap safe (degrades to manifest grep). | --intent, --symbols, --spec, --limit, --json, --markdown | reuse/generate-only brief (JSON or prompt markdown) |

## Coherence checks (tools/workflow/coherence_checker.py)

| Check | Status | Description |
|-------|--------|-------------|
| no_placeholders | BLOCKING | Zero-tolerance: any TODO/FIXME/pass-only/ellipsis/NotImplementedError/placeholder-return in a changed non-test source file fails. Reuses `tools/testing/stub_detector.py:check_substantive`. Scope: `--changed-files` only (tests + .tmp exempt). |
| duplicate_code | WARN | Flags a changed function that is a verbatim (rename-insensitive) copy of an existing `tools/` function — import the original instead. |

Gate config: `args/security_gates.yaml` → `codegen_quality`.
CLI: `python tools/workflow/coherence_checker.py --check no_placeholders,duplicate_code --changed-files "<files>" --gate`
