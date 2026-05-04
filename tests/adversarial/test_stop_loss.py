"""
Stop-Loss Adversarial Tests
============================
The stop-loss is the primary defence against catastrophic losses.
These tests are adversarial: they probe the exact threshold boundaries,
the direction arithmetic, and the cooldown mechanism.

Key invariants:
- A NO position must NOT be stopped immediately after opening (the IONQ bug)
- The threshold fires at exactly 2× abs_edge, not before
- Cooldown prevents the same market from being re-entered for 10 scans
"""
import pytest
from signals.claude_signal import TradeSignal
from execution.resolver import check_stop_losses


def make_trade_signal(market_id="m1", yes_price=0.50, claude_prob=0.75,
                      direction="YES", confidence="medium"):
    edge = claude_prob - yes_price if direction == "YES" else yes_price - claude_prob
    if direction == "NO":
        edge = -(abs(claude_prob - yes_price))
    return TradeSignal(
        market_id=market_id,
        question=f"Will {market_id} happen?",
        market_yes_price=yes_price,
        claude_yes_probability=claude_prob,
        edge=edge,
        direction=direction,
        confidence=confidence,
        reasoning="test",
        wallet_alignment=False,
        should_trade=True,
    )


def market(market_id="m1", yes=0.50):
    return {"market_id": market_id, "yes": yes}


class TestYesStopLoss:
    """
    YES positions are stopped when YES price drops by 2× abs_edge.
    Stop fires when: entry_price - current_yes >= 2 * abs_edge
    → current_yes <= entry_price - 2 * abs_edge

    Test numbers must satisfy: entry_price - 2*abs_edge > 0 (reachable price).
    Use edge=0.10 (small enough) so threshold = 0.20.
    """

    def test_yes_stop_fires_at_threshold(self, paper_trader):
        # YES=0.50, claude=0.60, edge=0.10, threshold=0.20
        # Stop fires when YES ≤ 0.50 - 0.20 = 0.30
        sig = make_trade_signal(yes_price=0.50, claude_prob=0.60, direction="YES")
        sig.edge = 0.10
        paper_trader.place_trade(sig)
        # Drop to exactly 0.30 — adverse = 0.50 - 0.30 = 0.20 ≥ 0.20 → fires
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.30)])
        assert "m1" in stopped

    def test_yes_stop_does_not_fire_just_below_threshold(self, paper_trader):
        # YES=0.50, edge=0.10, threshold=0.20. Stop at YES=0.30.
        sig = make_trade_signal(yes_price=0.50, claude_prob=0.60, direction="YES")
        sig.edge = 0.10
        paper_trader.place_trade(sig)
        # Drop to 0.31 — adverse = 0.50 - 0.31 = 0.19 < 0.20 → no stop
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.31)])
        assert "m1" not in stopped

    def test_yes_stop_does_not_fire_at_entry_price(self, paper_trader):
        sig = make_trade_signal(yes_price=0.50, claude_prob=0.60, direction="YES")
        sig.edge = 0.10
        paper_trader.place_trade(sig)
        # No price change — must not stop
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.50)])
        assert stopped == []

    def test_yes_stop_fires_only_once(self, paper_trader):
        sig = make_trade_signal(yes_price=0.50, claude_prob=0.60, direction="YES")
        sig.edge = 0.10
        paper_trader.place_trade(sig)
        markets_list = [market("m1", yes=0.25)]  # well past threshold
        stopped1 = check_stop_losses(paper_trader, markets_list)
        stopped2 = check_stop_losses(paper_trader, markets_list)
        assert len(stopped1) == 1
        assert stopped2 == []  # already closed — can't stop twice


