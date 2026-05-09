# signals/wallet_surge.py
# ─────────────────────────────────────────────────────────────
# FEAT-06: Detect when multiple elite wallets newly enter the
# same market direction between consecutive scans.
#
# Works by diffing consecutive WalletConsensus snapshots — no
# extra API calls beyond what get_elite_consensus() already makes.
#
# A "surge" occurs when:
#   - A market is brand-new in consensus with ≥2 wallets, OR
#   - An existing market gained ≥2 new wallet entries since last scan
# ─────────────────────────────────────────────────────────────

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_MIN_WALLETS_FOR_SURGE    = 2   # new entries needed to call it a surge
_NEW_MARKET_MIN_WALLETS   = 2   # brand-new market must have ≥ this many wallets


@dataclass
class SurgeSignal:
    condition_id: str
    question: str
    direction: str
    new_wallet_count: int
    total_wallet_count: int
    consensus_score: float

    def to_enrichment_str(self) -> str:
        return (
            f"WALLET SURGE: {self.new_wallet_count} new elite wallet(s) entered "
            f"{self.direction} (total={self.total_wallet_count}, "
            f"consensus={self.consensus_score:.0%})"
        )


class WalletSurgeDetector:
    """
    Maintains a snapshot of the previous scan's wallet consensus and diffs
    it against the current scan to surface newly-forming position clusters.
    """

    def __init__(self):
        # condition_id -> WalletConsensus from the previous scan
        self._prev_consensus: dict = {}

    def detect(self, new_consensus: dict) -> dict[str, SurgeSignal]:
        """
        Compare new_consensus to the previous snapshot.

        Args:
            new_consensus: dict[condition_id → WalletConsensus]

        Returns:
            dict[condition_id → SurgeSignal] for markets with surge activity.
        """
        surges: dict[str, SurgeSignal] = {}

        for cid, wc in new_consensus.items():
            prev = self._prev_consensus.get(cid)

            if prev is None:
                # Market not seen last scan → entirely new cluster forming
                if wc.trader_count >= _NEW_MARKET_MIN_WALLETS:
                    surges[cid] = SurgeSignal(
                        condition_id=cid,
                        question=wc.question,
                        direction=wc.winning_direction,
                        new_wallet_count=wc.trader_count,
                        total_wallet_count=wc.trader_count,
                        consensus_score=wc.consensus_score,
                    )
                    logger.info(
                        f"Wallet surge (new market): {wc.trader_count} wallets → "
                        f"{wc.winning_direction} on '{wc.question[:50]}'"
                    )
            else:
                # Existing market — check if trader count jumped
                new_entries = wc.trader_count - prev.trader_count
                if new_entries >= _MIN_WALLETS_FOR_SURGE:
                    surges[cid] = SurgeSignal(
                        condition_id=cid,
                        question=wc.question,
                        direction=wc.winning_direction,
                        new_wallet_count=new_entries,
                        total_wallet_count=wc.trader_count,
                        consensus_score=wc.consensus_score,
                    )
                    logger.info(
                        f"Wallet surge (+{new_entries} wallets): "
                        f"{wc.winning_direction} on '{wc.question[:50]}'"
                    )

        self._prev_consensus = dict(new_consensus)

        if surges:
            logger.info(f"Surge detector: {len(surges)} surge(s) found this scan")
        return surges
