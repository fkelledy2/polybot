# tests/unit/test_arbitrage.py
# Tests for the arbitrage pair detector — especially the tournament guard
# introduced in Cycle 2 to prevent the Montenegro-style spurious-pair bug.

import pytest
from signals.arbitrage import find_arbitrage_pairs, is_tournament_market


def make_market(question, yes, market_id=None):
    return {
        "market_id": market_id or question[:8],
        "question": question,
        "yes": yes,
    }


# ── is_tournament_market() unit tests ────────────────────────────────────────

class TestIsTournamentMarket:
    def test_eurovision_advance_flagged(self):
        assert is_tournament_market(
            "Will Montenegro advance through the first Eurovision Semi-Final?"
        ) is True

    def test_eurovision_alone_flagged(self):
        assert is_tournament_market("Will Greece win the Eurovision Song Contest?") is True

    def test_semi_final_flagged(self):
        assert is_tournament_market("Will TeamA advance through the semi-final?") is True

    def test_qualify_flagged(self):
        assert is_tournament_market("Will France qualify for the knockout round?") is True

    def test_world_cup_advance_flagged(self):
        assert is_tournament_market(
            "Will England advance from the World Cup group stage?"
        ) is True

    def test_award_winner_flagged(self):
        # Award shows with multiple categories — a film winning Best Picture
        # doesn't prevent another from winning Best Director (non-exclusive).
        # The "award ... winner" pattern catches "award winner" ordering.
        assert is_tournament_market(
            "Will Film X be the award winner for Best Picture?"
        ) is True

    def test_nominated_flagged(self):
        assert is_tournament_market(
            "Will the album be nominated for a Grammy?"
        ) is True

    def test_regular_binary_not_flagged(self):
        assert is_tournament_market(
            "Will Elon Musk post fewer than 40 tweets this week?"
        ) is False

    def test_politics_election_not_flagged(self):
        assert is_tournament_market(
            "Will the Democratic candidate win the 2026 Senate race?"
        ) is False

    def test_sports_head_to_head_not_flagged(self):
        assert is_tournament_market(
            "Will the Los Angeles Angels beat the Toronto Blue Jays?"
        ) is False

    def test_ipo_market_not_flagged(self):
        assert is_tournament_market(
            "Will Cerebras' market cap be at least $50B at IPO?"
        ) is False


# ── find_arbitrage_pairs() integration tests ─────────────────────────────────

class TestFindArbitragePairs:
    def test_basic_underpriced_pair_detected(self):
        markets = [
            make_market("Will candidate Alice win the city council seat?", yes=0.40, market_id="a"),
            make_market("Will candidate Alice lose the city council seat race?", yes=0.40, market_id="b"),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 1
        assert pairs[0].direction == "UNDERPRICED"
        assert pairs[0].implied_sum == pytest.approx(0.80)

    def test_overpriced_pair_detected(self):
        markets = [
            make_market("Will company Acme stock rise above target price?", yes=0.70, market_id="a"),
            make_market("Will company Acme stock fall below target price?", yes=0.60, market_id="b"),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 1
        assert pairs[0].direction == "OVERPRICED"
        assert pairs[0].implied_sum == pytest.approx(1.30)

    def test_near_sum_pair_not_flagged(self):
        # Sum = 1.03 — within 5% gap threshold → not an arbitrage pair
        markets = [
            make_market("Will team Alpha beat team Beta in the finals?", yes=0.55, market_id="a"),
            make_market("Will team Alpha lose to team Beta in the finals?", yes=0.48, market_id="b"),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 0

    def test_tournament_markets_excluded(self):
        # Eurovision pair — both can advance in the semi-final (>10 qualifiers)
        # This was the root cause of the Montenegro loss (Cycle 2).
        markets = [
            make_market(
                "Will Montenegro advance through the first Eurovision Semi-Final?",
                yes=0.51, market_id="mont"
            ),
            make_market(
                "Will Greece advance through the first Eurovision Semi-Final?",
                yes=0.994, market_id="greece"
            ),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 0, (
            "Tournament markets must NOT be flagged as arbitrage pairs — "
            "multiple entrants can advance simultaneously (the sum need not equal 1.0)"
        )

    def test_qualify_tournament_markets_excluded(self):
        markets = [
            make_market("Will France qualify for the knockout stage?", yes=0.80, market_id="fra"),
            make_market("Will Germany qualify for the knockout stage?", yes=0.60, market_id="ger"),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 0

    def test_low_keyword_overlap_not_flagged(self):
        # Less than 3 shared keywords
        markets = [
            make_market("Will it rain tomorrow?", yes=0.30, market_id="a"),
            make_market("Will SpaceX launch Starship?", yes=0.80, market_id="b"),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 0

    def test_empty_market_list_returns_empty(self):
        assert find_arbitrage_pairs([]) == []

    def test_single_market_returns_empty(self):
        markets = [make_market("Will X happen?", yes=0.50)]
        assert find_arbitrage_pairs(markets) == []


# ── Cycle 2 regression guard ─────────────────────────────────────────────────

class TestCycle2Regressions:
    def test_montenegro_greece_pair_not_detected(self):
        """
        Regression guard: the Montenegro/Greece Eurovision pair that caused a
        -$36.77 loss in Cycle 2 must not be detected as an arbitrage opportunity.

        Root cause: the binary sum heuristic (YES_a + YES_b != 1.0) is invalid for
        multi-entrant tournament markets where multiple participants can advance.
        """
        markets = [
            make_market(
                "Will Montenegro advance through the first Eurovision Semi-Final?",
                yes=0.51, market_id="mont"
            ),
            make_market(
                "Will Greece advance through the Eurovision Semi-Final?",
                yes=0.994, market_id="greece"
            ),
        ]
        pairs = find_arbitrage_pairs(markets)
        assert len(pairs) == 0, (
            "Cycle 2 regression: Montenegro/Greece Eurovision pair must never "
            "trigger arbitrage signal (multi-entrant tournament)"
        )
