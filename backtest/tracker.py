# backtest/tracker.py
# ─────────────────────────────────────────────────────────────
# Forward prediction tracker.
# Every live scan, all Claude signals are logged with their
# prices. When a market resolves, the outcome is recorded
# and accuracy is calculated automatically.
#
# This builds a growing ground-truth dataset from live trading.
# ─────────────────────────────────────────────────────────────

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from config import TRADES_DB

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL,
    yes_price   REAL NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_market ON price_history (market_id, recorded_at);

CREATE TABLE IF NOT EXISTS predictions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id             TEXT NOT NULL,
    question              TEXT,
    scan_timestamp        TEXT,
    market_yes_price      REAL,
    claude_yes_prob       REAL,
    edge                  REAL,
    direction             TEXT,
    confidence            TEXT,
    should_trade          INTEGER,
    wallet_alignment      INTEGER,
    resolved_yes          INTEGER DEFAULT NULL,
    outcome_correct       INTEGER DEFAULT NULL,
    pnl_simulated         REAL    DEFAULT NULL,
    resolved_at           TEXT    DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_pred_market ON predictions (market_id);
CREATE INDEX IF NOT EXISTS idx_pred_resolved ON predictions (resolved_yes);
"""


def init_tracker() -> None:
    """Create the predictions table if it doesn't exist."""
    conn = sqlite3.connect(TRADES_DB)
    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    conn.close()
    logger.debug("Prediction tracker initialised")


def record_prices(markets: list[dict]) -> None:
    """Record current YES prices for all markets. Called each scan for momentum tracking."""
    if not markets:
        return
    ts = datetime.now().isoformat()
    rows = [
        (m["market_id"], m["yes"], ts)
        for m in markets
        if m.get("market_id") and m.get("yes") is not None
    ]
    if not rows:
        return
    conn = sqlite3.connect(TRADES_DB)
    conn.executemany(
        "INSERT INTO price_history (market_id, yes_price, recorded_at) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def get_price_velocities(market_ids: list[str]) -> dict[str, float]:
    """
    Return 24h price change (current - 24h_ago) for each market as a decimal.
    Only markets with data in both windows are included.
    """
    if not market_ids:
        return {}

    now = datetime.now()
    cutoff_low  = (now - timedelta(hours=26)).isoformat()
    cutoff_high = (now - timedelta(hours=22)).isoformat()

    conn = sqlite3.connect(TRADES_DB)
    c = conn.cursor()
    result = {}

    for mid in market_ids:
        c.execute(
            "SELECT yes_price FROM price_history WHERE market_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (mid,),
        )
        row = c.fetchone()
        if not row:
            continue
        current_price = row[0]

        c.execute(
            """SELECT yes_price FROM price_history
               WHERE market_id = ? AND recorded_at BETWEEN ? AND ?
               ORDER BY recorded_at ASC LIMIT 1""",
            (mid, cutoff_low, cutoff_high),
        )
        row = c.fetchone()
        if not row:
            continue

        result[mid] = round(current_price - row[0], 4)

    conn.close()
    return result


def prune_price_history(days: int = 7) -> None:
    """Remove price history older than `days` days to keep the DB small."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(TRADES_DB)
    conn.execute("DELETE FROM price_history WHERE recorded_at < ?", (cutoff,))
    conn.commit()
    conn.close()


def log_signals(signals: list) -> None:
    """
    Save all Claude signals from a scan to the predictions table.
    Skips markets we've already predicted (deduplicates by market_id).
    """
    if not signals:
        return

    conn = sqlite3.connect(TRADES_DB)
    c = conn.cursor()
    ts = datetime.now().isoformat()
    saved = 0

    for s in signals:
        # Only keep the first prediction per market
        c.execute("SELECT id FROM predictions WHERE market_id = ?", (s.market_id,))
        if c.fetchone():
            continue

        c.execute("""
            INSERT INTO predictions
            (market_id, question, scan_timestamp, market_yes_price,
             claude_yes_prob, edge, direction, confidence,
             should_trade, wallet_alignment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s.market_id,
            s.question,
            ts,
            s.market_yes_price,
            s.claude_yes_probability,
            s.edge,
            s.direction,
            s.confidence,
            int(s.should_trade),
            int(s.wallet_alignment),
        ))
        saved += 1

    conn.commit()
    conn.close()
    if saved:
        logger.debug(f"Logged {saved} new predictions")


