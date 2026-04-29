# tests/integration/test_tracker.py
# Forward prediction tracker: log, resolve, and score predictions.

import pytest
from unittest.mock import MagicMock
from backtest.tracker import init_tracker, log_signals, resolve_market, get_tracker_stats


def make_signal(market_id="m1", direction="YES", yes_prob=0.65, mkt_price=0.45,
                confidence="medium", should_trade=True):
    s = MagicMock()
    s.market_id = market_id
    s.question = f"Will something happen? ({market_id})"
    s.direction = direction
    s.claude_yes_probability = yes_prob
    s.market_yes_price = mkt_price
    s.edge = yes_prob - mkt_price
    s.confidence = confidence
    s.should_trade = should_trade
    s.wallet_alignment = False
    return s


@pytest.fixture(autouse=True)
def setup_tracker(tmp_db):
    init_tracker()


class TestLogSignals:
    def test_logs_new_signal(self, tmp_db):
        log_signals([make_signal("m1")])
        stats = get_tracker_stats()
        assert stats["total_predictions"] == 1

    def test_deduplicates_by_market_id(self, tmp_db):
        log_signals([make_signal("m1")])
        log_signals([make_signal("m1")])  # second call same market
        stats = get_tracker_stats()
        assert stats["total_predictions"] == 1

    def test_logs_multiple_different_markets(self, tmp_db):
        log_signals([make_signal("m1"), make_signal("m2"), make_signal("m3")])
        stats = get_tracker_stats()
        assert stats["total_predictions"] == 3

    def test_empty_list_safe(self, tmp_db):
        log_signals([])
        stats = get_tracker_stats()
        assert stats["total_predictions"] == 0


class TestResolveMarket:
    def test_correct_yes_prediction(self, tmp_db):
        log_signals([make_signal("m1", direction="YES")])
        n = resolve_market("m1", resolved_yes=True)
        assert n == 1
        stats = get_tracker_stats()
        assert stats["resolved"] == 1
        assert stats["directional_accuracy"] == 1.0

    def test_wrong_yes_prediction(self, tmp_db):
        log_signals([make_signal("m1", direction="YES")])
        resolve_market("m1", resolved_yes=False)
        stats = get_tracker_stats()
        assert stats["directional_accuracy"] == 0.0

    def test_correct_no_prediction(self, tmp_db):
        log_signals([make_signal("m1", direction="NO", yes_prob=0.30, mkt_price=0.60)])
        resolve_market("m1", resolved_yes=False)
        stats = get_tracker_stats()
        assert stats["directional_accuracy"] == 1.0

    def test_simulated_pnl_yes_win(self, tmp_db):
        # YES bet at market price 0.45, YES wins → pnl = 1 - 0.45 = 0.55
        log_signals([make_signal("m1", direction="YES", mkt_price=0.45)])
        resolve_market("m1", resolved_yes=True)
        stats = get_tracker_stats()
        assert stats["simulated_pnl"] == pytest.approx(0.55, abs=0.01)

    def test_simulated_pnl_yes_loss(self, tmp_db):
        # YES bet at market price 0.45, YES loses → pnl = -0.45
        log_signals([make_signal("m1", direction="YES", mkt_price=0.45)])
        resolve_market("m1", resolved_yes=False)
        stats = get_tracker_stats()
        assert stats["simulated_pnl"] == pytest.approx(-0.45, abs=0.01)

    def test_simulated_pnl_no_win(self, tmp_db):
        # NO bet: no_price = 1 - 0.70 = 0.30, NO wins → pnl = 1 - 0.30 = 0.70
        log_signals([make_signal("m1", direction="NO", yes_prob=0.30, mkt_price=0.70)])
        resolve_market("m1", resolved_yes=False)
        stats = get_tracker_stats()
        assert stats["simulated_pnl"] == pytest.approx(0.70, abs=0.01)

    def test_resolve_nonexistent_market_returns_zero(self, tmp_db):
        n = resolve_market("ghost_market", resolved_yes=True)
        assert n == 0

    def test_resolve_already_resolved_not_double_counted(self, tmp_db):
        log_signals([make_signal("m1")])
        resolve_market("m1", resolved_yes=True)
        resolve_market("m1", resolved_yes=True)  # second call
        stats = get_tracker_stats()
        assert stats["resolved"] == 1  # still only 1


class TestGetTrackerStats:
    def test_no_data_returns_defaults(self, tmp_db):
        stats = get_tracker_stats()
        assert stats["total_predictions"] == 0
        assert stats["resolved"] == 0
        assert stats["directional_accuracy"] is None
        assert stats["traded_accuracy"] is None
        assert stats["simulated_pnl"] == 0.0

    def test_mixed_results(self, tmp_db):
        log_signals([make_signal("m1"), make_signal("m2"), make_signal("m3")])
        resolve_market("m1", resolved_yes=True)   # direction=YES, correct
        resolve_market("m2", resolved_yes=False)  # direction=YES, wrong
        # m3 stays unresolved
        stats = get_tracker_stats()
        assert stats["total_predictions"] == 3
        assert stats["resolved"] == 2
        assert stats["directional_accuracy"] == pytest.approx(0.5)
