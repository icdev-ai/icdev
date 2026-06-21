# Research Engine (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Research Engine (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Forecast Generator | tools/research/forecast_generator.py | Cross-engine prediction with surprise scoring (D-RES-17) | --generate, --session-id, --json | Forecast predictions |
| YouTube Scanner | tools/research/youtube_scanner.py | YouTube video transcript scanning (D-RES-14) | --scan, --queries, --urls, --json | Video signals |
| Social Trend Scanner | tools/research/source_scanners/social_trend_scanner.py | Multi-source social trend aggregation: Reddit /search.json, HN Algolia API, GitHub Repos API; entity disambiguation (handle→subreddit→repo); cross-source content_hash dedup; max 30 signals/source (adapt-l30-01) | scanner_key="social_trends", session_config={keywords, max_per_source} | List of normalized signal dicts |