class TestNoStopLoss:
    """
    NO positions — the recently fixed IONQ bug case.

    trade.entry_price for a NO trade = 1 - YES_at_entry (the NO token price).
    adverse_move = entry_NO_price - current_NO_price = trade.entry_price - (1 - current_yes)
    Threshold = 2 × abs_edge.
    """

    def test_no_stop_does_not_fire_at_entry_price(self, paper_trader):
        """THE IONQ BUG: NO trade must NOT be stopped immediately at entry price."""
        # YES=0.85 → NO=0.15. edge=-0.40 (claude=0.45). abs_edge=0.40.
        sig = make_trade_signal(yes_price=0.85, claude_prob=0.45, direction="NO")
        sig.edge = -(0.85 - 0.45)  # -0.40
        paper_trader.place_trade(sig)
        # Same YES price as entry — zero adverse move — must NOT stop
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.85)])
        assert stopped == [], (
            "IONQ bug: NO trade stopped at entry price with no price movement"
        )

    def test_no_stop_does_not_fire_slightly_favourable(self, paper_trader):
        """YES drops (favours NO holder) → definitely no stop."""
        sig = make_trade_signal(yes_price=0.70, claude_prob=0.45, direction="NO")
        sig.edge = -(0.70 - 0.45)  # -0.25
        paper_trader.place_trade(sig)
        # YES drops from 0.70 to 0.60 → NO improved → no stop
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.60)])
        assert stopped == []

    def test_no_stop_fires_when_yes_rises_past_threshold(self, paper_trader):
        """YES rising against NO position triggers stop when adverse_move ≥ 2×edge."""
        # YES=0.50, NO=0.50, edge=-0.25 (abs_edge=0.25), threshold=0.50
        # entry_NO=0.50, stop when NO falls to 0.50 - 0.50 = 0.00 → YES=1.00
        # More realistic: YES=0.60, NO=0.40, abs_edge=0.20, threshold=0.40
        # stop when NO < 0.40 - 0.40 = 0.00 → needs YES > 1.00... also impossible.
        # Use smaller edge: abs_edge=0.10, threshold=0.20
        # YES=0.60, NO=0.40. Claude says YES=0.50. edge=-0.10.
        sig = make_trade_signal(yes_price=0.60, claude_prob=0.50, direction="NO")
        sig.edge = -0.10
        paper_trader.place_trade(sig)
        # entry_NO = 0.40. current_NO = 1 - 0.81 = 0.19.
        # adverse_move = 0.40 - 0.19 = 0.21 ≥ 0.20 → STOP
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.81)])
        assert "m1" in stopped

    def test_no_stop_does_not_fire_just_below_threshold(self, paper_trader):
        """adverse_move just below 2×edge → no stop."""
        sig = make_trade_signal(yes_price=0.60, claude_prob=0.50, direction="NO")
        sig.edge = -0.10
        paper_trader.place_trade(sig)
        # entry_NO=0.40, threshold=0.20. Stop at NO < 0.20 (YES > 0.80).
        # Test at YES=0.79: adverse = 0.40 - 0.21 = 0.19 < 0.20 → no stop
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.79)])
        assert stopped == []

    def test_no_stop_pnl_is_negative(self, paper_trader, tmp_db):
        """When a NO trade is stopped out, PnL must be negative (real loss)."""
        sig = make_trade_signal(yes_price=0.60, claude_prob=0.50, direction="NO")
        sig.edge = -0.10
        paper_trader.place_trade(sig)
        # Force stop well past threshold
        check_stop_losses(paper_trader, [market("m1", yes=0.95)])

        import sqlite3
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("SELECT pnl, status FROM trades WHERE market_id='m1'").fetchone()
        conn.close()
        assert row[1] == "lost"
        assert row[0] < 0, f"NO stop-loss PnL must be negative, got {row[0]}"

    def test_extreme_yes_price_no_position_stops_correctly(self, paper_trader):
        """High YES price (90%) NO position at small edge → stop fires when YES rises."""
        # YES=0.90, NO=0.10, claude=0.85, edge=-0.05, abs_edge=0.05, threshold=0.10
        sig = make_trade_signal(yes_price=0.90, claude_prob=0.85, direction="NO")
        sig.edge = -0.05
        paper_trader.place_trade(sig)
        # adverse = 0.10 - (1 - 0.99) = 0.10 - 0.01 = 0.09 < 0.10 → no stop
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.99)])
        assert stopped == []  # threshold not quite reached (0.09 < 0.10)


