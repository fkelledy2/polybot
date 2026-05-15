# tests/unit/test_resolution_scorer.py
# Tests for the resolution ambiguity scorer (FEAT-05).
# Cycle 2 additions: IPO / stale-valuation patterns.

import pytest
from signals.resolution_scorer import score_ambiguity, ambiguity_label


class TestScoreAmbiguity:
    # ── Existing patterns (pre-Cycle 2) ──────────────────────────────────────

    def test_clear_criteria_scores_low(self):
        criteria = "Resolves YES if the Federal Reserve raises rates at the May 2026 meeting, according to the official FOMC announcement."
        assert score_ambiguity(criteria) < 0.35

    def test_discretion_scores_high(self):
        criteria = "Resolution is at Polymarket's discretion based on available information."
        assert score_ambiguity(criteria) >= 0.35

    def test_empty_criteria_gets_penalty(self):
        assert score_ambiguity("") == pytest.approx(0.20)

    def test_short_criteria_gets_penalty(self):
        assert score_ambiguity("Yes if true.") == pytest.approx(0.20)

    def test_significant_language_adds_score(self):
        criteria = "Resolves YES if there is a significant improvement in relations."
        score = score_ambiguity(criteria)
        assert score >= 0.15

    def test_official_source_reduces_score(self):
        high_without = score_ambiguity("Resolves if substantial changes occur.")
        lower_with = score_ambiguity(
            "Resolves if substantial changes occur, as reported by official government data."
        )
        assert lower_with < high_without

    # ── Cycle 2: IPO / valuation market patterns ─────────────────────────────

    def test_ipo_criteria_scores_elevated(self):
        """
        IPO markets require real-time pricing data Claude cannot have.
        Regression guard: Cerebras-style markets must score >= AMBIGUITY_WARN_THRESHOLD.
        """
        criteria = (
            "Resolves YES if Cerebras' market cap is at least $50 billion "
            "at the close of trading on IPO day."
        )
        score = score_ambiguity(criteria)
        assert score >= 0.20, (
            f"IPO market cap criteria scored {score:.3f} — expected >= 0.20. "
            "Cycle 2 lesson: Cerebras IPO market should have been flagged as "
            "ambiguous due to stale valuation data."
        )

    def test_ipo_market_cap_phrase_elevates_score(self):
        criteria = "Resolves YES if the company's market cap exceeds $10B on IPO day."
        score = score_ambiguity(criteria)
        assert score >= 0.20

    def test_ipo_valuation_phrase_elevates_score(self):
        criteria = "Resolves YES if IPO valuation is above $5 billion at listing."
        score = score_ambiguity(criteria)
        assert score >= 0.20

    def test_non_ipo_market_not_affected(self):
        criteria = "Resolves YES if the S&P 500 closes above 5,000 on June 1, 2026."
        score = score_ambiguity(criteria)
        # Should stay low — no IPO language
        assert score < 0.20

    # ── Cycle 2 regression guard ─────────────────────────────────────────────

    def test_cerebras_ipo_market_gets_warn_threshold(self):
        """
        Regression guard for the Cerebras -$34.06 loss in Cycle 2.
        The market's resolution criteria contain 'market cap' + 'IPO' — the ambiguity
        scorer must flag this at or above the WARN threshold (0.35) so the bot logs
        a warning, even if it doesn't block the trade outright.
        """
        from config import AMBIGUITY_WARN_THRESHOLD
        criteria = (
            "This market will resolve YES if Cerebras' market cap is at least $50 billion "
            "at market close on IPO day."
        )
        score = score_ambiguity(criteria)
        assert score >= AMBIGUITY_WARN_THRESHOLD, (
            f"Cerebras-style IPO market cap criteria scored {score:.3f} — "
            f"must be >= AMBIGUITY_WARN_THRESHOLD ({AMBIGUITY_WARN_THRESHOLD}). "
            "Cycle 2 regression: this market cost -$34.06 because stale valuation data."
        )


class TestAmbiguityLabel:
    def test_low_score_is_clear(self):
        assert ambiguity_label(0.10) == "clear"

    def test_moderate_score(self):
        assert ambiguity_label(0.25) == "moderate"

    def test_ambiguous_score(self):
        assert ambiguity_label(0.45) == "ambiguous"

    def test_high_score_is_highly_ambiguous(self):
        assert ambiguity_label(0.70) == "highly_ambiguous"
