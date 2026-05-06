# tests/unit/test_wallet_consensus_signal.py
# Tests that _build_signal correctly uses WalletConsensus for alignment/veto.
import pytest
from signals.claude_signal import _build_signal
from data.wallet_tracker import WalletConsensus


def _mkt(yes=0.40, cid="0xabc"):
    return {"market_id": "m1", "condition_id": cid, "question": "Will X?",
            "yes": yes, "no": 1 - yes}


def _res(yes_prob=0.65, confidence="medium"):
    return {"market_id": "m1", "yes_probability": yes_prob,
            "confidence": confidence, "reasoning": "test"}


def _wc(direction="YES", score=0.80, trader_count=5, yes_count=4, no_count=1,
        cid="0xabc"):
    return WalletConsensus(
        condition_id=cid, question="Will X?",
        winning_direction=direction, consensus_score=score,
        trader_count=trader_count, yes_count=yes_count, no_count=no_count,
        raw_usd=5000.0, weighted_usd=8000.0,
    )


def test_consensus_alignment_yes():
    sig = _build_signal(_mkt(), _res(yes_prob=0.65), wallet_consensus={"0xabc": _wc("YES")})
    assert sig.wallet_alignment is True


def test_consensus_alignment_no():
    # Claude says YES (edge positive), consensus says NO → misaligned
    sig = _build_signal(_mkt(), _res(yes_prob=0.65), wallet_consensus={"0xabc": _wc("NO")})
    assert sig.wallet_alignment is False


def test_consensus_missing_cid_no_alignment():
    # Market has no condition_id — no wallet data
    mkt = {"market_id": "m1", "question": "Will X?", "yes": 0.40, "no": 0.60}
    sig = _build_signal(mkt, _res(yes_prob=0.65), wallet_consensus={"0xabc": _wc("YES")})
    assert sig.wallet_alignment is False


def test_consensus_none_means_no_wallet_data():
    sig = _build_signal(_mkt(), _res(yes_prob=0.65), wallet_consensus=None)
    assert sig.wallet_alignment is False


def test_consensus_prefers_over_legacy_signals():
    # Both consensus (YES) and legacy signals (NO) provided — consensus wins
    legacy = [{"market_id": "m1", "outcome": "NO", "win_rate": 0.6,
               "total_trades": 100, "wallet": "0x1234", "size_usd": 100}]
    sig = _build_signal(_mkt(), _res(yes_prob=0.65),
                        wallet_signals=legacy,
                        wallet_consensus={"0xabc": _wc("YES")})
    assert sig.wallet_alignment is True