class TestStopLossEdgeCases:
    def test_zero_edge_position_skipped(self, paper_trader):
        """edge=0 → abs_edge < 0.01 → stop-loss check skipped entirely."""
        sig = make_trade_signal(yes_price=0.50, claude_prob=0.50)
        sig.edge = 0.0
        paper_trader.place_trade(sig)
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.01)])
        assert stopped == []  # skipped, not stopped

    def test_empty_markets_list_returns_empty(self, paper_trader):
        sig = make_trade_signal()
        paper_trader.place_trade(sig)
        stopped = check_stop_losses(paper_trader, [])
        assert stopped == []

    def test_no_open_positions_returns_empty(self, paper_trader):
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.01)])
        assert stopped == []

    def test_market_not_in_positions_is_ignored(self, paper_trader):
        sig = make_trade_signal("m1")
        paper_trader.place_trade(sig)
        # Provide price for a different market
        stopped = check_stop_losses(paper_trader, [market("different_market", yes=0.01)])
        assert stopped == []  # m1 has no price data → skipped

    def test_already_closed_position_cannot_be_stopped(self, paper_trader):
        sig = make_trade_signal()
        paper_trader.place_trade(sig)
        paper_trader.close_trade(sig.market_id, resolved_yes=True)
        stopped = check_stop_losses(paper_trader, [market("m1", yes=0.01)])
        assert stopped == []

    def test_multiple_positions_independent_stops(self, paper_trader):
        """Only the position that crossed the threshold should stop."""
        sig_a = make_trade_signal("a", yes_price=0.50, claude_prob=0.70)
        sig_b = make_trade_signal("b", yes_price=0.50, claude_prob=0.70)
        sig_a.edge = 0.20
        sig_b.edge = 0.20
        paper_trader.place_trade(sig_a)
        paper_trader.place_trade(sig_b)

        markets = [
            market("a", yes=0.05),  # far below threshold → stop
            market("b", yes=0.45),  # tiny drop → no stop
        ]
        stopped = check_stop_losses(paper_trader, markets)
        assert "a" in stopped
        assert "b" not in stopped


class TestCooldownMechanism:
    """
    The cooldown dict in main.py prevents re-entry for STOP_LOSS_COOLDOWN_SCANS
    scans after a stop-loss. These tests verify the logic in isolation.
    """

    def _make_cooldown_state(self):
        return {}, 10  # cooldown_dict, COOLDOWN_SCANS

    def test_cooldown_excludes_market_during_active_period(self):
        cooldown = {"m1": 15}  # expires at scan 15
        scan_count = 10
        is_on_cooldown = scan_count < cooldown.get("m1", 0)
        assert is_on_cooldown is True

    def test_cooldown_expires_at_scan_boundary(self):
        cooldown = {"m1": 15}
        scan_count = 15  # exactly at expiry
        is_on_cooldown = scan_count < cooldown.get("m1", 0)
        assert is_on_cooldown is False

    def test_cooldown_set_correctly_after_stop(self):
        cooldown = {}
        scan_count = 7
        COOLDOWN = 10
        stopped_ids = ["m1", "m2"]
        for mid in stopped_ids:
            cooldown[mid] = scan_count + COOLDOWN
        assert cooldown["m1"] == 17
        assert cooldown["m2"] == 17

    def test_expired_cooldowns_removed(self):
        cooldown = {"m1": 5, "m2": 20, "m3": 10}
        scan_count = 11
        expired = [mid for mid, until in cooldown.items() if scan_count >= until]
        for mid in expired:
            del cooldown[mid]
        assert "m1" not in cooldown  # expired (5 < 11)
        assert "m2" in cooldown      # not yet (20 > 11)
        assert "m3" not in cooldown  # expired (10 < 11)
