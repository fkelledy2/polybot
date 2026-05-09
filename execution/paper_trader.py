# execution/paper_trader.py
# ─────────────────────────────────────────────────────────────
# Paper trading = simulated trading with fake money.
# All trades are recorded to the database (SQLite or Postgres via db.py).
# ─────────────────────────────────────────────────────────────

import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

import db
from config import STARTING_BALANCE, MAX_POSITION_PCT, MAX_OPEN_POSITIONS

KELLY_FRACTION = 0.35
KELLY_MAX_PCT  = MAX_POSITION_PCT

_CONFIDENCE_MULTIPLIER = {"high": 1.0, "medium": 0.75, "low": 0.5}

logger = logging.getLogger(__name__)

_ph = db.placeholder

_SCHEMA = db.adapt_schema("""
    CREATE TABLE IF NOT EXISTS trades (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id   TEXT,
        question    TEXT,
        direction   TEXT,
        entry_price REAL,
        size_usd    REAL,
        shares      REAL,
        timestamp   TEXT,
        status      TEXT DEFAULT 'open',
        exit_price  REAL DEFAULT 0,
        pnl         REAL DEFAULT 0,
        reasoning   TEXT,
        closed_at   TEXT DEFAULT NULL,
        edge        REAL DEFAULT NULL
    )
""")

_BALANCE_SCHEMA = db.adapt_schema("""
    CREATE TABLE IF NOT EXISTS balance_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        balance   REAL,
        event     TEXT
    )
""")


@dataclass
class Trade:
    market_id: str
    question: str
    direction: str
    entry_price: float
    size_usd: float
    shares: float
    timestamp: str
    status: str = "open"
    exit_price: float = 0.0
    pnl: float = 0.0
    reasoning: str = ""
    edge: float = 0.0
    end_date: Optional[str] = None
    trade_id: Optional[int] = None


