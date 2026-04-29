# execution/resolver.py
# ─────────────────────────────────────────────────────────────
# Checks open paper-trading positions against the live Polymarket
# API and closes any that have resolved.
#
# Called every N scans from main.py.
# ─────────────────────────────────────────────────────────────

import json
import logging

import requests

logger = logging.getLogger(__name__)


def _fetch_market(session: requests.Session, market_id: str) -> dict | None:
    try:
        resp = session.get(
            f"https://gamma-api.polymarket.com/markets/{market_id}",
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"Could not fetch market {market_id[:8]}: {e}")
    return None


def _parse_outcome(market: dict) -> bool | None:
    """
    Return True if YES won, False if NO won, None if still unresolved.
    Uses outcomePrices: the side near 1.0 is the winner.
    """
    prices_raw = market.get("outcomePrices", '["0.5","0.5"]')
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
        yes_price = float(prices[0])
        if yes_price > 0.85:
            return True
        if yes_price < 0.15:
            return False
    except Exception:
        pass
    return None


def resolve_open_positions(paper_trader) -> int:
    """
    For each open position, query Polymarket to check if the market
    has resolved. Close any that have.

    Returns the number of positions closed.
    """
    if not paper_trader.open_positions:
        return 0

    session = requests.Session()
    closed = 0

    for market_id, trade in list(paper_trader.open_positions.items()):
        market_data = _fetch_market(session, market_id)
        if not market_data:
            continue

        # Market resolved?
        is_resolved = (
            market_data.get("resolved") is True
            or market_data.get("closed") is True
            or market_data.get("umaResolutionStatus") == "resolved"
        )

        if not is_resolved:
            continue

        outcome = _parse_outcome(market_data)
        if outcome is None:
            logger.debug(f"Market {market_id[:8]} closed but outcome unclear — skipping")
            continue

        result = paper_trader.close_trade(market_id, resolved_yes=outcome)
        if result:
            closed += 1
            logger.info(
                f"{'✅' if result.status == 'won' else '❌'} Auto-resolved: "
                f"{trade.direction} on '{trade.question[:50]}…' → "
                f"{'YES' if outcome else 'NO'} won | PnL: ${result.pnl:+.2f}"
            )

    if closed:
        logger.info(f"Resolver closed {closed} position(s). Balance: ${paper_trader.balance:,.2f}")

    return closed
