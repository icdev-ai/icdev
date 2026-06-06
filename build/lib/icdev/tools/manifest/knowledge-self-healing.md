# Knowledge & Self-Healing

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Knowledge & Self-Healing
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Knowledge Ingest | tools/knowledge/knowledge_ingest.py | Ingest patterns and lessons | --content, --type | Pattern ID |
| Pattern Detector | tools/knowledge/pattern_detector.py | Detect patterns from logs/metrics | --source, --data | Patterns found |
| Recommendation Engine | tools/knowledge/recommendation_engine.py | Generate recommendations via Bedrock | --context | Recommendations |
| Self-Heal Analyzer | tools/knowledge/self_heal_analyzer.py | Analyze failures and auto-correct | --failure-data | Healing result |
| Deviation Rules | tools/knowledge/deviation_rules.py | Category-based deviation rules (GSD-adapted): 5 categories layered on confidence-based healing — security/blocking auto-fix at lower threshold, architectural/compliance always escalate (D-GSD-7 through D-GSD-9) | --classify, --apply, --confidence, --stats, --list-categories, --json | Classification + decision override |

