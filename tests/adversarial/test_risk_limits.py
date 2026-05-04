"""
Risk Limit Adversarial Tests
=============================
The risk manager is the last line of defence before money leaves the account.
These tests deliberately construct scenarios that should trigger each limit,
then verify trading is blocked. One slipped limit = real financial loss.
"""
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock
from risk.manager import RiskManager, MAX_CLUSTER_EXPOSURE
from config import STARTING_BALANCE, DAILY_LOSS_LIMIT, MAX_POSITION_PCT, MIN_EDGE_TO_TRADE


def make_signal(edge=0.20, confidence="medium", market_id="m1"):
    sig = MagicMock()
    sig.edge = edge
    sig.confidence = confidence
    sig.market_id = market_id
    return sig


class TestDailyLossLimit:
    """10% daily loss = halt. This is non-negotiable."""

    def setup_method(self):
        self.rm = RiskManager(starting_balance=STARTING_BALANCE)

    def test_exactly_at_limit_triggers_halt(self):
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT
        ok, _ = self.rm.can_trade(STARTING_BALANCE - loss, make_signal())
        assert ok is False
        assert self.rm.is_halted is True

    def test_one_cent_above_limit_does_not_halt(self):
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT - 0.01
        ok, _ = self.rm.can_trade(STARTING_BALANCE - loss, make_signal())
        assert ok is True
        assert self.rm.is_halted is False

    def test_halted_bot_stays_halted_across_calls(self):
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT + 1
        self.rm.can_trade(STARTING_BALANCE - loss, make_signal())
        assert self.rm.is_halted is True
        # Second call with same (bad) balance — still blocked
        ok, _ = self.rm.can_trade(STARTING_BALANCE - loss, make_signal())
        assert ok is False

    def test_halt_resets_on_new_day(self):
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT + 1
        self.rm.can_trade(STARTING_BALANCE - loss, make_signal())
        assert self.rm.is_halted is True

        # Simulate next day
        self.rm.current_day = date.today() - timedelta(days=1)
        ok, _ = self.rm.can_trade(STARTING_BALANCE, make_signal())
        assert ok is True
        assert self.rm.is_halted is False

    def test_massive_loss_halts(self):
        ok, _ = self.rm.can_trade(0.01, make_signal())
        assert ok is False

    def test_balance_growth_does_not_falsely_halt(self):
        # Balance grew (profitable day) — should never halt
        ok, _ = self.rm.can_trade(STARTING_BALANCE * 1.10, make_signal())
        assert ok is True


class TestBalanceFloor:
    """Below 20% of starting balance, trading is blocked unconditionally."""

    def _make_rm_at_balance(self, current_balance: float) -> RiskManager:
        """Create a risk manager where daily loss check passes at the given balance."""
        rm = RiskManager(starting_balance=STARTING_BALANCE)
        # Set day_start_balance equal to current to avoid daily loss interference
        rm.day_start_balance = current_balance
        return rm

    def test_below_floor_blocks_trade(self):
        floor_minus_one = STARTING_BALANCE * 0.20 - 1.0
        rm = self._make_rm_at_balance(floor_minus_one)
        ok, reason = rm.can_trade(floor_minus_one, make_signal())
        assert ok is False
        assert "balance" in reason.lower() or "low" in reason.lower()

    def test_exactly_at_floor_allows_trade(self):
        floor = STARTING_BALANCE * 0.20
        rm = self._make_rm_at_balance(floor)
        ok, _ = rm.can_trade(floor, make_signal())
        assert ok is True


class TestEdgeMinimum:
    """Risk manager must use MIN_EDGE_TO_TRADE from config, not a hardcoded value."""

    def setup_method(self):
        self.rm = RiskManager(starting_balance=STARTING_BALANCE)

    def test_exactly_at_min_edge_allows_trade(self):
        ok, _ = self.rm.can_trade(STARTING_BALANCE, make_signal(edge=MIN_EDGE_TO_TRADE))
        assert ok is True

    def test_one_basis_point_below_min_edge_blocks(self):
        ok, reason = self.rm.can_trade(
            STARTING_BALANCE, make_signal(edge=MIN_EDGE_TO_TRADE - 0.001)
        )
        assert ok is False
        assert "edge" in reason.lower()

    def test_negative_large_edge_allowed(self):
        # -0.30 edge → NO direction, abs = 0.30 > MIN_EDGE_TO_TRADE
        ok, _ = self.rm.can_trade(STARTING_BALANCE, make_signal(edge=-0.30))
        assert ok is True

    def test_tiny_negative_edge_blocked(self):
        ok, _ = self.rm.can_trade(
            STARTING_BALANCE, make_signal(edge=-(MIN_EDGE_TO_TRADE - 0.001))
        )
        assert ok is False

    def test_risk_manager_threshold_matches_signal_builder(self):
        """
        The signal builder marks should_trade=True at edge=MIN_EDGE_TO_TRADE.
        The risk manager must approve that same trade — they can't disagree.
        """
        from signals.claude_signal import _build_signal
        prob = 0.40 + MIN_EDGE_TO_TRADE
        sig = _build_signal(
            {"market_id": "m1", "question": "Will X?", "yes": 0.40},
            {"market_id": "m1", "yes_probability": prob, "confidence": "medium", "reasoning": "test"},
            [],
        )
        assert sig.should_trade is True, "Signal builder must approve this trade"
        ok, reason = self.rm.can_trade(STARTING_BALANCE, sig)
        assert ok is True, (
            f"Risk manager blocked a trade the signal builder approved (edge={sig.edge:.3f}): {reason}"
        )


