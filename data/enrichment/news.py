# data/enrichment/news.py
# ─────────────────────────────────────────────────────────────
# Pulls recent headlines from category-specific RSS feeds.
# For each market, scores headlines by keyword relevance to
# the market question and returns the top matches.
#
# No external dependencies — uses stdlib xml + email.utils.
# Cache: 30 minutes per feed URL.
# ─────────────────────────────────────────────────────────────

import email.utils
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import requests

from .cache import _cache

logger = logging.getLogger(__name__)

# ── Category → RSS feed URLs ─────────────────────────────────
CATEGORY_FEEDS: dict[str, list[str]] = {
    "CRYPTO": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ],
    "SPORTS": [
        "https://www.espn.com/espn/rss/news",
        "https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml",
    ],
    "POLITICS": [
        "https://rss.politico.com/politics-news.xml",
        "https://thehill.com/feed/",
    ],
    "MACRO": [
        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    ],
    "TECH": [
        "https://techcrunch.com/feed/",
        "https://feeds.arstechnica.com/arstechnica/index",
    ],
    "ENTERTAINMENT": [
        "https://variety.com/feed/",
        "https://deadline.com/feed/",
    ],
    "GEO": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    ],
    "GENERAL": [
        "https://feeds.bbci.co.uk/news/rss.xml",
    ],
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; polybot/1.0)"}

STOP_WORDS = {
    "will", "the", "a", "an", "be", "is", "are", "was", "were", "been",
    "in", "on", "at", "to", "of", "and", "or", "for", "with", "by",
    "it", "its", "this", "that", "have", "has", "had", "do", "does",
    "did", "not", "but", "from", "as", "if", "than", "more", "most",
    "would", "could", "should", "may", "might", "get", "got", "above",
    "below", "over", "under", "after", "before", "during", "between",
}


def _fetch_feed(url: str, session: requests.Session) -> list[tuple[str, float]]:
    """
    Fetch one RSS feed. Returns list of (headline, age_hours) tuples.
    Cached for 30 minutes.
    """
    cache_key = f"rss_{url}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    items: list[tuple[str, float]] = []
    try:
        r = session.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []

        # Strip any XML namespace declarations that trip up ElementTree
        content = re.sub(r'\s+xmlns[^"]*"[^"]*"', "", r.text)
        root = ET.fromstring(content)

        now = datetime.now(tz=timezone.utc)
        for item in root.findall(".//item"):
            title    = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            if not title:
                continue

            age_hours = 48.0   # default if no date
            if pub_date:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date)
                    age_hours = (now - dt).total_seconds() / 3600
                except Exception:
                    pass

            if age_hours <= 48:   # Only last 48 hours
                items.append((title, age_hours))

        _cache.set(cache_key, items, ttl=1800)   # 30 min
    except Exception as e:
        logger.debug(f"RSS fetch failed for {url}: {e}")

    return items


def _keywords(text: str) -> set[str]:
    """Extract meaningful words from a question or headline."""
    words = re.findall(r"\b[a-zA-Z0-9]{3,}\b", text.lower())
    return {w for w in words if w not in STOP_WORDS}


def _relevance(headline: str, question: str) -> int:
    """Count keyword overlap between a headline and a market question."""
    q_kw = _keywords(question)
    h_kw = _keywords(headline)
    return len(q_kw & h_kw)


def _age_label(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def get_headlines(
    category: str,
    question: str,
    session: requests.Session,
    max_headlines: int = 3,
) -> list[str]:
    """
    Return up to max_headlines relevant headlines for a market question.
    Each is a string: '"Headline text" (Xh ago)'
    """
    urls = CATEGORY_FEEDS.get(category, CATEGORY_FEEDS["GENERAL"])

    all_items: list[tuple[str, float]] = []
    for url in urls:
        all_items.extend(_fetch_feed(url, session))

    if not all_items:
        return []

    # Score and sort: first by relevance, then recency
    scored = [
        (title, age, _relevance(title, question))
        for title, age in all_items
    ]
    scored.sort(key=lambda x: (-x[2], x[1]))   # high relevance, low age

    # If any headline is relevant to the question, only surface relevant ones
    max_score = scored[0][2] if scored else 0
    min_score = 1 if max_score > 0 else 0

    seen: set[str] = set()
    results: list[str] = []
    for title, age, score in scored:
        if score < min_score:
            break   # list is sorted — no more relevant items
        # Deduplicate near-identical headlines
        key = title[:40].lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(f'"{title}" ({_age_label(age)})')
        if len(results) >= max_headlines:
            break

    return results


def get_context(category: str, question: str, session: requests.Session) -> str:
    """
    Return a formatted news context string, or "" if no headlines found.
    Example: '"BTC ETF inflows hit $400M" (3h ago) | "Fed signals pause" (6h ago)'
    """
    headlines = get_headlines(category, question, session)
    return " | ".join(headlines)
