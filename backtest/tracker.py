# backtest/tracker.py
# ─────────────────────────────────────────────────────────────
# Forward prediction tracker.
# Every live scan, all Claude signals are logged with their
# prices. When a market resolves, the outcome is recorded
# and accuracy is calculated automatically.
# ─────────────────────────────────────────────────────────────

import logging
from datetime import datetime, timedelta
from typing import Optional

import db

logger = logging.getLogger(__name__)

_ph = db.placeholder

SCHEMA = db.adapt_schema("""
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
CREATE INDEX IF NOT EXISTS idx_pred_resolved ON predictions (resolved_yes)
""")


def init_tracker() -> None:
    """Create tables if they don't exist."""
    conn = db.get_connection()
    c = db.get_cursor(conn)
    for stmt in SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            c.execute(stmt)
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
    conn = db.get_connection()
    c = db.get_cursor(conn)
    c.executemany(
        f"INSERT INTO price_history (market_id, yes_price, recorded_at) VALUES ({_ph}, {_ph}, {_ph})",
        rows,
    )
    conn.commit()
    conn.close()


def get_price_velocities(market_ids: list[str]) -> dict[str, float]:
    """Return 24h price change (current - 24h_ago) for each market."""
    if not market_ids:
        return {}

    now = datetime.now()
    cutoff_low  = (now - timedelta(hours=26)).isoformat()
    cutoff_high = (now - timedelta(hours=22)).isoformat()

    conn = db.get_connection()
    c = db.get_cursor(conn)
    result = {}

    for mid in market_ids:
        c.execute(
            f"SELECT yes_price FROM price_history WHERE market_id = {_ph} ORDER BY recorded_at DESC LIMIT 1",
            (mid,),
        )
        row = c.fetchone()
        if not row:
            continue
        current_price = row["yes_price"]

        c.execute(
            f"""SELECT yes_price FROM price_history
               WHERE market_id = {_ph} AND recorded_at BETWEEN {_ph} AND {_ph}
               ORDER BY recorded_at ASC LIMIT 1""",
            (mid, cutoff_low, cutoff_high),
        )
        row = c.fetchone()
        if not row:
            continue

        result[mid] = round(current_price - row["yes_price"], 4)

    conn.close()
    return result


