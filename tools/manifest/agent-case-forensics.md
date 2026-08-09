# AGOV CASE — Agent-Session Forensics

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Case Bundle

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Case Bundle Format | tools/agent_case/bundle_format.py | Library — the on-disk shape of a case bundle plus the three digest recipes (manifest SHA-256, hook_events HMAC mirroring `.claude/hooks/send_event.py::compute_hmac`, migration-149 audit row hash). No DB access: a bundle is verified from the bundle alone | `build_manifest(dir)`, `write_manifest`, `read_manifest`, `compute_event_hmac(payload, secret)`, `compute_audit_row_hash(row)` | Manifest dicts, hex digests |
| Case Bundle Verifier | tools/agent_case/bundle_verifier.py | Three independent verification layers (manifest / per-event HMAC / audit hash chain) reporting WHICH records failed WHICH layer — member path with expected vs actual digest, hook event id, audit row id. Degrades honestly: an unset, default, or rotated `ICDEV_HOOK_HMAC_SECRET` reports NOT_VERIFIED, never passed and never failed | --bundle, --layer {manifest,hmac,chain}, --secret, --json | Per-record findings; exit 0 pass / 1 a layer failed / 2 indeterminate / 3 bundle unreadable |
