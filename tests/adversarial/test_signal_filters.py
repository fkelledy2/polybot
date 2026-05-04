"""
Signal Filter Adversarial Tests
================================
The signal pipeline is the gate between Claude's analysis and real trades.
Failures here either leak bad trades through (money lost on bad bets) or
block good trades (money left on the table). Both are costly.

These tests probe exact boundary conditions in every filter layer.
"""
import pytest
from unittest.mock import patch
from signals.claude_signal import _build_signal, TradeSignal
from config import (
    MIN_EDGE_TO_TRADE, MIN_EDGE_TO_TRADE_EXTREME,
    EXTREME_PRICE_THRESHOLD, MIN_ENTRY_PROBABILITY,
)


def mkt(yes=0.40, market_id="m1", question="Will X happen?", **kwargs):
    m = {"market_id": market_id, "question": question, "yes": yes}
    m.update(kwargs)
    return m


def result(yes_prob=0.60, confidence="medium", reasoning="Reason"):
    return {"market_id": "m1", "yes_probability": yes_prob,
            "confidence": confidence, "reasoning": reasoning}


class TestEdgeThresholdBoundary:
    """MIN_EDGE_TO_TRADE is the first monetary gate. Must be exact."""

    def test_edge_exactly_at_threshold_should_trade(self):
        prob = 0.40 + MIN_EDGE_TO_TRADE
        sig = _build_signal(mkt(yes=0.40), result(yes_prob=prob), [])
        assert sig.should_trade is True, (
            f"Edge exactly at threshold ({MIN_EDGE_TO_TRADE}) should trade"
        )

    def test_edge_one_basis_point_below_threshold_should_not_trade(self):
        prob = 0.40 + MIN_EDGE_TO_TRADE - 0.001
        sig = _build_signal(mkt(yes=0.40), result(yes_prob=prob), [])
        assert sig.should_trade is False, (
            f"Edge {MIN_EDGE_TO_TRADE - 0.001:.3f} is below threshold — must not trade"
        )

    def test_zero_edge_should_not_trade(self):
        sig = _build_signal(mkt(yes=0.50), result(yes_prob=0.50), [])
        assert sig.should_trade is False

    def test_large_edge_should_trade(self):
        sig = _build_signal(mkt(yes=0.30), result(yes_prob=0.70), [])
        assert sig.should_trade is True

    def test_negative_edge_no_direction_uses_abs(self):
        # NO bet: market=0.70, claude=0.45 → edge=-0.25, abs=0.25 > threshold
        sig = _build_signal(mkt(yes=0.70), result(yes_prob=0.45), [])
        assert sig.direction == "NO"
        assert sig.should_trade is True


class TestExtremePriceFilter:
    """Markets near 0% or 100% require a higher edge. Easy money is never easy."""

    def test_extreme_low_price_requires_higher_edge(self):
        # YES at 2% (extreme). MIN_EDGE_TO_TRADE < abs(edge) < MIN_EDGE_TO_TRADE_EXTREME
        yes = EXTREME_PRICE_THRESHOLD - 0.005  # e.g. 0.025 → extreme
        mid_edge_prob = yes + (MIN_EDGE_TO_TRADE + MIN_EDGE_TO_TRADE_EXTREME) / 2
        mid_edge_prob = min(mid_edge_prob, 0.99)
        sig = _build_signal(mkt(yes=yes), result(yes_prob=mid_edge_prob), [])
        # Should not trade: edge > MIN_EDGE_TO_TRADE but < MIN_EDGE_TO_TRADE_EXTREME
        assert sig.should_trade is False, (
            f"Extreme price market (YES={yes}) with mid-range edge must not trade "
            f"(needs ≥{MIN_EDGE_TO_TRADE_EXTREME} edge)"
        )

    def test_extreme_high_price_requires_higher_edge(self):
        # YES at 98% (extreme NO bet)
        yes = 1.0 - EXTREME_PRICE_THRESHOLD + 0.005
        # NO edge = mid-range
        mid_edge_prob = yes - (MIN_EDGE_TO_TRADE + MIN_EDGE_TO_TRADE_EXTREME) / 2
        sig = _build_signal(mkt(yes=yes), result(yes_prob=mid_edge_prob), [])
        assert sig.should_trade is False

    def test_extreme_price_with_sufficient_edge_trades(self):
        # Use NO direction at high YES price: YES=0.97, NO=0.03 (extreme zone)
        # NO entry_probability = 0.03 = MIN_ENTRY_PROBABILITY → passes floor check
        # Claude says YES is only 0.65 → edge=-0.32, abs=0.32 > 0.20 (EXTREME threshold)
        yes = 0.97
        prob = 0.65  # claude says YES at 65%, market at 97% → strong NO edge
        sig = _build_signal(mkt(yes=yes), result(yes_prob=prob), [])
        assert sig.direction == "NO"
        assert sig.should_trade is True, (
            f"NO trade at extreme price (YES={yes}) with edge>EXTREME threshold must trade. "
            f"Got: edge={sig.edge:.3f}, entry_prob at NO={1-yes:.3f}"
        )

    def test_normal_price_uses_standard_threshold(self):
        # YES at 40% is not extreme
        yes = 0.40  # > EXTREME_PRICE_THRESHOLD (0.03)
        prob = yes + MIN_EDGE_TO_TRADE + 0.01  # just above standard threshold
        sig = _build_signal(mkt(yes=yes), result(yes_prob=prob), [])
        assert sig.should_trade is True


