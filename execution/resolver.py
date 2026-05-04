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


def check_stop_losses(paper_trader, markets: list[dict]) -> list[str]:
    """
    Close open positions where the market has moved 2× entry_edge against us (S4-3).
    Uses current market prices from the markets list.
    Returns list of market_ids that were stopped out.
    """
    if not paper_trader.open_positions or not markets:
        return []

    price_map = {m["market_id"]: m.get("yes") for m in markets if m.get("market_id")}
    stopped_ids: list[str] = []

    for market_id, trade in list(paper_trader.open_positions.items()):
        current_yes = price_map.get(market_id)
        if current_yes is None:
            continue

        abs_edge = abs(trade.edge)
        if abs_edge < 0.01:
            continue   # No edge stored — skip stop-loss check

        # How far has the price moved against our position?
        if trade.direction == "YES":
            adverse_move = trade.entry_price - current_yes   # positive = bad for YES
        else:
            # trade.entry_price IS the NO price (1 - YES_at_entry)
            current_no = 1.0 - current_yes
            adverse_move = trade.entry_price - current_no    # positive = NO price fell

        if adverse_move >= 2.0 * abs_edge:
            # Stop-loss always closes at a loss. resolved_yes must produce won=False:
            # won = (dir=="YES" and resolved_yes) or (dir=="NO" and not resolved_yes)
            # For YES position: resolved_yes=False → won=False ✓
            # For NO  position: resolved_yes=True  → won=False ✓
            resolved_yes = trade.direction == "NO"
            result = paper_trader.close_trade(
                market_id,
                resolved_yes=resolved_yes,
                exit_price=current_yes if trade.direction == "YES" else (1.0 - current_yes),
            )
            if result:
                stopped_ids.append(market_id)
                logger.warning(
                    f"🛑 Stop-loss: closing {trade.direction} "
                    f"'{trade.question[:40]}…' — "
                    f"moved {adverse_move:+.0%} against position "
                    f"(threshold: {2*abs_edge:.0%})"
                )

    return stopped_ids