class PaperTrader:
    def __init__(self):
        self.balance = STARTING_BALANCE
        self.open_positions: dict[str, Trade] = {}
        self._init_db()
        self._load_state()

    def _init_db(self):
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute(_SCHEMA)
        c.execute(_BALANCE_SCHEMA)
        conn.commit()
        # Idempotent column additions for older DBs
        for col_sql in [
            "ALTER TABLE trades ADD COLUMN closed_at TEXT DEFAULT NULL",
            "ALTER TABLE trades ADD COLUMN edge REAL DEFAULT NULL",
            "ALTER TABLE trades ADD COLUMN end_date TEXT DEFAULT NULL",
        ]:
            db.safe_alter(conn, col_sql)
        conn.close()
        logger.debug("Database initialised")

    def _load_state(self):
        conn = db.get_connection()
        c = db.get_cursor(conn)

        c.execute("SELECT balance FROM balance_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            self.balance = row["balance"]
            logger.info(f"Restored balance: ${self.balance:,.2f}")
        else:
            c.execute(
                f"INSERT INTO balance_log (timestamp, balance, event) VALUES ({_ph}, {_ph}, {_ph})",
                (datetime.now().isoformat(), self.balance, "initial_deposit")
            )
            conn.commit()

        c.execute("""
            SELECT id, market_id, question, direction, entry_price, size_usd,
                   shares, timestamp, status, exit_price, pnl, reasoning,
                   COALESCE(edge, 0) AS edge
            FROM trades WHERE status = 'open'
        """)
        for row in c.fetchall():
            trade = Trade(
                trade_id=row["id"],
                market_id=row["market_id"],
                question=row["question"],
                direction=row["direction"],
                entry_price=row["entry_price"],
                size_usd=row["size_usd"],
                shares=row["shares"],
                timestamp=row["timestamp"],
                status=row["status"],
                exit_price=row["exit_price"],
                pnl=row["pnl"],
                reasoning=row["reasoning"] or "",
                edge=row["edge"] or 0.0,
            )
            self.open_positions[trade.market_id] = trade

        conn.close()
        logger.info(f"Loaded {len(self.open_positions)} open positions")

    @property
    def portfolio_value(self) -> float:
        open_cost = sum(t.size_usd for t in self.open_positions.values())
        return self.balance + open_cost

    def _position_size(self, win_prob: float = None, entry_price: float = None,
                       confidence: str = "medium") -> float:
        conf_mult = _CONFIDENCE_MULTIPLIER.get(confidence, 0.75)
        if win_prob is not None and entry_price is not None and entry_price > 0:
            b = (1.0 / entry_price) - 1.0
            if b > 0:
                kelly_full = (win_prob * (b + 1) - 1) / b
                kelly_half = kelly_full * KELLY_FRACTION * conf_mult
                fraction = max(0.0, min(kelly_half, KELLY_MAX_PCT))
                size = round(self.balance * fraction, 2)
                if size > 0:
                    logger.debug(
                        f"Kelly: p={win_prob:.2%} e={entry_price:.2%} "
                        f"b={b:.2f} conf={confidence}({conf_mult}x) → ${size:.2f}"
                    )
                    return size
        # Fallback: Kelly undefined or zero — use hard cap without confidence penalty.
        # conf_mult is an adjustment on positive Kelly, not an additional restriction
        # when we already can't compute a meaningful fraction.
        return round(self.balance * MAX_POSITION_PCT, 2)

    def place_trade(self, signal, end_date: str = None) -> Optional[Trade]:
        if signal.market_id in self.open_positions:
            return None

        if len(self.open_positions) >= MAX_OPEN_POSITIONS:
            logger.warning(f"At max open positions ({MAX_OPEN_POSITIONS}) — skipping")
            return None

        if signal.direction == "YES":
            entry_price = signal.market_yes_price
            win_prob    = signal.claude_yes_probability
        else:
            entry_price = 1 - signal.market_yes_price
            win_prob    = 1 - signal.claude_yes_probability

        size_usd = self._position_size(
            win_prob=win_prob, entry_price=entry_price,
            confidence=getattr(signal, "confidence", "medium"),
        )

        if size_usd > self.balance:
            logger.warning(f"Insufficient balance (${self.balance:.2f}) for ${size_usd:.2f}")
            return None

        shares = size_usd / entry_price
        trade = Trade(
            market_id=signal.market_id,
            question=signal.question,
            direction=signal.direction,
            entry_price=entry_price,
            size_usd=size_usd,
            shares=shares,
            timestamp=datetime.now().isoformat(),
            reasoning=signal.reasoning,
            edge=signal.edge,
            end_date=end_date,
        )

        self.balance -= size_usd

        conn = db.get_connection()
        c = db.get_cursor(conn)

        _insert_vals = (
            trade.market_id, trade.question, trade.direction,
            trade.entry_price, trade.size_usd, trade.shares,
            trade.timestamp, trade.reasoning, trade.edge, trade.end_date,
        )
        if db.IS_POSTGRES:
            c.execute(f"""
                INSERT INTO trades
                (market_id, question, direction, entry_price, size_usd,
                 shares, timestamp, status, reasoning, edge, end_date)
                VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},'open',{_ph},{_ph},{_ph})
                RETURNING id
            """, _insert_vals)
            trade.trade_id = c.fetchone()["id"]
        else:
            c.execute(f"""
                INSERT INTO trades
                (market_id, question, direction, entry_price, size_usd,
                 shares, timestamp, status, reasoning, edge, end_date)
                VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},'open',{_ph},{_ph},{_ph})
            """, _insert_vals)
            trade.trade_id = c.lastrowid

        c.execute(
            f"INSERT INTO balance_log (timestamp, balance, event) VALUES ({_ph}, {_ph}, {_ph})",
            (datetime.now().isoformat(), self.balance, f"trade_open:{trade.trade_id}")
        )
        conn.commit()
        conn.close()

        self.open_positions[signal.market_id] = trade
        logger.info(
            f"📄 PAPER TRADE: {signal.direction} '{signal.question[:50]}…' | "
            f"${size_usd:.2f} @ {entry_price:.2%} | Balance: ${self.balance:,.2f}"
        )
        return trade

    def close_trade(self, market_id: str, resolved_yes: bool,
                    exit_price: float = None) -> Optional[Trade]:
        if market_id not in self.open_positions:
            return None

        trade = self.open_positions.pop(market_id)

        won = (trade.direction == "YES" and resolved_yes) or \
              (trade.direction == "NO" and not resolved_yes)

        if exit_price is None:
            exit_price = 1.0 if won else 0.0

        payout = trade.shares * exit_price
        pnl    = payout - trade.size_usd

        trade.status     = "won" if won else "lost"
        trade.exit_price = exit_price
        trade.pnl        = pnl

        self.balance += payout

        closed_ts = datetime.now().isoformat()
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute(f"""
            UPDATE trades SET status={_ph}, exit_price={_ph},
            pnl={_ph}, closed_at={_ph} WHERE id={_ph}
        """, (trade.status, exit_price, pnl, closed_ts, trade.trade_id))
        c.execute(
            f"INSERT INTO balance_log (timestamp, balance, event) VALUES ({_ph},{_ph},{_ph})",
            (closed_ts, self.balance, f"trade_close:{trade.trade_id}")
        )
        conn.commit()
        conn.close()

        emoji = "✅" if won else "❌"
        logger.info(
            f"{emoji} TRADE CLOSED: {trade.direction} | "
            f"PnL: ${pnl:+.2f} | Balance: ${self.balance:,.2f}"
        )
        return trade

    def print_summary(self):
        conn = db.get_connection()
        c = db.get_cursor(conn)

        c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS total FROM trades WHERE status='won'")
        r = c.fetchone()
        won_count, won_pnl = (r["n"] or 0), float(r["total"] or 0)

        c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS total FROM trades WHERE status='lost'")
        r = c.fetchone()
        lost_count, lost_pnl = (r["n"] or 0), float(r["total"] or 0)

        c.execute("SELECT COUNT(*) AS n FROM trades WHERE status='open'")
        open_count = c.fetchone()["n"] or 0

        conn.close()

        total_closed = won_count + lost_count
        win_rate = won_count / total_closed if total_closed > 0 else 0
        total_pnl = won_pnl + lost_pnl

        print("\n" + "═" * 50)
        print("  PAPER TRADING SUMMARY")
        print("═" * 50)
        print(f"  Current Balance : ${self.balance:>10,.2f}")
        print(f"  Starting Balance: ${STARTING_BALANCE:>10,.2f}")
        print(f"  Total PnL       : ${total_pnl:>+10,.2f}")
        print(f"  Open Positions  : {open_count}")
        print(f"  Won / Lost      : {won_count} / {lost_count}")
        print(f"  Win Rate        : {win_rate:.1%}")
        print("═" * 50 + "\n")
