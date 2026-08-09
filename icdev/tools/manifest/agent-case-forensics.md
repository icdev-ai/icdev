# AGOV CASE — Agent-Session Forensics

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Case Bundle

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Case Bundle Format | tools/agent_case/bundle_format.py | Library — the on-disk shape of a case bundle plus the three digest recipes (manifest SHA-256, hook_events HMAC mirroring `.claude/hooks/send_event.py::compute_hmac`, migration-149 audit row hash). No DB access: a bundle is verified from the bundle alone | `build_manifest(dir)`, `write_manifest`, `read_manifest`, `compute_event_hmac(payload, secret)`, `compute_audit_row_hash(row)` | Manifest dicts, hex digests |
| Agent Session Timeline | tools/agent_case/session_timeline.py | Joins the per-session records ICDEV already writes (hook_events, audit_trail, agent_findings) into one ordered timeline keyed by session_id, and names the tables it CANNOT join — agent_executions, ai_telemetry and ace_audit_log have no session_id column | --session, --since, --until, --limit, --json | Ordered entries + explicit `limits` |
| Agent Case Bundler | tools/agent_case/case_bundler.py | Writes a session out as a portable SHA-256-manifested bundle — normalized timeline, tamper-evident records, `agent_approval_log` enforcement decisions with free text redacted, referenced artifact paths, W3C PROV-JSON from `prov_recorder`, and an endpoint/context header. No member carries export wall-clock, so identical input yields an identical manifest and a time-free `bundle_digest`. Reads no transcript table; classification comes from `classification_manager` | --session, --out, --since, --until, --limit, --force, --json | Bundle directory + manifest.json |
