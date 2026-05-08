# data/enrichment/search.py
# ─────────────────────────────────────────────────────────────
# Web search enrichment for market questions.
# Uses Brave Search API if BRAVE_SEARCH_API_KEY is set,
# falls back to DuckDuckGo HTML scraping (no key needed).
# ─────────────────────────────────────────────────────────────

import os
import re
import logging
import requests
from .cache import _cache

logger = logging.getLogger(__name__)

_BRAVE_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
_BRAVE_URL = "https://api.search.brave.com/res/web/v1/search"
_DDG_URL   = "https://html.duckduckgo.com/html/"

_NEWS_SUFFIX = " site:reuters.com OR site:bbc.com OR site:apnews.com"

_STOP = frozenset({
    "will", "does", "is", "are", "has", "have", "did", "do", "be", "been",
    "the", "a", "an", "by", "in", "on", "at", "to", "of", "for", "from",
    "with", "about", "and", "or", "not", "this", "that", "which", "who",
    "when", "what", "how", "if", "its", "it", "than", "then",
})


def _build_query(question: str) -> str:
    words = re.sub(r"[^\w\s]", " ", question).split()
    key_words = [w for w in words if w.lower() not in _STOP and len(w) > 2]
    return " ".join(key_words[:7]) + _NEWS_SUFFIX


def _brave(query: str, session: requests.Session) -> list[str]:
    resp = session.get(
        _BRAVE_URL,
        params={"q": query, "count": 5},
        headers={"X-Subscription-Token": _BRAVE_KEY, "Accept": "application/json"},
        timeout=8,
    )
    resp.raise_for_status()
    results = resp.json().get("web", {}).get("results", [])
    return [r.get("title", "") for r in results if r.get("title")]


def _ddg(query: str, session: requests.Session) -> list[str]:
    # DuckDuckGo HTML form (no API key required)
    clean_q = query.replace(_NEWS_SUFFIX, "").strip()
    resp = session.get(
        _DDG_URL,
        params={"q": clean_q},
        headers={"User-Agent": "Mozilla/5.0 (compatible; polybot/1.0)"},
        timeout=8,
    )
    titles = re.findall(r'class="result__a"[^>]*>([^<]+)<', resp.text)
    return [t.strip() for t in titles[:5] if t.strip()]


def get_context(question: str, session: requests.Session) -> str:
    """
    Return up to 3 recent news headlines relevant to the market question.
    Format: "Search: Headline one | Headline two | Headline three"
    """
    query = _build_query(question)
    cache_key = f"search:{query[:60]}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        if _BRAVE_KEY:
            titles = _brave(query, session)
            try:
                from web.usage import record_brave_search
                record_brave_search(1)
            except Exception:
                pass
        else:
            titles = _ddg(query, session)
        if not titles:
            return ""
        result = "Search: " + " | ".join(t[:70] for t in titles[:3])
        _cache.set(cache_key, result, ttl=300)
        return result
    except Exception as e:
        logger.debug(f"Search failed for '{question[:40]}': {e}")
        return ""
