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
        }
        for expected_cat, q in questions.items():
            cat, ctx = get_category_context(q)
            assert cat == expected_cat, f"Expected {expected_cat} for: {q}"
            assert len(ctx) > 20
