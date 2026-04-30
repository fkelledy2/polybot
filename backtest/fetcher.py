# backtest/fetcher.py
# ─────────────────────────────────────────────────────────────
# Fetches resolved markets from Polymarket for backtesting.
#
# Because the CLOB price-history API returns empty data for
# closed markets, we use two data points:
#   - The market question + outcome (resolved YES or NO)
#   - lastTradePrice if it's still in a meaningful range (0.05–0.95)
#
# We supplement with synthetic price testing to calibrate thresholds.
# ─────────────────────────────────────────────────────────────

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class ResolvedMarket:
    market_id:   str
    question:    str
    resolved_yes: bool           # True = YES won, False = NO won
    last_price:  Optional[float] # Last known YES price (may be near 0/1)
    volume_usd:  float
    end_date:    Optional[str]


def fetch_resolved_markets(
    limit: int = 500,
    min_volume: float = 100,
) -> list[ResolvedMarket]:
    """
    Fetch recently closed and resolved binary markets via pagination.

    min_volume is intentionally low for backtesting — we care about
    signal quality, not liquidity. Volume matters for live trading,
    not for measuring whether Claude's probability estimate was right.
    """
    session = requests.Session()
    results = []
    offset  = 0
    page_size = 200

    logger.info(f"Fetching resolved markets from Polymarket (target: {limit})…")

    while len(results) < limit:
        try:
            resp = session.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "closed":    True,
                    "limit":     page_size,
                    "offset":    offset,
                    "order":     "volume",
                    "ascending": False,
                },
                timeout=15,
            )
            resp.raise_for_status()
            page = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch markets (offset={offset}): {e}")
            break

        if not page:
            break

        logger.info(f"Page offset={offset}: {len(page)} markets received")

        for m in page:
            try:
                volume = float(m.get("volumeNum") or m.get("volume") or 0)
                if volume < min_volume:
                    continue

                prices_raw = m.get("outcomePrices", '["0.5","0.5"]')
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                if len(prices) < 2:
                    continue

                yes_final = float(prices[0])

                # Accept only clearly resolved markets: price near 0 or 1
                if abs(yes_final - 0.5) < 0.40:
                    continue

                resolved_yes  = yes_final > 0.5
                last_price_raw = m.get("lastTradePrice")
                last_price     = float(last_price_raw) if last_price_raw else None

                results.append(ResolvedMarket(
                    market_id=m.get("id", ""),
                    question=m.get("question", "Unknown"),
                    resolved_yes=resolved_yes,
                    last_price=last_price,
                    volume_usd=volume,
                    end_date=m.get("endDate") or m.get("closedTime"),
                ))

            except Exception as e:
                logger.debug(f"Skipping market: {e}")
                continue

            if len(results) >= limit:
                break

        if len(page) < page_size:
            break  # Last page — no more data
        offset += page_size
        time.sleep(0.3)  # Be polite to the API

    logger.info(f"Found {len(results)} usable resolved markets")
    return results
