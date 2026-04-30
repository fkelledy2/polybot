# data/clob_stream.py
# ─────────────────────────────────────────────────────────────
# Real-time price feed via Polymarket CLOB WebSocket (S4-1).
# Maintains an in-memory price cache updated on each event.
# Falls back gracefully if websocket-client is not installed.
# ─────────────────────────────────────────────────────────────

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

_price_cache: dict[str, float] = {}   # market_id → latest YES price
_lock = threading.Lock()
_ws_thread: threading.Thread | None = None

# Updated externally each scan: [(market_id, yes_token_id), ...]
_tracked: list[tuple[str, str]] = []


def get_cached_price(market_id: str) -> float | None:
    with _lock:
        return _price_cache.get(market_id)


def update_subscriptions(markets: list[dict]) -> None:
    """Refresh the list of token IDs to subscribe to."""
    global _tracked
    pairs = []
    for m in markets:
        token_id = m.get("clob_token_id_yes")
        if token_id and m.get("market_id"):
            pairs.append((m["market_id"], token_id))
    with _lock:
        _tracked = pairs


def _run_ws() -> None:
    try:
        import websocket
    except ImportError:
        logger.warning("websocket-client not installed — CLOB stream disabled")
        return

    delay = 5
    while True:
        with _lock:
            token_pairs = list(_tracked)

        if not token_pairs:
            time.sleep(30)
            continue

        try:
            ws = websocket.create_connection(
                "wss://ws-subscriptions-clob.polymarket.com/ws/market",
                timeout=20,
            )
            ws.send(json.dumps({
                "type": "SUBSCRIBE",
                "channel": "PRICE_CHANGE",
                "assets_ids": [tid for _, tid in token_pairs],
            }))

            token_to_market = {tid: mid for mid, tid in token_pairs}
            delay = 5  # Reset backoff on successful connect
            logger.info(f"CLOB WebSocket subscribed to {len(token_pairs)} markets")

            while True:
                raw = ws.recv()
                if not raw:
                    break
                try:
                    events = json.loads(raw)
                    if not isinstance(events, list):
                        events = [events]
                    for ev in events:
                        asset_id  = ev.get("asset_id")
                        price_str = ev.get("price")
                        if asset_id and price_str and asset_id in token_to_market:
                            with _lock:
                                _price_cache[token_to_market[asset_id]] = float(price_str)
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"CLOB WebSocket: {e} — reconnecting in {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, 120)


def start(markets: list[dict]) -> None:
    """Start the WebSocket feed in a daemon thread (idempotent)."""
    global _ws_thread
    update_subscriptions(markets)
    if _ws_thread and _ws_thread.is_alive():
        return
    _ws_thread = threading.Thread(target=_run_ws, daemon=True, name="clob-ws")
    _ws_thread.start()
    logger.info("CLOB WebSocket feed thread started")
