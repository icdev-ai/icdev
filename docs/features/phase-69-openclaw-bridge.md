# Phase 69: OpenClaw Skill Bridge

CUI // SP-CTI

## Summary

Zero-trust import/export bridge between the ClawHub (clawhub.ai) open skill ecosystem and the ICDEV™ Marketplace, with full compatibility checking, translation, and security scanning.

## Problem

ICDEV™'s marketplace is tenant-federated but internal. The OpenClaw/ClawHub ecosystem has 2,857+ community-authored AI skills that could accelerate capability development. However, OpenClaw and ICDEV™ have fundamentally different architectures (Node.js vs Python, flat markdown vs FORGE 6-layer, no compliance vs CUI/NIST).

## Solution

A quarantine-first import pipeline with:
- **Compatibility checker** — 13-point analysis scoring OpenClaw skills for ICDEV™ compatibility
- **Translator** — Automatic conversion of OpenClaw skill.md → ICDEV™ SKILL.md format
- **10-gate security scanning** — SAST, secrets, deps, CUI, SBOM, provenance, signature, prompt injection, behavioral sandbox, code pattern
- **AST-based script safety** — Deterministic analysis of Python scripts for blocked imports/calls
- **Functional validation** — 8-point dry-run check on translated skills
- **License compliance** — Permissive vs copyleft enforcement for Gov/DoD environments
- **Rollback/revoke** — One-command revert for promoted skills that cause issues

## Key Architecture Decisions

- **No registration/renewal required** — imported skills are free community assets
- **Feature-flagged** — `ICDEV_OPENCLAW_ENABLED` (disabled by default, air-gap safe)
- **Safe scripts auto-promote** — AST analysis passes → no human review needed
- **Unsafe scripts require review** — eval/exec/subprocess/network → mandatory ISSO review
- **JS/TS scripts block import** — ICDEV™ is Python-only; manual translation required
- **Copyleft licenses blocked for IL5/IL6** — legal risk in Gov/DoD distribution

## Files

### Created
| File | Purpose |
|------|---------|
| `tools/marketplace/openclaw_bridge.py` | Core import/export/promote/reject/revoke tool |
| `tools/marketplace/openclaw_compat.py` | Compatibility checker, translator, functional validator |
| `args/openclaw_config.yaml` | Configuration with CVE-2026-25253 mitigations |
| `tests/test_openclaw_bridge.py` | 37 test cases |
| `tests/test_openclaw_compat.py` | 34 test cases |

### Modified
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | `openclaw_imports` + `openclaw_exports` tables |
| `args/security_gates.yaml` | `openclaw_import` + `openclaw_export` gates |
| `tools/audit/audit_logger.py` | 7 OpenClaw event types |
| `tools/mcp/tool_registry.py` | 6 MCP tools |
| `tools/mcp/marketplace_server.py` | 6 handler functions |
| `.gitlab-ci.yml` | `security:openclaw-gate` CI job |
| `tools/testing/test_orchestrator.py` | OpenClaw gate in `evaluate_security_gate()` |
| `.claude/hooks/pre_tool_use.py` | `openclaw_exports` append-only protection |

## Test Results

- **71 tests**, all passing (1.52s)
- **Bandit:** 0 medium/high findings
- **Ruff:** 0 errors
- **Coherence:** append-only tables protected, manifest registered

## Validated Against

Real-world ClawHub skill: `pskoett/self-improving-agent` (v3.0.5, MIT-0)
- Tool mapping: 4/4 successful (read_file→Read, write_file→Write, run_command→Bash, search_files→Grep)
- Shell scripts: warned (cross-platform review)
- JS/TS hooks: warned and stripped
- License: MIT-0 compatible with all ILs
- Functional validation: 100/100 score
