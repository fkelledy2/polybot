# risk/manager.py
# ─────────────────────────────────────────────────────────────
# The risk manager is the bot's safety layer.
# Before any trade is placed, it checks whether we're still
# within safe limits. It also monitors for daily loss limits.
# ─────────────────────────────────────────────────────────────

import logging
from datetime import datetime, date
from config import DAILY_LOSS_LIMIT, STARTING_BALANCE

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Enforces trading risk limits.
    
    Think of this as the circuit breaker — it stops the bot
    from blowing up the account on a bad day.
    """

    def __init__(self, starting_balance: float = STARTING_BALANCE):
        self.starting_balance = starting_balance
        self.day_start_balance: float = starting_balance
        self.current_day: date = date.today()
        self.trades_today: int = 0
        self.is_halted: bool = False

    def _check_new_day(self, current_balance: float):
        """Reset daily tracking if it's a new calendar day."""
        today = date.today()
        if today != self.current_day:
            logger.info(f"New day ({today}) — resetting daily risk counters")
            self.current_day = today
            self.day_start_balance = current_balance
            self.trades_today = 0
            self.is_halted = False  # Unhalt at start of new day

    def check_daily_loss_limit(self, current_balance: float) -> bool:
        """
        Check if we've lost too much today.
        
        Returns True if we're safe to trade, False if halted.
        """
        self._check_new_day(current_balance)

        daily_loss = (self.day_start_balance - current_balance) / self.day_start_balance
        limit = DAILY_LOSS_LIMIT

        if daily_loss >= limit:
            if not self.is_halted:
                logger.warning(
                    f"🛑 DAILY LOSS LIMIT HIT: Down {daily_loss:.1%} today "
                    f"(limit: {limit:.1%}). Halting trading until tomorrow."
                )
                self.is_halted = True
            return False

        return True

    def can_trade(self, current_balance: float, signal) -> tuple[bool, str]:
        """
        Main gate: should we allow this trade?
        
        Returns (True, "") if OK, or (False, reason_string) if blocked.
        """
        # 1. Daily halt?
        if not self.check_daily_loss_limit(current_balance):
            return False, "Daily loss limit reached — halted until tomorrow"

        # 2. Balance too low?
        min_balance = self.starting_balance * 0.20   # Stop if down 80%
        if current_balance < min_balance:
            return False, f"Balance too low (${current_balance:.2f}) — minimum is ${min_balance:.2f}"

        # 3. Confidence too low?
        if signal.confidence == "low":
            return False, "Signal confidence is 'low' — skipping"

        # 4. Edge too small?
        abs_edge = abs(signal.edge)
        if abs_edge < 0.08:     # Hard floor regardless of config
            return False, f"Edge too small ({abs_edge:.1%}) — minimum 8%"

        # All checks passed
        self.trades_today += 1
        return True, ""

    def status_report(self, current_balance: float) -> str:
        """Return a one-line risk status string."""
        self._check_new_day(current_balance)
        daily_pnl = current_balance - self.day_start_balance
        total_pnl_pct = (current_balance - self.starting_balance) / self.starting_balance
        status = "🛑 HALTED" if self.is_halted else "✅ ACTIVE"
        return (
            f"Risk: {status} | "
            f"Today PnL: ${daily_pnl:+.2f} | "
            f"Total: {total_pnl_pct:+.1%} | "
            f"Trades today: {self.trades_today}"
        )
