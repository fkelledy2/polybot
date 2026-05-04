"""
Adversarial Input Tests
========================
What happens when the environment is hostile? Real systems receive garbage
data, empty responses, boundary values, and race conditions. The bot must
degrade gracefully — never crash, never produce phantom trades or PnL,
never lock up capital without recording it.
"""
import pytest
from unittest.mock import patch, MagicMock
from signals.claude_signal import _build_signal, TradeSignal
from execution.resolver import check_stop_losses


def mkt(yes=0.50, market_id="m1"):
    return {"market_id": market_id, "question": "Will X happen?", "yes": yes}


class TestMalformedClaudeOutputs:
    """Claude returning bad JSON/types must not crash signal building."""

    def test_non_numeric_probability_returns_none(self):
        bad = {"market_id": "m1", "yes_probability": "definitely",
               "confidence": "high", "reasoning": "test"}
        sig = _build_signal(mkt(), bad, [])
        assert sig is None

    def test_probability_above_one_clamped_or_rejected(self):
        bad = {"market_id": "m1", "yes_probability": 1.5,
               "confidence": "high", "reasoning": "test"}
        sig = _build_signal(mkt(), bad, [])
        # Either None (rejected) or clamped to valid range
        if sig is not None:
            assert 0 < sig.claude_yes_probability <= 1.0

    def test_probability_below_zero_clamped_or_rejected(self):
        bad = {"market_id": "m1", "yes_probability": -0.5,
               "confidence": "medium", "reasoning": "test"}
        sig = _build_signal(mkt(), bad, [])
        if sig is not None:
            assert sig.claude_yes_probability >= 0

    def test_missing_reasoning_field_returns_signal(self):
        """Missing reasoning is annoying but must not crash."""
        r = {"market_id": "m1", "yes_probability": 0.65, "confidence": "medium"}
        sig = _build_signal(mkt(), r, [])
        assert sig is not None

    def test_unknown_confidence_value_handled(self):
        r = {"market_id": "m1", "yes_probability": 0.65,
             "confidence": "SUPER_HIGH", "reasoning": "test"}
        sig = _build_signal(mkt(), r, [])
        # Should not crash; should trade is implementation-defined for unknown confidence
        # but signal should exist
        assert sig is not None

    def test_null_reasoning_handled(self):
        r = {"market_id": "m1", "yes_probability": 0.65,
             "confidence": "medium", "reasoning": None}
        sig = _build_signal(mkt(), r, [])
        # Should not crash
        assert sig is not None


class TestMarketBoundaryPrices:
    """Prices at or near 0% and 100% can break arithmetic if not handled."""

    def test_yes_price_near_zero_signal_builds(self):
        sig = _build_signal(mkt(yes=0.01), {"market_id": "m1", "yes_probability": 0.05,
                                             "confidence": "medium", "reasoning": "r"}, [])
        assert sig is not None
        assert sig.market_yes_price == pytest.approx(0.01)

    def test_yes_price_near_one_signal_builds(self):
        sig = _build_signal(mkt(yes=0.99), {"market_id": "m1", "yes_probability": 0.90,
                                             "confidence": "high", "reasoning": "r"}, [])
        assert sig is not None

    def test_trade_at_extreme_yes_price_no_division_by_zero(self, paper_trader):
        """Shares = size / entry_price. entry_price near 0 → huge shares — must not crash."""
        sig = TradeSignal(
            market_id="m_extreme",
            question="Extreme?",
            market_yes_price=0.03,
            claude_yes_probability=0.25,
            edge=0.22,
            direction="YES",
            confidence="high",
            reasoning="extreme test",
            wallet_alignment=False,
            should_trade=True,
        )
        trade = paper_trader.place_trade(sig)
        assert trade is not None
        assert trade.shares > 0
        assert trade.size_usd > 0
        assert paper_trader.balance >= 0  # must not go negative

    def test_trade_at_extreme_no_price_no_division_by_zero(self, paper_trader):
        """NO entry price = 1 - 0.97 = 0.03 → same extreme case."""
        sig = TradeSignal(
            market_id="m_extreme_no",
            question="Extreme NO?",
            market_yes_price=0.97,
            claude_yes_probability=0.75,
            edge=-0.22,
            direction="NO",
            confidence="high",
            reasoning="extreme NO test",
            wallet_alignment=False,
            should_trade=True,
        )
        trade = paper_trader.place_trade(sig)
        assert trade is not None
        assert trade.entry_price == pytest.approx(0.03)


