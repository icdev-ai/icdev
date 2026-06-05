#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Pillar 2: Trending Open Source — discover relevant repos, papers, discussions.

Delegates to existing web_scanner for GitHub/HackerNews, adds Reddit and arXiv
scanning with ICDEV™ relevance scoring.

Usage:
    python tools/scout/pillars/trending.py --scan --json
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ICDEV™ relevance keywords for scoring
RELEVANCE_KEYWORDS = [
    "agent",
    "autonomous",
    "llm",
    "multi-agent",
    "mcp",
    "compliance",
    "devsecops",
    "code generation",
    "self-healing",
    "local model",
    "ollama",
    "tool use",
    "function calling",
    "rag",
    "fine-tuning",
    "sbom",
    "fedramp",
    "nist",
    "zero trust",
    "govcloud",
    "orchestrat",
    "workflow",
    "pipeline",
    "terraform",
    "kubernetes",
    "security",
    "testing",
    "ci/cd",
    "automation",
    "framework",
    "sdk",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finding(
    category: str,
    title: str,
    description: str,
    url: str = "",
    severity: str = "medium",
    action: str = "",
    score: float = 0.5,
    meta: dict = None,
) -> dict:
    return {
        "id": f"scout-trend-{uuid.uuid4().hex[:12]}",
        "pillar": "trending",
        "category": category,
        "title": title,
        "description": description,
        "url": url,
        "severity": severity,
        "actionable": bool(action),
        "suggested_action": action,
        "source": category,
        "relevance_score": score,
        "metadata": meta or {},
        "discovered_at": _now(),
    }


def _score_relevance(text: str) -> float:
    """Score 0-1 relevance to ICDEV™'s domain based on keyword matches."""
    if not text:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for kw in RELEVANCE_KEYWORDS if kw in text_lower)
    return min(matches / 5.0, 1.0)  # 5+ matches = 1.0


