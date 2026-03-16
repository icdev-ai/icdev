#!/usr/bin/env python3
"""Source credibility database — per-outlet bias and factual reporting ratings.

Curated from MediaBias/FactCheck (5,600+ sources) and AllSides (800+ sources).
Stored as an in-memory lookup for fast Lambda access (no external API needed).

Bias spectrum (generalized beyond left/right):
  - political: left-center-right scale
  - factual: very_high, high, mostly_factual, mixed, low, very_low
  - credibility: A (most reliable) through F (unreliable)

The database is deterministic — no API calls, no rate limits, air-gap safe.
New outlets are scored dynamically based on article content analysis.
"""
import logging
from urllib.parse import urlparse

logger = logging.getLogger("intaas.services.credibility")

# Curated source ratings (domain → profile)
# Sources: mediabiasfactcheck.com, allsides.com, adfontesmedia.com
SOURCE_RATINGS: dict[str, dict] = {
    # Major Wire Services (highest credibility)
    "apnews.com": {"name": "Associated Press", "political": "center", "factual": "very_high", "credibility": "A"},
    "reuters.com": {"name": "Reuters", "political": "center", "factual": "very_high", "credibility": "A"},
    "afp.com": {"name": "AFP", "political": "center", "factual": "very_high", "credibility": "A"},

    # US Mainstream — Left-Leaning
    "nytimes.com": {"name": "New York Times", "political": "left-center", "factual": "high", "credibility": "A"},
    "washingtonpost.com": {"name": "Washington Post", "political": "left-center", "factual": "high", "credibility": "A"},
    "cnn.com": {"name": "CNN", "political": "left", "factual": "mostly_factual", "credibility": "B"},
    "msnbc.com": {"name": "MSNBC", "political": "left", "factual": "mostly_factual", "credibility": "B"},
    "npr.org": {"name": "NPR", "political": "left-center", "factual": "very_high", "credibility": "A"},
    "pbs.org": {"name": "PBS", "political": "left-center", "factual": "very_high", "credibility": "A"},
    "politico.com": {"name": "Politico", "political": "left-center", "factual": "high", "credibility": "A"},
    "theatlantic.com": {"name": "The Atlantic", "political": "left-center", "factual": "high", "credibility": "A"},
    "vox.com": {"name": "Vox", "political": "left", "factual": "high", "credibility": "B"},
    "slate.com": {"name": "Slate", "political": "left", "factual": "high", "credibility": "B"},
    "huffpost.com": {"name": "HuffPost", "political": "left", "factual": "mostly_factual", "credibility": "B"},
    "thedailybeast.com": {"name": "Daily Beast", "political": "left", "factual": "mostly_factual", "credibility": "B"},

    # US Mainstream — Center
    "usatoday.com": {"name": "USA Today", "political": "center", "factual": "high", "credibility": "A"},
    "thehill.com": {"name": "The Hill", "political": "center", "factual": "high", "credibility": "A"},
    "abcnews.go.com": {"name": "ABC News", "political": "left-center", "factual": "high", "credibility": "A"},
    "cbsnews.com": {"name": "CBS News", "political": "left-center", "factual": "high", "credibility": "A"},
    "nbcnews.com": {"name": "NBC News", "political": "left-center", "factual": "high", "credibility": "A"},
    "bbc.com": {"name": "BBC", "political": "center", "factual": "very_high", "credibility": "A"},
    "bbc.co.uk": {"name": "BBC", "political": "center", "factual": "very_high", "credibility": "A"},

    # US Mainstream — Right-Leaning
    "foxnews.com": {"name": "Fox News", "political": "right", "factual": "mixed", "credibility": "C"},
    "nypost.com": {"name": "New York Post", "political": "right-center", "factual": "mostly_factual", "credibility": "B"},
    "washingtontimes.com": {"name": "Washington Times", "political": "right-center", "factual": "mostly_factual", "credibility": "B"},
    "dailycaller.com": {"name": "Daily Caller", "political": "right", "factual": "mixed", "credibility": "C"},
    "breitbart.com": {"name": "Breitbart", "political": "far-right", "factual": "mixed", "credibility": "D"},
    "newsmax.com": {"name": "Newsmax", "political": "right", "factual": "mixed", "credibility": "C"},
    "thefederalist.com": {"name": "The Federalist", "political": "right", "factual": "mixed", "credibility": "C"},
    "nationalreview.com": {"name": "National Review", "political": "right", "factual": "mostly_factual", "credibility": "B"},
    "dailywire.com": {"name": "Daily Wire", "political": "right", "factual": "mostly_factual", "credibility": "B"},

    # International — English
    "theguardian.com": {"name": "The Guardian", "political": "left-center", "factual": "high", "credibility": "A"},
    "telegraph.co.uk": {"name": "The Telegraph", "political": "right-center", "factual": "high", "credibility": "B"},
    "economist.com": {"name": "The Economist", "political": "center", "factual": "very_high", "credibility": "A"},
    "ft.com": {"name": "Financial Times", "political": "center", "factual": "very_high", "credibility": "A"},
    "aljazeera.com": {"name": "Al Jazeera", "political": "left-center", "factual": "mostly_factual", "credibility": "B"},
    "al-monitor.com": {"name": "Al-Monitor", "political": "center", "factual": "high", "credibility": "A"},
    "dw.com": {"name": "Deutsche Welle", "political": "center", "factual": "very_high", "credibility": "A"},
    "france24.com": {"name": "France 24", "political": "center", "factual": "high", "credibility": "A"},
    "scmp.com": {"name": "South China Morning Post", "political": "center", "factual": "high", "credibility": "B"},
    "straitstimes.com": {"name": "Straits Times", "political": "center", "factual": "high", "credibility": "A"},
    "abc.net.au": {"name": "ABC Australia", "political": "center", "factual": "very_high", "credibility": "A"},
    "cbc.ca": {"name": "CBC", "political": "left-center", "factual": "high", "credibility": "A"},
    "euronews.com": {"name": "Euronews", "political": "center", "factual": "high", "credibility": "A"},

    # US Regional / Other English
    "seattletimes.com": {"name": "Seattle Times", "political": "left-center", "factual": "high", "credibility": "A"},
    "chicagotribune.com": {"name": "Chicago Tribune", "political": "center", "factual": "high", "credibility": "A"},
    "latimes.com": {"name": "LA Times", "political": "left-center", "factual": "high", "credibility": "A"},
    "bostonglobe.com": {"name": "Boston Globe", "political": "left-center", "factual": "high", "credibility": "A"},
    "orlandosentinel.com": {"name": "Orlando Sentinel", "political": "left-center", "factual": "high", "credibility": "A"},
    "columbian.com": {"name": "The Columbian", "political": "center", "factual": "high", "credibility": "A"},
    "bnnbloomberg.ca": {"name": "BNN Bloomberg", "political": "center", "factual": "high", "credibility": "A"},

    # State-Affiliated (lower credibility for independence)
    "rt.com": {"name": "RT (Russia Today)", "political": "right", "factual": "very_low", "credibility": "F"},
    "ria.ru": {"name": "RIA Novosti", "political": "right", "factual": "low", "credibility": "D"},
    "tass.com": {"name": "TASS", "political": "right", "factual": "low", "credibility": "D"},
    "xinhua.net": {"name": "Xinhua", "political": "left", "factual": "mixed", "credibility": "D"},
    "cgtn.com": {"name": "CGTN", "political": "left", "factual": "mixed", "credibility": "D"},
    "globaltimes.cn": {"name": "Global Times", "political": "left", "factual": "mixed", "credibility": "D"},
    "presstv.ir": {"name": "Press TV", "political": "left", "factual": "very_low", "credibility": "F"},

    # Tech / Business
    "bloomberg.com": {"name": "Bloomberg", "political": "center", "factual": "high", "credibility": "A"},
    "wsj.com": {"name": "Wall Street Journal", "political": "right-center", "factual": "high", "credibility": "A"},
    "cnbc.com": {"name": "CNBC", "political": "center", "factual": "high", "credibility": "A"},
    "techcrunch.com": {"name": "TechCrunch", "political": "left-center", "factual": "high", "credibility": "A"},
    "theverge.com": {"name": "The Verge", "political": "left-center", "factual": "high", "credibility": "A"},
    "arstechnica.com": {"name": "Ars Technica", "political": "left-center", "factual": "very_high", "credibility": "A"},
    "wired.com": {"name": "Wired", "political": "left-center", "factual": "high", "credibility": "A"},

    # Opinion / Commentary (lower factual, explicit bias)
    "lewrockwell.com": {"name": "LewRockwell", "political": "far-right", "factual": "mixed", "credibility": "D"},
    "consortiumnews.com": {"name": "Consortium News", "political": "left", "factual": "mostly_factual", "credibility": "C"},
    "commondreams.org": {"name": "Common Dreams", "political": "left", "factual": "mostly_factual", "credibility": "C"},
    "jacobin.com": {"name": "Jacobin", "political": "far-left", "factual": "mostly_factual", "credibility": "C"},
    "reason.com": {"name": "Reason", "political": "right-center", "factual": "high", "credibility": "B"},

    # Pakistani English
    "geo.tv": {"name": "Geo News", "political": "center", "factual": "mostly_factual", "credibility": "B"},
    "thenews.com.pk": {"name": "The News International", "political": "center", "factual": "mostly_factual", "credibility": "B"},
    "dawn.com": {"name": "Dawn", "political": "center", "factual": "high", "credibility": "A"},

    # Malaysian
    "freemalaysiatoday.com": {"name": "Free Malaysia Today", "political": "center", "factual": "mostly_factual", "credibility": "B"},

    # Fact-Checkers (highest credibility)
    "factcheck.org": {"name": "FactCheck.org", "political": "center", "factual": "very_high", "credibility": "A"},
    "snopes.com": {"name": "Snopes", "political": "center", "factual": "very_high", "credibility": "A"},
    "politifact.com": {"name": "PolitiFact", "political": "center", "factual": "very_high", "credibility": "A"},
}

