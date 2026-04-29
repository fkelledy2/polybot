# tests/unit/test_risk_manager.py
# Risk manager guards — these block or permit real money decisions.

import pytest
from unittest.mock import MagicMock
from risk.manager import RiskManager
from config import STARTING_BALANCE, DAILY_LOSS_LIMIT


def make_signal(edge=0.20, confidence="medium"):
    sig = MagicMock()
    sig.edge = edge
    sig.confidence = confidence
    return sig


class TestCanTrade:
    def setup_method(self):
        self.rm = RiskManager(starting_balance=STARTING_BALANCE)

    def test_fresh_state_allows_trade(self):
        ok, reason = self.rm.can_trade(STARTING_BALANCE, make_signal())
        assert ok is True
        assert reason == ""

    def test_daily_loss_limit_blocks_trade(self):
        # Push balance down past daily loss threshold
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT + 1
        reduced = STARTING_BALANCE - loss
        ok, reason = self.rm.can_trade(reduced, make_signal())
        assert ok is False
        assert self.rm.is_halted is True

    def test_daily_loss_limit_reason_message(self):
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT + 1
        reduced = STARTING_BALANCE - loss
        ok, reason = self.rm.can_trade(reduced, make_signal())
        assert "limit" in reason.lower() or "halted" in reason.lower()

    def test_halted_flag_set_after_loss(self):
        loss = STARTING_BALANCE * DAILY_LOSS_LIMIT + 1
        self.rm.can_trade(STARTING_BALANCE - loss, make_signal())
        assert self.rm.is_halted is True

    def test_low_confidence_signal_blocked(self):
        ok, reason = self.rm.can_trade(STARTING_BALANCE, make_signal(confidence="low"))
        assert ok is False
        assert "confidence" in reason.lower()

    def test_edge_too_small_blocked(self):
        ok, reason = self.rm.can_trade(STARTING_BALANCE, make_signal(edge=0.05))
        assert ok is False
        assert "edge" in reason.lower()

    def test_negative_large_edge_allowed(self):
        # Negative edge means NO direction — abs is what matters
        ok, _ = self.rm.can_trade(STARTING_BALANCE, make_signal(edge=-0.20))
        assert ok is True

    def test_increments_trades_today_on_approval(self):
        before = self.rm.trades_today
        self.rm.can_trade(STARTING_BALANCE, make_signal())
        assert self.rm.trades_today == before + 1

    def test_does_not_increment_on_rejection(self):
        before = self.rm.trades_today
        self.rm.can_trade(STARTING_BALANCE, make_signal(confidence="low"))
        assert self.rm.trades_today == before

    def test_returns_tuple(self):
        result = self.rm.can_trade(STARTING_BALANCE, make_signal())
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestStatusReport:
    def test_returns_string(self):
        rm = RiskManager(starting_balance=STARTING_BALANCE)
        report = rm.status_report(STARTING_BALANCE)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_shows_active_when_not_halted(self):
        rm = RiskManager(starting_balance=STARTING_BALANCE)
        report = rm.status_report(STARTING_BALANCE)
        assert "ACTIVE" in report

    def test_shows_halted_when_halted(self):
        rm = RiskManager(starting_balance=STARTING_BALANCE)
        rm.is_halted = True
        report = rm.status_report(STARTING_BALANCE)
        assert "HALTED" in report

    def test_daily_reset_unhalt(self):
        from datetime import date, timedelta
        rm = RiskManager(starting_balance=STARTING_BALANCE)
        rm.is_halted = True
        rm.current_day = date.today() - timedelta(days=1)  # yesterday
        # Calling status_report should trigger _check_new_day and unhalt
        rm.status_report(STARTING_BALANCE)
        assert rm.is_halted is False
