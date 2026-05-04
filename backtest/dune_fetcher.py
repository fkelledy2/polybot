# backtest/dune_fetcher.py
# ─────────────────────────────────────────────────────────────
# Fetches resolved Polymarket markets with REAL historical entry
# prices from Dune Analytics (polymarket_polygon schema).
#
# Replaces synthetic price testing with actual avg YES-token price
# from the 3–14 days before each market resolved — a realistic
# entry window for a bot scanning daily.
#
# Setup:
#   1. Free account at dune.com → Settings → API Keys → New key
#   2. Add to .env:  DUNE_API_KEY=your_key_here
#   3. Run optimizer:  python backtest/optimizer.py --dune
#
# Free tier: 2,500 credits/month. One backtest run ≈ 5–15 credits.
# ─────────────────────────────────────────────────────────────

import logging
import time
from typing import Optional

import requests

from backtest.fetcher import ResolvedMarket

logger = logging.getLogger(__name__)

DUNE_API_BASE = "https://api.dune.com/api/v1"

# ── SQL ───────────────────────────────────────────────────────
# DuneSQL (Trino dialect). Returns one row per resolved market
# with the average YES-token price from 3–14 days before closure.
# The 3-day gap avoids price distortion as the market converges
# to its resolution value in the final hours.
#
# Partition filters (block_month) are mandatory for performance
# on the market_trades table.

_SQL = """
WITH resolved AS (
    SELECT condition_id, question, outcome, resolved_on_timestamp, market_end_time
    FROM polymarket_polygon.market_details
    WHERE token_outcome = 'Yes'
      AND outcome IN ('yes', 'no')
      AND resolved_on_timestamp >= NOW() - INTERVAL '{lookback_days}' DAY
      AND resolved_on_timestamp IS NOT NULL
),
entry_prices AS (
    SELECT
        LOWER('0x' || to_hex(t.condition_id)) AS condition_id,
        AVG(t.price)                           AS avg_yes_price,
        APPROX_PERCENTILE(t.price, 0.5)        AS median_yes_price,
        SUM(t.amount)                          AS volume_usd,
        COUNT(*)                               AS trade_count
    FROM polymarket_polygon.market_trades t
    INNER JOIN resolved r
           ON LOWER('0x' || to_hex(t.condition_id)) = LOWER(r.condition_id)
    WHERE t.token_outcome = 'Yes'
      AND t.block_month  >= DATE_TRUNC('month', NOW() - INTERVAL '{lookback_days}' DAY)
      AND t.block_time   >= r.resolved_on_timestamp - INTERVAL '14' DAY
      AND t.block_time   <  r.resolved_on_timestamp - INTERVAL '3'  DAY
    GROUP BY 1
    HAVING COUNT(*) >= 5
       AND AVG(t.price) BETWEEN 0.03 AND 0.97
)
SELECT
    r.condition_id,
    r.question,
    (r.outcome = 'yes') AS resolved_yes,
    r.market_end_time,
    ep.avg_yes_price    AS entry_price,
    ep.median_yes_price,
    ep.volume_usd,
    ep.trade_count
FROM resolved r
JOIN entry_prices ep ON LOWER(r.condition_id) = ep.condition_id
ORDER BY ep.volume_usd DESC
LIMIT {limit}
"""


class DuneFetcher:
    """Thin async-poll client for Dune Analytics ad-hoc SQL."""

    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers.update({"X-Dune-Api-Key": api_key})

    def _execute(self, sql: str) -> str:
        """Submit SQL. Returns execution_id."""
        resp = self._session.post(
            f"{DUNE_API_BASE}/sql/execute",
            json={"sql": sql},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Dune execute failed {resp.status_code}: {resp.text[:300]}")
        return resp.json()["execution_id"]

    def _poll(self, exec_id: str, max_wait: int = 300) -> list[dict]:
        """Poll until complete. Returns list of row dicts."""
        deadline = time.time() + max_wait
        delay = 5
        while time.time() < deadline:
            resp = self._session.get(
                f"{DUNE_API_BASE}/execution/{exec_id}/results",
                timeout=30,
            )
            if not resp.ok:
                raise RuntimeError(f"Dune poll failed {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            state = data.get("state", "")

            if state == "QUERY_STATE_COMPLETED":
                rows = data.get("result", {}).get("rows", [])
                logger.info(f"Dune query complete: {len(rows)} rows")
                return rows
            if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                err = data.get("error", state)
                raise RuntimeError(f"Dune query {state}: {err}")

            logger.info(f"Dune state: {state} — waiting {delay}s…")
            time.sleep(delay)
            delay = min(delay * 1.5, 30)

        raise TimeoutError(f"Dune query timed out after {max_wait}s")

    def fetch_resolved_markets(
        self,
        lookback_days: int = 180,
        limit: int = 1000,
    ) -> list[ResolvedMarket]:
        """
        Returns resolved markets with real historical entry prices.

        entry_price = avg YES-token price 3–14 days before resolution.
        This is a realistic proxy for what the bot would have paid.
        """
        sql = _SQL.format(lookback_days=lookback_days, limit=limit)
        logger.info(
            f"Submitting Dune query: {lookback_days}d lookback, up to {limit} markets…"
        )
        exec_id = self._execute(sql)
        logger.info(f"Execution ID: {exec_id}")
        rows = self._poll(exec_id)

        markets: list[ResolvedMarket] = []
        skipped = 0
        for row in rows:
            try:
                markets.append(ResolvedMarket(
                    market_id=row["condition_id"],
                    question=row["question"],
                    resolved_yes=bool(row["resolved_yes"]),
                    last_price=float(row["entry_price"]),   # real price, not synthetic
                    volume_usd=float(row.get("volume_usd") or 0),
                    end_date=str(row.get("market_end_time") or ""),
                ))
            except (KeyError, TypeError, ValueError) as e:
                skipped += 1
                logger.debug(f"Skipping row: {e}")

        if skipped:
            logger.warning(f"Skipped {skipped} malformed rows from Dune")
        logger.info(
            f"Dune: {len(markets)} markets with real entry prices "
            f"(lookback={lookback_days}d)"
        )
        return markets
