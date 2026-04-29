# data/enrichment/macro.py
# ─────────────────────────────────────────────────────────────
# Real-time macro context for Claude via Yahoo Finance API.
# No API key or extra library required — direct HTTP to the
# same endpoint yfinance uses internally.
#
# Tickers: S&P 500 (^GSPC), VIX (^VIX), 10Y yield (^TNX),
#          Gold (GC=F), DXY Dollar Index (DX-Y.NYB)
# Cache: 10 minutes
# ─────────────────────────────────────────────────────────────

import logging
import requests
from .cache import _cache

logger = logging.getLogger(__name__)

YF_URL   = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=2d"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; polybot/1.0)"}

TICKERS = {
    "^GSPC":    "S&P500",
    "^VIX":     "VIX",
    "^TNX":     "10Y Yield",
    "GC=F":     "Gold",
    "DX-Y.NYB": "DXY",
}


def _fetch_ticker(ticker: str, session: requests.Session) -> dict | None:
    cache_key = f"macro_{ticker}"
    cached = _cache.get(cache_key)
    if cached:
        return cached
    try:
        url = YF_URL.format(ticker=ticker)
        r   = session.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            result = r.json().get("chart", {}).get("result")
            if result:
                meta = result[0].get("meta", {})
                data = {
                    "price":  meta.get("regularMarketPrice"),
                    "prev":   meta.get("chartPreviousClose"),
                    "currency": meta.get("currency", "USD"),
                }
                _cache.set(cache_key, data, ttl=600)   # 10 min
                return data
    except Exception as e:
        logger.debug(f"Yahoo Finance fetch failed for {ticker}: {e}")
    return None


def get_context(session: requests.Session = None) -> str:
    """
    Return a one-line macro context string.
    Example: "S&P500 5,304 (+0.8%) | VIX 18.2 | 10Y Yield 4.32% | Gold $3,220 | DXY 103.1"
    """
    if session is None:
        session = requests.Session()

    parts = []
    for ticker, label in TICKERS.items():
        data = _fetch_ticker(ticker, session)
        if not data or data["price"] is None:
            continue

        price = data["price"]
        prev  = data["prev"]

        if ticker == "^TNX":
            # 10Y yield is expressed as percentage already
            s = f"{label} {price:.2f}%"
        elif ticker == "^VIX":
            s = f"{label} {price:.1f}"
        elif ticker == "DX-Y.NYB":
            s = f"{label} {price:.1f}"
        else:
            change_pct = ((price - prev) / prev * 100) if prev else None
            sign = "+" if change_pct and change_pct >= 0 else ""
            chg  = f" ({sign}{change_pct:.1f}%)" if change_pct is not None else ""
            prefix = "$" if ticker == "GC=F" else ""
            s = f"{label} {prefix}{price:,.0f}{chg}"

        parts.append(s)

    return " | ".join(parts)
