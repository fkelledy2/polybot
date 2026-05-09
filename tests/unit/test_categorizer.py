# tests/unit/test_categorizer.py
# Pure regex logic — no mocks, no DB, no network.

import pytest
from signals.categorizer import detect_category, get_category_context, CATEGORY_CONTEXT


class TestDetectCategory:
    def test_bitcoin_is_crypto(self):
        assert detect_category("Will Bitcoin hit $100k?") == "CRYPTO"

    def test_btc_abbreviation(self):
        assert detect_category("BTC price above $80,000 by April?") == "CRYPTO"

    def test_ethereum_is_crypto(self):
        assert detect_category("Will ETH reach all-time high?") == "CRYPTO"

    def test_nfl_is_sports(self):
        assert detect_category("Who will win the NFL Super Bowl?") == "SPORTS"

    def test_champions_league(self):
        assert detect_category("Will Arsenal win the Champions League?") == "SPORTS"

    def test_esports(self):
        assert detect_category("CS:GO — will Team A win the bo3?") == "SPORTS"

    def test_election_is_politics(self):
        assert detect_category("Will the incumbent win the 2026 election?") == "POLITICS"

    def test_trump(self):
        assert detect_category("Trump approval rating above 50%?") == "POLITICS"

    def test_fed_rate_is_macro(self):
        assert detect_category("Will the Fed cut rates in June?") == "MACRO"

    def test_cpi_inflation(self):
        assert detect_category("Will CPI inflation exceed 3% in Q2?") == "MACRO"

    def test_apple_is_tech(self):
        assert detect_category("Will Apple release the new iPhone by September?") == "TECH"

    def test_ai_is_tech(self):
        assert detect_category("Will GPT-5 be released this year?") == "TECH"

    def test_oscar_is_entertainment(self):
        assert detect_category("Which film will win the Oscar for Best Picture?") == "ENTERTAINMENT"

    def test_ukraine_is_geo(self):
        assert detect_category("Will there be a ceasefire in Ukraine by end of year?") == "GEO"

    def test_unknown_is_general(self):
        assert detect_category("Will the weather be nice on Saturday?") == "GENERAL"

    def test_empty_string_is_general(self):
        assert detect_category("") == "GENERAL"

    def test_case_insensitive(self):
        # All-caps should still match
        assert detect_category("BITCOIN PRICE ABOVE $100,000") == "CRYPTO"

    def test_highest_score_wins(self):
        # Multiple crypto signals should still beat a single sports mention
        q = "Will Bitcoin ETH crypto price exceed expectations in the NBA?"
        cat = detect_category(q)
        assert cat == "CRYPTO"

    def test_returns_string(self):
        result = detect_category("Anything at all")
        assert isinstance(result, str)

    # ── EARNINGS category tests (Cycle 1 tuning, 2026-05-09) ──────────────
    def test_earnings_beat_pattern(self):
        assert detect_category("Will IONQ (IONQ) beat quarterly earnings?") == "EARNINGS"

    def test_earnings_uber_pattern(self):
        assert detect_category("Will Uber (UBER) beat quarterly earnings?") == "EARNINGS"

    def test_earnings_eps_pattern(self):
        assert detect_category("Will Tesla beat EPS estimates for Q2?") == "EARNINGS"

    def test_earnings_revenue_estimate(self):
        # Avoid company names that hit TECH patterns; test the earnings keyword alone
        assert detect_category("Will the company beat revenue estimates this quarter?") == "EARNINGS"

    def test_earnings_q1_results(self):
        assert detect_category("Q1 results: will Nvidia beat consensus?") == "EARNINGS"

    def test_earnings_surprise(self):
        assert detect_category("Earnings surprise: will Microsoft beat Q3?") == "EARNINGS"

    def test_earnings_not_sports_beat(self):
        # "beat" in a sports context should NOT trigger EARNINGS
        # (sports regex also matches "beat" but EARNINGS patterns require "earnings" keyword)
        result = detect_category("Will Arsenal beat Chelsea in the Premier League?")
        assert result == "SPORTS"

    def test_earnings_context_has_claude_warning(self):
        _, ctx = get_category_context("Will IONQ beat quarterly earnings?")
        assert "claude" in ctx.lower() or "caution" in ctx.lower() or "base rate" in ctx.lower()


class TestGetCategoryContext:
    def test_returns_tuple(self):
        cat, ctx = get_category_context("Will Bitcoin reach 100k?")
        assert isinstance(cat, str)
        assert isinstance(ctx, str)

    def test_crypto_context_contains_halving(self):
        _, ctx = get_category_context("BTC halving effect on price?")
        assert "halving" in ctx.lower() or "crypto" in ctx.lower()

    def test_general_fallback(self):
        cat, ctx = get_category_context("Random unknowable question?")
        assert cat == "GENERAL"
        assert ctx == CATEGORY_CONTEXT["GENERAL"]

    def test_all_categories_have_context(self):
        questions = {
            "CRYPTO":        "Will BTC hit $100k?",
            "SPORTS":        "Will Arsenal win the Premier League?",
            "POLITICS":      "Will the senator win re-election?",
            "MACRO":         "Will the Fed cut rates?",
            "TECH":          "Will Apple release a new product?",
            "ENTERTAINMENT": "Who will win the Grammy award?",
            "GEO":           "Will the Ukraine war end this year?",
            "EARNINGS":      "Will Nvidia beat quarterly earnings?",
        }
        for expected_cat, q in questions.items():
            cat, ctx = get_category_context(q)
            assert cat == expected_cat, f"Expected {expected_cat} for: {q}"
            assert len(ctx) > 20


class TestCycle1RegressionGuards:
    """Regression guards added in Cycle 1 tuning (2026-05-09).
    These enforce that hard-won parameter decisions cannot silently regress.
    """

    def test_earnings_is_disabled_in_config(self):
        """EARNINGS had 0% win rate in production — must remain disabled."""
        from config import DISABLED_CATEGORIES
        assert "EARNINGS" in DISABLED_CATEGORIES, (
            "EARNINGS category must be disabled — 0W/2L in production "
            "(-$97.50). See Cycle 1 build log."
        )

    def test_crypto_is_disabled_in_config(self):
        """CRYPTO disabled pre-launch — must remain disabled."""
        from config import DISABLED_CATEGORIES
        assert "CRYPTO" in DISABLED_CATEGORIES

    def test_sports_is_disabled_in_config(self):
        """SPORTS had 25% WR in production — must remain disabled until 20+ trades."""
        from config import DISABLED_CATEGORIES
        assert "SPORTS" in DISABLED_CATEGORIES, (
            "SPORTS category must stay disabled until >= 20 post-filter trades. "
            "See Cycle 1 build log."
        )

    def test_min_entry_probability_at_least_15pct(self):
        """MIN_ENTRY_PROBABILITY < 15% caused 73% of realized losses in Cycle 1."""
        from config import MIN_ENTRY_PROBABILITY
        assert MIN_ENTRY_PROBABILITY >= 0.15, (
            f"MIN_ENTRY_PROBABILITY={MIN_ENTRY_PROBABILITY} must be >= 0.15. "
            "Sub-15% entries lost -$141.42 in the first 5 trading days."
        )

    def test_kelly_fraction_at_most_35pct(self):
        """KELLY_FRACTION lowered from 0.5 to 0.35 to reduce position size volatility."""
        from execution.paper_trader import KELLY_FRACTION
        assert KELLY_FRACTION <= 0.35, (
            f"KELLY_FRACTION={KELLY_FRACTION} must be <= 0.35. "
            "Higher values produce oversized positions on longshots."
        )
