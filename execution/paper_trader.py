# execution/paper_trader.py
# ─────────────────────────────────────────────────────────────
# Paper trading = simulated trading with fake money.
# All trades are recorded to a SQLite database (trades.db).
# No real transactions happen. No wallet needed.
#
# When you're ready to go live, you'd replace this module with
# a real execution layer that signs and submits Polygon transactions.
# ─────────────────────────────────────────────────────────────

import sqlite3
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from config import STARTING_BALANCE, MAX_POSITION_PCT, MAX_OPEN_POSITIONS, TRADES_DB

# Kelly fraction cap — never bet more than this regardless of Kelly output
KELLY_FRACTION = 0.5   # Half-Kelly (reduces variance)
KELLY_MAX_PCT  = MAX_POSITION_PCT  # Hard cap = same as before

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a single paper trade."""
    market_id: str
    question: str
    direction: str          # "YES" or "NO"
    entry_price: float      # Price paid (0.0 to 1.0)
    size_usd: float         # How many USD worth of contracts
    shares: float           # = size_usd / entry_price
    timestamp: str
    status: str = "open"    # "open", "won", "lost", "cancelled"
    exit_price: float = 0.0
    pnl: float = 0.0
    reasoning: str = ""
    trade_id: Optional[int] = None


class PaperTrader:
    """
    Simulates trade execution without touching real money.
    
    Tracks:
    - Current balance (starts at STARTING_BALANCE)
    - All open positions
    - Full trade history in SQLite
    """

    def __init__(self):
        self.balance = STARTING_BALANCE
        self.open_positions: dict[str, Trade] = {}
        self._init_db()
        self._load_state()   # Restore state if we've run before

    def _init_db(self):
        """Create the trades database if it doesn't exist."""
        conn = sqlite3.connect(TRADES_DB)
        c = conn.cursor()
        c.execute("""
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
                closed_at   TEXT DEFAULT NULL
            )
        """)
        # Add closed_at column if upgrading from older DB
        try:
            c.execute("ALTER TABLE trades ADD COLUMN closed_at TEXT DEFAULT NULL")
            conn.commit()
        except Exception:
            pass  # Column already exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS balance_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                balance   REAL,
                event     TEXT
            )
        """)
        conn.commit()
        conn.close()
        logger.debug("Database initialised")

    def _load_state(self):
        """Load current balance and open positions from the database."""
        conn = sqlite3.connect(TRADES_DB)
        c = conn.cursor()

        # Get the latest balance entry
        c.execute("SELECT balance FROM balance_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            self.balance = row[0]
            logger.info(f"Restored balance: ${self.balance:,.2f}")
        else:
            # First run — log the starting balance
            c.execute(
                "INSERT INTO balance_log (timestamp, balance, event) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), self.balance, "initial_deposit")
            )
            conn.commit()

        # Load open positions
        c.execute("SELECT * FROM trades WHERE status = 'open'")
        rows = c.fetchall()
        for row in rows:
            trade = Trade(
                trade_id=row[0], market_id=row[1], question=row[2],
                direction=row[3], entry_price=row[4], size_usd=row[5],
                shares=row[6], timestamp=row[7], status=row[8],
                exit_price=row[9], pnl=row[10], reasoning=row[11]
            )
            self.open_positions[trade.market_id] = trade

        conn.close()
        logger.info(f"Loaded {len(self.open_positions)} open positions")

    @property
    def portfolio_value(self) -> float:
        """Cash balance + cost of all open positions (= total capital deployed)."""
        open_cost = sum(t.size_usd for t in self.open_positions.values())
        return self.balance + open_cost

    def _position_size(self, win_prob: float = None, entry_price: float = None) -> float:
        """
        Kelly Criterion position sizing.

        Full Kelly: f = (p*(b+1) - 1) / b
          where p = estimated win probability, b = net odds paid on a win.

        For binary prediction markets:
          b = (1 / entry_price) - 1   (e.g. entry=0.40 → b=1.5 → 1.5x payout)

        We use half-Kelly to reduce variance, then cap at MAX_POSITION_PCT.
        Falls back to flat MAX_POSITION_PCT when no probability is supplied.
        """
        if win_prob is not None and entry_price is not None and entry_price > 0:
            b = (1.0 / entry_price) - 1.0
            if b > 0:
                kelly_full = (win_prob * (b + 1) - 1) / b
                kelly_half = kelly_full * KELLY_FRACTION
                # Clamp between 0 and the hard cap
                fraction = max(0.0, min(kelly_half, KELLY_MAX_PCT))
                size = round(self.balance * fraction, 2)
                if size > 0:
                    logger.debug(
                        f"Kelly sizing: p={win_prob:.2%} entry={entry_price:.2%} "
                        f"b={b:.2f} → full={kelly_full:.3f} half={kelly_half:.3f} → ${size:.2f}"
                    )
                    return size
        return round(self.balance * MAX_POSITION_PCT, 2)

    def place_trade(self, signal) -> Optional[Trade]:
        """
        Simulate placing a trade based on a TradeSignal.
        
        Checks:
        - Do we have enough balance?
        - Are we already in this market?
        - Are we at the max open positions limit?
        
        If all checks pass, records the trade and deducts from balance.
        """

        # Guard: already in this market
        if signal.market_id in self.open_positions:
            logger.info(f"Already have a position in market {signal.market_id[:8]}... — skipping")
            return None

        # Guard: too many open positions
        if len(self.open_positions) >= MAX_OPEN_POSITIONS:
            logger.warning(f"At max open positions ({MAX_OPEN_POSITIONS}) — skipping")
            return None

        # Determine entry price based on direction
        # If betting YES: we pay the YES price
        # If betting NO:  we pay the NO price (= 1 - YES price)
        if signal.direction == "YES":
            entry_price = signal.market_yes_price
            win_prob = signal.claude_yes_probability
        else:
            entry_price = 1 - signal.market_yes_price  # NO price
            win_prob = 1 - signal.claude_yes_probability

        size_usd = self._position_size(win_prob=win_prob, entry_price=entry_price)

        # Guard: not enough money (re-check after Kelly sizing)
        if size_usd > self.balance:
            logger.warning(f"Insufficient balance (${self.balance:.2f}) for trade size ${size_usd:.2f}")
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
        )

        # Deduct from balance
        self.balance -= size_usd

        # Save to DB
        conn = sqlite3.connect(TRADES_DB)
        c = conn.cursor()
        c.execute("""
            INSERT INTO trades
            (market_id, question, direction, entry_price, size_usd, shares, timestamp, status, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """, (trade.market_id, trade.question, trade.direction,
              trade.entry_price, trade.size_usd, trade.shares,
              trade.timestamp, trade.reasoning))
        trade.trade_id = c.lastrowid

        c.execute(
            "INSERT INTO balance_log (timestamp, balance, event) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), self.balance, f"trade_open:{trade.trade_id}")
        )
        conn.commit()
        conn.close()

        self.open_positions[signal.market_id] = trade

        logger.info(
            f"📄 PAPER TRADE: {signal.direction} on '{signal.question[:50]}...' | "
            f"${size_usd:.2f} @ {entry_price:.2%} | "
            f"Balance: ${self.balance:,.2f}"
        )

        return trade

    def close_trade(self, market_id: str, resolved_yes: bool) -> Optional[Trade]:
        """
        Close an open paper trade when a market resolves.
        
        Args:
            market_id:    The market that resolved
            resolved_yes: True if YES won, False if NO won
        
        Polymarket contracts pay out $1.00 per share if you win, $0 if you lose.
        """
        if market_id not in self.open_positions:
            logger.warning(f"No open position for market {market_id}")
            return None

        trade = self.open_positions.pop(market_id)

        # Did we win?
        won = (trade.direction == "YES" and resolved_yes) or \
              (trade.direction == "NO" and not resolved_yes)

        exit_price = 1.0 if won else 0.0
        payout = trade.shares * exit_price  # $1/share if won, $0 if lost
        pnl = payout - trade.size_usd

        trade.status = "won" if won else "lost"
        trade.exit_price = exit_price
        trade.pnl = pnl

        self.balance += payout

        # Update DB
        closed_ts = datetime.now().isoformat()
        conn = sqlite3.connect(TRADES_DB)
        c = conn.cursor()
        c.execute("""
            UPDATE trades SET status=?, exit_price=?, pnl=?, closed_at=? WHERE id=?
        """, (trade.status, exit_price, pnl, closed_ts, trade.trade_id))
        c.execute(
            "INSERT INTO balance_log (timestamp, balance, event) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), self.balance, f"trade_close:{trade.trade_id}")
        )
        conn.commit()
        conn.close()

        emoji = "✅" if won else "❌"
        logger.info(
            f"{emoji} TRADE CLOSED: {trade.direction} | "
            f"PnL: ${pnl:+.2f} | "
            f"Balance: ${self.balance:,.2f}"
        )

        return trade

    def print_summary(self):
        """Print a human-readable summary of current state."""
        conn = sqlite3.connect(TRADES_DB)
        c = conn.cursor()

        c.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE status='won'")
        won_count, won_pnl = c.fetchone()

        c.execute("SELECT COUNT(*), SUM(pnl) FROM trades WHERE status='lost'")
        lost_count, lost_pnl = c.fetchone()

        c.execute("SELECT COUNT(*) FROM trades WHERE status='open'")
        open_count = c.fetchone()[0]

        conn.close()

        won_count  = won_count  or 0
        lost_count = lost_count or 0
        won_pnl    = won_pnl    or 0.0
        lost_pnl   = lost_pnl   or 0.0

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
