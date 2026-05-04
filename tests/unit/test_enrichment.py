# tests/unit/test_enrichment.py
# Tests for the enrichment module — all HTTP calls are mocked.

import json
import time
import pytest
import responses as responses_lib

from data.enrichment.cache import TTLCache
from data.enrichment import crypto, macro, news
from data.enrichment.news import _keywords, _relevance, _age_label


# ─────────────────────────────────────────────────────────────
# Cache tests
# ─────────────────────────────────────────────────────────────

class TestTTLCache:
    def test_set_and_get(self):
        c = TTLCache()
        c.set("k", "hello", ttl=10)
        assert c.get("k") == "hello"

    def test_miss_returns_none(self):
        c = TTLCache()
        assert c.get("nonexistent") is None

    def test_expired_returns_none(self):
        c = TTLCache()
        c.set("k", "bye", ttl=0)
        time.sleep(0.01)
        assert c.get("k") is None

    def test_overwrite(self):
        c = TTLCache()
        c.set("k", "first",  ttl=60)
        c.set("k", "second", ttl=60)
        assert c.get("k") == "second"

    def test_clear_expired(self):
        c = TTLCache()
        c.set("live", "ok",   ttl=60)
        c.set("dead", "gone", ttl=0)
        time.sleep(0.01)
        c.clear_expired()
        assert c.get("live") == "ok"
        assert c.get("dead") is None

    def test_thread_safe(self):
        import threading
        c = TTLCache()
        errors = []
        def worker(i):
            try:
                c.set(f"k{i}", i, ttl=10)
                assert c.get(f"k{i}") == i
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert errors == []


# ─────────────────────────────────────────────────────────────
# News utility tests (pure — no HTTP)
# ─────────────────────────────────────────────────────────────

class TestNewsUtils:
    def test_keywords_removes_stop_words(self):
        kw = _keywords("Will the Fed cut rates in June?")
        assert "the" not in kw
        assert "will" not in kw
        assert "fed" in kw
        assert "cut" in kw
        assert "rates" in kw
        assert "june" in kw

    def test_keywords_filters_short_words(self):
        kw = _keywords("Will X be Y?")
        # X and Y are 1 char — filtered out
        assert "x" not in kw
        assert "y" not in kw

    def test_relevance_exact_match(self):
        score = _relevance("Bitcoin price hits record high", "Will Bitcoin reach $100k?")
        assert score >= 1

    def test_relevance_zero_on_unrelated(self):
        score = _relevance("Arsenal beats Liverpool 3-0", "Will the Fed cut rates?")
        assert score == 0

    def test_relevance_case_insensitive(self):
        s1 = _relevance("BITCOIN ETF INFLOWS", "Will Bitcoin ETF be approved?")
        s2 = _relevance("bitcoin etf inflows", "Will Bitcoin ETF be approved?")
        assert s1 == s2

    def test_age_label_minutes(self):
        assert "m ago" in _age_label(0.3)

    def test_age_label_hours(self):
        assert "h ago" in _age_label(5)

    def test_age_label_days(self):
        assert "d ago" in _age_label(30)


# ─────────────────────────────────────────────────────────────
# Crypto enricher (mocked HTTP)
# ─────────────────────────────────────────────────────────────

MOCK_COINGECKO = {
    "bitcoin":  {"usd": 79240, "usd_24h_change": 2.3},
    "ethereum": {"usd": 3420,  "usd_24h_change": -0.5},
    "solana":   {"usd": 145,   "usd_24h_change": 5.1},
    "ripple":   {"usd": 0.52,  "usd_24h_change": 1.2},
}

MOCK_FEAR_GREED = {
    "data": [{"value": "61", "value_classification": "Greed"}]
}


@responses_lib.activate
def test_crypto_get_context_format():
    from data.enrichment.cache import _cache
    _cache._store.clear()   # clear cache between tests

    responses_lib.add(responses_lib.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        json=MOCK_COINGECKO, status=200)
    responses_lib.add(responses_lib.GET,
        "https://api.alternative.me/fng/",
        json=MOCK_FEAR_GREED, status=200)

    import requests
    ctx = crypto.get_context(requests.Session())

    assert "BTC" in ctx
    assert "79,240" in ctx
    assert "+2.3%" in ctx
    assert "ETH" in ctx
    assert "Fear&Greed" in ctx
    assert "61" in ctx
    assert "Greed" in ctx


@responses_lib.activate
def test_crypto_returns_empty_on_api_failure():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    responses_lib.add(responses_lib.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        body=Exception("timeout"))
    responses_lib.add(responses_lib.GET,
        "https://api.alternative.me/fng/",
        body=Exception("timeout"))

    import requests
    ctx = crypto.get_context(requests.Session())
    assert isinstance(ctx, str)  # returns "" not exception


