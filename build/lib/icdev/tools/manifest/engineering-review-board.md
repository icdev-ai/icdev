# Engineering Review Board (Phase 67, D-RB-1 through D-RB-7)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Engineering Review Board (Phase 67, D-RB-1 through D-RB-7)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Review Board Daemon | tools/review_board/daemon.py | Multi-persona analysis daemon — 7 reflexes (SRE, QA, Security, Perf, UX, Docs, Product) on configurable schedules (D-RB-1) | --once, --status, --reflex, --enable, --disable, --reset, --json | Daemon status, findings |
| SRE Reflex | tools/review_board/reflexes/sre.py | Reliability checks — backup freshness, error rate, circuit breaker state, disk usage (D-RB-3) | config dict | Findings list |
| QA Reflex | tools/review_board/reflexes/qa.py | Coverage checks — untested modules, E2E gaps, syntax errors, test-to-code ratio (D-RB-3) | config dict | Findings list |
| Security Reflex | tools/review_board/reflexes/security.py | Red team checks — secret exposure, CVE SLA, injection patterns, dangerous code (D-RB-3) | config dict | Findings list |
| Performance Reflex | tools/review_board/reflexes/perf.py | Performance checks — DB file sizes, large tables, audit growth, temp dir size (D-RB-3) | config dict | Findings list |
| UX Reflex | tools/review_board/reflexes/ux.py | Accessibility checks — ARIA coverage, template quality, form labels (D-RB-3) | config dict | Findings list |
| Docs Reflex | tools/review_board/reflexes/docs.py | Documentation checks — stale docs, undocumented tools, broken refs, missing phases (D-RB-3) | config dict | Findings list |
| Product Reflex | tools/review_board/reflexes/product.py | Product analytics — feature usage, gate pass rates, tool distribution (D-RB-3) | config dict | Findings list |
| Report Reflex | tools/review_board/reflexes/report.py | Weekly digest reflex — generates Markdown summary of Review Board health score, findings, auto-remediation stats, and recommendations | config dict | Findings list |

