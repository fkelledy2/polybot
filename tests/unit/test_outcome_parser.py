# tests/unit/test_outcome_parser.py
# Tests for execution/resolver._parse_outcome and _fetch_market error paths.
# This logic decides who won — getting it wrong closes trades incorrectly.

import pytest
from execution.resolver import _parse_outcome


class TestParseOutcome:
    def test_yes_wins_when_price_near_one(self):
        market = {"outcomePrices": '["0.99","0.01"]'}
        assert _parse_outcome(market) is True

    def test_yes_wins_just_above_threshold(self):
        market = {"outcomePrices": '["0.86","0.14"]'}
        assert _parse_outcome(market) is True

    def test_no_wins_when_price_near_zero(self):
        market = {"outcomePrices": '["0.01","0.99"]'}
        assert _parse_outcome(market) is False

    def test_no_wins_just_below_threshold(self):
        market = {"outcomePrices": '["0.14","0.86"]'}
        assert _parse_outcome(market) is False

    def test_unresolved_returns_none(self):
        market = {"outcomePrices": '["0.50","0.50"]'}
        assert _parse_outcome(market) is None

    def test_borderline_just_below_yes_threshold(self):
        # 0.84 < 0.85 → not YES; 0.84 > 0.15 → not NO → None
        market = {"outcomePrices": '["0.84","0.16"]'}
        assert _parse_outcome(market) is None

    def test_borderline_just_above_no_threshold(self):
        # 0.16 > 0.15 → not NO; 0.16 < 0.85 → not YES → None
        market = {"outcomePrices": '["0.16","0.84"]'}
        assert _parse_outcome(market) is None

    def test_prices_as_list_not_string(self):
        """API can return prices as actual list, not JSON string."""
        market = {"outcomePrices": ["0.97", "0.03"]}
        assert _parse_outcome(market) is True

    def test_malformed_prices_returns_none(self):
        market = {"outcomePrices": "not-valid-json"}
        assert _parse_outcome(market) is None

    def test_missing_prices_returns_none(self):
        market = {}
        assert _parse_outcome(market) is None

    def test_empty_prices_returns_none(self):
        market = {"outcomePrices": "[]"}
        assert _parse_outcome(market) is None
