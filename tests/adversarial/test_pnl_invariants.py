"""
PnL Accounting Invariants
=========================
The bot's only job is to make money. These tests enforce the accounting
identities that must hold for reported performance to be trustworthy.

The core invariant:
  STARTING_BALANCE == current_balance + net_open_cost - sum(closed_pnl)

Any deviation means the system is lying about its performance.
"""
import pytest
from config import STARTING_BALANCE, MAX_POSITION_PCT
from signals.claude_signal import TradeSignal


def make_signal(market_id="m1", yes_price=0.40, claude_prob=0.65,
                confidence="medium", direction=None):
    edge = claude_prob - yes_price
    if direction is None:
        direction = "YES" if edge >= 0 else "NO"
    return TradeSignal(
        market_id=market_id,
        question=f"Will {market_id} resolve?",
        market_yes_price=yes_price,
        claude_yes_probability=claude_prob,
        edge=edge,
        direction=direction,
        confidence=confidence,
        reasoning="test",
        wallet_alignment=False,
        should_trade=True,
    )


class TestAccountingIdentity:
    """balance + open_cost == STARTING_BALANCE + sum(closed_pnl)"""

    def _check_identity(self, pt):
        """Assert the core accounting invariant holds."""
        import db, sqlite3
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute("SELECT COALESCE(SUM(pnl), 0) AS total FROM trades WHERE status IN ('won','lost')")
        closed_pnl = float(c.fetchone()["total"])
        c.execute("SELECT COALESCE(SUM(size_usd), 0) AS total FROM trades WHERE status='open'")
        open_cost = float(c.fetchone()["total"])
        conn.close()

        expected_balance = STARTING_BALANCE + closed_pnl - open_cost
        assert pt.balance == pytest.approx(expected_balance, abs=0.01), (
            f"Accounting identity violated: "
            f"balance={pt.balance:.4f} expected={expected_balance:.4f} "
            f"(closed_pnl={closed_pnl:.4f}, open_cost={open_cost:.4f})"
        )

    def test_identity_holds_after_single_win(self, paper_trader):
        sig = make_signal()
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=True)
        self._check_identity(paper_trader)

    def test_identity_holds_after_single_loss(self, paper_trader):
        sig = make_signal()
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=False)
        self._check_identity(paper_trader)

    def test_identity_holds_with_open_positions(self, paper_trader):
        for i in range(3):
            paper_trader.place_trade(make_signal(f"m{i}"))
        self._check_identity(paper_trader)

    def test_identity_holds_after_mix_of_results(self, paper_trader):
        for i in range(5):
            paper_trader.place_trade(make_signal(f"m{i}"))
        # Win first two, lose next two, leave one open
        paper_trader.close_trade("m0", resolved_yes=True)
        paper_trader.close_trade("m1", resolved_yes=True)
        paper_trader.close_trade("m2", resolved_yes=False)
        paper_trader.close_trade("m3", resolved_yes=False)
        self._check_identity(paper_trader)

    def test_identity_holds_after_no_trade_win(self, paper_trader):
        sig = make_signal(yes_price=0.70, claude_prob=0.45, direction="NO")
        sig.edge = -0.25
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=False)  # NO wins
        self._check_identity(paper_trader)

    def test_portfolio_value_unchanged_by_opening_trade(self, paper_trader):
        """Opening a trade shifts cash→position but portfolio total must not change."""
        sig = make_signal()
        before = paper_trader.portfolio_value
        paper_trader.place_trade(sig)
        assert paper_trader.portfolio_value == pytest.approx(before, abs=0.01)

    def test_portfolio_value_decreases_on_loss(self, paper_trader):
        sig = make_signal()
        initial_pv = paper_trader.portfolio_value
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=False)
        assert paper_trader.portfolio_value < initial_pv

    def test_portfolio_value_increases_on_win(self, paper_trader):
        sig = make_signal()
        initial_pv = paper_trader.portfolio_value
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=True)
        assert paper_trader.portfolio_value > initial_pv


