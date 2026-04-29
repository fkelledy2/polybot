# data/enrichment/crypto.py
# ─────────────────────────────────────────────────────────────
# Real-time crypto context for Claude:
#   - BTC / ETH / SOL spot prices + 24h change (CoinGecko free API)
#   - Crypto Fear & Greed Index (alternative.me)
#
# Cache: prices 5 min, Fear&Greed 1 hour
# ─────────────────────────────────────────────────────────────

import logging
import requests
from .cache import _cache

logger = logging.getLogger(__name__)

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin,ethereum,solana,ripple"
    "&vs_currencies=usd"
    "&include_24hr_change=true"
    "&include_market_cap=true"
)
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"


def _fetch_prices(session: requests.Session) -> dict:
    cached = _cache.get("crypto_prices")
    if cached:
        return cached
    try:
        r = session.get(COINGECKO_URL, timeout=8)
        if r.status_code == 200:
            data = r.json()
            _cache.set("crypto_prices", data, ttl=300)   # 5 min
            return data
    except Exception as e:
        logger.debug(f"CoinGecko fetch failed: {e}")
    return {}


def _fetch_fear_greed(session: requests.Session) -> dict:
    cached = _cache.get("fear_greed")
    if cached:
        return cached
    try:
        r = session.get(FEAR_GREED_URL, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [{}])[0]
            _cache.set("fear_greed", data, ttl=3600)    # 1 hour
            return data
    except Exception as e:
        logger.debug(f"Fear & Greed fetch failed: {e}")
    return {}


def _fmt_price(symbol: str, data: dict, coin_id: str) -> str:
    info = data.get(coin_id, {})
    price = info.get("usd")
    change = info.get("usd_24h_change")
    if price is None:
        return ""
    sign = "+" if change and change >= 0 else ""
    chg  = f" ({sign}{change:.1f}%)" if change is not None else ""
    return f"{symbol} ${price:,.0f}{chg}"


def get_context(session: requests.Session = None) -> str:
    """
    Return a one-line context string for CRYPTO markets.
    Example: "BTC $79,240 (+2.3%) | ETH $3,420 (-0.5%) | Fear&Greed: 61/100 (Greed)"
    Returns "" on total failure so Claude prompt is unaffected.
    """
    if session is None:
        session = requests.Session()

    prices    = _fetch_prices(session)
    fear_data = _fetch_fear_greed(session)

    parts = []

    for symbol, coin_id in [("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana")]:
        s = _fmt_price(symbol, prices, coin_id)
        if s:
            parts.append(s)

    if fear_data:
        value      = fear_data.get("value", "?")
        label      = fear_data.get("value_classification", "?").title()
        parts.append(f"Fear&Greed: {value}/100 ({label})")

    return " | ".join(parts)