class TestDisabledCategories:
    """Disabled categories must be blocked regardless of edge or confidence."""

    def test_disabled_crypto_category_blocked(self):
        with patch("signals.claude_signal._DISABLED_CATEGORIES", ["CRYPTO"]):
            sig = _build_signal(
                mkt(question="Will Bitcoin hit $100k?"),
                result(yes_prob=0.90, confidence="high"),
                [],
            )
            assert sig.should_trade is False, (
                "CRYPTO category should be disabled — trade must be blocked"
            )

    def test_disabled_category_blocked_even_with_high_confidence(self):
        with patch("signals.claude_signal._DISABLED_CATEGORIES", ["SPORTS"]):
            sig = _build_signal(
                mkt(question="Will the NFL Super Bowl happen?"),
                result(yes_prob=0.95, confidence="high"),
                [],
            )
            assert sig.should_trade is False

    def test_non_disabled_category_still_trades(self):
        with patch("signals.claude_signal._DISABLED_CATEGORIES", ["CRYPTO"]):
            sig = _build_signal(
                mkt(question="Will interest rates be cut this month?"),
                result(yes_prob=0.75, confidence="medium"),
                [],
            )
            # MACRO is not disabled
            assert sig.should_trade is True


class TestMinEntryProbability:
    """
    Never enter a trade where the ENTRY token price is below MIN_ENTRY_PROBABILITY.
    For YES: entry_probability = market_yes_price.
    For NO:  entry_probability = 1 - market_yes_price.
    """

    def test_yes_entry_below_min_probability_blocked(self):
        # YES direction with market_yes_price = 0.01 < MIN_ENTRY_PROBABILITY (0.03)
        # Claude says 0.30 → strong YES edge, but entry price is too low to trust
        yes = 0.01
        sig = _build_signal(mkt(yes=yes), result(yes_prob=0.30, confidence="high"), [])
        assert sig.should_trade is False, (
            f"YES trade with entry_price={yes} < MIN_ENTRY_PROBABILITY={MIN_ENTRY_PROBABILITY} "
            f"must be blocked"
        )

    def test_no_entry_below_min_probability_blocked(self):
        # NO direction with market_yes_price=0.99 → entry_NO = 0.01 < 0.03
        yes = 0.99
        sig = _build_signal(mkt(yes=yes), result(yes_prob=0.60, confidence="high"), [])
        assert sig.should_trade is False, (
            f"NO trade with entry_NO_price={1-yes} < MIN_ENTRY_PROBABILITY={MIN_ENTRY_PROBABILITY} "
            f"must be blocked"
        )

    def test_at_min_entry_probability_can_trade(self):
        # YES at exactly MIN_ENTRY_PROBABILITY — right on the floor, should pass
        yes = MIN_ENTRY_PROBABILITY  # 0.03
        prob = yes + MIN_EDGE_TO_TRADE + 0.01  # ensure sufficient edge
        # Also need to be outside extreme zone: yes >= EXTREME_PRICE_THRESHOLD = 0.03 ✓
        # But extreme filter requires 20% edge if yes IS in extreme zone
        prob = yes + MIN_EDGE_TO_TRADE_EXTREME + 0.01  # satisfy extreme threshold too
        sig = _build_signal(mkt(yes=yes), result(yes_prob=prob, confidence="high"), [])
        assert sig.should_trade is True