@responses_lib.activate
def test_crypto_uses_cache():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    responses_lib.add(responses_lib.GET,
        "https://api.coingecko.com/api/v3/simple/price",
        json=MOCK_COINGECKO, status=200)
    responses_lib.add(responses_lib.GET,
        "https://api.alternative.me/fng/",
        json=MOCK_FEAR_GREED, status=200)

    import requests
    session = requests.Session()
    crypto.get_context(session)   # first call — hits API
    crypto.get_context(session)   # second call — should use cache

    # Only 2 HTTP calls total (one per endpoint), not 4
    assert len(responses_lib.calls) == 2


# ─────────────────────────────────────────────────────────────
# Macro enricher (mocked HTTP)
# ─────────────────────────────────────────────────────────────

def _yf_response(price, prev):
    return {"chart": {"result": [{"meta": {
        "regularMarketPrice": price,
        "chartPreviousClose": prev,
        "currency": "USD",
    }}]}}


@responses_lib.activate
def test_macro_get_context_format():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    tickers = {"^GSPC": (5304, 5262), "^VIX": (18.2, 19.0),
               "^TNX": (4.32, 4.28), "GC=F": (3220, 3180), "DX-Y.NYB": (103.1, 102.8)}
    for ticker, (price, prev) in tickers.items():
        responses_lib.add(responses_lib.GET,
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
            json=_yf_response(price, prev), status=200)

    import requests
    ctx = macro.get_context(requests.Session())

    assert "S&P500" in ctx
    assert "VIX" in ctx
    assert "10Y Yield" in ctx
    assert "Gold" in ctx


@responses_lib.activate
def test_macro_returns_empty_on_failure():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    tickers = ["^GSPC", "^VIX", "^TNX", "GC=F", "DX-Y.NYB"]
    for ticker in tickers:
        responses_lib.add(responses_lib.GET,
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
            body=Exception("network error"))

    import requests
    ctx = macro.get_context(requests.Session())
    assert ctx == ""


# ─────────────────────────────────────────────────────────────
# News enricher (mocked HTTP)
# ─────────────────────────────────────────────────────────────

def _make_sample_rss(hours_ago: int = 2) -> str:
    """Generate RSS with pubDates that are hours_ago hours in the past."""
    from datetime import datetime, timezone, timedelta
    import email.utils
    def ts(offset_h):
        dt = datetime.now(tz=timezone.utc) - timedelta(hours=offset_h)
        return email.utils.format_datetime(dt)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Bitcoin ETF inflows hit record $400M this week</title>
      <pubDate>{ts(hours_ago)}</pubDate>
    </item>
    <item>
      <title>Ethereum staking yields rise amid DeFi boom</title>
      <pubDate>{ts(hours_ago + 2)}</pubDate>
    </item>
    <item>
      <title>Arsenal wins Premier League title in dramatic fashion</title>
      <pubDate>{ts(hours_ago + 4)}</pubDate>
    </item>
  </channel>
</rss>"""


@responses_lib.activate
def test_news_returns_relevant_headlines():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    from data.enrichment.news import CATEGORY_FEEDS
    rss_body = _make_sample_rss(hours_ago=2).encode()
    for url in CATEGORY_FEEDS.get("CRYPTO", []):
        responses_lib.add(responses_lib.GET, url, body=rss_body, status=200)

    import requests
    ctx = news.get_context("CRYPTO", "Will Bitcoin ETF be approved?", requests.Session())

    # Should find BTC-related headlines, not Arsenal
    assert "Bitcoin" in ctx or "bitcoin" in ctx.lower()
    assert "Arsenal" not in ctx


@responses_lib.activate
def test_news_returns_empty_string_on_failure():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    from data.enrichment.news import CATEGORY_FEEDS
    for url in CATEGORY_FEEDS.get("CRYPTO", []):
        responses_lib.add(responses_lib.GET, url, body=Exception("timeout"))

    import requests
    ctx = news.get_context("CRYPTO", "Will Bitcoin hit 100k?", requests.Session())
    assert ctx == ""


@responses_lib.activate
def test_news_deduplicates_similar_headlines():
    from data.enrichment.cache import _cache
    _cache._store.clear()

    duplicate_rss = """<?xml version="1.0"?>
<rss><channel>
  <item><title>Bitcoin price surges above key level</title><pubDate>Thu, 10 Apr 2026 10:00:00 +0000</pubDate></item>
  <item><title>Bitcoin price surges above key level</title><pubDate>Thu, 10 Apr 2026 09:00:00 +0000</pubDate></item>
  <item><title>Bitcoin price surges above key level today</title><pubDate>Thu, 10 Apr 2026 08:00:00 +0000</pubDate></item>
</channel></rss>"""

    from data.enrichment.news import CATEGORY_FEEDS, get_headlines
    for url in CATEGORY_FEEDS.get("CRYPTO", []):
        responses_lib.add(responses_lib.GET, url, body=duplicate_rss.encode(), status=200)

    import requests
    headlines = get_headlines("CRYPTO", "Bitcoin price", requests.Session(), max_headlines=5)
    # All start with same 40 chars — should deduplicate
    assert len(headlines) <= 2