def _fetch_json(url: str, headers: dict = None, timeout: int = 15) -> dict:
    """Fetch JSON from URL with timeout and user-agent."""
    hdrs = {"User-Agent": "ICDEV-Scout/1.0 (autonomous research scanner)"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 — config-driven URLs
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _scan_github_trending(config: dict) -> List[dict]:
    """Delegate to existing web_scanner for GitHub trending."""
    findings = []
    try:
        from tools.innovation.web_scanner import _load_config, scan_github

        results = scan_github(_load_config())
        if isinstance(results, list):
            signals = results
        elif isinstance(results, dict):
            signals = results.get("signals", results.get("results", []))
        else:
            signals = []
        if isinstance(signals, list):
            for sig in signals[:30]:
                title = sig.get("title", sig.get("name", ""))
                desc = sig.get("description", sig.get("body", ""))
                url = sig.get("url", sig.get("html_url", ""))
                combined = f"{title} {desc}"
                score = _score_relevance(combined)
                stars = sig.get("stars", sig.get("stargazers_count", 0))
                findings.append(
                    _finding(
                        category="github_trending",
                        title=title,
                        description=desc[:300] if desc else "",
                        url=url,
                        score=score,
                        severity="high" if score >= 0.8 else "medium" if score >= 0.5 else "low",
                        action="Evaluate for ICDEV™ integration" if score >= 0.6 else "",
                        meta={"stars": stars, "source_type": "github"},
                    )
                )
    except Exception as exc:
        findings.append(
            _finding(
                category="github_trending",
                title="GitHub trending scan failed",
                description=str(exc),
                severity="low",
                score=0.0,
            )
        )
    return findings


def _scan_hackernews(config: dict) -> List[dict]:
    """Delegate to existing web_scanner for HackerNews."""
    findings = []
    try:
        from tools.innovation.web_scanner import _load_config, scan_hackernews

        results = scan_hackernews(_load_config())
        if isinstance(results, list):
            signals = results
        elif isinstance(results, dict):
            signals = results.get("signals", results.get("results", []))
        else:
            signals = []
        if isinstance(signals, list):
            for sig in signals[:20]:
                title = sig.get("title", "")
                url = sig.get("url", "")
                score = _score_relevance(title)
                hn_score = sig.get("score", sig.get("points", 0))
                findings.append(
                    _finding(
                        category="hackernews",
                        title=title,
                        description=f"HN score: {hn_score}",
                        url=url,
                        score=score,
                        severity="medium" if score >= 0.5 else "low",
                        action="Read and evaluate for ICDEV™ relevance" if score >= 0.6 else "",
                        meta={"hn_score": hn_score, "source_type": "hackernews"},
                    )
                )
    except Exception as exc:
        findings.append(
            _finding(
                category="hackernews",
                title="HackerNews scan failed",
                description=str(exc),
                severity="low",
                score=0.0,
            )
        )
    return findings


def _scan_reddit(config: dict) -> List[dict]:
    """Scan Reddit subreddits for relevant discussions."""
    findings = []
    reddit_cfg = config.get("trending", {}).get("sources", {}).get("reddit", {})
    if not reddit_cfg.get("enabled", True):
        return findings

    subreddits = reddit_cfg.get("subreddits", ["LocalLLaMA", "MachineLearning"])
    min_upvotes = reddit_cfg.get("min_upvotes", 50)
    max_results = reddit_cfg.get("max_results", 20)

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=25"
        data = _fetch_json(url)
        posts = data.get("data", {}).get("children", [])

        for post in posts:
            pdata = post.get("data", {})
            title = pdata.get("title", "")
            ups = pdata.get("ups", 0)
            permalink = pdata.get("permalink", "")
            selftext = pdata.get("selftext", "")[:200]

            if ups < min_upvotes:
                continue

            combined = f"{title} {selftext}"
            score = _score_relevance(combined)
            if score < 0.3:
                continue

            findings.append(
                _finding(
                    category="reddit",
                    title=f"[r/{sub}] {title}",
                    description=selftext[:200] if selftext else f"Upvotes: {ups}",
                    url=f"https://reddit.com{permalink}" if permalink else "",
                    score=score,
                    severity="medium" if score >= 0.6 else "low",
                    action="Review discussion for ICDEV™ feature ideas" if score >= 0.6 else "",
                    meta={"subreddit": sub, "upvotes": ups, "source_type": "reddit"},
                )
            )

        if len(findings) >= max_results:
            break

    return findings[:max_results]


def _scan_arxiv(config: dict) -> List[dict]:
    """Scan arXiv for recent papers in relevant categories."""
    findings = []
    arxiv_cfg = config.get("trending", {}).get("sources", {}).get("arxiv", {})
    if not arxiv_cfg.get("enabled", True):
        return findings

    categories = arxiv_cfg.get("categories", ["cs.AI", "cs.SE", "cs.MA", "cs.CL"])
    max_results = arxiv_cfg.get("max_results", 15)
    keywords = arxiv_cfg.get(
        "relevance_keywords",
        [
            "autonomous agent",
            "code generation",
            "self-improving",
            "tool use",
            "multi-agent",
        ],
    )

    cat_query = " OR ".join(f"cat:{c}" for c in categories)
    kw_query = " OR ".join(f'all:"{k}"' for k in keywords[:5])
    search_query = f"({cat_query}) AND ({kw_query})"

    # urlencode percent-encodes spaces/quotes/parens so the arXiv API accepts
    # multi-word phrases like all:"autonomous agent" (literal spaces in the URL
    # raise "URL can't contain control characters").
    params = urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-Scout/1.0 (autonomous research scanner)"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310 — arXiv API
            xml_text = resp.read().decode("utf-8")

        # Simple XML parsing (no external dependency)
        entries = re.findall(r"<entry>(.*?)</entry>", xml_text, re.DOTALL)
        for entry in entries[:max_results]:
            title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            link_m = re.search(r"<id>(.*?)</id>", entry)

            title = title_m.group(1).strip().replace("\n", " ") if title_m else ""
            summary = summary_m.group(1).strip().replace("\n", " ")[:300] if summary_m else ""
            link = link_m.group(1).strip() if link_m else ""

            score = _score_relevance(f"{title} {summary}")
            if score < 0.3:
                continue

            findings.append(
                _finding(
                    category="arxiv",
                    title=title,
                    description=summary,
                    url=link,
                    score=score,
                    severity="medium" if score >= 0.6 else "low",
                    action="Review paper for applicable techniques" if score >= 0.7 else "",
                    meta={"source_type": "arxiv"},
                )
            )
    except Exception as exc:
        findings.append(
            _finding(
                category="arxiv",
                title="arXiv scan failed",
                description=str(exc),
                severity="low",
                score=0.0,
            )
        )

    return findings


def scan(config: dict = None) -> dict:
    """Run all trending source scans. Returns structured results."""
    config = config or {}
    result = {"pillar": "trending", "started_at": _now(), "findings": []}

    trending_cfg = config.get("trending", {})
    sources = trending_cfg.get("sources", {})

    if sources.get("github_trending", {}).get("enabled", True):
        result["findings"].extend(_scan_github_trending(config))

    if sources.get("hackernews", {}).get("enabled", True):
        result["findings"].extend(_scan_hackernews(config))

    if sources.get("reddit", {}).get("enabled", True):
        result["findings"].extend(_scan_reddit(config))

    if sources.get("arxiv", {}).get("enabled", True):
        result["findings"].extend(_scan_arxiv(config))

    # Sort by relevance score descending
    result["findings"].sort(key=lambda f: f.get("relevance_score", 0), reverse=True)
    result["finding_count"] = len(result["findings"])
    result["completed_at"] = _now()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Pillar 2: Trending Open Source")
    parser.add_argument("--scan", action="store_true", help="Run trending scan")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    if not args.scan:
        parser.error("Specify --scan")

    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    result = scan(config)
    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Trending: {result['finding_count']} findings")
        for f in result["findings"][:10]:
            print(f"  [{f['relevance_score']:.2f}] {f['title']}")


if __name__ == "__main__":
    main()
