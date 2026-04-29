# tests/integration/test_paper_trader.py
# Full lifecycle tests against a real (temp) SQLite DB.
# Place → verify DB → close → verify PnL → verify balance.

import sqlite3
import pytest
from config import STARTING_BALANCE, MAX_OPEN_POSITIONS


class TestPlaceTrade:
    def test_balance_decremented_on_open(self, paper_trader, minimal_signal):
        before = paper_trader.balance
        trade = paper_trader.place_trade(minimal_signal)
        assert trade is not None
        assert paper_trader.balance < before
        assert paper_trader.balance == pytest.approx(before - trade.size_usd)

    def test_trade_written_to_db(self, paper_trader, minimal_signal, tmp_db):
        paper_trader.place_trade(minimal_signal)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT * FROM trades WHERE market_id=?",
                           (minimal_signal.market_id,)).fetchone()
        conn.close()
        assert row is not None

    def test_trade_in_open_positions(self, paper_trader, minimal_signal):
        paper_trader.place_trade(minimal_signal)
        assert minimal_signal.market_id in paper_trader.open_positions

    def test_duplicate_market_blocked(self, paper_trader, minimal_signal):
        paper_trader.place_trade(minimal_signal)
        second = paper_trader.place_trade(minimal_signal)
        assert second is None
        assert len(paper_trader.open_positions) == 1

    def test_max_positions_enforced(self, paper_trader, tmp_db):
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
            paper_trader.place_trade(sig)

        # One more should be blocked
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
        assert result is None

    def test_no_direction_uses_no_price(self, paper_trader, no_signal):
        trade = paper_trader.place_trade(no_signal)
        assert trade is not None
        expected_entry = 1 - no_signal.market_yes_price
        assert trade.entry_price == pytest.approx(expected_entry)

    def test_shares_calculated_correctly(self, paper_trader, minimal_signal):
        trade = paper_trader.place_trade(minimal_signal)
        expected_shares = trade.size_usd / trade.entry_price
        assert trade.shares == pytest.approx(expected_shares)


class TestCloseTrade:
    def test_won_trade_credits_full_payout(self, paper_trader, minimal_signal):
        trade = paper_trader.place_trade(minimal_signal)
        balance_after_open = paper_trader.balance
        # YES bet, YES wins
        paper_trader.close_trade(minimal_signal.market_id, resolved_yes=True)
        expected_payout = trade.shares  # $1/share
        assert paper_trader.balance == pytest.approx(balance_after_open + expected_payout)

    def test_lost_trade_no_payout(self, paper_trader, minimal_signal):
        paper_trader.place_trade(minimal_signal)
        balance_after_open = paper_trader.balance
        # YES bet, NO wins → loss
        paper_trader.close_trade(minimal_signal.market_id, resolved_yes=False)
        assert paper_trader.balance == pytest.approx(balance_after_open)  # no payout

    def test_won_pnl_positive(self, paper_trader, minimal_signal):
        trade = paper_trader.place_trade(minimal_signal)
        result = paper_trader.close_trade(minimal_signal.market_id, resolved_yes=True)
        assert result.pnl > 0
        assert result.status == "won"

    def test_lost_pnl_negative(self, paper_trader, minimal_signal):
        trade = paper_trader.place_trade(minimal_signal)
        result = paper_trader.close_trade(minimal_signal.market_id, resolved_yes=False)
        assert result.pnl < 0
        assert result.status == "lost"

    def test_pnl_formula_correct(self, paper_trader, minimal_signal):
        trade = paper_trader.place_trade(minimal_signal)
        result = paper_trader.close_trade(minimal_signal.market_id, resolved_yes=True)
        # pnl = (shares * 1.0) - size_usd
        expected_pnl = trade.shares - trade.size_usd
        assert result.pnl == pytest.approx(expected_pnl)

    def test_no_direction_no_wins(self, paper_trader, no_signal):
        trade = paper_trader.place_trade(no_signal)
        result = paper_trader.close_trade(no_signal.market_id, resolved_yes=False)
        assert result.status == "won"
        assert result.pnl > 0

    def test_no_direction_yes_wins_is_loss(self, paper_trader, no_signal):
        paper_trader.place_trade(no_signal)
        result = paper_trader.close_trade(no_signal.market_id, resolved_yes=True)
        assert result.status == "lost"
        assert result.pnl < 0

    def test_close_removes_from_open_positions(self, paper_trader, minimal_signal):
        paper_trader.place_trade(minimal_signal)
        assert minimal_signal.market_id in paper_trader.open_positions
        paper_trader.close_trade(minimal_signal.market_id, resolved_yes=True)
        assert minimal_signal.market_id not in paper_trader.open_positions

    def test_close_updates_db_status(self, paper_trader, minimal_signal, tmp_db):
        paper_trader.place_trade(minimal_signal)
        paper_trader.close_trade(minimal_signal.market_id, resolved_yes=True)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT status, pnl, closed_at FROM trades WHERE market_id=?",
                           (minimal_signal.market_id,)).fetchone()
        conn.close()
        assert row[0] == "won"
        assert row[1] > 0
        assert row[2] is not None  # closed_at was set

    def test_close_nonexistent_returns_none(self, paper_trader):
        result = paper_trader.close_trade("does-not-exist", resolved_yes=True)
        assert result is None


class TestStateRestoration:
    def test_balance_restored_across_instances(self, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt1 = PaperTrader()
        pt1.place_trade(minimal_signal)
        saved_balance = pt1.balance

        pt2 = PaperTrader()
        assert pt2.balance == pytest.approx(saved_balance)

    def test_open_positions_restored(self, tmp_db, minimal_signal):
        from execution.paper_trader import PaperTrader
        pt1 = PaperTrader()
        pt1.place_trade(minimal_signal)

        pt2 = PaperTrader()
        assert minimal_signal.market_id in pt2.open_positions


class TestPortfolioValue:
    def test_portfolio_value_includes_open_positions(self, paper_trader, minimal_signal):
        before = paper_trader.portfolio_value
        trade = paper_trader.place_trade(minimal_signal)
        after = paper_trader.portfolio_value
        # portfolio_value = balance + open_cost, so placing a trade shouldn't change it
        assert after == pytest.approx(before)

    def test_portfolio_value_decreases_after_loss(self, paper_trader, minimal_signal):
        initial = paper_trader.portfolio_value
        paper_trader.place_trade(minimal_signal)
        paper_trader.close_trade(minimal_signal.market_id, resolved_yes=False)
        assert paper_trader.portfolio_value < initial