def prune_price_history(days: int = 7) -> None:
    """Remove price history older than `days` days."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = db.get_connection()
    c = db.get_cursor(conn)
    c.execute(f"DELETE FROM price_history WHERE recorded_at < {_ph}", (cutoff,))
    conn.commit()
    conn.close()


def log_signals(signals: list) -> None:
    """Save all Claude signals from a scan. Deduplicates by market_id."""
    if not signals:
        return

    conn = db.get_connection()
    c = db.get_cursor(conn)
    ts = datetime.now().isoformat()
    saved = 0

    for s in signals:
        c.execute(f"SELECT id FROM predictions WHERE market_id = {_ph}", (s.market_id,))
        if c.fetchone():
            continue

        c.execute(f"""
            INSERT INTO predictions
            (market_id, question, scan_timestamp, market_yes_price,
             claude_yes_prob, edge, direction, confidence,
             should_trade, wallet_alignment)
            VALUES ({_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph})
        """, (
            s.market_id, s.question, ts,
            s.market_yes_price, s.claude_yes_probability,
            s.edge, s.direction, s.confidence,
            int(s.should_trade), int(s.wallet_alignment),
        ))
        saved += 1

    conn.commit()
    conn.close()
    if saved:
        logger.debug(f"Logged {saved} new predictions")


def resolve_market(market_id: str, resolved_yes: bool) -> int:
    """Record the outcome for a market and calculate simulated PnL."""
    conn = db.get_connection()
    c = db.get_cursor(conn)

    c.execute(f"""
        SELECT id, direction, market_yes_price, should_trade
        FROM predictions
        WHERE market_id = {_ph} AND resolved_yes IS NULL
    """, (market_id,))
    rows = c.fetchall()

    updated = 0
    for row in rows:
        row_id      = row["id"]
        direction   = row["direction"]
        entry_price = row["market_yes_price"]
        should_trade = row["should_trade"]

        correct = (direction == "YES" and resolved_yes) or \
                  (direction == "NO"  and not resolved_yes)

        pnl = None
        if should_trade and entry_price is not None:
            if direction == "YES":
                pnl = (1.0 - entry_price) if resolved_yes else -entry_price
            else:
                no_price = 1.0 - entry_price
                pnl = (1.0 - no_price) if not resolved_yes else -no_price

        c.execute(f"""
            UPDATE predictions
            SET resolved_yes = {_ph}, outcome_correct = {_ph},
                pnl_simulated = {_ph}, resolved_at = {_ph}
            WHERE id = {_ph}
        """, (int(resolved_yes), int(correct), pnl, datetime.now().isoformat(), row_id))
        updated += 1

    conn.commit()
    conn.close()
    return updated


def check_and_resolve_markets() -> int:
    """Check Polymarket for any predicted markets that have resolved."""
    import requests as req
    import json as _json

    conn = db.get_connection()
    c = db.get_cursor(conn)
    c.execute(f"""
        SELECT DISTINCT market_id FROM predictions
        WHERE resolved_yes IS NULL
        LIMIT 50
    """)
    unresolved_ids = [row["market_id"] for row in c.fetchall()]
    conn.close()

    if not unresolved_ids:
        return 0

    session = req.Session()
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

            prices_raw = data.get("outcomePrices", '["0.5","0.5"]')
            if isinstance(prices_raw, str):
                prices = _json.loads(prices_raw)
            else:
                prices = prices_raw

            yes_final = float(prices[0])
            if abs(yes_final - 0.5) < 0.3:
                continue

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
    conn = db.get_connection()
    c = db.get_cursor(conn)

    c.execute("SELECT COUNT(*) AS n FROM predictions")
    total = c.fetchone()["n"] or 0

    c.execute("SELECT COUNT(*) AS n FROM predictions WHERE resolved_yes IS NOT NULL")
    resolved = c.fetchone()["n"] or 0

    c.execute("""
        SELECT COUNT(*) AS n, SUM(outcome_correct) AS s
        FROM predictions WHERE resolved_yes IS NOT NULL
    """)
    row = c.fetchone()
    scored, correct_sum = (row["n"] or 0), (row["s"] or 0)

    c.execute("""
        SELECT COUNT(*) AS n, SUM(outcome_correct) AS s
        FROM predictions WHERE resolved_yes IS NOT NULL AND should_trade = 1
    """)
    row2 = c.fetchone()
    traded_scored, traded_correct = (row2["n"] or 0), (row2["s"] or 0)

    c.execute("""
        SELECT COUNT(*) AS n, COALESCE(SUM(pnl_simulated), 0) AS total
        FROM predictions WHERE resolved_yes IS NOT NULL AND should_trade = 1
    """)
    row3 = c.fetchone()
    traded_total, total_pnl = (row3["n"] or 0), float(row3["total"] or 0)

    conn.close()

    return {
        "total_predictions":    total,
        "resolved":             resolved,
        "directional_accuracy": round(correct_sum / scored, 4) if scored > 0 else None,
        "traded_accuracy":      round(traded_correct / traded_scored, 4) if traded_scored > 0 else None,
        "simulated_pnl":        round(total_pnl, 4),
        "traded_resolved":      traded_total,
    }


def get_recent_predictions(limit: int = 50) -> list[dict]:
    """Return recent predictions for display in the UI."""
    conn = db.get_connection()
    c = db.get_cursor(conn)
    c.execute(f"""
        SELECT id, market_id, question, scan_timestamp,
               market_yes_price, claude_yes_prob, edge,
               direction, confidence, should_trade,
               resolved_yes, outcome_correct, pnl_simulated
        FROM predictions
        ORDER BY id DESC
        LIMIT {_ph}
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
