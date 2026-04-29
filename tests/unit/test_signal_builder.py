# tests/unit/test_signal_builder.py
# Tests for _build_signal — the function that converts Claude's JSON into a TradeSignal.

import pytest
from signals.claude_signal import _build_signal


def make_market(yes=0.40, market_id="m1", question="Will X happen?"):
    return {"market_id": market_id, "question": question, "yes": yes}


def make_result(yes_prob=0.60, confidence="medium", reasoning="Test reason"):
    return {"market_id": "m1", "yes_probability": yes_prob,
            "confidence": confidence, "reasoning": reasoning}


class TestBuildSignal:
    def test_yes_direction_when_claude_higher(self):
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.65), [])
        assert sig.direction == "YES"
        assert sig.edge > 0

    def test_no_direction_when_claude_lower(self):
        sig = _build_signal(make_market(yes=0.70), make_result(yes_prob=0.45), [])
        assert sig.direction == "NO"
        assert sig.edge < 0

    def test_edge_magnitude_correct(self):
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.65), [])
        assert sig.edge == pytest.approx(0.25)

    def test_should_trade_false_for_low_confidence(self):
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.65, confidence="low"), [])
        assert sig.should_trade is False

    def test_should_trade_true_with_sufficient_edge(self):
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.60, confidence="medium"), [])
        # edge = 0.20 >= MIN_EDGE_TO_TRADE (0.12)
        assert sig.should_trade is True

    def test_should_trade_false_with_small_edge(self):
        sig = _build_signal(make_market(yes=0.48), make_result(yes_prob=0.52, confidence="medium"), [])
        # edge = 0.04 < 0.12
        assert sig.should_trade is False

    def test_wallet_alignment_detected(self):
        wallet_signals = [{"market_id": "m1", "outcome": "YES",
                           "wallet": "0xabc", "win_rate": 0.60, "size_usd": 100}]
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.65), wallet_signals)
        assert sig.wallet_alignment is True

    def test_wallet_misalignment_not_flagged(self):
        wallet_signals = [{"market_id": "m1", "outcome": "NO",
                           "wallet": "0xabc", "win_rate": 0.60, "size_usd": 100}]
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.65), wallet_signals)
        assert sig.wallet_alignment is False

    def test_wallet_alignment_can_unlock_borderline_trade(self):
        # Edge just below MIN_EDGE_TO_TRADE but wallet agrees → should still trade
        wallet_signals = [{"market_id": "m1", "outcome": "YES",
                           "wallet": "0xabc", "win_rate": 0.65, "size_usd": 200}]
        # edge = 0.10, which is 0.10 >= 0.12*0.8=0.096 → unlocked by wallet
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.50, confidence="medium"),
                            wallet_signals)
        assert sig.should_trade is True

    def test_reasoning_preserved(self):
        sig = _build_signal(make_market(), make_result(reasoning="Specific reason here"), [])
        assert sig.reasoning == "Specific reason here"

    def test_missing_market_id_returns_none(self):
        bad_result = {"yes_probability": 0.60, "confidence": "medium", "reasoning": "test"}
        sig = _build_signal(make_market(), bad_result, [])
        # _build_signal doesn't use result market_id for the signal itself
        assert sig is not None  # It uses market dict, not result dict for id

    def test_invalid_probability_returns_none(self):
        bad_result = {"market_id": "m1", "yes_probability": "not-a-number",
                      "confidence": "medium", "reasoning": "test"}
        sig = _build_signal(make_market(), bad_result, [])
        assert sig is None

    def test_claude_probability_stored(self):
        sig = _build_signal(make_market(yes=0.40), make_result(yes_prob=0.72), [])
        assert sig.claude_yes_probability == pytest.approx(0.72)

    def test_market_price_stored(self):
        sig = _build_signal(make_market(yes=0.35), make_result(yes_prob=0.60), [])
        assert sig.market_yes_price == pytest.approx(0.35)