class TestClusterExposure:
    """No single topic cluster should exceed 15% of portfolio."""

    def setup_method(self):
        self.rm = RiskManager(starting_balance=STARTING_BALANCE)

    def _make_position(self, market_id, size_usd):
        pos = MagicMock()
        pos.size_usd = size_usd
        return pos

    def test_cluster_under_limit_allows_trade(self):
        cluster_id = 1
        self.rm.clusters = {"existing": cluster_id, "m1": cluster_id}
        open_positions = {"existing": self._make_position("existing", STARTING_BALANCE * 0.10)}
        sig = make_signal(market_id="m1")
        ok, _ = self.rm.can_trade(
            STARTING_BALANCE, sig,
            open_positions=open_positions,
            portfolio_value=STARTING_BALANCE,
        )
        assert ok is True  # 10% < 15%

    def test_cluster_at_limit_blocks_trade(self):
        cluster_id = 1
        self.rm.clusters = {"existing": cluster_id, "m1": cluster_id}
        open_positions = {"existing": self._make_position("existing", STARTING_BALANCE * 0.15)}
        sig = make_signal(market_id="m1")
        ok, reason = self.rm.can_trade(
            STARTING_BALANCE, sig,
            open_positions=open_positions,
            portfolio_value=STARTING_BALANCE,
        )
        assert ok is False
        assert "cluster" in reason.lower()

    def test_different_clusters_dont_interfere(self):
        self.rm.clusters = {"existing": 0, "m1": 1}  # different clusters
        open_positions = {"existing": self._make_position("existing", STARTING_BALANCE * 0.15)}
        sig = make_signal(market_id="m1")
        ok, _ = self.rm.can_trade(
            STARTING_BALANCE, sig,
            open_positions=open_positions,
            portfolio_value=STARTING_BALANCE,
        )
        assert ok is True  # different cluster, not affected


class TestMaxOpenPositions:
    """The 11th position must be blocked at the paper trader level."""

    def test_max_positions_enforced(self, paper_trader):
        from config import MAX_OPEN_POSITIONS
        from signals.claude_signal import TradeSignal

        for i in range(MAX_OPEN_POSITIONS):
            sig = TradeSignal(
                market_id=f"mkt_{i}",
                question=f"Q{i}?",
                market_yes_price=0.40,
                claude_yes_probability=0.60,
                edge=0.20,
                direction="YES",
                confidence="medium",
                reasoning="test",
                wallet_alignment=False,
                should_trade=True,
            )
            result = paper_trader.place_trade(sig)
            assert result is not None, f"Trade {i} should have been placed"

        overflow = TradeSignal(
            market_id="overflow",
            question="Overflow?",
            market_yes_price=0.40,
            claude_yes_probability=0.60,
            edge=0.20,
            direction="YES",
            confidence="medium",
            reasoning="test",
            wallet_alignment=False,
            should_trade=True,
        )
        result = paper_trader.place_trade(overflow)
        assert result is None, f"Position #{MAX_OPEN_POSITIONS + 1} must be rejected"
        assert len(paper_trader.open_positions) == MAX_OPEN_POSITIONS

    def test_closing_a_position_allows_new_one(self, paper_trader):
        from config import MAX_OPEN_POSITIONS
        from signals.claude_signal import TradeSignal

        sigs = []
        for i in range(MAX_OPEN_POSITIONS):
            sig = TradeSignal(
                market_id=f"mkt_{i}", question=f"Q{i}?",
                market_yes_price=0.40, claude_yes_probability=0.60,
                edge=0.20, direction="YES", confidence="medium",
                reasoning="test", wallet_alignment=False, should_trade=True,
            )
            paper_trader.place_trade(sig)
            sigs.append(sig)

        paper_trader.close_trade("mkt_0", resolved_yes=True)

        new_sig = TradeSignal(
            market_id="new_market", question="New?",
            market_yes_price=0.40, claude_yes_probability=0.60,
            edge=0.20, direction="YES", confidence="medium",
            reasoning="test", wallet_alignment=False, should_trade=True,
        )
        result = paper_trader.place_trade(new_sig)
        assert result is not None, "Slot freed by close should allow a new position"
