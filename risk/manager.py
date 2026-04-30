# risk/manager.py
# ─────────────────────────────────────────────────────────────
# Risk manager: circuit-breaker, cluster exposure limits (S4-2).
# ─────────────────────────────────────────────────────────────

import logging
from datetime import datetime, date
from config import DAILY_LOSS_LIMIT, STARTING_BALANCE

logger = logging.getLogger(__name__)

MAX_CLUSTER_EXPOSURE = 0.15   # Max 15% of bankroll in one topic cluster


class RiskManager:
    def __init__(self, starting_balance: float = STARTING_BALANCE):
        self.starting_balance    = starting_balance
        self.day_start_balance   = starting_balance
        self.current_day         = date.today()
        self.trades_today        = 0
        self.is_halted           = False
        # Cluster assignments updated each scan: {market_id: cluster_id}
        self.clusters: dict[str, int] = {}

    def update_clusters(self, clusters: dict[str, int]) -> None:
        self.clusters = clusters

    def _check_new_day(self, current_balance: float):
        today = date.today()
        if today != self.current_day:
            logger.info(f"New day ({today}) — resetting daily risk counters")
            self.current_day      = today
            self.day_start_balance = current_balance
            self.trades_today     = 0
            self.is_halted        = False

    def check_daily_loss_limit(self, current_balance: float) -> bool:
        self._check_new_day(current_balance)
        daily_loss = (self.day_start_balance - current_balance) / self.day_start_balance
        if daily_loss >= DAILY_LOSS_LIMIT:
            if not self.is_halted:
                logger.warning(
                    f"🛑 DAILY LOSS LIMIT HIT: Down {daily_loss:.1%} today "
                    f"(limit: {DAILY_LOSS_LIMIT:.1%}). Halting until tomorrow."
                )
                self.is_halted = True
            return False
        return True

    def _cluster_exposure(self, open_positions: dict, cluster_id: int,
                          portfolio_value: float) -> float:
        """Return fraction of portfolio already committed to this cluster."""
        if cluster_id < 0 or portfolio_value <= 0:
            return 0.0
        total = sum(
            t.size_usd
            for mid, t in open_positions.items()
            if self.clusters.get(mid) == cluster_id
        )
        return total / portfolio_value

    def can_trade(self, current_balance: float, signal,
                  open_positions: dict = None, portfolio_value: float = None) -> tuple[bool, str]:
        if not self.check_daily_loss_limit(current_balance):
            return False, "Daily loss limit reached — halted until tomorrow"

        min_balance = self.starting_balance * 0.20
        if current_balance < min_balance:
            return False, f"Balance too low (${current_balance:.2f})"

        if signal.confidence == "low":
            return False, "Signal confidence is 'low' — skipping"

        abs_edge = abs(signal.edge)
        if abs_edge < 0.08:
            return False, f"Edge too small ({abs_edge:.1%})"

        # Cluster exposure check (S4-2)
        if open_positions and portfolio_value:
            cluster_id = self.clusters.get(signal.market_id, -1)
            if cluster_id >= 0:
                exposure = self._cluster_exposure(open_positions, cluster_id, portfolio_value)
                if exposure >= MAX_CLUSTER_EXPOSURE:
                    return False, (
                        f"Cluster {cluster_id} exposure {exposure:.1%} ≥ "
                        f"{MAX_CLUSTER_EXPOSURE:.0%} limit"
                    )

        self.trades_today += 1
        return True, ""

    def status_report(self, current_balance: float) -> str:
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