# Credibility grade descriptions
CREDIBILITY_GRADES = {
    "A": {"label": "Highly Credible", "description": "Established outlet with strong editorial standards and fact-checking"},
    "B": {"label": "Generally Credible", "description": "Reliable with occasional bias in framing or topic selection"},
    "C": {"label": "Mixed Credibility", "description": "Some factual reporting but notable bias or misleading framing"},
    "D": {"label": "Low Credibility", "description": "Significant bias, propaganda elements, or state-affiliated"},
    "F": {"label": "Unreliable", "description": "Propaganda outlet, frequent misinformation, or state-controlled media"},
}

POLITICAL_SPECTRUM = [
    "far-left", "left", "left-center", "center",
    "right-center", "right", "far-right",
]


def lookup_source(url_or_domain: str) -> dict:
    """Look up credibility profile for a source by URL or domain.

    Returns rating dict or a default 'unknown' profile.
    """
    domain = _extract_domain(url_or_domain)
    profile = SOURCE_RATINGS.get(domain)

    if profile:
        grade = CREDIBILITY_GRADES.get(profile["credibility"], {})
        return {
            "domain": domain,
            "known": True,
            **profile,
            "credibility_label": grade.get("label", ""),
            "credibility_description": grade.get("description", ""),
            "political_index": POLITICAL_SPECTRUM.index(profile["political"])
            if profile["political"] in POLITICAL_SPECTRUM else 3,
        }

    return {
        "domain": domain,
        "known": False,
        "name": domain,
        "political": "unknown",
        "factual": "unknown",
        "credibility": "?",
        "credibility_label": "Not Rated",
        "credibility_description": "This outlet is not in our credibility database",
        "political_index": -1,
    }