class TestPaperTraderRobustness:
    """The paper trader must not crash or corrupt state on bad inputs."""

    def test_close_nonexistent_market_returns_none(self, paper_trader):
        result = paper_trader.close_trade("nonexistent_market_id", resolved_yes=True)
        assert result is None

    def test_close_nonexistent_does_not_change_balance(self, paper_trader):
        before = paper_trader.balance
        paper_trader.close_trade("nonexistent", resolved_yes=True)
        assert paper_trader.balance == pytest.approx(before)

    def test_place_trade_with_zero_edge_still_sizes_correctly(self, paper_trader):
        sig = TradeSignal(
            market_id="zero_edge",
            question="Zero edge?",
            market_yes_price=0.50,
            claude_yes_probability=0.50,
            edge=0.0,
            direction="YES",
            confidence="medium",
            reasoning="zero edge",
            wallet_alignment=False,
            should_trade=True,
        )
        trade = paper_trader.place_trade(sig)
        # Should use Kelly fallback (flat MAX_POSITION_PCT) — not crash
        assert trade is not None
        assert trade.size_usd > 0

    def test_balance_never_goes_negative(self, paper_trader):
        """A sequence of max-sized trades must not take balance negative."""
        from config import MAX_OPEN_POSITIONS
        sigs = []
        for i in range(MAX_OPEN_POSITIONS):
            sig = TradeSignal(
                market_id=f"m{i}", question="Q?",
                market_yes_price=0.40, claude_yes_probability=0.65,
                edge=0.25, direction="YES", confidence="high",
                reasoning="r", wallet_alignment=False, should_trade=True,
            )
            paper_trader.place_trade(sig)
            sigs.append(sig)

        for s in sigs:
            paper_trader.close_trade(s.market_id, resolved_yes=False)

        assert paper_trader.balance >= 0, "Balance must never go negative"

    def test_duplicate_close_does_not_double_count_pnl(self, paper_trader):
        from config import STARTING_BALANCE
        sig = TradeSignal(
            market_id="m1", question="Q?",
            market_yes_price=0.40, claude_yes_probability=0.65,
            edge=0.25, direction="YES", confidence="medium",
            reasoning="r", wallet_alignment=False, should_trade=True,
        )
        paper_trader.place_trade(sig)
        paper_trader.close_trade("m1", resolved_yes=True)
        balance_after_first_close = paper_trader.balance
        # Second close on same market — must be no-op
        result = paper_trader.close_trade("m1", resolved_yes=True)
        assert result is None
        assert paper_trader.balance == pytest.approx(balance_after_first_close)


class TestStopLossRobustness:
    """check_stop_losses must never crash regardless of input quality."""

    def test_empty_open_positions_no_crash(self, paper_trader):
        markets = [{"market_id": "m1", "yes": 0.10}]
        result = check_stop_losses(paper_trader, markets)
        assert result == []

    def test_empty_markets_no_crash(self, paper_trader):
        result = check_stop_losses(paper_trader, [])
        assert result == []

    def test_market_missing_yes_price_skipped(self, paper_trader):
        sig = TradeSignal(
            market_id="m1", question="Q?",
            market_yes_price=0.50, claude_yes_probability=0.75,
            edge=0.25, direction="YES", confidence="medium",
            reasoning="r", wallet_alignment=False, should_trade=True,
        )
        paper_trader.place_trade(sig)
        markets = [{"market_id": "m1"}]  # no "yes" key
        result = check_stop_losses(paper_trader, markets)
        assert result == []  # skipped, not crashed

    def test_position_with_no_edge_skipped(self, paper_trader):
        """edge=0 → skip stop check to avoid 0/0 threshold."""
        sig = TradeSignal(
            market_id="m1", question="Q?",
            market_yes_price=0.50, claude_yes_probability=0.50,
            edge=0.0, direction="YES", confidence="medium",
            reasoning="r", wallet_alignment=False, should_trade=True,
        )
        paper_trader.place_trade(sig)
        # Price crashes — but edge=0 means stop is skipped
        result = check_stop_losses(paper_trader, [{"market_id": "m1", "yes": 0.01}])
        assert result == []


class TestEnrichmentFailureIsolation:
    """A failing enricher must not break the scan loop or suppress valid signals."""

    def test_all_enrichers_fail_returns_empty_dict(self):
        from data.enrichment.dispatcher import enrich_markets
        markets = [{"market_id": "m1", "question": "Will X happen?", "yes": 0.50}]
        with patch("data.enrichment.dispatcher._safe", return_value=""):
            result = enrich_markets(markets)
        # Empty enrichment is fine — no crash
        assert isinstance(result, dict)

    def test_single_enricher_exception_does_not_propagate(self):
        from data.enrichment.dispatcher import _safe

        def always_raises(*a, **kw):
            raise RuntimeError("API exploded")

        result = _safe(always_raises)
        assert result == ""
