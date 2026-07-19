# Pulse AI Blog Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Pulse AI Blog Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Capability Scanner | tools/pulse/engine/capability_scanner.py | Load capability YAMLs and match to article topics via deterministic keyword scoring (D-PULSE-CAP-1) | --list, --domains, --match, --format-context, --json | Matched capabilities |
| Demand Detector | tools/pulse/engine/demand_detector.py | Identify unmet capability gaps from SAM.gov pain points, build capability graph (D-PULSE-CAP-2/3) | --detect, --aggregate, --high-demand, --suggest-articles, --graph, --json | Demand signals + suggestions |
| SAM Bridge | tools/pulse/engine/sam_bridge.py | SAM.gov to Pulse article pipeline — extract pain points from solicitations, generate articles | --run, --dry-run, --list-pending, --stats, --json | Generated articles |
| Researcher | tools/pulse/engine/researcher.py | Web research engine — scrape DuckDuckGo for developer pain points across Reddit/SO/HN/LinkedIn/DEV.to | (library) — `research(topic)` | Research cache entries |
| Topic Clusterer | tools/pulse/engine/topic_clusterer.py | Group related pain points into coherent article themes via TF-IDF keyword overlap (stdlib only) | (library) — `cluster_topics(items)` | Topic clusters |
| SEO Optimizer | tools/pulse/engine/seo_optimizer.py | SEO optimization — title/meta tuning, keyword extraction, JSON-LD schema, YAML frontmatter | (library) — `optimize(post)` | SEO metadata |
| Image Generator | tools/pulse/engine/image_generator.py | Hero image wrapper around the ICDEV-native `AssetGenerator`; prefers local SDXL Turbo when GPU is available, falls back to programmatic SVG in air-gap mode | (library) — `generate_image(prompt)`, `generate_hero_image(title)` | Image file path |
| Video Finder | tools/pulse/engine/video_finder.py | YouTube/Vimeo video search for blog embeds — no API keys (web scraping + oEmbed) | (library) — `find_videos(query)` | Video URLs + metadata |
| Video Generator | tools/pulse/engine/video_generator.py | Local GPU-accelerated video generation via LTX-Video 2B (optional, requires GPU) | (library) — `generate_video(prompt)` | Video file path |
| WordPress Publisher | tools/pulse/engine/wordpress_publisher.py | Publish Pulse posts to WordPress (icdev.ai) via XML-RPC API | (library) — `publish(post)` | Published post URL |
| Hostinger Publisher | tools/pulse/engine/hostinger_publisher.py | Publish Pulse posts to Hostinger Website Builder via browser automation | (library) — `publish(post)` | Published post URL |
| Content Drafter | tools/pulse/engine/drafter.py | Template-aware content drafter: builds prompts, calls LLM router for drafting (qwen3.5 scanner tier) and rewriting (Claude Sonnet planner tier), extracts SEO metadata, and ensures unique titles | (library) — `draft_article_via_llm(cluster, template_type, research_context)`, `rewrite_article_via_llm(text, findings)`, `process_draft(body_markdown, cluster)` | Dict with body_markdown, title, slug, tldr, seo metadata, word_count |
| Content Exporter | tools/pulse/engine/exporter.py | Generates blog posts as MDX with YAML frontmatter or standalone HTML with embedded styles and JSON-LD schema | (library) — `export(post, format)` | MDX or HTML string |
| Pipeline Scheduler | tools/pulse/engine/scheduler.py | Multi-stage Pulse content pipeline orchestrator (research, draft, quality, rewrite, review, publish) with LLM router and WriteGuard integration | --run, --topic, --dry-run, --json | Pipeline run result |
| Publish Gate | tools/pulse/publish_gate.py | Judge-verdict publish gate ("block red", nav-intel-09): a RED LLM-judge verdict — or no verdict at all — hard-blocks publishing (fail-closed). Shared by the publish endpoint and batch auto-publish; audited admin force override recorded in append-only `pulse_publish_audit` (migration 281) | (library) — `evaluate_publish_gate(post)`, `record_publish_override(post_id, reviewer, verdict, reason, tenant_id)` | Gate decision dict `{blocked, cleared, verdict, reason}` |