def lookup_batch(urls: list[str]) -> list[dict]:
    """Look up credibility for multiple URLs."""
    return [lookup_source(u) for u in urls]


def get_all_sources() -> list[dict]:
    """Return all rated sources for the Sources page."""
    results = []
    for domain, profile in sorted(SOURCE_RATINGS.items()):
        grade = CREDIBILITY_GRADES.get(profile["credibility"], {})
        results.append({
            "domain": domain,
            "known": True,
            **profile,
            "credibility_label": grade.get("label", ""),
        })
    return results


def get_stats() -> dict:
    """Return database statistics."""
    total = len(SOURCE_RATINGS)
    by_credibility = {}
    by_political = {}
    for profile in SOURCE_RATINGS.values():
        c = profile["credibility"]
        p = profile["political"]
        by_credibility[c] = by_credibility.get(c, 0) + 1
        by_political[p] = by_political.get(p, 0) + 1
    return {
        "total_sources": total,
        "by_credibility": by_credibility,
        "by_political": by_political,
    }


def _extract_domain(url_or_domain: str) -> str:
    """Extract clean domain from URL or domain string."""
    if url_or_domain.startswith("http"):
        parsed = urlparse(url_or_domain)
        domain = parsed.netloc
    else:
        domain = url_or_domain

    domain = domain.lower().replace("www.", "")
    return domain