class TestLowConfidenceBlock:
    """Low confidence signals must never trade, regardless of edge."""

    def test_low_confidence_blocked(self):
        # Massive edge but low confidence
        sig = _build_signal(mkt(yes=0.20), result(yes_prob=0.80, confidence="low"), [])
        assert sig.should_trade is False

    def test_medium_confidence_allowed(self):
        sig = _build_signal(mkt(yes=0.40), result(yes_prob=0.65, confidence="medium"), [])
        assert sig.should_trade is True

    def test_high_confidence_allowed(self):
        sig = _build_signal(mkt(yes=0.40), result(yes_prob=0.65, confidence="high"), [])
        assert sig.should_trade is True


class TestWalletVeto:
    """Elite wallet disagreement suppresses trades (wallet veto)."""

    def test_opposing_wallet_suppresses_trade(self):
        with patch("signals.claude_signal.ENABLE_WALLET_VETO", True):
            # Claude says YES, wallet says NO → veto
            wallets = [{"market_id": "m1", "outcome": "NO",
                        "wallet": "0xabc", "win_rate": 0.65, "size_usd": 500}]
            sig = _build_signal(mkt(yes=0.40), result(yes_prob=0.65), wallets)
            assert sig.should_trade is False, (
                "Elite wallet on opposite side should veto the trade"
            )

    def test_aligned_wallet_does_not_suppress(self):
        with patch("signals.claude_signal.ENABLE_WALLET_VETO", True):
            # Claude says YES, wallet says YES → no veto
            wallets = [{"market_id": "m1", "outcome": "YES",
                        "wallet": "0xabc", "win_rate": 0.65, "size_usd": 500}]
            sig = _build_signal(mkt(yes=0.40), result(yes_prob=0.65), wallets)
            assert sig.should_trade is True

    def test_veto_disabled_allows_opposing_wallet(self):
        with patch("signals.claude_signal.ENABLE_WALLET_VETO", False):
            wallets = [{"market_id": "m1", "outcome": "NO",
                        "wallet": "0xabc", "win_rate": 0.65, "size_usd": 500}]
            sig = _build_signal(mkt(yes=0.40), result(yes_prob=0.65), wallets)
            # Without veto, wallet disagreement is noted but doesn't block
            assert sig.should_trade is True


class TestEdgeDirectionConsistency:
    """Edge sign and direction must always be consistent."""

    def test_positive_edge_always_means_yes(self):
        for prob, market_p in [(0.65, 0.40), (0.80, 0.50), (0.55, 0.45)]:
            sig = _build_signal(mkt(yes=market_p), result(yes_prob=prob), [])
            assert sig.direction == "YES", f"Positive edge must give YES for prob={prob}, mkt={market_p}"
            assert sig.edge > 0

    def test_negative_edge_always_means_no(self):
        for prob, market_p in [(0.35, 0.60), (0.20, 0.70), (0.45, 0.55)]:
            sig = _build_signal(mkt(yes=market_p), result(yes_prob=prob), [])
            assert sig.direction == "NO", f"Negative edge must give NO for prob={prob}, mkt={market_p}"
            assert sig.edge < 0

    def test_edge_magnitude_equals_abs_probability_difference(self):
        market_p, claude_p = 0.40, 0.65
        sig = _build_signal(mkt(yes=market_p), result(yes_prob=claude_p), [])
        assert abs(sig.edge) == pytest.approx(abs(claude_p - market_p))