def resolve_market(market_id: str, resolved_yes: bool) -> int:
    """
    Record the outcome for a market and calculate simulated PnL.
    Returns number of predictions updated.
    """
    conn = sqlite3.connect(TRADES_DB)
    c = conn.cursor()

    c.execute("""
        SELECT id, direction, market_yes_price, should_trade
        FROM predictions
        WHERE market_id = ? AND resolved_yes IS NULL
    """, (market_id,))
    rows = c.fetchall()

    updated = 0
    for row_id, direction, entry_price, should_trade in rows:
        correct = (direction == "YES" and resolved_yes) or \
                  (direction == "NO"  and not resolved_yes)

        pnl = None
        if should_trade and entry_price is not None:
            if direction == "YES":
                pnl = (1.0 - entry_price) if resolved_yes else -entry_price
            else:
                no_price = 1.0 - entry_price
                pnl = (1.0 - no_price) if not resolved_yes else -no_price

        c.execute("""
            UPDATE predictions
            SET resolved_yes = ?, outcome_correct = ?, pnl_simulated = ?, resolved_at = ?
            WHERE id = ?
        """, (int(resolved_yes), int(correct), pnl, datetime.now().isoformat(), row_id))
        updated += 1

    conn.commit()
    conn.close()
    return updated


def check_and_resolve_markets() -> int:
    """
    Check Polymarket for any predicted markets that have now resolved.
    Call this periodically (e.g., every 10 scans) to auto-score predictions.
    """
    import requests

    conn = sqlite3.connect(TRADES_DB)
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT market_id FROM predictions
        WHERE resolved_yes IS NULL
        LIMIT 50
    """)
    unresolved_ids = [row[0] for row in c.fetchall()]
    conn.close()

    if not unresolved_ids:
        return 0

    session = requests.Session()
    resolved_count = 0

    for market_id in unresolved_ids:
        try:
            resp = session.get(
                f"https://gamma-api.polymarket.com/markets/{market_id}",
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()

            if not data.get("resolved"):
                continue

            import json as _json
            prices_raw = data.get("outcomePrices", '["0.5","0.5"]')
            if isinstance(prices_raw, str):
                prices = _json.loads(prices_raw)
            else:
                prices = prices_raw

            yes_final = float(prices[0])
            if abs(yes_final - 0.5) < 0.3:
                continue  # Not clearly resolved yet

            resolved_yes = yes_final > 0.5
            n = resolve_market(market_id, resolved_yes)
            if n > 0:
                resolved_count += n
                logger.info(
                    f"Resolved market {market_id[:8]}… → "
                    f"{'YES' if resolved_yes else 'NO'} ({n} predictions updated)"
                )

        except Exception as e:
            logger.debug(f"Could not check market {market_id[:8]}: {e}")
            continue

    return resolved_count


def get_tracker_stats() -> dict:
    """Return summary stats from the predictions table."""
    conn = sqlite3.connect(TRADES_DB)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM predictions")
    total = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM predictions WHERE resolved_yes IS NOT NULL")
    resolved = c.fetchone()[0] or 0

    c.execute("""
        SELECT COUNT(*), SUM(outcome_correct)
        FROM predictions
        WHERE resolved_yes IS NOT NULL
    """)
    row = c.fetchone()
    scored, correct_sum = (row[0] or 0), (row[1] or 0)

    c.execute("""
        SELECT COUNT(*), SUM(outcome_correct)
        FROM predictions
        WHERE resolved_yes IS NOT NULL AND should_trade = 1
    """)
    row2 = c.fetchone()
    traded_scored, traded_correct = (row2[0] or 0), (row2[1] or 0)

    c.execute("""
        SELECT COUNT(*), COALESCE(SUM(pnl_simulated), 0)
        FROM predictions
        WHERE resolved_yes IS NOT NULL AND should_trade = 1
    """)
    row3 = c.fetchone()
    traded_total, total_pnl = (row3[0] or 0), float(row3[1] or 0)

    conn.close()

    return {
        "total_predictions":   total,
        "resolved":            resolved,
        "directional_accuracy": round(correct_sum / scored, 4) if scored > 0 else None,
        "traded_accuracy":     round(traded_correct / traded_scored, 4) if traded_scored > 0 else None,
        "simulated_pnl":       round(total_pnl, 4),
        "traded_resolved":     traded_total,
    }


def get_recent_predictions(limit: int = 50) -> list[dict]:
    """Return recent predictions for display in the UI."""
    conn = sqlite3.connect(TRADES_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, market_id, question, scan_timestamp,
               market_yes_price, claude_yes_prob, edge,
               direction, confidence, should_trade,
               resolved_yes, outcome_correct, pnl_simulated
        FROM predictions
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