class TestPnlMagnitude:
    """The size of the PnL must make mathematical sense."""

    def test_yes_win_pnl_equals_shares_minus_cost(self, paper_trader):
        sig = make_signal(yes_price=0.40)
        trade = paper_trader.place_trade(sig)
        result = paper_trader.close_trade(sig.market_id, resolved_yes=True)
        # pnl = shares * 1.0 - size_usd
        expected = trade.shares * 1.0 - trade.size_usd
        assert result.pnl == pytest.approx(expected, rel=1e-4)

    def test_yes_loss_pnl_equals_negative_cost(self, paper_trader):
        sig = make_signal(yes_price=0.40)
        trade = paper_trader.place_trade(sig)
        result = paper_trader.close_trade(sig.market_id, resolved_yes=False)
        # pnl = shares * 0.0 - size_usd = -size_usd
        assert result.pnl == pytest.approx(-trade.size_usd, rel=1e-4)

    def test_no_win_pnl_positive(self, paper_trader):
        sig = make_signal(yes_price=0.70, claude_prob=0.45, direction="NO")
        sig.edge = -0.25
        trade = paper_trader.place_trade(sig)
        result = paper_trader.close_trade(sig.market_id, resolved_yes=False)
        assert result.pnl > 0

    def test_no_loss_pnl_equals_negative_cost(self, paper_trader):
        sig = make_signal(yes_price=0.70, claude_prob=0.45, direction="NO")
        sig.edge = -0.25
        trade = paper_trader.place_trade(sig)
        result = paper_trader.close_trade(sig.market_id, resolved_yes=True)  # YES wins = NO loses
        assert result.pnl == pytest.approx(-trade.size_usd, rel=1e-4)

    def test_higher_entry_price_gives_lower_pnl_on_yes_win(self, paper_trader):
        """YES at 80¢ wins $20 per $100 invested; YES at 20¢ wins $400 per $100."""
        import db
        sig_cheap = make_signal("cheap", yes_price=0.20, claude_prob=0.45)
        sig_expensive = make_signal("expensive", yes_price=0.80, claude_prob=0.95)

        paper_trader.place_trade(sig_cheap)
        paper_trader.place_trade(sig_expensive)

        r_cheap = paper_trader.close_trade("cheap", resolved_yes=True)
        r_expensive = paper_trader.close_trade("expensive", resolved_yes=True)

        # Same SIZE but cheap entry → more shares → more profit on win
        if abs(r_cheap.size_usd - r_expensive.size_usd) < 1.0:  # similar size
            assert r_cheap.pnl > r_expensive.pnl

    def test_lost_trade_pnl_stored_in_db_as_negative(self, paper_trader, tmp_db):
        import sqlite3
        sig = make_signal()
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=False)
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT pnl FROM trades WHERE market_id=?",
                           (sig.market_id,)).fetchone()
        conn.close()
        assert row[0] < 0

    def test_cumulative_pnl_across_multiple_trades(self, paper_trader):
        """Sum of individual PnLs must match balance delta."""
        sigs = [make_signal(f"m{i}") for i in range(4)]
        for s in sigs:
            paper_trader.place_trade(s)

        outcomes = [True, True, False, False]
        pnls = []
        for s, won in zip(sigs, outcomes):
            r = paper_trader.close_trade(s.market_id, resolved_yes=won)
            pnls.append(r.pnl)

        balance_delta = paper_trader.balance - STARTING_BALANCE
        assert sum(pnls) == pytest.approx(balance_delta, abs=0.01)


class TestStopLossAccounting:
    """Stop-loss closes must produce negative PnL and correct balance changes."""

    def test_stop_loss_pnl_is_negative(self, paper_trader):
        from execution.resolver import check_stop_losses
        # YES=0.50, claude=0.65 → edge=0.15, threshold=0.30
        # Stop fires when YES drops to 0.50 - 0.30 = 0.20; use 0.15 (well past)
        sig = make_signal(yes_price=0.50, claude_prob=0.65)
        paper_trader.place_trade(sig)
        markets = [{"market_id": sig.market_id, "yes": 0.15}]
        stopped = check_stop_losses(paper_trader, markets)
        assert len(stopped) == 1, "Stop-loss must fire when price crashes past threshold"
        import db
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute("SELECT pnl FROM trades WHERE market_id=?", (sig.market_id,))
        pnl = c.fetchone()["pnl"]
        conn.close()
        assert pnl < 0

    def test_stop_loss_accounting_identity_holds(self, paper_trader):
        from execution.resolver import check_stop_losses
        sig = make_signal(yes_price=0.50, claude_prob=0.65)
        paper_trader.place_trade(sig)
        markets = [{"market_id": sig.market_id, "yes": 0.15}]
        check_stop_losses(paper_trader, markets)

        import db
        conn = db.get_connection()
        c = db.get_cursor(conn)
        c.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status IN ('won','lost')")
        closed_pnl = float(c.fetchone()[0])
        conn.close()
        expected = STARTING_BALANCE + closed_pnl
        assert paper_trader.balance == pytest.approx(expected, abs=0.01)
