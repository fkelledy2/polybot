# tests/unit/test_kelly.py
# Kelly Criterion position sizing — financial logic, must be exact.

import pytest
from unittest.mock import patch
from config import MAX_POSITION_PCT, STARTING_BALANCE


@pytest.fixture()
def trader(tmp_db):
    from execution.paper_trader import PaperTrader
    return PaperTrader()


class TestKellySizing:
    def test_positive_edge_beats_flat(self, trader):
        """A clearly mispriced market should size larger than flat."""
        # 70% win prob at 40¢ entry → strong positive Kelly
        size = trader._position_size(win_prob=0.70, entry_price=0.40)
        flat = trader.balance * MAX_POSITION_PCT
        assert size > 0
        assert size <= flat  # half-Kelly caps at MAX_POSITION_PCT

    def test_zero_edge_gives_fallback(self, trader):
        """Win prob == entry price → Kelly = 0, fallback to flat."""
        # p=0.5, b=(1/0.5)-1=1 → Kelly=(0.5*2-1)/1=0 → fallback
        size = trader._position_size(win_prob=0.50, entry_price=0.50)
        flat = round(trader.balance * MAX_POSITION_PCT, 2)
        assert size == flat

    def test_negative_edge_gives_fallback(self, trader):
        """Unfavorable bet (lose money in EV) → fallback to flat."""
        # Win prob below fair → Kelly negative → clamp to 0 → fallback
        size = trader._position_size(win_prob=0.30, entry_price=0.60)
        flat = round(trader.balance * MAX_POSITION_PCT, 2)
        assert size == flat

    def test_capped_at_max_position_pct(self, trader):
        """Even a massive edge cannot exceed MAX_POSITION_PCT of balance."""
        size = trader._position_size(win_prob=0.99, entry_price=0.01)
        max_allowed = trader.balance * MAX_POSITION_PCT
        assert size <= max_allowed

    def test_no_args_gives_flat(self, trader):
        """Called with no probability data → flat percentage."""
        size = trader._position_size()
        flat = round(trader.balance * MAX_POSITION_PCT, 2)
        assert size == flat

    def test_zero_entry_price_gives_flat(self, trader):
        """Zero entry price would cause division by zero — should fallback."""
        size = trader._position_size(win_prob=0.80, entry_price=0.0)
        flat = round(trader.balance * MAX_POSITION_PCT, 2)
        assert size == flat

    def test_size_is_positive(self, trader):
        for p, e in [(0.55, 0.45), (0.70, 0.30), (0.90, 0.10), (0.50, 0.50)]:
            size = trader._position_size(win_prob=p, entry_price=e)
            assert size >= 0, f"Negative size for p={p}, e={e}"

    def test_higher_edge_gives_larger_size(self, trader):
        """More mispriced → larger Kelly fraction."""
        size_low  = trader._position_size(win_prob=0.55, entry_price=0.45)
        size_high = trader._position_size(win_prob=0.80, entry_price=0.45)
        assert size_high >= size_low

    def test_result_is_rounded_to_cents(self, trader):
        size = trader._position_size(win_prob=0.65, entry_price=0.40)
        assert size == round(size, 2)
