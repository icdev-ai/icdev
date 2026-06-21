# Platform Connectors (Unified Internet Access Layer)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.
>
> Consolidates fragmented per-engine platform fetches into a shared adapter registry
> with multi-backend routing, zero-config APIs, and health monitoring.
> Inspired by agent-reach (innovation signal sig-270a39e5c493, score 0.835).

## Platform Connectors

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Connector CLI | tools/platform_connectors/connector_cli.py | Single-entry internet access for AI agents — fetch from GitHub/HN/SO/Reddit/YouTube with multi-backend routing and fallbacks | --fetch PLATFORM QUERY, --fetch-all QUERY, --doctor, --list-platforms, --json | Normalized JSON items + audit trail |
| Registry | tools/platform_connectors/registry.py | Unified adapter registry with priority-ordered multi-backend routing and health monitoring | `get_registry().fetch(platform, query)`, `get_registry().doctor()` | FetchResult / HealthResult objects |
| Base ABC | tools/platform_connectors/base.py | Abstract base classes: PlatformConnector, FetchResult, HealthResult (D66 pattern) | (import) | ABCs + dataclasses |
| GitHub Adapter | tools/platform_connectors/adapters/github.py | GitHub REST API v3 — repo search with optional token auth (60/5000 req/hr) | query, max_results, since_days, language | List of repo items |
| HackerNews Adapter | tools/platform_connectors/adapters/hackernews.py | HN Algolia Search API — zero-config, no auth required | query, max_results, since_days | List of story items |
| StackOverflow Adapter | tools/platform_connectors/adapters/stackoverflow.py | SO API v2.3 — questions by keyword (optional key for higher quota) | query, max_results, since_days, tags | List of question items |
| Reddit Adapter | tools/platform_connectors/adapters/reddit.py | Reddit public JSON API — no OAuth, multi-subreddit search | query, max_results, subreddits, time_filter | List of post items |
| YouTube Adapters | tools/platform_connectors/adapters/youtube.py | YouTube Data API v3 (primary) + yt-dlp fallback (no key needed) | query, max_results, order | List of video items |
| Config | args/platform_connectors_config.yaml | Per-adapter enable flags, default limits, circuit breaker, health probe settings | (data) | YAML config |

## CLI Examples

```bash
# Fetch from a single platform
python tools/platform_connectors/connector_cli.py --fetch github "kubernetes operators" --json

# Fetch from all platforms (aggregated)
python tools/platform_connectors/connector_cli.py --fetch-all "zero trust network" --json

# Health check all adapter backends
python tools/platform_connectors/connector_cli.py --doctor --json

# List registered platforms and adapters
python tools/platform_connectors/connector_cli.py --list-platforms --json
```

## Platform Support

| Platform | Adapter | Auth | Rate Limit | Fallback |
|----------|---------|------|-----------|---------|
| GitHub | github_rest | GITHUB_TOKEN (optional) | 60/hr unauth, 5000/hr auth | None |
| Hacker News | hackernews_algolia | None (zero-config) | Generous | None |
| Stack Overflow | stackoverflow_v2 | SO_API_KEY (optional) | 300/day unauth | None |
| Reddit | reddit_json | None (zero-config) | ~60 req/min | None |
| YouTube | youtube_data_api | YOUTUBE_API_KEY (required) | Quota-based | youtube_ytdlp |
| YouTube | youtube_ytdlp | None (yt-dlp fallback) | Slow but reliable | — |

## Extending

```python
from tools.platform_connectors.base import PlatformConnector, FetchResult, HealthResult
from tools.platform_connectors.registry import get_registry

class TwitterAdapter(PlatformConnector):
    name = "twitter_v2"
    platform = "twitter"
    priority = 0

    def fetch(self, query, **kwargs):
        # ... implementation ...
        return self._ok(items)

    def health(self):
        return HealthResult(adapter_name=self.name, platform=self.platform, status="ok")

get_registry().register(TwitterAdapter())
```
