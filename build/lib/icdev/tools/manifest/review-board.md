# Review Board (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Review Board (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Docs Fixer | tools/review_board/fixers/docs_fixes.py | Auto-fix engine for documentation findings | --json | Fix results |
| Perf Fixer | tools/review_board/fixers/perf_fixes.py | Auto-fix engine for performance findings | --json | Fix results |
| QA Fixer | tools/review_board/fixers/qa_fixes.py | Auto-fix engine for QA findings | --json | Fix results |
| SRE Fixer | tools/review_board/fixers/sre_fixes.py | Auto-fix engine for SRE findings | --json | Fix results |
| Health Scorer | tools/review_board/health_scorer.py | Aggregate health scoring across reflexes | --json | Health scores |
| Notifier | tools/review_board/notifier.py | Review board notification dispatcher | --json | Notification status |
| Remediation Engine | tools/review_board/remediation_engine.py | Auto-fix findings pipeline — 3-tier confidence model (auto/suggest/escalate), rate limiting, declarative fix registry, NIST AU audit log (D-RB-4,8,9,10) | --run, --dry-run, --pending, --history, --stats, --json | Remediation results |

