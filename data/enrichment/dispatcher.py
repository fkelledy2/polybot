# data/enrichment/dispatcher.py
# ─────────────────────────────────────────────────────────────
# Routes each market to the right enrichers based on category,
# then formats a compact context string to inject into Claude's
# prompt alongside the market question.
#
# Designed to fail gracefully — any enricher error returns ""
# so the main loop is never blocked.
# ─────────────────────────────────────────────────────────────

import logging
import threading
import requests

from signals.categorizer import get_category_context
from . import crypto, macro, metaculus, news, sports

logger = logging.getLogger(__name__)


def _safe(fn, *args, **kwargs) -> str:
    """Call fn(*args) and return "" on any exception."""
    try:
        return fn(*args, **kwargs) or ""
    except Exception as e:
        logger.debug(f"Enricher {fn.__name__} failed: {e}")
        return ""


def enrich_markets(markets: list[dict]) -> dict[str, str]:
    """
    For each market, build a LIVE CONTEXT string to inject into Claude's prompt.

    Returns: { market_id → context_string }
    Empty string means no enrichment available for that market.

    Fetches shared category-level data once (prices, macro) then adds
    per-market news filtered by relevance to the specific question.
    """
    if not markets:
        return {}

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; polybot/1.0)"

    # ── 1. Determine which categories appear in this batch ────
    categories: dict[str, str] = {}    # market_id → category
    for m in markets:
        cat, _ = get_category_context(m.get("question", ""))
        categories[m["market_id"]] = cat

    unique_cats = set(categories.values())

    # ── 2. Fetch shared context per category in parallel ──────
    shared: dict[str, str] = {}   # category → shared context string

    def _fetch_shared(cat: str) -> None:
        parts = []
        if cat == "CRYPTO":
            ctx = _safe(crypto.get_context, session)
            if ctx:
                parts.append(ctx)
        if cat == "MACRO":
            ctx = _safe(macro.get_context, session)
            if ctx:
                parts.append(ctx)
        if cat == "TECH":
            # Tech benefits from knowing macro risk-on/off backdrop
            ctx = _safe(macro.get_context, session)
            if ctx:
                # Just SPY and VIX are relevant for tech
                relevant = [p for p in ctx.split(" | ")
                            if any(k in p for k in ("S&P", "VIX"))]
                if relevant:
                    parts.append(" | ".join(relevant))
        shared[cat] = " | ".join(parts)

    threads = [threading.Thread(target=_fetch_shared, args=(cat,)) for cat in unique_cats]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=12)

    # ── 3. Per-market: news + sports odds ─────────────────────
    result: dict[str, str] = {}

    for m in markets:
        market_id = m["market_id"]
        question  = m.get("question", "")
        cat       = categories[market_id]

        parts = []

        # Shared price/macro context
        if shared.get(cat):
            parts.append(shared[cat])

        # Per-market news headlines
        news_ctx = _safe(news.get_context, cat, question, session)
        if news_ctx:
            parts.append(f"Headlines: {news_ctx}")

        # Sports betting lines (only if Odds API key is set)
        if cat == "SPORTS":
            odds_ctx = _safe(sports.get_context, question, session)
            if odds_ctx:
                parts.append(odds_ctx)

        # Metaculus expert consensus (best signal for non-market-driven categories)
        if cat in ("POLITICS", "MACRO", "GEO", "TECH"):
            meta_ctx = _safe(metaculus.get_context, question, session)
            if meta_ctx:
                parts.append(meta_ctx)

        result[market_id] = " | ".join(parts)

    found = sum(1 for v in result.values() if v)
    logger.info(f"Enrichment: {found}/{len(markets)} markets enriched")
    return result
