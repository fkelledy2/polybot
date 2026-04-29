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
    limit: int = 200,
    min_volume: float = 5_000,
) -> list[ResolvedMarket]:
    """
    Fetch recently closed and resolved binary markets.
    Filters to high-volume markets with clear binary outcomes.
    """
    session = requests.Session()
    results = []

    logger.info(f"Fetching up to {limit} resolved markets from Polymarket…")

    try:
        resp = session.get(
            "https://gamma-api.polymarket.com/markets",
            params={
                "closed":    True,
                "limit":     limit,
                "order":     "volume",
                "ascending": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch resolved markets: {e}")
        return []

    logger.info(f"Received {len(raw)} closed markets, filtering…")

    for m in raw:
        try:
            # Volume filter
            volume = float(m.get("volumeNum") or m.get("volume") or 0)
            if volume < min_volume:
                continue

            # Parse outcomePrices to determine winner
            prices_raw = m.get("outcomePrices", '["0.5","0.5"]')
            if isinstance(prices_raw, str):
                try:
                    prices = json.loads(prices_raw)
                except Exception:
                    continue
            else:
                prices = prices_raw

            if len(prices) < 2:
                continue

            yes_final = float(prices[0])
            no_final  = float(prices[1])

            # Use outcomePrices as the resolution signal:
            # near 1 = YES won, near 0 = NO won, ~0.5 = not yet resolved
            if abs(yes_final - 0.5) < 0.35:
                continue  # Unclear outcome, skip

            resolved_yes = yes_final > 0.5

            # Last trade price — may still reflect pre-resolution price for recent markets
            last_price_raw = m.get("lastTradePrice")
            last_price = float(last_price_raw) if last_price_raw else None

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

    logger.info(f"Found {len(results)} usable resolved markets")
    return results
