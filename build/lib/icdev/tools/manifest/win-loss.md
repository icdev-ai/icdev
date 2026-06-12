# Win/Loss Analysis

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Win/Loss Analysis
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pattern Analyzer | tools/win_loss/pattern_analyzer.py | Correlates feature tags with proposal outcomes to compute per-feature win rates and impact scores | --json | List of FeatureImpact (feature_tag, win_rate, impact_score, win_count, loss_count) |
